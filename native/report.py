"""The chief sheet: what the account looks like now, what changed, what the
readers could not trust. Terminal text by default; `--html` renders the same
data as a single page with sparklines from the series.

    latest_static ─▶ sheet rows          deltas() ─▶ change column
    fields.status ─▶ warning block       sections history ─▶ --doctor
"""
import html
import json
from collections import defaultdict

from native import model, schema

SHEET = [
    ("Identity", [("identity.name", "name"), ("identity.id", "id"), ("identity.state", "state"),
                  ("identity.state_age_days", "state age (days)")]),
    ("Progress", [("progress.power", "power"), ("progress.furnace.level", "furnace"),
                  ("progress.furnace.upgrading.remaining_s", "furnace upgrade (s left)"),
                  ("progress.vip.level", "VIP"), ("progress.kills", "kills")]),
    ("Economy", [("economy.gems", "gems"), ("economy.resources.meat", "meat"), ("economy.resources.wood", "wood"),
                 ("economy.resources.coal", "coal"), ("economy.resources.iron", "iron"),
                 ("economy.stamina.value", "stamina"), ("city.survivors.value", "survivors")]),
    ("Troops", [("troops.total.value", "total troops"), ("troops.totals.infantry", "infantry"),
                ("troops.totals.lancer", "lancer"), ("troops.totals.marksman", "marksman"),
                ("troops.wounded.value", "injured"), ("troops.march_queue.used", "marches used"),
                ("troops.march_queue.cap", "marches")]),
    ("City", [("city.buildings.furnace", "furnace"), ("city.buildings.storehouse", "storehouse"),
              ("city.buildings.research_center", "research center"), ("city.buildings.infantry_camp", "infantry camp"),
              ("city.buildings.lancer_camp", "lancer camp"), ("city.buildings.marksman_camp", "marksman camp"),
              ("research.current.name", "research now")]),
    ("Chief gear (stars)", [("gear.chief.helmet.stars", "helmet"), ("gear.chief.watch.stars", "watch"),
                            ("gear.chief.jacket.stars", "jacket"), ("gear.chief.pants.stars", "pants"),
                            ("gear.chief.ring.stars", "ring"), ("gear.chief.cane.stars", "cane")]),
    ("Alliance", [("alliance.name", "alliance"), ("alliance.tag", "tag"), ("alliance.members", "members"),
                  ("alliance.state_rank", "state rank"), ("alliance.level", "level")]),
]
SPARKS = ["progress.power", "economy.gems", "troops.total.value", "economy.resources.coal", "progress.kills"]


def _fmt(row):
    if row is None:
        return "-"
    v = row.get("value_num")
    if v is None:
        return row.get("value_text") or "-"
    v = int(v) if float(v).is_integer() else v
    s = f"{v:,}"
    return s + ("~" if not row.get("exact", 1) else "")


def build(conn, player_id, snapshot_id=None):
    """Everything the terminal and HTML renderers need, as plain data."""
    snap = None
    if snapshot_id is None:
        snap = conn.execute("SELECT * FROM snapshots WHERE player_id = ? AND status != 'aborted' "
                            "ORDER BY id DESC LIMIT 1", (player_id,)).fetchone()
        snapshot_id = snap["id"] if snap else None
    else:
        snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    latest = model.latest(conn, player_id)
    deltas = model.deltas(conn, player_id, snapshot_id) if snapshot_id else {}
    rows = conn.execute("SELECT path, status, raw, frame FROM fields WHERE snapshot_id = ?", (snapshot_id,)).fetchall() if snapshot_id else []
    sections = json.loads(snap["sections"]) if snap else {}
    warnings = {"rejected": [], "unread": [], "unmatched": [], "carried": 0, "unread_no_reader": 0}
    for r in rows:
        st = r["status"]
        if st.startswith("rejected"):
            warnings["rejected"].append((r["path"], st, r["raw"]))
        elif st == "unread":
            # Only a path whose reader RAN and still did not read it is a
            # warning; paths of readers that do not exist yet are expected gaps.
            if sections.get(schema.SECTION_OF_PATH(r["path"])) in ("ok", "partial"):
                warnings["unread"].append(r["path"])
            else:
                warnings["unread_no_reader"] += 1
        elif st == "unmatched-name":
            warnings["unmatched"].append(r["path"])
        elif st == "carried":
            warnings["carried"] += 1
    series = {p: model.series(conn, player_id, p) for p in SPARKS}
    heroes = defaultdict(dict)
    for path, row in model.latest_dynamic(conn, player_id, "heroes").items():
        parts = path.split(".")
        if len(parts) == 3 and parts[0] == "heroes":
            heroes[parts[1]][parts[2]] = row["value_num"] if row["value_num"] is not None else row["value_text"]
    return {"snapshot": dict(snap) if snap else None, "latest": latest, "deltas": deltas,
            "warnings": warnings, "sections": sections, "series": series, "heroes": dict(heroes)}


