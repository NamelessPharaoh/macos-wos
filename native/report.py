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
    ("Manual facts", [("manual.pet_slots_unlocked", "pet slots unlocked"), ("manual.hero_generation", "hero generation"),
                      ("manual.castle_battle_at", "castle battle (UTC)")]),
    ("Alliance", [("alliance.name", "alliance"), ("alliance.tag", "tag"), ("alliance.members", "members"),
                  ("alliance.state_rank", "state rank"), ("alliance.level", "level")]),
]
SPARKS = ["progress.power", "economy.gems", "troops.total.value", "economy.resources.coal", "progress.kills"]


def _n(v):
    return "-" if v is None else f"{int(v):,}"


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
        # The header describes a RUN; operator rows (--set) are not runs and
        # carry no gems/power, so the newest native/emulator snapshot is shown
        # while latest_static still honours operator values.
        snap = conn.execute("SELECT * FROM snapshots WHERE player_id = ? AND status != 'aborted' "
                            "AND source != 'operator' ORDER BY id DESC LIMIT 1", (player_id,)).fetchone()
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
    out.append(f"gems {_n(snap['gems_before'])} -> {_n(snap['gems_after'])}   power {_n(snap['power_before'])} -> {_n(snap['power_after'])}"
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
            lv = hv.get("level"); st = hv.get("stars")
            out.append(f"  {str(hv.get('name', key)):16s} {str(hv.get('rarity', '-')):7s} "
                       f"lv {('-' if lv is None else str(int(lv))):>3s} {('-' if st is None else str(int(st)))}* "
                       f"{int(hv.get('power') or 0):>12,}")
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
        return '<svg width="%d" height="%d"><text x="4" y="30" font-size="12">needs 2+ snapshots</text></svg>' % (width, height)
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = " ".join(f"{i * (width - 8) / (len(vals) - 1) + 4:.1f},{height - 4 - (v - lo) / span * (height - 8):.1f}"
                   for i, v in enumerate(vals))
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="{pts}"/>'
            f'<circle cx="{pts.split()[-1].split(",")[0]}" cy="{pts.split()[-1].split(",")[1]}" r="3.5" fill="var(--accent)"/></svg>')


def render_html(data, title="Chief Sheet"):
    snap = data["snapshot"]
    if snap is None:
        return f"<title>{title}</title><p>No snapshot yet.</p>"
    # Design: frost-blue accent (the game's HUD glyphs), blue-biased neutrals,
    # IBM Plex Sans for text and Plex Mono for the figures. Tokens carry both
    # themes; components only ever read the tokens.
    parts = [f"<title>{html.escape(title)}</title>",
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">',
             "<style>:root{--ground:#f5f8fc;--ink:#14213d;--mut:#5b6b85;--line:#d8e1ee;--panel:#ffffff;--accent:#2f7fd1;--good:#1f9d55;--bad:#c8402f;--warnbg:rgba(200,64,47,.07)}"
             "@media (prefers-color-scheme: dark){:root:not([data-theme=light]){--ground:#0f1626;--ink:#e6edf7;--mut:#9aa9c0;--line:#26324a;--panel:#141d30;--accent:#6fb1f0;--good:#4cc27e;--bad:#ef7a66;--warnbg:rgba(239,122,102,.10)}}"
             ":root[data-theme=dark]{--ground:#0f1626;--ink:#e6edf7;--mut:#9aa9c0;--line:#26324a;--panel:#141d30;--accent:#6fb1f0;--good:#4cc27e;--bad:#ef7a66;--warnbg:rgba(239,122,102,.10)}"
             "body{background:var(--ground);color:var(--ink);font:15px/1.5 'IBM Plex Sans',system-ui,sans-serif;margin:0;padding:32px 24px 64px;max-width:920px;margin-inline:auto}"
             "h1{font-size:26px;font-weight:600;margin:0;letter-spacing:-.01em;text-wrap:balance}.sub{color:var(--mut);margin:6px 0 28px;font-size:13px}"
             "h2{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);margin:28px 0 8px}"
             "table{border-collapse:collapse;width:100%}td{padding:7px 4px;border-bottom:1px solid var(--line)}td.n{text-align:right;font-family:'IBM Plex Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}"
             ".pos{color:var(--good)}.neg{color:var(--bad)}.tilde{color:var(--mut)}"
             ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px 14px}"
             ".card .k{font-size:12px;color:var(--mut);margin-bottom:6px;font-family:'IBM Plex Mono',ui-monospace,monospace}"
             ".warn{background:var(--warnbg);border-left:3px solid var(--bad);padding:12px 14px;border-radius:4px}svg text{fill:var(--mut)}"
             "@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}</style>",
             f"<h1>{html.escape(title)}</h1>",
             f"<div class=sub>snapshot {snap['id']} · {snap['taken_at'][:16].replace('T', ' ')} UTC · {snap['status']} · gems {_n(snap['gems_before'])} → {_n(snap['gems_after'])} · power {_n(snap['power_before'])} → {_n(snap['power_after'])}</div>"]
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
        parts.append(f"<div class=card><div class=k>{html.escape(path)}</div>{_spark(pts)}</div>")
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
