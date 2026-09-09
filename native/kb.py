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

furnace_ordinal, next_level_label, write_table, slugify, classify_kind and
CLASSIFIER_VERSION are all imported from knowledge/util.py (B9/R4/fix-round-1):
this module never redefines the ordinal maths, the atomic-write helper, or
item-name classification -- it only re-exports them for its own callers
(`kb.next_level_label(...)` works). Importing classify_kind and slugify
from knowledge/util.py rather than from native/readers/backpack.py or
native/screen.py is deliberate, not incidental: both of those modules pull
in cv2, numpy and native.drive at module level, and this module's opening
claim ("no game access") must hold for every caller of `record_item`, not
only the backpack reader that happens to have already paid that import
cost.

Verification (mark_verified, C12): opens the COMMITTED file directly --
never the merged in-memory table `load()` returns -- so a level that only
exists via the local overlay (an ordinal > 30 Fire Crystal row, terms-
restricted data that must never reach a committed file) always returns
False.

Two functions write to disk: `mark_verified` (above) and `record_item`
(A11), which upserts `knowledge/items.json` from in-game backpack
tooltips read by native/readers/backpack.py -- an in-game source with no
terms restriction, unlike the vendored tables' local overlay. Both keep
the same property: neither ever writes anything sourced from the local
overlay. `mark_verified` can't, because it opens the committed file
directly rather than `load()`'s overlay-merged table; `record_item` can't,
because its only inputs are a name, tab and description just read off the
screen -- it never touches `load()`, the cache or the overlay at all.
"""
import json
import os
from collections import namedtuple

from knowledge.util import CLASSIFIER_VERSION, classify_kind, furnace_ordinal, next_level_label, slugify, write_table

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
    """RESOURCES the row actually costs, zero/absent ones dropped as usual.

    A resource the row explicitly carries as None (finding 1, 2026-09-09 fix
    round: an overlay row whose page-parse for that one column failed --
    `power`/`fire_crystals`/`refined_fire_crystals` exist nowhere but the
    overlay, so nothing else can catch it) is a distinct fact from a real,
    parsed zero and must not vanish from the cost the same way -- that is
    exactly how a level needing 132 Fire Crystals silently priced out at
    zero. Raise instead: a caller summing costs across levels needs to know
    the total is unknown, not receive a silently cheaper one."""
    cost = {}
    for k in RESOURCES:
        if k not in row:
            continue
        v = row[k]
        if v is None:
            raise ValueError(f"{k}: unknown cost (overlay row parsed with a missing value) -- cannot total it")
        if v:
            cost[k] = v
    return cost


# ----------------------------------------------------------------------------- buildings
def building_row(name, level, kb=None):
    """`level` is either a plain ordinal (int or digit string) or, for the
    furnace, one of the game's Fire-Crystal labels ("30-1", "FC1", "FC1-1",
    ...), resolved through `furnace_ordinal`. A label that doesn't parse as
    a furnace level returns None rather than raising -- callers already
    treat a missing row as "not in the knowledge base"."""
    key = str(level)
    if name == "furnace" and not key.isdigit():
        try:
            key = str(furnace_ordinal(key))
        except ValueError:
            return None
    return _kb(kb)["buildings"].get(name, {}).get(key)


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


def prerequisites(name, level, building_levels, kb=None, assume_untracked_met=True):
    """Unmet (building, needed, have) for reaching `level`, plus the ones
    assumed met because the sheet doesn't track them (D-T1/C8).

    `building_levels` is {building_name: level_int} -- renamed from `sheet`
    (fix round 2, item 2): `speed_bonus_from_sheet`'s `bonuses` param used
    that same name for an incompatible dotted-path mapping, and both being
    called `sheet` invited passing one where the other belongs with no
    error, just a wrong answer.

    `have` is None when `building_levels` has no reading for that building
    (missing key or an explicit None) -- reported, never silently treated
    as 0 or as satisfied, unless the building is in UNTRACKED_ASSUMED_MET,
    in which case it is dropped from `unmet` and named in `assumed` instead.

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
        have = building_levels.get(b)
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


