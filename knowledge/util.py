"""Pure helpers shared by the refresh script, the normalisers and the
calculators. Standard-library only (json, os, re) so `knowledge/` never
depends on `native/` (E1) -- `native/screen.py` imports the three parsers
back out of here for the ten reader modules that already use them.

    parse_number/parse_ratio/parse_duration -- OCR text -> numbers (moved
        from native/screen.py; native/screen.py re-exports them unchanged)
    furnace_ordinal/next_level_label -- the furnace/Embassy/camp level label
        <-> ordinal convention (B6/B7): `level + 5*fc + sub`
    write_table -- atomic, pretty-printed JSON write, reused by the refresh
        script and by `native/kb.py::mark_verified`
"""
import json
import os
import re

# ----------------------------------------------------------------------------- parsing
# Moved from native/screen.py (E1): knowledge/ must not import native/, and
# ten reader modules already pull these names through native/screen.py's
# re-export, so their behaviour (and tests/fixtures/local/native_goldens.json,
# which does NOT pin these three) must not change with the move.
_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_number(text):
    """'36.30M' -> (36300000, False); '1,423' -> (1423, True); garbage -> (None, True).

    The second value is `exact`: an abbreviated figure is stored with exact=0 so
    a later screen that shows the full number can override it. The decimal point
    is scaled, not stripped (usecases/chief_order.py read 77.3M as 773,000,000)."""
    if text is None:
        return None, True
    t = str(text).strip().replace(" ", "")
    # The game prints abbreviated figures with a locale decimal: "37,84M" is
    # 37.84M. A comma is a decimal only with 1-2 digits after it AND a suffix;
    # "1,423" and "35,020,652" keep their thousands commas.
    t = re.sub(r"^(\d+),(\d{1,2})([kmb])$", r"\1.\2\3", t, flags=re.IGNORECASE)
    t = t.replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb])?", t, re.IGNORECASE)
    if not m:
        return None, True
    num, suf = m.group(1), m.group(2)
    if suf:
        return int(round(float(num) * _SUFFIX[suf.lower()])), False
    if "." in num:
        return None, True
    return int(num), True


def parse_ratio(text):
    """'71/200' -> (71, 200); '36,940/143,010' -> (36940, 143010); else None."""
    if text is None:
        return None
    m = re.fullmatch(r"\s*([\d,.]+\s*[kmb]?)\s*/\s*([\d,.]+\s*[kmb]?)\s*", str(text), re.IGNORECASE)
    if not m:
        return None
    a, _ = parse_number(m.group(1))
    b, _ = parse_number(m.group(2))
    if a is None or b is None:
        return None
    return a, b


def parse_duration(text):
    """'9d 11:22:32' -> seconds; '04:50' -> 290; '1h 20m' -> 4800; else None."""
    if text is None:
        return None
    t = str(text).strip().lower()
    total, matched = 0, False
    m = re.search(r"(\d+)\s*d", t)
    if m:
        total += int(m.group(1)) * 86400
        matched = True
        t = t[m.end():]
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if m.group(3) is None:
            # mm:ss when short, hh:mm when a day prefix or 'h' context exists
            total += h * 60 + mi if not matched else h * 3600 + mi * 60
        else:
            total += h * 3600 + mi * 60 + s
        return total
    for unit, mult in (("h", 3600), ("m", 60), ("s", 1)):
        m = re.search(rf"(\d+)\s*{unit}\b", t)
        if m:
            total += int(m.group(1)) * mult
            matched = True
    return total if matched else None


