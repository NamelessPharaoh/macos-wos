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
from cmd_program.screen_action import swipe_screen, tap_screen
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

# Where the shop lives on the home screen. This is the TOP-RIGHT cart, which
# opens the Top-up Center -- the only place the free rewards live. It is NOT
# the bottom-nav "Shop" button; references/TextArea/Home.json puts that anchor
# at y 97.8-99.47%, and it opens the VIP/Gem shop, where every tile is
# gem-priced or "Reach VIP Lv. 2 to unlock" and there is nothing free at all.
SHOP_ENTRY = (96.0, 8.2)

# How far from a "Free" label its claimable tile sits. A single signed offset
# cannot serve both layouts observed live, because the label plays a different
# role on each:
#
#   Dawn Fund     "Free" is a COLUMN HEADER and the tile sits BELOW it
#                 (the Lv-6 tile measures about +6.7%; +7.8% was tuned here).
#   Daily Deals   "Free" is a CAPTION and the chest sits ABOVE it: label centre
#                 y=790px, chest centre y=738px of 2460, so -2.1%.
#
# The order is a safety property, not a preference. On Daily Deals the +7.8%
# candidate lands at y~39.9% -- inside the "Purchase All Discount Packs /
# AED 17.99" banner, which spans y 825-1000px -- and _price_free_zone does NOT
# refuse it, because the "AED 17.99" text sits about 70% away horizontally,
# far outside PRICE_EXCLUSION_PCT. Probing UP first means that screen claims
# its chest and never evaluates the downward candidate at all.
SHOP_TILE_OFFSETS_PCT = (-2.1, 7.8)

# Labels that mark a free reward. "Claimable" is not a synonym anyone would
# guess: it is what the Dawn Market chest says, and that chest is free.
SHOP_CLAIM_LABELS = ("free", "claimable")

# What the reward popup says once a tile has actually paid out. Matching bare
# "claim" would be wrong now that "Claimable" is a target label -- the tile's
# own unclaimed caption would read back as proof it had been claimed.
SHOP_CLAIMED_MARKERS = ("claimed", "tap anywhere to exit")

SHOP_MAX_CLAIMS_PER_TAB = 4

# The Top-up Center's tab row scrolls, and the shop always opens on the first
# tab. Nine tabs were counted on 2026-09-04 -- Dawn Market, Training, Rise of
# the City, Daily Deals, Speedy Development Pack, Weekly/Monthly Cards, Dawn
# Fund, Get Gems, Tundra Supply Station -- with three or four visible at once.
# Until this existed the routine only ever read Dawn Market, so the daily free
# chest (100 gems, on Daily Deals) was unreachable no matter how the entry or
# the offsets were fixed. Three slots x three pages covers nine tabs; the
# overlap is harmless, a tab visited twice simply has nothing left to claim.
SHOP_TAB_ROW_Y_PCT = 13.7
SHOP_TAB_SLOTS_PCT = (18.5, 46.3, 74.1)
SHOP_TAB_PAGES = 3
SHOP_TAB_SWIPE_FROM_PCT = 83.3
SHOP_TAB_SWIPE_TO_PCT = 23.1

# How long to let a screen draw before believing what it shows, and how often
# to look while waiting.
SCREEN_SETTLE_TIMEOUT_S = 8.0
SCREEN_SETTLE_POLL_S = 0.5

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


def _wait_for_screen(label, timeout=None):
    """Block until the screen has actually drawn. True if it did.

    Trials cost 1,500 gems to this distinction on 2026-09-04. The sweep tapped
    in and immediately read:

        Visiting Trials...
        detect green_button: 0 hit(s)
        detect red_dot:     0 hit(s)
        No OCR results found.

    That was a screen carrying FOUR large green Claim buttons -- "Log in for
    2/3/4/5 day(s)", every one at full progress, 300+300+400+500 gems -- and
    several red badges. Zero of EVERYTHING is the signature of a screen that
    has not drawn yet, not of an empty one, and tap_on_green_button's own 2s
    deadline was not long enough to outlast the transition. Deals, read
    moments later on the same run, returned red_dot: 1 hit -- so the detectors
    were working fine; the frame was blank.

    This is the c94aa80 lesson one level lower. That commit established that
    zero dots DETECTED is not zero dots PRESENT. This one: a frame with no
    text on it at all is not evidence about anything, and must never be the
    basis for concluding "nothing free here".
    """
    # Resolved here rather than as a default argument so the constant stays
    # patchable -- a default binds once at import and tests could not shrink it.
    timeout = SCREEN_SETTLE_TIMEOUT_S if timeout is None else timeout
    deadline = time.time() + timeout
    while True:
        if req_text():
            return True
        if time.time() >= deadline:
            break
        time.sleep(SCREEN_SETTLE_POLL_S)
    print(f"{label}: still blank after {timeout:.0f}s — NOT read. Nothing was "
          f"claimed there and nothing was ruled out either.")
    return False


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

        # Same settle gate as the entry points: a sub-tab that has not drawn
        # reads as "no green button" and is indistinguishable from one with
        # nothing free on it.
        if _wait_for_screen(f"{label} sub-tab") and tap_on_green_button(wait=2):
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


