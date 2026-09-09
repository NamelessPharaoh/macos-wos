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
    change, and record_item's slug always matches what they compute."""
    t = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
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
SPEEDUP_RE = re.compile(
    r"(?:(general|construction|research|training|healing)\s+)?"
    r"(?:(\d+)\s*(m|min|h|hr|d)\s+)?speed-?up[s]?(?:\s*\(?(\d+)\s*(m|min|h|hr|d)\)?)?", re.I)
_SPEEDUP_DUR = {"m": "m", "min": "m", "h": "h", "hr": "h", "d": "d"}

# Bump whenever classify_kind's rules change. native/kb.py::record_item
# stamps every catalogue row with the version that produced its `kind`;
# native/readers/backpack.py::fold treats a row stamped with an older (or
# missing) version as uncatalogued and recomputes `kind` fresh rather than
# trusting the stored value. Without this, a classifier improvement (a new
# keyword rule) would never reach an item already in the catalogue until
# the backpack reader happened to see that exact tile live again -- a fix
# that silently does nothing for every item already recorded.
CLASSIFIER_VERSION = 1


def speedup_duration(name):
    """(kind, "5m"/"1h"/...) for a name shaped like a speedup, else None.
    Kind and duration come from one SPEEDUP_RE match so classify_kind and
    the ledger router (`native.readers.backpack.fold`) never re-derive
    them two different ways."""
    m = SPEEDUP_RE.search(name)
    if not (m and (m.group(2) or m.group(4))):
        return None
    kind = (m.group(1) or "general").lower()
    num, unit = (m.group(2), m.group(3)) if m.group(2) else (m.group(4), m.group(5))
    return kind, f"{num}{_SPEEDUP_DUR[unit.lower()]}"


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
