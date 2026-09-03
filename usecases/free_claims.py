"""Sweep the home screen's pending-action dots and press what is FREE.

The bot's task list was written screen by screen, so anything without a
hand-written task stayed unclaimed no matter how loudly the game flagged it.
The 7-day sign-in gift and the exploration chest both sat behind a solid red
dot for the whole first production run.

This task inverts that: read the game's own pending-work signal, visit the
entry points it marks, and press only what is green. Colour is the money guard
(see core/visual_cues) — orange is a purchase and blue is navigation, so a
screen offering nothing free simply gets backed out of.

Adding coverage for another screen is one row in ENTRY_POINTS, not a new module.

WHY DOTS REMAIN AFTER A CLEAN SWEEP
-----------------------------------
Investigated dot by dot on 2026-09-03 (Furnace 7). A home screen with no dots
left is NOT achievable, and chasing it is the wrong goal -- every dot below is
either a permanent indicator or gated behind spending:

  VIP badge     "VIP 1 Daily Free Bundle" reads *Locked*. It needs active VIP
                status, which costs gems or an owned VIP-time item. Nothing
                free to press. _activate_owned_vip_time already spends an item
                if one is owned; with none, this dot cannot clear.

  Shop banner   The dot never clears -- it advertises the paid packs -- but
                REAL free rewards sit behind it, across more than one tab: a
                daily Free chest and the unlocked Free-column tier of the Dawn
                Fund furnace track. 200 gems in one visit on 2026-09-03, none
                of which the bot had ever collected. Now handled by
                claim_shop_freebies(), guarded by PRICE_MARKER because
                req_detect finds ZERO green buttons on those screens and the
                colour guard therefore cannot vouch for them.

                (An earlier version of this note said the shop offered nothing
                worth automating. That was wrong: it generalised from the one
                free chest found first and missed the Dawn Fund track.)

  Events        Three dots: two are "new event available" tab markers, one is a
                Tips list of point-earning tasks (Lucky Wheel spins, hero-shard
                ascension). All cost gems or materials. Zero green buttons at
                any level.

  Heroes        Recruit/upgrade counters (0/10, 2/10, "Recruit Hero"). Pressing
                these spends resources.

So a persistent dot is not evidence of a bug. Before "fixing" one, open it and
check for a green button; if there is none, the game is advertising, not
offering.
"""
import re
import time

from core.coord_utils import BASE_HEIGHT, BASE_WIDTH
from core.recalibrate import recalibrate

from core.core import (
    req_detect,
    req_text,
    tap_on_text,
    tap_on_template,
    tap_on_green_button,
)
from cmd_program.screen_action import tap_screen
from core.visual_cues import dot_near


# (label, icon centre as screen %, dot search box as screen % [x1,y1,x2,y2])
# The dot hangs off the icon's top-right corner, so the search box is the
# icon's neighbourhood rather than the icon itself.
# These are the side/bottom banners, and their positions ROTATE with whatever
# events are running. A row whose box no longer contains its dot fails silently:
# the sweep just never follows it and says nothing. tests/test_free_claims_
# entry_points.py pins the positions measured live so a mismatch is at least
# visible, but it cannot predict a banner moving again -- re-measure after a
# game event changes the home screen.
ENTRY_POINTS = [
    # Was (75.9, 14.4) / [72, 10, 84, 18], which contained no dot on any screen
    # observed. Measured live 2026-09-03: the dot sits at (96.2%, 29.8%) and
    # tapping (90, 33) opens "Daily Sign-in Gift" with a claimable. The old
    # coordinates are why the free daily sign-in was never collected.
    ("Daily sign-in",  (90.0, 33.0), [83.0, 29.0, 100.0, 35.5]),
    # Trials sits immediately left of Events and was never an entry point, so
    # the sweep had never opened it. Found live 2026-09-03 holding a COMPLETED
    # mission ("Reach Squad's Total Power of 50,000", 50,000/50,000) with a
    # green Claim button going unpressed. Box stops short of the VIP dot at
    # y=8.2% on purpose: that one leads to a Locked bundle, not a claim.
    ("Trials",         (76.0, 13.5), [68.0, 10.0, 86.0, 17.0]),
    ("Exploration",    (9.7, 96.3),  [5.0, 92.0, 20.0, 99.0]),
    # Events' box used to run to y=18 and overlap Deals' 16-24. A dot in that
    # band matched Events first, so Deals could be skipped for its own dot.
    ("Events",         (91.7, 13.0), [88.0, 10.0, 99.0, 15.9]),
    ("Deals",          (91.7, 20.3), [88.0, 16.0, 99.0, 24.0]),
    ("Heroes",         (26.2, 96.3), [21.0, 92.0, 36.0, 99.0]),
    ("Backpack",       (42.1, 96.3), [37.0, 92.0, 52.0, 99.0]),
]