def slugify(text):
    """'Supreme Infantry' -> 'supreme_infantry': the key form the model
    stores. Moved from native/screen.py alongside the parsers above (E1):
    native/kb.py::record_item needs a slug for every item name it catalogues
    and must not import native/screen.py (cv2, numpy, native.drive at module
    level) just to get one. native/screen.py re-exports this unchanged, so
    the five reader modules that already import it from there see no
    change, and record_item's slug always matches what they compute.

    Apostrophes are dropped, not turned into separators (fix round 2, item
    3): "Hunter's Hut" -> 'hunters_hut', matching
    `knowledge.normalise.slug` -- the vendored tables' own slugger, whose
    output is what `knowledge/buildings.json` and
    `native/kb.py::UNTRACKED_ASSUMED_MET` key by. Before this fix this
    function alone produced 'hunter_s_hut', an apostrophe convention no
    other slug in `knowledge/` uses; unreachable only because no reader
    read a building whose screen name has one yet. Safe to change here
    (rather than in normalise.slug) because `knowledge/items.json` is
    empty in every checkout today -- there is no existing catalogue row
    keyed by the old apostrophe-as-separator form for this change to
    orphan; the day it isn't empty, this is the direction that agrees with
    the already-committed vendored tables, not the one that would need a
    migration of its own."""
    t = re.sub(r"[^a-z0-9]+", "_", re.sub(r"['’]", "", str(text).lower())).strip("_")
    return t or "unnamed"


# ----------------------------------------------------------------------------- item classification (A11)
# Moved out of native/readers/backpack.py for the same reason as the move
# above: knowledge/ depends on nothing but the standard library, but
# native/readers/backpack.py imports native.screen at module level, which
# imports cv2, numpy and native.drive at module level in turn. Before this
# move, native/kb.py::record_item imported classify_kind from the reader
# module to compute one field, so calling record_item from anything but the
# backpack reader (a backfill script, a unit test with no screen session)
# dragged OpenCV, NumPy and the driver in to resolve a two-line string
# classifier. native/readers/backpack.py re-exports these names unchanged,
# so `fold`'s call sites did not have to move.
# The live sweep of 2026-09-09 is why the duration may precede the type.
# The game names these "1m Construction Speedup" -- duration FIRST, then the
# queue it applies to. The original pattern only allowed "Construction 1m
# Speedup", so on real names the type group never matched, the duration group
# (which had to sit immediately before "speedup") never matched either, and
# every single speedup classified as "other". `backpack.speedups.<type>.<dur>`
# was therefore never populated from a real sweep -- 45 such paths existed in
# the database, every one of them null. Observed names, verbatim:
#   1m Construction Speedup   5m Construction Speedup   1h Construction Speedup
#   1m Training Speedup       5m Training Speedup       1h Training Speedup
#   1m Healing Speedup        5m Healing Speedup
# Both orders are accepted now, plus a bare "1m Speedup" (general) and a
# trailing "Speedup (5m)".
# Named groups, not positional: the original used group(1)..group(5) and
# adding one alternative here would have silently renumbered every read in
# speedup_duration. Names cannot drift that way.
_TYPE = r"general|construction|research|training|healing"


def _dur(tag):
    return rf"(?P<n{tag}>\d+)\s*(?P<u{tag}>m|min|h|hr|d)"


SPEEDUP_RE = re.compile(
    rf"(?:{_dur('a')}\s+(?P<ta>{_TYPE})\s+)?"    # "1m Construction "  (the real game order)
    rf"(?:(?P<tb>{_TYPE})\s+)?"                   # "Construction "
    rf"(?:{_dur('b')}\s+)?"                       # "1m "
    r"speed-?up[s]?"
    rf"(?:\s*\(?{_dur('c')}\)?)?", re.I)          # " (5m)"
_SPEEDUP_DUR = {"m": "m", "min": "m", "h": "h", "hr": "h", "d": "d"}

# Bump whenever classify_kind's rules change. native/kb.py::record_item
# stamps every catalogue row with the version that produced its `kind`;
# native/readers/backpack.py::fold treats a row stamped with an older (or
# missing) version as uncatalogued and recomputes `kind` fresh rather than
# trusting the stored value. Without this, a classifier improvement (a new
# keyword rule) would never reach an item already in the catalogue until
# the backpack reader happened to see that exact tile live again -- a fix
# that silently does nothing for every item already recorded.
CLASSIFIER_VERSION = 2