def _buildings_verified_row(doc, table, key, level):
    return doc[table].get(key, {}).get(str(level))


def _research_verified_row(doc, table, key, level):
    return doc[table].get(key, {}).get("levels", {}).get(str(level))


# table -> (committed filename, row-shape getter). One dict, not two
# (finding 5, 2026-09-09 fix round): the filename lookup below already
# raises a named error for any table outside this set, so keeping the row
# shape in the SAME mapping (rather than a parallel `if table ==
# "buildings" ... else ...`) means a third supported table cannot pass the
# filename guard while silently taking the wrong (research-shaped) branch --
# there is only one dispatch to keep in sync with itself.
_MARK_VERIFIED = {
    "buildings": ("buildings.json", _buildings_verified_row),
    "research": ("research.json", _research_verified_row),
}


def mark_verified(table, key, level, snapshot_id, directory=None):
    """Record that a screen read agreed with a row (`native.kb.verify`); the
    planner trusts verified rows first and the briefing lists unverified
    ones it relies on.

    C12 (binding): this opens the COMMITTED file at `directory` directly --
    never `load()`'s merged, cached table -- so a level that exists only
    via the local overlay (a Fire Crystal furnace row, ordinal > 30,
    whiteoutdata-sourced and terms-restricted) is never found here and
    `mark_verified` returns False for it, same as any other unknown level.
    This and `record_item` (A11) are the only two places in the knowledge
    base that write to disk, which is exactly why neither ever writes
    overlay data into a committed file.

    Not a transaction: this reads the file, mutates the in-memory doc and
    writes the whole thing back (the write itself is atomic via
    `write_table`, but the read-modify-write is not). A
    `scripts/refresh_knowledge.py --write` run landing between the read and
    the write here would be silently clobbered, re-serialized from this
    call's own stale read. Fine for single-actor use (the executor calls
    this, nothing else writes these files); don't run a refresh concurrently
    with marking a row verified.
    """
    directory = directory or KNOWLEDGE_DIR
    try:
        fname, row_getter = _MARK_VERIFIED[table]
    except KeyError:
        raise KeyError(f"mark_verified: unsupported table {table!r}; expected one of {sorted(_MARK_VERIFIED)}") from None
    path = os.path.join(directory, fname)
    with open(path) as f:
        doc = json.load(f)
    row = row_getter(doc, table, key, level)
    if row is None:
        return False
    row["verified_in_game"] = snapshot_id
    write_table(path, doc)
    _CACHE.pop(directory, None)
    return True


# ----------------------------------------------------------------------------- item catalogue (A11)
ITEMS_FILE = "items.json"


def items(directory=None):
    """The item catalogue `record_item` builds from backpack tooltips:
    {slug: {name, slug, tab, description, kind, classifier_version,
    first_seen, last_seen}}.

    Read fresh from disk every call, never through the `load()`/`_CACHE`
    path: `record_item` can add a row mid-run (a backpack sweep classifies
    tiles as it reads them, `fold` looks the same slug up moments later)
    and a process-lifetime cache would hide what the same run just wrote.
    Empty when `knowledge/items.json` doesn't exist yet -- a fresh clone,
    or before the backpack reader has ever run."""
    directory = directory or KNOWLEDGE_DIR
    path = os.path.join(directory, ITEMS_FILE)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f).get("items", {})


