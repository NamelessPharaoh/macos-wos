"""Cross-check tables from sites whose terms restrict reproduction.

Everything here writes under knowledge/local/ (gitignored) and is never
committed (spec D8). One page fetch per table (wostools makes two: the
calculator page, then its data chunk -- B11), no crawling. The parsers are
plain HTML-table and JS-literal readers; when a page's shape is not
recognised they return {} and say so rather than guess.

    page ─fetch_text─▶ html/js ─parse─▶ {"furnace": {ordinal: row}} ─crosscheck─▶ crosscheck.json + overlay.json

Layering (B9/E1/R16): this module imports nothing from `native/`.
`furnace_ordinal` and the number/duration parsers live in `knowledge/util.py`
(moved out of `native/screen.py` by E1); `native/screen.py` re-exports them
for the readers, `native/kb.py` imports `furnace_ordinal` from the same
place. Nothing here reaches into `native.kb` or `native.screen`.

Scope (D-T2, binding over A5 and the task body -- a later, user-recorded
decision that sits above the A-amendments): cross-checks are report-only.
`crosscheck()` never edits the committed `knowledge/buildings.json` and
never writes `disputed`/`power` into anything the committed table already
covers. Only furnace levels with ordinal >= `FLOOR_ORDINAL` are parsed and
compared (the account is upgrading 26 -> 27; two known sub-26 disagreements
are deliberately out of scope). No prerequisite parsing at all -- it is the
part that breaks on the live pages. `crosscheck()` returns
`(report, overlay, lines)`: `report` (-> knowledge/local/crosscheck.json)
holds, per level >= FLOOR_ORDINAL, each source's costs/times and a
`disagrees` list; `overlay` (-> knowledge/local/overlay.json, consumed
verbatim by `native/kb.py::_apply_overlay`) holds ONLY the Fire Crystal rows
(ordinal 31-80) whiteoutdata carries that the committed table lacks
entirely, each with `prerequisites: {}` and a `"source"` marker.
"""
import html as htmllib
import re

from knowledge.fetch import fetch_text
from knowledge.normalise import slug
from knowledge.util import furnace_ordinal, parse_duration, parse_number

WHITEOUTDATA_FURNACE = "https://whiteoutdata.com/buildings/furnace/"
WIKI_FURNACE = "https://www.whiteoutsurvival.wiki/buildings/furnace/"
WOSTOOLS_BUILDINGS = "https://wostools.net/building-calculator"

# D-T2: the account is upgrading 26 -> 27; only furnace levels at or above
# this ordinal are parsed and compared. Also why the two known sub-26
# disagreements (whiteoutdata vs wosnerds at levels 11 and 17) are not this
# module's problem.
FLOOR_ORDINAL = 26

CHECKED = ("meat", "wood", "coal", "iron", "seconds")


# ----------------------------------------------------------------------------- text parsing
def parse_amount(text):
    """Web tables print '140M', '1,213,100', '132', '–'; knowledge.util's
    screen-facing parser (A4/E1) already handles every one of those shapes,
    so this is just the "missing/garbage -> 0" wrapper the local tables want."""
    v, _exact = parse_number(str(text).replace("–", "").replace("—", "").strip())
    return v or 0


def parse_time(text):
    return parse_duration(text) or 0


# ----------------------------------------------------------------------------- HTML tables
def parse_html_tables(page):
    """[[cells...], ...] per <table>, header row included, tags stripped."""
    tables = []
    for t in re.findall(r"<table.*?</table>", page, re.S):
        rows = []
        for r in re.findall(r"<tr.*?</tr>", t, re.S):
            cells = [htmllib.unescape(re.sub(r"<.*?>", "", c)).strip() for c in re.findall(r"<t[hd][^>]*>.*?</t[hd]>", r, re.S)]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _table_rows(rows, columns):
    """Map header names (lower-cased) to column indexes, yield one dict per
    data row keyed by `columns`' keys; a header the page doesn't have is
    simply absent from the dict rather than raising."""
    header = [h.lower() for h in rows[0]]
    idx = {}
    for key, names in columns.items():
        for n in names:
            if n in header:
                idx[key] = header.index(n)
                break
    for r in rows[1:]:
        if len(r) < len(header) - 1:
            continue
        yield {k: (r[i] if i < len(r) else "") for k, i in idx.items()}


def _drop_if_all_zero(name, rows):
    """Mirrors `wostools_buildings`: when a source's every returned row has
    zero cost and time, the parser found the 'Level' column (so it didn't
    bail out earlier) but not the ones that carry the actual numbers -- a
    header-label mismatch on the live page, not real data. A source that
    contributes nothing usable is dropped and named, never reported as a
    pile of disagreements (finding 2, 2026-09-09 fix round)."""
    if rows and all(all(int(row.get(f) or 0) == 0 for f in CHECKED) for row in rows.values()):
        print(f"{name}: every parsed row has zero cost/time (page shape probably changed); source dropped")
        return {}
    return rows