# Fix round 2, item 1: the enforcement mechanism above only works if SOMETHING
# forces a bump whenever classify_kind's rules actually change. The first
# attempt at that -- a test pinning four literal name -> kind pairs -- did
# not: a wholly new rule for a wholly new family (e.g. "resource box"/"chest"
# -> "resource_box", the exact addition this module's own docstring used to
# invite) touches none of the four pinned names, so the suite stayed green
# with CLASSIFIER_VERSION left at 1 and every already-catalogued row silently
# kept trusting its stale "other".
#
# A name-to-kind table can never close that gap for certain, because
# classify_kind's input space is unbounded -- no finite set of pinned
# examples proves nothing ELSE changed. What actually determines its output
# for every input is the source of speedup_duration and classify_kind plus
# SPEEDUP_RE's pattern; fingerprinting THAT (not a sample of outputs) is
# sensitive to any edit that could change behaviour for any input, not just
# the ones a test author thought to enumerate.
#
# CLASSIFIER_VERSION itself stays a plain, hand-bumped int rather than the
# hash (native/kb.py stores it in items.json, and tests/native code diff it,
# e.g. `CLASSIFIER_VERSION - 1`, which a hash-typed version would break) --
# only the fingerprint used to police it is derived from the rules.
# CLASSIFIER_RULES_FINGERPRINTS pins the fingerprint each CLASSIFIER_VERSION
# was released with; tests/test_knowledge_util.py recomputes today's
# fingerprint via `_classifier_fingerprint()` and asserts it equals
# `CLASSIFIER_RULES_FINGERPRINTS[CLASSIFIER_VERSION]`. Edit a rule without
# bumping the version and the lookup still finds the OLD fingerprint pinned
# under the unchanged version number, so the test goes red; the only way
# back to green is to bump CLASSIFIER_VERSION to a version this dict has no
# entry for yet and add one -- which is the bump the whole mechanism exists
# to force.
CLASSIFIER_RULES_FINGERPRINTS = {
    1: "93df932edaa3d077",
    # v2, 2026-09-09: SPEEDUP_RE learned the order the game actually uses,
    # "1m Construction Speedup" (duration before type). Under v1 every real
    # speedup name classified as "other" and speedup_duration returned None,
    # so no sweep ever populated backpack.speedups.<type>.<duration>. This
    # bump is what makes the 45 rows already catalogued under v1 recompute
    # instead of keeping their stale "other" -- the mechanism's first real use.
    2: "179a969854a12fcd",
}


def speedup_duration(name):
    """(kind, "5m"/"1h"/...) for a name shaped like a speedup, else None.
    Kind and duration come from one SPEEDUP_RE match so classify_kind and
    the ledger router (`native.readers.backpack.fold`) never re-derive
    them two different ways."""
    m = SPEEDUP_RE.search(name)
    if not m:
        return None
    for tag in ("a", "b", "c"):
        num, unit = m.group(f"n{tag}"), m.group(f"u{tag}")
        if num:
            kind = (m.group("ta") or m.group("tb") or "general").lower()
            return kind, f"{num}{_SPEEDUP_DUR[unit.lower()]}"
    return None


def classify_kind(name):
    """"speedup" | "fire_crystal" | "other" from the exact SPEEDUP_RE match
    and "fire crystal" keyword `native.readers.backpack.fold` uses to route
    ledger writes (A11): one function, so the item catalogue's `kind`
    (native/kb.py::record_item) and the ledger router can never classify
    the same name two different ways. "resource_box" is part of the kind
    enum `record_item` promises, but like `fold` before this module existed,
    nothing here recognises it yet -- the day a keyword for it is added, it
    is added HERE, and both the catalogue and the ledger pick it up from
    this one place immediately (see CLASSIFIER_VERSION)."""
    n = str(name).lower()
    if speedup_duration(name):
        return "speedup"
    if "fire crystal" in n:
        return "fire_crystal"
    return "other"


