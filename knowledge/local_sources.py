"""Cross-check tables from sites whose terms restrict reproduction.

Everything here writes under knowledge/local/ (gitignored) and is never
committed (spec D8). One page fetch per table (wostools makes two: the
calculator page, then its data chunk -- B11), no crawling. The parsers are
plain HTML-table and JS-literal readers; when a page's shape is not
recognised they return {} and say so rather than guess.

    page ─fetch_text─▶ html/js ─parse─▶ {"furnace": {ordinal: row}} ─crosscheck─▶ knowledge/local/overlay.json

Layering (B9/E1/R16): this module imports nothing from `native/`.
`furnace_ordinal` and the number/duration parsers live in `knowledge/util.py`
(moved out of `native/screen.py` by E1); `native/screen.py` re-exports them
for the readers, `native/kb.py` imports `furnace_ordinal` from the same
place. Nothing here reaches into `native.kb` or `native.screen`.

Overlay shape (A5, binding over the task body): `crosscheck()` never edits
the committed `knowledge/buildings.json`. It returns an overlay dict
consumed verbatim by `native/kb.py::_apply_overlay` -- for a level the
committed table already has, the overlay holds only the fields to ADD
(`power`, `disputed`); for a level it lacks (an FC row past 30), the overlay
holds the full row, marked `"source"`.
"""
import html as htmllib
import re

from knowledge.fetch import fetch_text
from knowledge.normalise import slug
from knowledge.util import furnace_ordinal, parse_duration, parse_number

WHITEOUTDATA_FURNACE = "https://whiteoutdata.com/buildings/furnace/"
WIKI_FURNACE = "https://www.whiteoutsurvival.wiki/buildings/furnace/"
WOSTOOLS_BUILDINGS = "https://wostools.net/building-calculator"


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


def _prereqs(text):
    """'Embassy Lv. 27, Research Center Lv. 27' / 'Embassy FC 9' -> {slug: ordinal}."""
    out = {}
    for m in re.finditer(r"([A-Za-z'’ ]+?)\s+(Lv\.?\s*\d+|FC\s*\d+(?:-\d)?)", text):
        label = m.group(2).replace("Lv.", "").replace("Lv", "").strip()
        out[slug(m.group(1))] = furnace_ordinal(label)
    return out


def whiteoutdata_furnace(page):
    """whiteoutdata.com's furnace page: two tables (pre-FC, FC), same
    Level/Requirements/Wood/Meat/Coal/Iron/[Fire Crystal/Refined FC]/Upgrade
    Time/Power shape. Rows are keyed by ordinal (B6/furnace_ordinal)."""
    columns = {"level": ["level"], "req": ["requirements"], "wood": ["wood"], "meat": ["meat"], "coal": ["coal"],
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
            out[str(ordinal)] = {"label": label, "meat": parse_amount(d.get("meat")), "wood": parse_amount(d.get("wood")),
                                  "coal": parse_amount(d.get("coal")), "iron": parse_amount(d.get("iron")),
                                  "fire_crystals": parse_amount(d.get("fc")), "refined_fire_crystals": parse_amount(d.get("rfc")),
                                  "seconds": parse_time(d.get("time")), "power": parse_amount(d.get("power")),
                                  "prerequisites": _prereqs(d.get("req", ""))}
    return {"furnace": out}


def wiki_furnace(page):
    """whiteoutsurvival.wiki's furnace page: one table, costs folded into a
    single 'Build Cost' cell ('Meat 190M, Wood 190M, Coal 39M, Iron 9.9M')."""
    columns = {"level": ["level"], "req": ["prerequisites", "requirements"], "cost": ["build cost", "cost"],
               "time": ["time", "upgrade time"], "power": ["power"]}
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
            cost = {}
            for res, amt in re.findall(r"(Meat|Wood|Coal|Iron|Fire Crystal|Refined FC)\s*([\d.,]+[KMB]?)", d.get("cost", ""), re.I):
                cost[slug(res)] = parse_amount(amt)
            out[str(ordinal)] = {"label": label, "meat": cost.get("meat", 0), "wood": cost.get("wood", 0),
                                  "coal": cost.get("coal", 0), "iron": cost.get("iron", 0),
                                  "fire_crystals": cost.get("fire_crystal", 0), "refined_fire_crystals": cost.get("refined_fc", 0),
                                  "seconds": parse_time(d.get("time")), "power": parse_amount(d.get("power")),
                                  "prerequisites": _prereqs(d.get("req", ""))}
    return {"furnace": out}


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
CHECKED = ("meat", "wood", "coal", "iron", "seconds")


def crosscheck(committed_buildings, local_docs, tolerance=0.02):
    """Diff each local source's furnace rows against the committed table and
    return an overlay for `native/kb.py::_apply_overlay` to merge at load
    time (A5) -- never the committed table itself.

    For a level the committed table already has: only fields that disagree
    by more than `tolerance` are recorded, under `disputed: {source: {field:
    value}}`; `power` is copied from whiteoutdata only (B10 -- the wiki's and
    wostools' `power` fields are ignored, and are not even in scope for
    wostools, which carries none). For a level the committed table lacks
    (an FC row, ordinal > 30): the full row is added from whiteoutdata,
    marked `"source": "whiteoutdata"`.

    Returns (overlay, lines): `overlay` is rebuilt from scratch every call
    (B10 -- a changed local row is always re-diffed, never merged onto a
    stale overlay), `lines` is the human-readable report the refresh prints.
    """
    furnace = {}
    lines = []
    for source, doc in sorted(local_docs.items()):
        rows = doc.get("furnace") or (doc.get("buildings") or {}).get("furnace") or {}
        for ordinal, row in sorted(rows.items(), key=lambda kv: int(kv[0])):
            committed_row = committed_buildings.get("furnace", {}).get(ordinal)
            if committed_row is not None:
                patch = furnace.setdefault(ordinal, {})
                for f in CHECKED:
                    a, b = int(committed_row.get(f) or 0), int(row.get(f) or 0)
                    if a == b or (a and abs(a - b) / a <= tolerance):
                        continue
                    patch.setdefault("disputed", {}).setdefault(source, {})[f] = b
                    lines.append(f"furnace.{ordinal} {f}: committed {a:,} vs {source} {b:,}")
                if source == "whiteoutdata" and row.get("power") and "power" not in patch:
                    patch["power"] = row["power"]
            elif int(ordinal) > 30 and ordinal not in furnace and source == "whiteoutdata":
                furnace[ordinal] = {"source": source, "label": row.get("label"), "meat": row["meat"], "wood": row["wood"],
                                     "coal": row["coal"], "iron": row["iron"], "fire_crystals": row["fire_crystals"],
                                     "refined_fire_crystals": row["refined_fire_crystals"], "seconds": row["seconds"],
                                     "power": row.get("power", 0), "prerequisites": row.get("prerequisites", {})}
                lines.append(f"furnace.{ordinal} added from {source} ({row.get('label')})")
    furnace = {k: v for k, v in furnace.items() if v}
    overlay = {"_meta": {"sources": sorted(local_docs)}, "buildings": ({"furnace": furnace} if furnace else {})}
    return overlay, lines