# Anything that looks like real money or a gem price. The shop is the one screen
# where a mis-tap costs something, so a tap is refused outright when any of this
# sits near the target -- belt and braces on top of the colour guard, because
# req_detect finds ZERO green buttons on these screens and so cannot vouch for
# them at all.
PRICE_MARKER = re.compile(
    r"(AED|USD|EUR|GBP|JPY|SAR|\$|€|£|¥|\d+[.,]\d{2}\b|purchase|buy)", re.I
)

# How far from a price a tap must stay, as a fraction of screen height/width.
PRICE_EXCLUSION_PCT = 9.0

# Where the shop lives on the home screen, and how far below a "Free"
# label its claimable tile sits (measured live 2026-09-03).
SHOP_ENTRY = (96.0, 8.2)
SHOP_TILE_OFFSET_PCT = 7.8
SHOP_MAX_TABS = 4

# Sub-tab descent budget: enough for every dot on screen plus a little room
# for ones that appear as others clear, hard-capped so a screen that keeps
# generating dots cannot hold the sweep forever.
SUBTAB_BUDGET_SLACK = 2
SUBTAB_HARD_CAP = 10


def _price_free_zone(target_pct, texts):
    """True when no price-looking text sits within the exclusion radius.

    target_pct is (x%, y%); texts is req_text()'s [[text, box], ...].
    """
    tx, ty = target_pct
    for text, box in texts or []:
        if not PRICE_MARKER.search(str(text)):
            continue
        x1, y1, x2, y2 = box
        cx = (x1 + x2) / 2 / BASE_WIDTH * 100
        cy = (y1 + y2) / 2 / BASE_HEIGHT * 100
        if abs(cx - tx) <= PRICE_EXCLUSION_PCT and abs(cy - ty) <= PRICE_EXCLUSION_PCT:
            print(f"Refusing to tap ({tx:.1f}%, {ty:.1f}%): {text!r} is "
                  f"{abs(cy - ty):.1f}% away and looks like a price.")
            return False
    return True


def _home_dots():
    """Solid dots only: those mark an action. Numbered badges are counts."""
    return [c for c in (req_detect("red_dot") or []) if c.get("kind") == "dot"]


def _pct_box_to_pixels(box):
    from core.coord_utils import BASE_HEIGHT, BASE_WIDTH
    return [int(box[0] / 100 * BASE_WIDTH), int(box[1] / 100 * BASE_HEIGHT),
            int(box[2] / 100 * BASE_WIDTH), int(box[3] / 100 * BASE_HEIGHT)]


def _claim_here(label, max_rounds=3, descend=True):
    """Press every free reward this screen offers, then look one level down.

    Returns how many landed.
    """
    claimed = 0
    for _ in range(max_rounds):
        if not tap_on_green_button(wait=2):
            break
        claimed += 1
        print(f"Claimed a free reward on {label}.")
        # Reward popups close on a tap-anywhere; recalibrate mops up the rest.
        tap_on_text("Home.VIP.Claim.TapAnywhereToExit", wait=1)

    if claimed or not descend:
        return claimed
    return _descend_into_subtabs(label)


