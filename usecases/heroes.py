"""Raise a hero's level with the Hero EXP the account already owns.

This is the deliberate counterpart to a guard in usecases/free_claims.py. That
sweep refuses hero screens on purpose -- green there means "you can afford
this", not "this is free", and following it once pressed the same Ascend button
eight times and spent hero shards for nothing. Levelling still has to happen,
so it happens HERE: entered on purpose, one screen, one button, and a spend
rule written down.

WHAT THIS MAY SPEND
-------------------
Hero EXP, and nothing else. It is an owned consumable earned from chests and
exploration -- no gems, no money. The Upgrade button carries its own receipt,
"216,922/2,200", have over need, and this routine presses it only when that
readout PARSES and have >= need. A missing readout, an unaffordable one, or a
currency marker anywhere near it refuses the press.

That single rule is what keeps the routine free. The game offers to top EXP up
with gems once you run out; stopping at have < need means the routine never
reaches that offer, rather than trusting itself to recognise and decline it.

WHY IT STOPS EARLY
------------------
Hero level is capped against the Furnace, and nothing on the screen announces
the cap -- an at-cap hero still shows an affordable Upgrade button. So the loop
measures the only thing that settles it: the level number. A press that does
not raise it ends the run and says so.
"""
import re
import time

from cmd_program.screen_action import tap_screen
from core.core import (
    ensure_screen,
    req_text_named,
    tap_on_template,
    tap_on_text,
)
from core.recalibrate import recalibrate


# Roster level labels, reading order, left to right then top to bottom.
#
# "Home.Heroes10" is row 2 slot 1. The name is not a typo to fix here -- it is
# the recorded key, and renaming it would silently break any other reader --
# but the ordering below is the truth about where it points.
ROSTER_LEVEL_ROIS = (
    "Home.Heroes.FirstHeroLevel",
    "Home.Heroes.SecondHeroLevel",
    "Home.Heroes.ThirdHeroLevel",
    "Home.Heroes.FourthHeroLevel",
    "Home.Heroes10",
    "Home.Heroes.FifthHeroLevel",
    "Home.Heroes.SixthHeroLevel",
    "Home.Heroes.SeventhHeroLevel",
)

DETAIL_LEVEL_ROI = "Home.Heroes.Detail.Level"
DETAIL_COST_ROI = "Home.Heroes.Detail.UpgradeCost"
DETAIL_UPGRADE_ROI = "Home.Heroes.Detail.Upgrade"

DEFAULT_LEVELS = 5

# Centre of the Upgrade button, as screen %. Measured live: the "Upgrade"
# caption reads back at x 42.7-57.6%, y 87.8-90.0%, and the button is larger
# than its caption in both directions, so the caption's centre is inside it.
UPGRADE_TAP_PCT = (50.0, 88.9)

# Real currency, and only that. Deliberately NOT usecases.free_claims's
# PRICE_MARKER, which also reads any "<digits>[.,]<two digits>" as a price. That
# extra clause is right for the shop and wrong here: on this screen every number
# is a resource count carrying a thousands separator, so one truncated OCR read
# ("205,22/4,200" for "205,522/4,200") looked like a price and refused a free
# upgrade four levels into a live run on 2026-09-04.
#
# Nothing is lost by dropping it, because the structural rule below is the
# stricter guard: the cost slot must read as <int>/<int> and nothing else, so
# "AED 17.99" is refused for not being a hero-EXP receipt rather than for
# matching a regex.
MONEY_MARKER = re.compile(r"(AED|USD|EUR|GBP|JPY|SAR|\$|€|£|¥|purchase|buy)", re.I)

# "Lv. 6", "Lv.6", "Lv 6" on the roster; a bare "7" on the detail screen.
LEVEL_RE = re.compile(r"(?:lv\.?\s*)?(\d{1,3})\b", re.I)
# "216,922/2,200" -- have over need. Thousands separators come back as either
# a comma or a dot depending on the engine and the crop.
COST_RE = re.compile(r"([\d,. ]*\d)\s*/\s*(\d[\d,. ]*)")

