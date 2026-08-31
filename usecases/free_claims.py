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
"""
from core.recalibrate import recalibrate

from core.core import (
    req_detect,
    tap_on_text,
    tap_on_green_button,
)
from cmd_program.screen_action import tap_screen
from core.visual_cues import dot_near


# (label, icon centre as screen %, dot search box as screen % [x1,y1,x2,y2])
# The dot hangs off the icon's top-right corner, so the search box is the
# icon's neighbourhood rather than the icon itself.
ENTRY_POINTS = [
    ("7-Day sign-in",  (75.9, 14.4), [72.0, 10.0, 84.0, 18.0]),
    ("Exploration",    (9.7, 96.3),  [5.0, 92.0, 20.0, 99.0]),
    ("Events",         (91.7, 14.4), [88.0, 10.0, 99.0, 18.0]),
    ("Deals",          (91.7, 20.3), [88.0, 16.0, 99.0, 24.0]),
    ("Heroes",         (26.2, 96.3), [21.0, 92.0, 36.0, 99.0]),
    ("Backpack",       (42.1, 96.3), [37.0, 92.0, 52.0, 99.0]),
]


def _home_dots():
    """Solid dots only: those mark an action. Numbered badges are counts."""
    return [c for c in (req_detect("red_dot") or []) if c.get("kind") == "dot"]


def _pct_box_to_pixels(box):
    from core.coord_utils import BASE_HEIGHT, BASE_WIDTH
    return [int(box[0] / 100 * BASE_WIDTH), int(box[1] / 100 * BASE_HEIGHT),
            int(box[2] / 100 * BASE_WIDTH), int(box[3] / 100 * BASE_HEIGHT)]


def _claim_here(label, max_rounds=3):
    """Press every free reward this screen offers. Returns how many landed."""
    claimed = 0
    for _ in range(max_rounds):
        if not tap_on_green_button(wait=2):
            break
        claimed += 1
        print(f"Claimed a free reward on {label}.")
        # Reward popups close on a tap-anywhere; recalibrate mops up the rest.
        tap_on_text("Home.VIP.Claim.TapAnywhereToExit", wait=1)
    return claimed


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
