import time
from core.recalibrate import recalibrate

from core.core import (
    ensure_screen,
    req_ocr,
    req_text,
    tap_on_text,
    req_temp_match,
    tap_on_template,
    tap_on_templates_batch
)
from cmd_program.screen_action import(
    tap_screen,
    swipe_screen
)

def open_alliance():
    """Make sure the Alliance screen is up, then say whether it is.

    This block was copy-pasted into all five alliance routines byte-for-byte.
    Extracting it is not just tidiness: the sixth caller (alliance state
    capture) was about to become a seventh copy, and every copy discards
    tap_on_text's return value so none of them knows whether the recovery
    actually worked.

    Returns True when the screen is confirmed up. Callers currently ignore the
    result, exactly as they ignored it when the block was inline, so behaviour
    is unchanged -- but it is now available to anyone who wants to stop early.
    """
    time.sleep(0.5)
    if ensure_screen("Home.Alliance.Title", "Alliance"):
        return True
    recalibrate()
    tap_on_text("Home.Alliance", wait=2)
    return False



def tech_contribution():
    open_alliance()
    tap_on_text("Home.Alliance.Tech", wait=2)
    tap_on_template("Home.Alliance.Tech.Recommended", wait=2)
    tap_on_text("Home.Alliance.Tech.Contribute.FreeContribute", hold=7000, wait=2)
    tap_on_template("Global.Close", wait=2)
    tap_on_template("Global.Back", wait=2)
    return True

def auto_join():
    open_alliance()
    tap_on_text("Home.Alliance.War", wait=2)
    status = tap_on_text("Home.Alliance.War.AutoJoin", wait=2)
    if not status:
        tap_on_text("Home.Alliance.War.AutoJoining", wait=2)
    status = tap_on_text("Home.Alliance.War.AutoJoin.Enable", wait=2)
    if not status:
        tap_on_text("Home.Alliance.War.AutoJoin.Restart", wait=2)
    tap_on_template("Global.Close", wait=2)
    tap_on_template("Global.Back", wait=2)
    return True

def collect_chests():
    open_alliance()
    tap_on_text("Home.Alliance.Chests", wait=2)
    tap_on_text("Home.Alliance.Chests.LootChest",wait=2)
    status = tap_on_text("Home.Alliance.Chests.LootChest.ClaimAll", wait=2)
    if status:
        tap_on_text("Home.Alliance.Chests.LootChest.ClaimAll.TapAnywhereToExit", wait=2)
    tap_on_text("Home.Alliance.Chests.AllianceGift", wait=2, sleep=1)
    status = True
    while status:
        status = tap_on_text("claim", wait=2, sleep=1, threshold=1.0)
        if not status:
            swipe_screen(50.93, 48.78, 50.93, 32.52)
            status = tap_on_text("claim", wait=2, sleep=1, threshold=1.0)

    tap_on_template("Home.Alliance.Chests.HonorChest", wait=2)
    tap_on_text("Home.Alliance.Chests.HonorChest.TapAnywhereToExit", wait=2)
    tap_on_template("Global.Back", wait=2)
    return True


def help():
    open_alliance()
    tap_on_text("Home.Alliance.Help", wait=2)
    tap_on_text("Home.Alliance.Help.HelpAll", wait=2)
    tap_on_template("Global.Back", wait=2)
    return True

def shop():
    return



def collect_triumph():
    open_alliance()

    tap_on_text("Home.Alliance.Triumph", wait=2)
    for i in range(2):
        tap_on_template("Home.Alliance.Triumph.WeeklyAllianceTriumphChest", wait=2, sleep=0.5)
    
    text = req_text("Home.Alliance.Triumph.ActivityTriumphPoints", read_kind="value")
    try:
        activity_points = text[0][0].split("/")
        activity_points  = [int(a.replace(",", "")) for a in activity_points]
        if activity_points[0] > activity_points[1]:
            tap_on_text(text[0][0], align=[0, -30])
    except Exception as e:
        print(f"Reading Error - {e}")