def record_item(name, tab, description, snapshot_id, directory=None):
    """Upsert one row into `knowledge/items.json` from an in-game backpack
    tooltip (A11) -- the item catalogue's only source, which is why, unlike
    every vendored table, it carries no terms restriction and is committed
    (see knowledge/README.md). `native/readers/backpack.py::read` calls this
    for every tooltip it reads; it taps nothing itself.

    `kind` and `slug` reuse `knowledge.util.classify_kind`/`slugify`, the
    exact functions `native.readers.backpack.fold` calls to route ledger
    writes, so the catalogue's classification and the ledger's routing can
    never drift apart. Both are pure-stdlib (`knowledge/` depends on
    nothing outside it) and imported at module level here -- this function
    does not, even transitively, import cv2, numpy or the driver to do its
    job, unlike an earlier version of this module that imported
    classify_kind from the reader package itself.

    `classifier_version` is stamped with `knowledge.util.CLASSIFIER_VERSION`
    so `fold` can tell a stale classification (from before a classify_kind
    rule change) from a current one and recompute instead of trusting it --
    see `fold`'s docstring.

    Upsert semantics: `first_seen` is set once and never overwritten;
    every other field, including `last_seen`, is refreshed to this call's
    values on every sighting.

    This is the second of the two functions in this module that write to
    disk (see the module docstring). Like `mark_verified`, it never writes
    anything sourced from the local overlay -- its only inputs are what
    the screen just showed, and it never reads `load()`, `_CACHE` or the
    overlay file at all."""
    directory = directory or KNOWLEDGE_DIR
    path = os.path.join(directory, ITEMS_FILE)
    if os.path.exists(path):
        with open(path) as f:
            doc = json.load(f)
    else:
        doc = {"_meta": {"source": "in-game backpack tooltips"}, "items": {}}
    slug = slugify(name)
    row = dict(doc["items"].get(slug) or {})
    first_seen = row.get("first_seen", snapshot_id)
    row.update({
        "name": name,
        "slug": slug,
        "tab": tab,
        "description": description,
        "kind": classify_kind(name),
        "classifier_version": CLASSIFIER_VERSION,
        "first_seen": first_seen,
        "last_seen": snapshot_id,
    })
    doc["items"][slug] = row
    write_table(path, doc)
    return row


def freshness(kb=None, now=None, stale_days=30):
    """[(table, age_days, stale)] from each table's `_meta.fetched_at`
    (A9): the report line that tells the operator a vendored table hasn't
    been refreshed in a while. A table with no `_meta` or no `fetched_at`
    (an optional table that hasn't shipped yet, or a hand-built test kb) is
    omitted rather than reported as either fresh or stale.

    Also omitted, rather than raising (finding 2, 2026-09-09 fix round): a
    `fetched_at` that doesn't parse against a timezone-aware `now` -- a bare
    date ("2026-09-01", every other table uses "%Y-%m-%dT%H:%M:%SZ") raises
    TypeError subtracting offset-naive from offset-aware, and any other
    malformed string raises ValueError out of fromisoformat. This is a
    reporting line (native/report.py calls it inside build()); a hand-
    maintained table's timestamp typo must never take the whole report down."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    out = []
    for key in TABLES:
        m = _kb(kb).get(f"_meta_{key}")
        if not m or not m.get("fetched_at"):
            continue
        try:
            age = (now - datetime.fromisoformat(m["fetched_at"].replace("Z", "+00:00"))).days
        except (ValueError, TypeError):
            continue
        out.append((key, age, age > stale_days))
    return out


def speed_bonus_from_sheet(bonuses, kind):
    """kind is construction | research | training. The sheet stores the
    game's own percentage (128 means +128%); building_time/training_time
    want a multiplier delta. Returns 0.0 when the stats reader (Task 7)
    has not run, so a caller must say a bonus was not applied rather than
    silently present a base time as an ETA (F-a).

    `bonuses` (renamed from `sheet`: `prerequisites` uses that name for an
    incompatible {building: level} shape, fix round 2 item 2) takes a plain
    {path: percent} mapping or the {path: row_dict} shape
    `native.model.latest()` returns -- the actual "current sheet" here.
    A row dict is read via its 'value_num' key (schema'd "int"); one with
    no such key is unrecognised and raises TypeError naming it, rather
    than silently returning 0.0 as if the stats reader never ran."""
    path = f"progress.bonus.{kind}_speed"
    pct = bonuses.get(path)
    if pct is None:
        return 0.0
    if isinstance(pct, dict):
        if "value_num" not in pct:
            raise TypeError(
                f"speed_bonus_from_sheet: unrecognised row shape for {path!r}: "
                f"expected a plain number or a row dict with 'value_num', got keys {sorted(pct)}"
            )
        pct = pct["value_num"]
        if pct is None:
            return 0.0
    return float(pct) / 100.0
