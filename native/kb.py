"""Pure calculators over the vendored knowledge tables. No game access, no
database: the planner passes in what the sheet already knows (current
levels, per-day income) and gets back costs, times, unmet prerequisites,
research paths and expected power. `verify` is what the executor runs
against the cost it reads off the screen before it presses anything.

    knowledge/*.json ─load()─▶ kb dict (cached per directory)
                                   │
                                   ├─▶ building_cost / building_time / prerequisites
                                   ├─▶ research_node / research_path
                                   ├─▶ training_cost / training_time / troop_power
                                   └─▶ power_gain / days_to / verify

Cache contract (E8): every dict `load`, `building_row` and `research_node`
hand back is OWNED BY THE CACHE (`_CACHE`, keyed by directory). A caller
that wants to annotate a row must copy it first (`dict(row)`) -- mutating
it in place corrupts every other caller's view of the same table until the
process cache is cleared. `research_path` already copies each row's `cost`
before returning it for exactly this reason.

Overlay (A5/B12): `load()` merges `<directory>/local/overlay.json` when
present -- it is the only place cross-checked FC rows and building `power`
values come from (M2 data; the load path and the None-without-overlay
behaviour ship now). Without it, `power_gain("building", ...)` returns
`None`, never `0`, so a planner never mistakes "unknown" for "no power".

furnace_ordinal is imported from knowledge/util.py (B9/R4): this module
never redefines the ordinal maths.
"""
import json
import os
from collections import namedtuple

from knowledge.util import furnace_ordinal

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_DIR = os.path.join(REPO, "knowledge")

# key -> filename. buildings/training/stats/research are required (R3); events
# (the calendar) is optional and its calculators (next_occurrences) are M2 --
# do not add them here (F-b).
TABLES = {
    "buildings": "buildings.json",
    "training": "troops.json",
    "stats": "troop_stats.json",
    "research": "research.json",
    "events": "calendar.json",
}
REQUIRED_TABLES = frozenset({"buildings", "training", "stats", "research"})

RESOURCES = ("meat", "wood", "coal", "iron", "fire_crystals", "refined_fire_crystals", "steel")

# Buildings the sheet reads today but wosnerds does not carry at all (D-T1):
# the popup is the only source, so building_cost/building_time/prerequisites
# raise a KeyError that says so instead of a bare "not in the knowledge base".
POPUP_ONLY_BUILDINGS = ("storehouse", "warehouse", "war_academy")

# Buildings the sheet never reads (no reader exists yet) but that gate real
# upgrades. Treated as met so the planner prints one "assumed met" line
# instead of five permanent, unactionable unmet rows (D-T1).
UNTRACKED_ASSUMED_MET = ("coal_mine", "iron_mine", "sawmill", "hunters_hut", "shelter", "barricade")

ResearchStep = namedtuple("ResearchStep", "node level cost seconds")

_CACHE = {}


# ----------------------------------------------------------------------------- load
def _apply_overlay(kb, path):
    """Merge the local, terms-restricted cross-check overlay when present
    (A5/spec D8): committed tables are wosnerds-only, and FC rows / building
    power values live only here. Without a file at `path` this is a no-op
    and `kb["_overlay"]` stays None, which is what tells power_gain to
    return None for buildings rather than a silently wrong 0."""
    if not os.path.exists(path):
        kb["_overlay"] = None
        return
    with open(path) as f:
        ov = json.load(f)
    for name, levels in (ov.get("buildings") or {}).items():
        table = kb["buildings"].setdefault(name, {})
        for level, patch in levels.items():
            if level in table:
                table[level].update(patch)
            else:
                table[level] = {"verified_in_game": None, **patch}
    kb["_overlay"] = ov.get("_meta")