def render_text(data):
    out = []
    snap = data["snapshot"]
    if snap is None:
        return "no snapshot yet: run snapshot.py first"
    out.append(f"Chief sheet — snapshot {snap['id']} ({snap['taken_at']}) status={snap['status']} source={snap['source']}")
    out.append(f"gems {snap['gems_before']} -> {snap['gems_after']}   power {snap['power_before']:,} -> {snap['power_after']:,}"
               + ("   POWER ROSE" if snap["power_rose"] else "") + f"   {snap['duration_s'] or 0}s")
    for title, fields in SHEET:
        out.append(f"\n{title}")
        for path, label in fields:
            row = data["latest"].get(path)
            d = data["deltas"].get(path)
            change = ""
            if d and d[0] is not None and d[2] not in (None, 0):
                change = f"  ({'+' if d[2] > 0 else ''}{int(d[2]):,})"
            elif d and d[0] is None:
                change = "  (n/a)"
            out.append(f"  {label:26s} {_fmt(row):>16s}{change}")
    heroes = data.get("heroes") or {}
    if heroes:
        out.append(f"\nHeroes ({len(heroes)})")
        for key, hv in sorted(heroes.items(), key=lambda kv: -(kv[1].get("power") or 0))[:20]:
            out.append(f"  {hv.get('name', key):16s} {str(hv.get('rarity', '-')):7s} lv {str(hv.get('level', '-')):>3s} "
                       f"{str(hv.get('stars', '-'))}* {int(hv.get('power') or 0):>12,}")
    w = data["warnings"]
    out.append("\nWarnings")
    bad = [f"  section {n}: {s}" for n, s in data["sections"].items() if s not in ("ok",)]
    out.extend(bad)
    for path, st, raw in w["rejected"]:
        out.append(f"  {path}: {st} (raw {raw!r})")
    if w["unread"]:
        out.append(f"  unread ({len(w['unread'])}): " + ", ".join(w["unread"][:12]) + (" ..." if len(w["unread"]) > 12 else ""))
    if w["unmatched"]:
        out.append("  unmatched names: " + ", ".join(w["unmatched"]))
    if w["carried"]:
        out.append(f"  carried from earlier snapshots: {w['carried']} fields")
    if w["unread_no_reader"]:
        out.append(f"  {w['unread_no_reader']} paths have no reader yet (phase 2/3), not counted as warnings")
    if snap["power_rose"]:
        out.append("  power rose during the run: a timer completed, or something was spent")
    if len(out) and out[-1] == "\nWarnings":
        out.append("  none")
    return "\n".join(out)