# How long to let the level number redraw after a press. The counter animates.
LEVEL_SETTLE_S = 1.5

# The EXP total animates for longer than the level does: it counts DOWN to its
# new value, and a frame caught mid-count reads the slash as a digit
# ("188,42217,800" for "188,422/7,800"), which the parser rightly refuses. Two
# extra reads, spaced, cover the tail of that animation. Measured live
# 2026-09-04: the same screen read cleanly two seconds later.
COST_READ_RETRIES = 2
COST_RETRY_SLEEP_S = 0.8


def _parse_level(text):
    """'Lv. 6' -> 6. None when the read holds no level at all.

    None is the answer for the Recruit card, which occupies a roster slot and
    shows a shard counter ("15/10") where a level would be. Treating that as a
    level is how a routine ends up opening the recruit screen.
    """
    if text is None:
        return None
    stripped = str(text).strip()
    if "/" in stripped:
        # A shard or fragment counter, not a level.
        return None
    match = LEVEL_RE.search(stripped)
    if not match:
        return None
    return int(match.group(1))


def _parse_cost(text):
    """'216,922/2,200' -> (216922, 2200). None when it is not a have/need pair.

    None is a refusal, not a zero: a cost that cannot be read is a cost that
    cannot be vouched for, and the caller must not press on it.
    """
    if text is None:
        return None
    match = COST_RE.search(str(text))
    if not match:
        return None
    try:
        have = int(re.sub(r"[,. ]", "", match.group(1)))
        need = int(re.sub(r"[,. ]", "", match.group(2)))
    except ValueError:
        return None
    return have, need


def _texts(reads, name):
    """Every line an ROI read, as plain strings."""
    return [str(item.get("text", "")) for item in (reads or {}).get(name, [])]


def _pick_lead_hero(reads):
    """(roi_name, level, box) for the highest-level hero on the roster.

    Highest, not first: slot 1 on a young account is the Recruit card and reads
    no level at all, and the heroes below the lead are level 1 fodder. The hero
    an account means by "my hero" is the one it has already invested in.

    Returns None when no slot reads a level -- an empty roster, or a screen
    that is not the roster.
    """
    best = None
    for name in ROSTER_LEVEL_ROIS:
        for item in (reads or {}).get(name, []):
            level = _parse_level(item.get("text"))
            if level is None:
                continue
            if best is None or level > best[1]:
                best = (name, level, item.get("box"))
    return best


def _spend_refusal(reads):
    """Why this screen must not be pressed, or None when it may be.

    Three rules, in order, each strictly narrower than the last:

      1. no real currency anywhere on the button,
      2. the cost slot must read as a hero-EXP receipt, <have>/<need>,
      3. have >= need.

    Rule 2 is what makes this safe rather than clever. Anything the routine
    cannot positively identify as an EXP receipt -- a price, a "Max Level"
    caption, an empty read -- fails it and stops the run.
    """
    cost_texts = _texts(reads, DETAIL_COST_ROI)
    button_texts = _texts(reads, DETAIL_UPGRADE_ROI)

    for text in cost_texts + button_texts:
        if MONEY_MARKER.search(text):
            return f"real-money marker on the upgrade button: {text!r}"

    if not cost_texts:
        return "no cost readout under the Upgrade button"

    for text in cost_texts:
        parsed = _parse_cost(text)
        if parsed is None:
            continue
        have, need = parsed
        if have < need:
            return f"not enough hero EXP ({have} < {need})"
        return None

    return f"cost readout did not parse as have/need: {cost_texts!r}"