def load(directory=None):
    """The tables keyed by their top-level name, cached per directory. A
    missing required table (buildings/training/stats/research) raises
    FileNotFoundError naming the refresh command; a missing optional table
    (events) is skipped with one printed line (R3)."""
    directory = directory or KNOWLEDGE_DIR
    if directory in _CACHE:
        return _CACHE[directory]
    kb = {}
    for key, fname in TABLES.items():
        path = os.path.join(directory, fname)
        if not os.path.exists(path):
            if key in REQUIRED_TABLES:
                raise FileNotFoundError(f"{path} missing: run scripts/refresh_knowledge.py --write")
            print(f"knowledge: optional table {key!r} not available ({fname} missing)")
            continue
        with open(path) as f:
            doc = json.load(f)
        kb[key] = doc[key]
        kb[f"_meta_{key}"] = doc.get("_meta")
    _apply_overlay(kb, os.path.join(directory, "local", "overlay.json"))
    _CACHE[directory] = kb
    return kb


def _kb(kb):
    return kb if kb is not None else load()


# ----------------------------------------------------------------------------- cost
def add_cost(a, b):
    """Cost + Cost -> Cost; keys absent from either side are treated as 0."""
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out


def _row_cost(row):
    return {k: row[k] for k in RESOURCES if row.get(k)}


# ----------------------------------------------------------------------------- buildings
def building_row(name, level, kb=None):
    return _kb(kb)["buildings"].get(name, {}).get(str(level))


def _require_building_row(name, level, kb):
    row = building_row(name, level, kb)
    if row is not None:
        return row
    if name in POPUP_ONLY_BUILDINGS:
        raise KeyError(f"{name}: not in the knowledge base (wosnerds doesn't track it) -- read the cost from the popup")
    raise KeyError(f"{name} level {level} not in the knowledge base")


def building_cost(name, from_level, to_level, kb=None):
    """Sum of the rows from_level+1 .. to_level (each row is the cost OF
    reaching that level)."""
    kb = _kb(kb)
    total = {}
    for lv in range(from_level + 1, to_level + 1):
        row = _require_building_row(name, lv, kb)
        total = add_cost(total, _row_cost(row))
    return total


def building_time(name, from_level, to_level, speed_bonus=0.0, kb=None):
    kb = _kb(kb)
    secs = 0
    for lv in range(from_level + 1, to_level + 1):
        row = _require_building_row(name, lv, kb)
        secs += row["seconds"]
    return int(round(secs / (1.0 + speed_bonus)))


def prerequisites(name, level, sheet, kb=None, assume_untracked_met=True):
    """Unmet (building, needed, have) for reaching `level`, plus the ones
    assumed met because the sheet doesn't track them (D-T1/C8).

    `have` is None when the sheet has no reading for that building (missing
    key or an explicit None) -- reported, never silently treated as 0 or as
    satisfied, unless the building is in UNTRACKED_ASSUMED_MET, in which
    case it is dropped from `unmet` and named in `assumed` instead.

    An overlay row (a Fire Crystal furnace level from the local cross-check,
    D-T2) carries a `"source"` marker and no real prerequisite data --
    D-T2's `local_sources.crosscheck` hard-codes `prerequisites: {}` there
    rather than parsing text that breaks on the live pages. Returning
    `([], [])` for such a row would read as "every prerequisite is met",
    which is not known; instead this returns `([], ["unknown: overlay
    row"])` so the planner never mistakes silence for satisfaction.

    Returns (unmet, assumed) where assumed is a sorted list of
    (building, needed) pairs -- except for an overlay row, where the second
    element is the one-line marker above instead.
    """
    kb = _kb(kb)
    row = _require_building_row(name, level, kb)
    if row.get("source"):
        return [], ["unknown: overlay row"]
    unmet = []
    assumed = []
    for b, needed in sorted(row["prerequisites"].items()):
        have = sheet.get(b)
        if have is None:
            if assume_untracked_met and b in UNTRACKED_ASSUMED_MET:
                assumed.append((b, needed))
            else:
                unmet.append((b, needed, None))
            continue
        have = int(have)
        if have < needed:
            unmet.append((b, needed, have))
    return unmet, assumed


# ----------------------------------------------------------------------------- research
def research_node(node_id, kb=None):
    return _kb(kb)["research"].get(node_id)