def doctor(conn, player_id, runs=3):
    """Readers that failed on each of the last `runs` non-operator snapshots."""
    rows = conn.execute("SELECT id, sections FROM snapshots WHERE player_id = ? AND source != 'operator' "
                        "ORDER BY id DESC LIMIT ?", (player_id, runs)).fetchall()
    if len(rows) < runs:
        return f"doctor: only {len(rows)} snapshot(s) so far, need {runs}"
    fails = defaultdict(int)
    for r in rows:
        for name, st in json.loads(r["sections"]).items():
            if st in ("failed", "partial"):
                fails[name] += 1
    sick = [n for n, c in fails.items() if c == runs]
    return "doctor: " + (", ".join(f"{n} failed {runs} runs in a row" for n in sick) if sick else "every reader succeeded at least once in the last %d runs" % runs)


def _spark(points, width=320, height=48):
    vals = [v for _, _, v in points if v is not None]
    if len(vals) < 2:
        return '<svg width="%d" height="%d"><text x="4" y="30" font-size="12" fill="#888">needs 2+ snapshots</text></svg>' % (width, height)
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = " ".join(f"{i * (width - 8) / (len(vals) - 1) + 4:.1f},{height - 4 - (v - lo) / span * (height - 8):.1f}"
                   for i, v in enumerate(vals))
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<polyline fill="none" stroke="#3b82f6" stroke-width="2" points="{pts}"/></svg>')


def render_html(data, title="Chief Sheet"):
    snap = data["snapshot"]
    if snap is None:
        return f"<title>{title}</title><p>No snapshot yet.</p>"
    parts = [f"<title>{html.escape(title)}</title>",
             "<style>:root{--fg:#111;--bg:#fafafa;--mut:#666;--line:#e5e7eb}"
             "@media (prefers-color-scheme: dark){:root:not([data-theme=light]){--fg:#eee;--bg:#111;--mut:#aaa;--line:#333}}"
             ":root[data-theme=dark]{--fg:#eee;--bg:#111;--mut:#aaa;--line:#333}"
             "body{background:var(--bg);color:var(--fg);font:14px system-ui;margin:0;padding:24px;max-width:960px}"
             "h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);margin-bottom:20px}"
             "table{border-collapse:collapse;width:100%;margin-bottom:20px}td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}"
             "td.n{text-align:right;font-variant-numeric:tabular-nums}.pos{color:#16a34a}.neg{color:#dc2626}"
             ".warn{background:rgba(220,38,38,.08);padding:12px;border-radius:8px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}"
             ".card{border:1px solid var(--line);border-radius:8px;padding:12px}</style>",
             f"<h1>{html.escape(title)}</h1>",
             f"<div class=sub>snapshot {snap['id']} · {snap['taken_at']} · {snap['status']} · gems {snap['gems_before']}→{snap['gems_after']} · power {snap['power_before']:,}→{snap['power_after']:,}</div>"]
    for sec, fields in SHEET:
        parts.append(f"<h2>{sec}</h2><table>")
        for path, label in fields:
            row = data["latest"].get(path)
            d = data["deltas"].get(path)
            ch = ""
            if d and d[0] is not None and d[2] not in (None, 0):
                cls = "pos" if d[2] > 0 else "neg"
                ch = f"<span class={cls}>{'+' if d[2] > 0 else ''}{int(d[2]):,}</span>"
            parts.append(f"<tr><td>{html.escape(label)}</td><td class=n>{html.escape(_fmt(row))}</td><td class=n>{ch}</td></tr>")
        parts.append("</table>")
    parts.append("<h2>Trends</h2><div class=grid>")
    for path, pts in data["series"].items():
        parts.append(f"<div class=card><div>{html.escape(path)}</div>{_spark(pts)}</div>")
    parts.append("</div>")
    w = data["warnings"]
    items = [f"section {n}: {s}" for n, s in data["sections"].items() if s != "ok"]
    items += [f"{p}: {st}" for p, st, _ in w["rejected"]]
    if w["unread"]:
        items.append(f"unread: {len(w['unread'])} fields")
    if snap["power_rose"]:
        items.append("power rose during the run")
    parts.append("<h2>Warnings</h2><div class=warn>" + ("<br>".join(html.escape(i) for i in items) if items else "none") + "</div>")
    return "\n".join(parts)