def whiteoutdata_furnace(page):
    """whiteoutdata.com's furnace page: two tables (pre-FC, FC), same
    Level/Requirements/Wood/Meat/Coal/Iron/[Fire Crystal/Refined FC]/Upgrade
    Time/Power shape. Rows are keyed by ordinal (B6/furnace_ordinal), floored
    at FLOOR_ORDINAL (D-T2). No prerequisite parsing (D-T2): the requirement
    text breaks on the live page ("Command Centre", "Lvl.", partial lists)."""
    columns = {"level": ["level"], "wood": ["wood"], "meat": ["meat"], "coal": ["coal"],
               "iron": ["iron"], "fc": ["fire crystal"], "rfc": ["refined fc"], "time": ["upgrade time"], "power": ["power"]}
    out = {}
    for rows in parse_html_tables(page):
        if "level" not in [h.lower() for h in rows[0]]:
            continue
        for d in _table_rows(rows, columns):
            label = d.get("level", "").strip()
            if not label or not re.match(r"(\d|FC)", label, re.I):
                continue
            try:
                ordinal = furnace_ordinal(label)
            except ValueError:
                continue
            if ordinal < FLOOR_ORDINAL:
                continue
            out[str(ordinal)] = {"label": label, "meat": parse_amount(d.get("meat")), "wood": parse_amount(d.get("wood")),
                                  "coal": parse_amount(d.get("coal")), "iron": parse_amount(d.get("iron")),
                                  "fire_crystals": parse_amount(d.get("fc")), "refined_fire_crystals": parse_amount(d.get("rfc")),
                                  "seconds": parse_time(d.get("time")), "power": parse_amount(d.get("power"))}
    return {"furnace": _drop_if_all_zero("whiteoutdata", out)}


def wiki_furnace(page):
    """whiteoutsurvival.wiki's furnace page: one table, costs folded into a
    single 'Build Cost' cell ('Meat 190M, Wood 190M, Coal 39M, Iron 9.9M').
    Floored at FLOOR_ORDINAL, no prerequisite parsing (D-T2, same as
    whiteoutdata_furnace)."""
    columns = {"level": ["level"], "cost": ["build cost", "cost"], "time": ["time", "upgrade time"], "power": ["power"]}
    out = {}
    for rows in parse_html_tables(page):
        if "level" not in [h.lower() for h in rows[0]]:
            continue
        for d in _table_rows(rows, columns):
            label = d.get("level", "").strip()
            try:
                ordinal = furnace_ordinal(label)
            except ValueError:
                continue
            if ordinal < FLOOR_ORDINAL:
                continue
            cost = {}
            for res, amt in re.findall(r"(Meat|Wood|Coal|Iron|Fire Crystal|Refined FC)\s*([\d.,]+[KMB]?)", d.get("cost", ""), re.I):
                cost[slug(res)] = parse_amount(amt)
            out[str(ordinal)] = {"label": label, "meat": cost.get("meat", 0), "wood": cost.get("wood", 0),
                                  "coal": cost.get("coal", 0), "iron": cost.get("iron", 0),
                                  "fire_crystals": cost.get("fire_crystal", 0), "refined_fire_crystals": cost.get("refined_fc", 0),
                                  "seconds": parse_time(d.get("time")), "power": parse_amount(d.get("power"))}
    return {"furnace": _drop_if_all_zero("wiki", out)}


# ----------------------------------------------------------------------------- wostools JS bundle
_JS_NUM = r"-?\d+(?:\.\d+)?(?:e\d+)?"


def _js_number(text):
    return int(round(float(text)))


def wostools_buildings(js):
    """Building tables from the calculator page's Next.js data chunk: JS
    object literals shaped {name:"Furnace",levels:[{level:27,meat:14e7,
    ...,time:2187780},...]}. Returns {} (with a printed reason) when no such
    object is found -- the bundle's shape changed, never guessed at."""
    out = {}
    for m in re.finditer(r'name:"([A-Za-z\'’ ]+)",levels:\[(.*?)\]\}', js, re.S):
        name, body = m.group(1), m.group(2)
        rows = {}
        for lv in re.finditer(r"\{level:(\d+),([^{}]*)\}", body, re.S):
            fields = dict(re.findall(rf"(\w+):({_JS_NUM})", lv.group(2)))
            rows[lv.group(1)] = {"meat": _js_number(fields.get("meat", 0)), "wood": _js_number(fields.get("wood", 0)),
                                  "coal": _js_number(fields.get("coal", 0)), "iron": _js_number(fields.get("iron", 0)),
                                  "fire_crystals": _js_number(fields.get("fireCrystal", 0)),
                                  "refined_fire_crystals": _js_number(fields.get("refined", 0)),
                                  "seconds": _js_number(fields.get("time", 0))}
        if rows:
            out[slug(name)] = rows
    if not out:
        print("wostools: no building objects recognised in the bundle; nothing written")
        return {}
    return {"buildings": out}


