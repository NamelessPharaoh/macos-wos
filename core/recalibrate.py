import time
import requests
from core.core import req_ocr, tap_on_template, tap_on_text, tap_on_templates_batch, req_text
from cmd_program.screen_action import tap_screen
from core.coord_utils import percent_to_pixel
from core.anchor_drift import (
    INCONCLUSIVE,
    NO_TEXT,
    OCR_UNAVAILABLE,
    anchor_is_in_place,
    format_report,
    measure_drift,
)


# This is the one place in the whole run that knows it is looking at the home
# screen, which is the precondition an anchor check needs. A gate in run.sh
# cannot have it: run.sh's last line is `python -m Main.main`, so anything added
# there fires before the bot has navigated anywhere, and "not on Home" -- by far
# the commonest cause of a missed anchor -- would be reported as layout drift.
HOME_ANCHOR = "Home.World"


def _warn_if_anchor_moved(anchor_read):
    """Home.World read back -- but did it read back in the right PLACE?

    Cheap: reuses the read the caller already performed (~17ms cropped) instead
    of paying for a full frame (86-376ms) on every loop. A cropped read is
    confined to its own ROI plus 50px of padding (core/ocr.py:995, :1023), so
    this catches drift small enough to still read; drift large enough to push the
    text clean out of the box shows up as a miss and lands in
    _diagnose_missing_homepage() instead.

    Warns rather than raises. Reading the right word in a slightly wrong place
    still works, and locking the bot out on a single sub-pixel-noise reading
    would be a worse failure than the drift it is reporting.
    """
    if anchor_is_in_place(HOME_ANCHOR, anchor_read):
        return
    report = measure_drift()
    if not report.drifted:
        return
    print(format_report(report))
    print(f"WARNING: {HOME_ANCHOR} read in the wrong place. Every recorded ROI may be "
          f"offset by the same amount. Run the anchor_drift task before trusting reads.")


def _diagnose_missing_homepage():
    """Say WHICH system failed, because 'Homepage Not found' hid three of them.

    An unreachable OCR server, a screen that moved under the ROIs, and a bot
    genuinely stuck on some other page all produced one identical message, which
    sent every investigation to the wrong place first.
    """
    report = measure_drift()

    if report.verdict == OCR_UNAVAILABLE:
        return ("Homepage not found, and OCR is unreachable. The READER is down, not "
                "the layout -- check the OCR server on $OCR_PORT before anything else.")

    if report.verdict == NO_TEXT:
        return ("Homepage not found and the frame carries no text at all -- the "
                "emulator is likely asleep, black, or mid-transition.")

    if report.drifted:
        return ("Homepage not found because the UI has MOVED, not because the bot is "
                "on the wrong screen. Every recorded ROI is offset:\n"
                + format_report(report))

    if report.verdict == INCONCLUSIVE:
        return ("Homepage not found, and too few anchors matched to say whether the "
                "layout moved:\n" + format_report(report))

    return ("Homepage not found. The anchors are exactly where they should be, so the "
            "layout is fine and the bot is genuinely stuck on another screen it could "
            "not back out of. Stopping the Bot...")


def recalibrate(timeout=30):
    is_home = False
    retry = 0
    start = time.time()
    
    # Percentage-based coordinates
    center_x_pct, center_y_pct = 50, 50  # Center of screen
    top_left_x_pct, top_left_y_pct = 6.48, 6.9  # Top-left area
    
    while(not is_home) and ((time.time()) - start) < timeout:
        found = False
        time.sleep(1)
        anchor_read = req_text(HOME_ANCHOR)
        text = anchor_read

        try:
            text = text[0][0].lower()
        except Exception as e:
            print("Finding The Homepage...")

        if text == "world":
            is_home = True
        elif text == "city":
            tap_on_text("World.City", sleep=2)
            is_home = True
            
        if is_home:
            print("On homepage")
            _warn_if_anchor_moved(anchor_read)
            time.sleep(1)
            break
        found = tap_on_templates_batch(
            [
                "Global.Back",
                "Global.Close", 
                "FirstPurchase.Close",
                "Home.Store.Back"
                
            ],
            wait=1,
            parallel = True
        )
        # found = tap_on_template("Global.Back", sleep=1)
        # if not found:
        #     found = tap_on_template("Global.Close", sleep=1)
        # if not found:
        #     found = tap_on_template("FirstPurchase.Close", sleep=1)

        targets = [
            "tap anywhere to continue",
            "tap to exit",
            "click to continue",
            "click anywhere to exit",
            "Reconnect"
        ]
        res = req_ocr()
        for item in res:
            if item["text"] in targets:
                box = item["box"]
                coord = ((box[0]+box[2])//2, (box[1]+box[3])//2)
                tap_screen(coord)
                found = True

        if not found:
            time.sleep(1)
            text = req_text("Home.World")
            try:
                text = text[0][0]
            except Exception as e:
                print(f"Error... {e}")
            if text:
                found = True
                if text.lower() != "city" and text.lower() != "world":
                    tap_screen(center_x_pct, center_y_pct)
            else:
                found = False

        if found:
            start = time.time()
        else:
            tap_screen(top_left_x_pct, top_left_y_pct)
            time.sleep(1)

    
    time.sleep(1)
    if not is_home:
        raise RuntimeError(_diagnose_missing_homepage())