def _enter_shop():
    """Open the Top-up Center via the top-right cart, and only via that.

    Deliberately NOT tap_on_text("Home.Shop"): that anchor is the BOTTOM NAV
    Shop button, a different screen (VIP/Gem shop) with no free tiles on it.
    It used to be tried first with SHOP_ENTRY merely as the fallback, so the
    routine reached the right screen only when OCR happened to MISS the
    bottom-nav text. That is why it claimed 200 gems on 2026-09-03 (OCR
    missed) and nothing on 2026-09-04 (OCR hit) -- with no error either time.
    """
    tap_screen(SHOP_ENTRY)
    time.sleep(2)


def _swipe_shop_tabs():
    """Scroll the Top-up Center's tab row one page to the left."""
    swipe_screen(
        (SHOP_TAB_SWIPE_FROM_PCT, SHOP_TAB_ROW_Y_PCT),
        (SHOP_TAB_SWIPE_TO_PCT, SHOP_TAB_ROW_Y_PCT),
        duration=400,
    )
    time.sleep(2)


def _claim_free_tiles_here():
    """Claim every free tile on the tab currently open; returns how many."""
    claimed = 0
    for _ in range(SHOP_MAX_CLAIMS_PER_TAB):
        texts = req_text() or []
        targets = [
            (box, text) for text, box in texts
            if str(text).strip().lower() in SHOP_CLAIM_LABELS
        ]
        if not targets:
            break

        progressed = False
        for box, _label in targets:
            x1, y1, x2, y2 = box
            tx = (x1 + x2) / 2 / BASE_WIDTH * 100
            label_y = (y1 + y2) / 2 / BASE_HEIGHT * 100
            for offset in SHOP_TILE_OFFSETS_PCT:
                ty = label_y + offset
                if not _price_free_zone((tx, ty), texts):
                    continue
                tap_screen((tx, ty))
                time.sleep(1.5)
                after = [t for t, _b in (req_text() or [])]
                if not any(marker in str(t).lower()
                           for t in after for marker in SHOP_CLAIMED_MARKERS):
                    # A probe that did not pay out may still have navigated
                    # somewhere. Only try the next offset while a label that
                    # could justify this target is demonstrably still on
                    # screen -- otherwise the next tap is a blind one on a
                    # layout nobody has looked at.
                    if any(str(t).strip().lower() in SHOP_CLAIM_LABELS
                           for t in after):
                        continue
                    break
                claimed += 1
                progressed = True
                print(f"Claimed a free shop reward at ({tx:.1f}%, {ty:.1f}%).")
                # Dismiss the popup and stay put. Deliberately NOT
                # recalibrate() + re-enter: recalibrate walks back to the home
                # screen, and re-entering the shop reopens the FIRST tab, so a
                # reward on any later tab could never be reached. Verified live
                # 2026-09-04: dismissing the popup leaves the same tab open.
                tap_on_text("Home.VIP.Claim.TapAnywhereToExit", wait=1)
                time.sleep(1.5)
                break
            if progressed:
                break
        if not progressed:
            break
    return claimed