def _read_detail(retries=COST_READ_RETRIES):
    """One OCR round trip for everything the loop decides on.

    Re-reads when the cost slot yields no have/need pair. A flaky read is not
    evidence of a price: the EXP bottle icon in front of the number used to
    swallow the slash until the ROI was moved off it, and the counter's own
    count-down animation does the same to any frame caught mid-flight. Refusing
    is still the right answer if it never resolves -- this only buys the screen
    time to finish drawing first.
    """
    reads = req_text_named(
        [DETAIL_LEVEL_ROI, DETAIL_COST_ROI, DETAIL_UPGRADE_ROI],
        read_kind="value",
    )
    if retries > 0 and not any(_parse_cost(t) for t in _texts(reads, DETAIL_COST_ROI)):
        time.sleep(COST_RETRY_SLEEP_S)
        return _read_detail(retries - 1)
    return reads


def _detail_level(reads):
    for text in _texts(reads, DETAIL_LEVEL_ROI):
        level = _parse_level(text)
        if level is not None:
            return level
    return None


def _open_lead_hero():
    """Navigate Home -> Heroes -> the lead hero's detail screen.

    Returns the first detail-screen read (level, cost and button in one round
    trip) so the caller does not pay for a second one, or None with the reason
    printed.
    """
    recalibrate()
    tap_on_text("Home.Heroes", wait=2)
    time.sleep(1)

    if not ensure_screen("Home.Heroes.Title", "Heroes"):
        print("Not on the Heroes roster after tapping it — stopping.")
        return None

    roster = req_text_named(list(ROSTER_LEVEL_ROIS), read_kind="value")
    lead = _pick_lead_hero(roster)
    if lead is None:
        print("No hero on the roster reads a level — nothing to upgrade.")
        return None

    name, level, box = lead
    print(f"Lead hero: {name} at level {level}")

    # Tap the level label itself rather than a guessed card centre: the label
    # is inside the card, and its box is the read we already paid for.
    x = int((box[0] + box[2]) / 2)
    y = int((box[1] + box[3]) / 2)
    tap_screen(x, y)
    time.sleep(1.5)

    reads = _read_detail()
    if _detail_level(reads) is None:
        print("Hero detail screen did not open (no level readout) — stopping.")
        tap_on_template("Global.Back", wait=2)
        return None
    return reads


def upgrade_hero(levels=DEFAULT_LEVELS):
    """Press Upgrade until the lead hero has gained `levels` levels.

    Returns {"start", "end", "gained", "stopped"}; `stopped` is None on a clean
    finish and a human-readable reason otherwise. `gained` is measured from the
    level number on screen, not counted from presses — a press that silently
    does nothing must not read back as progress.
    """
    result = {"start": None, "end": None, "gained": 0, "stopped": None}

    reads = _open_lead_hero()
    if reads is None:
        result["stopped"] = "could not open the lead hero"
        return result

    start = _detail_level(reads)
    result["start"] = start
    result["end"] = start
    target = start + int(levels)
    print(f"Upgrading from level {start} to level {target} (hero EXP only)")

    while result["end"] < target:
        refusal = _spend_refusal(reads)
        if refusal:
            result["stopped"] = refusal
            break

        before = result["end"]
        tap_screen(*UPGRADE_TAP_PCT)
        time.sleep(LEVEL_SETTLE_S)

        reads = _read_detail()
        after = _detail_level(reads)
        if after is None:
            result["stopped"] = "level readout disappeared after the press"
            break
        if after <= before:
            # Affordable and pressed, and the level did not move: the Furnace
            # cap, or a dialog over the screen. Either way, pressing again is
            # pressing something unknown.
            result["stopped"] = f"level stuck at {after} — cap reached or a dialog is open"
            result["end"] = after
            break

        result["end"] = after
        result["gained"] = after - start
        print(f"  level {before} -> {after}")

    result["gained"] = result["end"] - start
    if result["stopped"]:
        print(f"Stopped after +{result['gained']} levels: {result['stopped']}")
    else:
        print(f"Done: level {start} -> {result['end']} (+{result['gained']})")

    tap_on_template("Global.Back", wait=2)
    return result
