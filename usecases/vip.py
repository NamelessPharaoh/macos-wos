"""VIP task: activate owned (free) VIP time, then claim what the red dot points at.

Why this is cue-driven rather than ROI-driven. The old flow hunted a "Claim"
button at coordinates recorded on a VIP-6 account; on a VIP-1 account it polled
nine times and gave up. The deeper problem was that there was genuinely nothing
to claim: the daily bundle stays Locked while VIP time is inactive, and the item
that activates it sits behind the bottom "Unlock" CTA, one row above gem-priced
"Buy & Use" entries.

So the task follows the game's own signals instead of stored geometry:
  red dot     -> something here is actionable
  GREEN button-> that action is free (Claim / Use)
Paid actions are orange and navigation is blue, and tap_on_green_button only
ever presses green. That colour guard is what makes following a red dot safe:
the dot on this screen sits on an Unlock leading to an AED 17.99 pack.
"""
import time

from core.recalibrate import recalibrate

from core.core import (
    req_ocr,
    req_text,
    req_detect,
    read_lock_marker,
    tap_on_text,
    req_temp_match,
    tap_on_template,
    tap_on_green_button,
    tap_on_templates_batch
)
from cmd_program.screen_action import(
    tap_screen,
    swipe_screen,
    input_text
)


def _open_vip_screen():
    return bool(tap_on_text("Home.VIPLevel", wait=2))


def _vip_is_locked():
    """True while VIP time is inactive.

    Both "VIP 1 Benefits(Locked)" and the daily bundle's "Locked" say the same
    thing, so any Locked marker on the page is the signal — no ROI to drift.
    """
    # Delegates to core.core.read_lock_marker so this shares one definition of
    # "locked" with every other feature — and picks up its fix: `"locked" in
    # text` also matched "unlocked" and "Blocked".
    return read_lock_marker() is not None


def _activate_owned_vip_time():
    """Spend an ALREADY-OWNED VIP time item to switch VIP on. Never buys.

    The 'Obtain more' panel lists the owned item with a green Use beside
    gem-priced Buy & Use rows; tap_on_green_button refuses everything that is
    not green, so an empty inventory simply means nothing is pressed.
    """
    if not _vip_is_locked():
        return False

    if not tap_on_text("Home.VIP.Unlock", wait=2):
        print("VIP is locked but the Obtain-more button was not found, skipping activation...")
        return False

    used = tap_on_green_button(text="Use", wait=2)
    if used:
        print("Activated VIP time from inventory (free).")
    else:
        print("No free VIP time item owned — leaving VIP inactive (never buying).")

    # Re-enter the VIP screen cleanly rather than guessing the panel's close box.
    recalibrate()
    _open_vip_screen()
    return used


def _claim_free_rewards(max_rounds=3):
    """Press every FREE reward the screen is flagging, newest dot first."""
    claimed = 0
    for _ in range(max_rounds):
        if not (req_detect("red_dot") or []):
            break                      # nothing pending on this screen
        if not tap_on_green_button(text="Claim", wait=2):
            break                      # pending, but nothing free to press
        claimed += 1
        tap_on_text("Home.VIP.Claim.TapAnywhereToExit", wait=2)
        time.sleep(0.5)
    return claimed


def collect_vip_rewards():
    recalibrate()
    if not _open_vip_screen():
        print("Could not open the VIP screen, ending the task...")
        return False

    _activate_owned_vip_time()
    claimed = _claim_free_rewards()

    print(f"VIP task done — {claimed} free reward(s) claimed.")
    recalibrate()
    return True


def buy_vip_time(day=30):
    time.sleep(0.5)
    title = req_text("Home.VIP.Title")
    try:
        title = title[0][0]
    except Exception as e:
        print(f"Error while reading page title - {e}, Continuing...")

    if title.lower != "vip":
        recalibrate()
        tap_on_text("Home.VIPLevel", wait=2)