def claim_shop_freebies():
    """Claim the free rewards the shop hands out, and nothing else.

    The shop dot never clears -- it advertises the paid packs -- but real free
    rewards sit behind it: a daily Free chest on Daily Deals (100 gems, taken
    live 2026-09-04), a Claimable chest on Dawn Market, and the unlocked
    Free-column tier of the Dawn Fund furnace track.

    Reaching them needs three things that were each wrong or missing, and each
    of which failed silently -- the routine reported success while claiming
    nothing:

      * the top-right cart, not the bottom-nav Shop button (see _enter_shop);
      * every tab, not just the one the shop opens on (see SHOP_TAB_SLOTS_PCT);
      * an offset that can point UP as well as down (SHOP_TILE_OFFSETS_PCT).

    This is the only routine that navigates a screen carrying real-money
    buttons ("AED 74.99", "AED 17.99", "AED 184.99" all observed), so it is
    guarded three ways:

      1. Every candidate is a tile whose own label says "Free" or "Claimable".
         Nothing is tapped on position alone.
      2. _price_free_zone refuses the tap if anything price-shaped is within
         PRICE_EXCLUSION_PCT of it. The colour guard cannot help here --
         req_detect reports zero green buttons on these screens -- so this
         text check is the money guard for the shop.
      3. The upward offset is tried first, so the one screen where the
         downward offset is known to land on a purchase banner claims its
         chest and never evaluates the downward candidate.

    Guard 2 has a hole worth naming rather than pretending away: it measures
    distance to price TEXT, and a wide purchase banner puts its price at one
    end. On Daily Deals a tap 70% of the screen away from "AED 17.99" is still
    on that banner. Guard 3 is what actually covers that case, which is why
    the offset order is load-bearing and not a preference.
    """
    recalibrate()
    _enter_shop()

    claimed = 0
    for page in range(SHOP_TAB_PAGES):
        for slot in SHOP_TAB_SLOTS_PCT:
            tap_screen((slot, SHOP_TAB_ROW_Y_PCT))
            time.sleep(2)
            claimed += _claim_free_tiles_here()
        if page < SHOP_TAB_PAGES - 1:
            _swipe_shop_tabs()

    recalibrate()
    print(f"Shop free-claim sweep done — {claimed} reward(s) claimed.")
    return True


def sweep_free_claims():
    recalibrate()
    dots = _home_dots()
    # No early return on an empty dot list. That was the same mistake as the
    # per-entry-point gate below, one level up: zero dots DETECTED is not zero
    # dots PRESENT. Every dot on a red icon (Trials' shield, Deals' gift box)
    # merges with it and is rejected, so "no dots" is exactly what a screen
    # full of unclaimed rewards looks like to req_detect.
    print(f"{len(dots)} pending-action dot(s) detected on the home screen "
          f"(reported, not used as a gate).")

    # Visit EVERY entry point, dot or no dot. The dot was only ever an
    # optimisation -- it decided whether to bother navigating -- and it was
    # costing whole screens silently:
    #
    #   Trials is a RED shield and Deals a RED gift box, so their dots merge
    #   with the icon into one blob. Measured live 2026-09-03: the Trials blob
    #   is area 4179 against a RED_AREA cap of 2600, the Deals blob circularity
    #   0.24 against a 0.70 floor. Both are rejected. 54 red contours on that
    #   frame, 3 reported. The detector is fine on blue icons and blind on red
    #   ones, and no amount of tuning makes that guarantee coverage.
    #
    # Skipping the gate is safe because the CLAIM is already guarded: _claim_here
    # presses green buttons only, so visiting a screen with nothing free costs a
    # few seconds and takes nothing. Trading ~40s of navigation for the end of an
    # entire class of silent miss is the right way round -- the dot count is now
    # reported for the operator, not used as a gate.
    total = 0
    unread = []
    for label, (cx, cy), search_box in ENTRY_POINTS:
        flagged = dot_near(dots, _pct_box_to_pixels(search_box), margin=0)
        print(f"Visiting {label}{' (dot)' if flagged else ''}...")
        tap_screen((cx, cy))
        if _wait_for_screen(label):
            total += _claim_here(label)
        else:
            unread.append(label)
        recalibrate()
        dots = _home_dots()

    print(f"Free-claim sweep done — {total} reward(s) claimed.")
    if unread:
        # An unread screen is not a clean screen. Saying so is the whole point:
        # every miss this project has found was a routine reporting success
        # over a screen it had not actually looked at.
        print(f"⚠️  {len(unread)} screen(s) never drew and were NOT read: "
              f"{', '.join(unread)}. A sweep carrying this warning is not a "
              f"clean sweep — rerun before trusting it.")
    return True