# ----------------------------------------------------------------------------- fetchers (LOCAL registry)
def _fetch_whiteoutdata(opener):
    return "whiteoutdata-furnace.json", whiteoutdata_furnace(fetch_text(WHITEOUTDATA_FURNACE, opener))


def _fetch_wiki(opener):
    return "wiki-furnace.json", wiki_furnace(fetch_text(WIKI_FURNACE, opener))


def _fetch_wostools(opener):
    """Two requests (B11): the calculator page, then its data chunk (the
    page itself carries no building numbers, just a <script src> to one)."""
    page = fetch_text(WOSTOOLS_BUILDINGS, opener)
    m = re.search(r'src="([^"]*_next/static/chunks/app/building-calculator/page-[^"]+\.js)"', page)
    if not m:
        print("wostools: building-calculator chunk not found in the page; nothing written")
        return "wostools-buildings.json", {}
    url = m.group(1) if m.group(1).startswith("http") else "https://wostools.net" + m.group(1)
    return "wostools-buildings.json", wostools_buildings(fetch_text(url, opener))


LOCAL = {"whiteoutdata_furnace": _fetch_whiteoutdata, "wiki_furnace": _fetch_wiki, "wostools_buildings": _fetch_wostools}


# ----------------------------------------------------------------------------- crosscheck
def _local_furnace_rows(doc):
    return doc.get("furnace") or (doc.get("buildings") or {}).get("furnace") or {}


def crosscheck(committed_buildings, local_docs, tolerance=0.02):
    """Report-only cross-check, from FLOOR_ORDINAL up (D-T2, binding over
    A5's original "annotate the committed table" design).

    For a level the committed table already has (ordinal FLOOR_ORDINAL..30):
    every source's costs/times are recorded verbatim in `report`, plus a
    `disagrees` list naming the "source.field" pairs that differ from the
    committed row by more than `tolerance` -- never written back onto the
    committed table, never a `power` copy. For a level the committed table
    lacks (an FC row, ordinal > 30): the full row is added to `overlay` from
    whiteoutdata only, marked `"source": "whiteoutdata"`, `prerequisites: {}`
    (D-T2: no prerequisite parsing at all).

    Returns (report, overlay, lines): both dicts are rebuilt from scratch
    every call (a changed local row is always re-diffed, never merged onto a
    stale one); `lines` is the human-readable report the refresh prints.
    """
    furnace_report = {}
    furnace_overlay = {}
    lines = []
    for source, doc in sorted(local_docs.items()):
        rows = _local_furnace_rows(doc)
        for ordinal, row in sorted(rows.items(), key=lambda kv: int(kv[0])):
            if int(ordinal) < FLOOR_ORDINAL:
                continue
            committed_row = committed_buildings.get("furnace", {}).get(ordinal)
            if committed_row is not None:
                entry = furnace_report.setdefault(ordinal, {"committed": {f: int(committed_row.get(f) or 0) for f in CHECKED}})
                entry[source] = {f: int(row.get(f) or 0) for f in CHECKED}
                for f in CHECKED:
                    a, b = entry["committed"][f], entry[source][f]
                    if a == b or (a and abs(a - b) / a <= tolerance):
                        continue
                    entry.setdefault("disagrees", []).append(f"{source}.{f}")
                    lines.append(f"furnace.{ordinal} {f}: committed {a:,} vs {source} {b:,}")
            elif int(ordinal) > 30 and ordinal not in furnace_overlay and source == "whiteoutdata":
                furnace_overlay[ordinal] = {"source": source, "label": row.get("label"), "meat": row["meat"], "wood": row["wood"],
                                             "coal": row["coal"], "iron": row["iron"], "fire_crystals": row["fire_crystals"],
                                             "refined_fire_crystals": row["refined_fire_crystals"], "seconds": row["seconds"],
                                             "power": row.get("power", 0), "prerequisites": {}}
                lines.append(f"furnace.{ordinal} added from {source} ({row.get('label')})")
    report = {"_meta": {"sources": sorted(local_docs), "floor_ordinal": FLOOR_ORDINAL}, "furnace": furnace_report}
    overlay = {"_meta": {"sources": sorted(local_docs)}, "buildings": ({"furnace": furnace_overlay} if furnace_overlay else {})}
    return report, overlay, lines