def _descend_into_subtabs(label, max_subtabs=None):
    """Follow dots one level deeper when the screen itself offers nothing free.

    Heroes, Backpack and Events all flag pending work at the top level but keep
    their claimables in sub-tabs, so a home-level-only sweep walks right past
    them. Strictly one level: tap a dotted element, take anything green, come
    back, re-read. Bounded by max_subtabs, and each dot is visited once, so a
    dot that never clears cannot spin.
    """
    claimed = 0
    seen = set()
    # Budget derived from what is actually on screen, not a fixed 4. The Heroes
    # screen carries FIVE dots and the valuable one is last: "Recruit Hero",
    # behind which sit "Free Recruitments Today: 5" and a second batch of 1,
    # with real green buttons. A hardcoded 4 ran out one dot early and left six
    # free hero recruits unclaimed every run. Each dot is still visited at most
    # once (`seen`), so this cannot spin.
    budget = max_subtabs if max_subtabs is not None else min(
        len([c for c in (req_detect("red_dot") or []) if c.get("kind") == "dot"])
        + SUBTAB_BUDGET_SLACK,
        SUBTAB_HARD_CAP,
    )
    for _ in range(budget):
        dots = [c for c in (req_detect("red_dot") or []) if c.get("kind") == "dot"]
        target = next((d for d in dots if _key(d) not in seen), None)
        if target is None:
            break
        seen.add(_key(target))

        x1, y1, x2, y2 = target["box"]
        tap_screen(((x1 + x2) // 2, (y1 + y2) // 2))
        time.sleep(1)

        if tap_on_green_button(wait=2):
            claimed += 1
            print(f"Claimed a free reward one level into {label}.")
            tap_on_text("Home.VIP.Claim.TapAnywhereToExit", wait=1)

        # Back up to the screen we descended from so the next dot is addressable.
        tap_on_template("Global.Back", wait=2)
        time.sleep(0.5)
    return claimed


def _key(dot):
    """Grid-snapped identity so a dot that shifts a pixel is not 'new'."""
    x1, y1, x2, y2 = dot["box"]
    return ((x1 + x2) // 2 // 20, (y1 + y2) // 2 // 20)


def claim_shop_freebies():
    """Claim the free rewards the shop hands out, and nothing else.

    The shop dot never clears -- it advertises the paid packs -- but real free
    rewards sit behind it: a daily Free chest, and the unlocked Free-column
    tier of the Dawn Fund furnace track. Observed live 2026-09-03: 200 gems in
    one visit, none of which the bot had ever collected.

    This is the only routine that navigates a screen carrying real-money
    buttons ("AED 74.99", "AED 17.99" both observed), so it is guarded twice:

      1. Every candidate is a tile whose own label says "Free". Nothing is
         tapped on position alone.
      2. _price_free_zone refuses the tap if anything price-shaped is within
         PRICE_EXCLUSION_PCT of it. The colour guard cannot help here --
         req_detect reports zero green buttons on these screens -- so this
         text check IS the money guard for the shop.

    A layout change therefore makes this claim nothing, never something wrong.
    """
    recalibrate()
    if not tap_on_text("Home.Shop", wait=2):
        tap_screen(SHOP_ENTRY)
    time.sleep(2)

    claimed = 0
    for _ in range(SHOP_MAX_TABS):
        texts = req_text() or []
        targets = [
            (box, text) for text, box in texts
            if str(text).strip().lower() == "free"
        ]
        if not targets:
            break

        progressed = False
        for box, _label in targets:
            x1, y1, x2, y2 = box
            # The claimable tile sits just below its "Free" label.
            tx = (x1 + x2) / 2 / BASE_WIDTH * 100
            ty = (y1 + y2) / 2 / BASE_HEIGHT * 100 + SHOP_TILE_OFFSET_PCT
            if not _price_free_zone((tx, ty), texts):
                continue
            tap_screen((tx, ty))
            time.sleep(1.5)
            after = [t for t, _b in (req_text() or [])]
            if any("claim" in str(t).lower() for t in after):
                claimed += 1
                progressed = True
                print(f"Claimed a free shop reward at ({tx:.1f}%, {ty:.1f}%).")
                tap_on_text("Home.VIP.Claim.TapAnywhereToExit", wait=1)
                recalibrate()
                if not tap_on_text("Home.Shop", wait=2):
                    tap_screen(SHOP_ENTRY)
                time.sleep(2)
                break
        if not progressed:
            break

    recalibrate()
    print(f"Shop free-claim sweep done — {claimed} reward(s) claimed.")
    return True


def sweep_free_claims():
    recalibrate()
    dots = _home_dots()
    if not dots:
        print("No pending-action dots on the home screen, ending the task...")
        return True

    print(f"{len(dots)} pending-action dot(s) on the home screen.")
    total = 0
    for label, (cx, cy), search_box in ENTRY_POINTS:
        if not dot_near(dots, _pct_box_to_pixels(search_box), margin=0):
            continue
        print(f"Following the dot on {label}...")
        tap_screen((cx, cy))
        total += _claim_here(label)
        recalibrate()
        dots = _home_dots()          # the dot we just cleared should be gone

    print(f"Free-claim sweep done — {total} reward(s) claimed.")
    return True