def research_path(node_id, level, sheet_research, kb=None):
    """Every (node, level) still to research to reach node_id@level, in an
    order where prerequisites come first; each pair appears at most once.

    B8: `visiting` guards against a prerequisite cycle -- re-entering a
    (node, level) that is still on the current DFS stack raises ValueError
    naming it, instead of recursing forever.
    """
    kb = _kb(kb)
    done = set()
    visiting = set()
    steps = []

    def visit(nid, lv):
        have = int(sheet_research.get(nid) or 0)
        for l in range(have + 1, lv + 1):
            key = (nid, l)
            if key in done:
                continue
            if key in visiting:
                raise ValueError(f"research prerequisite cycle at {nid}@{l}")
            visiting.add(key)
            node = kb["research"].get(nid)
            if node is None:
                raise KeyError(f"research node {nid} unknown")
            row = node["levels"].get(str(l))
            if row is None:
                raise KeyError(f"{nid} level {l} unknown")
            for req, req_lv in row["requires_research"].items():
                visit(req, req_lv)
            visiting.discard(key)
            done.add(key)
            steps.append(ResearchStep(nid, l, dict(row["cost"]), row["seconds"]))

    visit(node_id, level)
    return steps


# ----------------------------------------------------------------------------- training
def training_cost(troop_type, tier, count, kb=None):
    row = _kb(kb)["training"][troop_type][str(tier)]
    return {k: row[k] * count for k in ("meat", "wood", "coal", "iron") if row.get(k)}


def training_time(troop_type, tier, count, speed_bonus=0.0, kb=None):
    row = _kb(kb)["training"][troop_type][str(tier)]
    return int(round(row["seconds"] * count / (1.0 + speed_bonus)))


def troop_power(troop_type, tier, fc=0, kb=None):
    return _kb(kb)["stats"][troop_type][f"{tier}-fc{fc}"]["power"]


# ----------------------------------------------------------------------------- power
def power_gain(kind, kb=None, **kw):
    """kind="building" (name, from_level, to_level): the sum of `power`
    across the range, or None (never 0) the moment one level in the range
    has no power value -- true for every furnace level today because no
    open source carries building power outside the M2 overlay (A5/B12).
    kind="research" (node, level); kind="training" (troop_type, tier, count, fc=0).
    """
    kb = _kb(kb)
    if kind == "building":
        total = 0
        for lv in range(kw["from_level"] + 1, kw["to_level"] + 1):
            row = _require_building_row(kw["name"], lv, kb)
            p = row.get("power")
            if p is None:
                return None
            total += int(p)
        return total
    if kind == "research":
        return int(kb["research"][kw["node"]]["levels"][str(kw["level"])]["power"])
    if kind == "training":
        return troop_power(kw["troop_type"], kw["tier"], kw.get("fc", 0), kb) * kw["count"]
    raise ValueError(f"unknown power_gain kind {kind!r}")


# ----------------------------------------------------------------------------- planning
def days_to(cost, income_per_day, stock=None):
    """Days until every resource in `cost` is covered at the sheet's per-day
    income; None when some still-needed resource has no income at all; 0.0
    when stock already covers everything."""
    stock = stock or {}
    worst = 0.0
    for res, need in cost.items():
        missing = need - int(stock.get(res, 0))
        if missing <= 0:
            continue
        inc = income_per_day.get(res) or 0
        if inc <= 0:
            return None
        worst = max(worst, missing / inc)
    return worst


def verify(kb_cost, screen_cost, tolerance=0.02):
    """Do the resources actually present on-screen match the table within
    `tolerance`? Resources the table has but the screen didn't show are not
    checked -- verify only judges what the executor could read."""
    for res, seen in screen_cost.items():
        expect = kb_cost.get(res, 0)
        if expect == 0 and seen == 0:
            continue
        if expect == 0 or abs(seen - expect) / expect > tolerance:
            return False, f"{res}: table {expect:,} vs screen {seen:,}"
    return True, "ok"


def speed_bonus_from_sheet(sheet, kind):
    """kind is construction | research | training. The sheet stores the
    game's own percentage (128 means +128%); building_time/training_time
    want a multiplier delta. Returns 0.0 when the stats reader (Task 7)
    has not run, so a caller must say a bonus was not applied rather than
    silently present a base time as an ETA (F-a)."""
    pct = sheet.get(f"progress.bonus.{kind}_speed")
    return (float(pct) / 100.0) if pct is not None else 0.0