def _classifier_fingerprint():
    """sha256 (first 16 hex chars) of everything classify_kind's output for
    ANY input can depend on: SPEEDUP_RE's pattern and the full source of
    speedup_duration and classify_kind (the fire-crystal keyword and any
    future keyword rule live in classify_kind's own body). Used only by
    tests/test_knowledge_util.py to police CLASSIFIER_VERSION -- see
    CLASSIFIER_RULES_FINGERPRINTS above."""
    import hashlib
    import inspect
    rules_src = SPEEDUP_RE.pattern + inspect.getsource(speedup_duration) + inspect.getsource(classify_kind)
    return hashlib.sha256(rules_src.encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------- ordinal
# Every building level past 30 is a Fire Crystal (FC) tier: the game shows
# "30-1".."30-4" for the four sub-levels right after 30, then "FC1" (a bare
# tier marker), then "FC1-1".."FC1-4", then "FC2", and so on through "FC10"
# at ordinal 80. The rule is the same for every building (furnace, Embassy,
# camps): ordinal = level + 5*fc + sub, level pinned at 30 once fc > 0.
_FC_SUB_RE = re.compile(r"FC(\d+)-(\d+)")
_FC_RE = re.compile(r"FC(\d+)")
_N_SUB_RE = re.compile(r"(\d+)-(\d+)")
_PLAIN_RE = re.compile(r"(\d+)")


def furnace_ordinal(label):
    """Furnace/Embassy/camp level label -> ordinal.

    '27' -> 27, '30-3' -> 33, 'FC1' -> 35, 'FC 10' -> 80, 'FC9-4' -> 79
    ('Embassy FC 9' is 75, matching the same rule with sub=0).

    Strict (B7): the `N-sub` form is only valid with N == 30 and sub in
    1..4; `FCn-sub` (and bare `FCn`, sub=0) only with n in 1..10 and sub in
    0..4. Anything else -- '27-3', 'FC1-5', 'FC11' -- raises ValueError so a
    malformed upstream label surfaces immediately rather than silently
    landing on the wrong row.
    """
    t = str(label).strip().upper().replace(" ", "")
    m = _FC_SUB_RE.fullmatch(t)
    if m:
        fc, sub = int(m.group(1)), int(m.group(2))
        if 1 <= fc <= 10 and 1 <= sub <= 4:
            return 30 + 5 * fc + sub
        raise ValueError(f"not a furnace level: {label!r}")
    m = _FC_RE.fullmatch(t)
    if m:
        fc = int(m.group(1))
        if 1 <= fc <= 10:
            return 30 + 5 * fc
        raise ValueError(f"not a furnace level: {label!r}")
    m = _N_SUB_RE.fullmatch(t)
    if m:
        n, sub = int(m.group(1)), int(m.group(2))
        if n == 30 and 1 <= sub <= 4:
            return n + sub
        raise ValueError(f"not a furnace level: {label!r}")
    m = _PLAIN_RE.fullmatch(t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 30:
            return n
        raise ValueError(f"not a furnace level: {label!r}")
    raise ValueError(f"not a furnace level: {label!r}")


def next_level_label(ordinal):
    """The label the game shows for the level after `ordinal` (furnace chain).

    27 -> "28", 30 -> "30-1", 34 -> "FC1", 35 -> "FC1-1", 79 -> "FC10"."""
    nxt = int(ordinal) + 1
    if nxt <= 30:
        return str(nxt)
    if nxt <= 34:
        return f"30-{nxt - 30}"
    fc, sub = divmod(nxt - 30, 5)
    return f"FC{fc}" if sub == 0 else f"FC{fc}-{sub}"


# ----------------------------------------------------------------------------- write
def write_table(path, doc):
    """Pretty-printed, atomic JSON write: a crash or a concurrent read never
    sees a half-written table (temp file + os.replace)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)
