"""Color-based UI cue detection: red-dot badges and green (free-action) buttons.

Two cues carry meaning uniformly across every WOS screen, independent of the
text inside them:

  RED DOT     — a SOLID red disc means an action is available behind this icon
                (a claim, a free collect). This is the signal a task should act
                on. A red badge carrying a NUMBER is a count instead (33 alliance
                requests, 2 unread mails): still pending work, but a quantity
                rather than a single button to press. Detections carry
                kind="dot" or kind="badge" so callers can tell them apart.

  GREEN BUTTON — the game's FREE-action color (Claim, Use). Paid actions are
                orange/yellow ("Buy & Use", price buttons) and navigation is
                blue ("Go"). Restricting taps to green buttons is what keeps a
                red-dot-following bot from walking into a purchase flow: on the
                VIP screen the red dot sits on an "Unlock" that leads to an
                AED 17.99 pack, one row above a genuinely free "Use".

Both return boxes as [x1, y1, x2, y2] in frame pixel space, matching the OCR
and template contracts.
"""
import cv2
import numpy as np

# Badge red: high saturation, bright, and red-dominant in BGR. The dominance
# check is what rejects the pink "2321%" discount starburst (high blue) and
# the orange price button (high green) that a hue window alone lets through.
RED_BANDS = (((0, 170, 150), (6, 255, 255)), ((175, 170, 150), (180, 255, 255)))
RED_AREA = (80, 2600)
RED_CIRCULARITY = 0.62
# A badge is a SOLID red disc, so nearly every pixel in its bounding box is
# red. A red price digit ("1,000" inside an orange Buy & Use button) fills far
# less of its box — the counters and corners stay orange. Measured on live
# frames: badges 0.78-0.95, price glyphs 0.35-0.55.
RED_MASK_FILL = 0.70
# Numbered badges ("33" on Alliance, "2" on Mail) are the same pending-work
# signal, but the white numeral punches a hole in the disc and drops mask-fill
# to ~0.4-0.65. Accept those too, on the condition that whatever is NOT red
# inside the box is WHITE (the numeral) rather than the orange/pink of a price
# glyph or a discount starburst.
RED_BADGE_MIN_FILL = 0.34
# A numbered badge must be MORE round than a plain dot needs to be: that is
# what separates it from the elongated red ribbons and banners in the city art
# (circularity 0.19-0.31), which also survive a fill floor.
RED_BADGE_CIRCULARITY = 0.70
# What is INSIDE the holes decides it. A badge's numeral is white with grey
# antialiasing; a red price glyph's counter shows the orange button beneath.
# Testing for "white" fails on the grey outlines (measured 0.17-0.53 white on
# real badges) — testing for "not warm" separates the two cleanly.
WARM_HUE = (8, 35)
WARM_S_MIN = 90
WARM_MAX_FRACTION = 0.25

# Free-action green. Bounds sized for real buttons so the small "NEW" badges
# and green benefit text ("+2.0%") never qualify.
GREEN_BAND = ((38, 90, 90), (85, 255, 255))
GREEN_MIN_W, GREEN_MAX_W = 150, 520
GREEN_MIN_H, GREEN_MAX_H = 45, 150


def _mask(hsv, band):
    lo, hi = band
    return cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))


def _numeral_inside(hsv_patch, red_mask):
    """True when the holes punched in a red badge are the white of a numeral.

    Distinguishes a numbered badge (red disc + white digits) from a red glyph
    printed on an orange button, where the surrounding pixels are orange.

    Only the ENCLOSED holes count. Testing every non-red pixel in the bounding
    box instead drags in the box corners that fall outside a round badge, and
    those carry whatever the page background is — enough to sink a real badge
    below any sane threshold (measured: 0.53 and 0.38 on live Alliance/Mail
    badges whose numerals are plainly white).
    """
    closed = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    holes = (closed > 0) & (red_mask == 0)
    if holes.sum() < 8:
        return False
    h_chan = hsv_patch[:, :, 0][holes]
    s_chan = hsv_patch[:, :, 1][holes]
    warm = ((h_chan >= WARM_HUE[0]) & (h_chan <= WARM_HUE[1]) & (s_chan >= WARM_S_MIN)).mean()
    return warm < WARM_MAX_FRACTION


def find_red_dots(img):
    """Pending-work cues.

    Returns [{'box': [x1,y1,x2,y2], 'area': int, 'kind': 'dot'|'badge'}].
    kind='dot' is a solid disc — an action is waiting. kind='badge' carries a
    numeral — a count of pending items.
    """
    if img is None or img.size == 0:
        return []
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = _mask(hsv, RED_BANDS[0]) | _mask(hsv, RED_BANDS[1])
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    dots = []
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        area = cv2.contourArea(c)
        if not (RED_AREA[0] <= area <= RED_AREA[1]):
            continue
        x, y, w, h = cv2.boundingRect(c)
        if not (0.55 <= w / max(h, 1) <= 2.2):   # a two-digit badge is wide
            continue
        if area / max(w * h, 1) < 0.55:
            continue
        peri = cv2.arcLength(c, True)
        circularity = 4 * np.pi * area / max(peri * peri, 1)
        sub_mask = mask[y:y + h, x:x + w]
        fill = sub_mask.mean() / 255.0

        if fill >= RED_MASK_FILL:
            if circularity < RED_CIRCULARITY:        # plain solid dot
                continue
        elif fill >= RED_BADGE_MIN_FILL:             # numbered badge?
            if circularity < RED_BADGE_CIRCULARITY:
                continue
            if not _numeral_inside(hsv[y:y + h, x:x + w], sub_mask):
                continue
        else:
            continue

        # Colour dominance over the RED pixels only — a badge's white numeral
        # would otherwise drag the box mean toward grey and fail the test.
        b, g, r = img[y:y + h, x:x + w][sub_mask > 0].mean(axis=0)
        if not (r > 120 and g < 0.62 * r and b < 0.62 * r):
            continue
        kind = "dot" if fill >= RED_MASK_FILL else "badge"
        dots.append({"box": [x, y, x + w, y + h], "area": int(area), "kind": kind})
    dots.sort(key=lambda d: (d["box"][1], d["box"][0]))
    return dots


def find_green_buttons(img):
    """Free-action buttons. Returns [{'box': [...], 'area': int}]."""
    if img is None or img.size == 0:
        return []
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.morphologyEx(_mask(hsv, GREEN_BAND), cv2.MORPH_CLOSE,
                            np.ones((7, 7), np.uint8))

    buttons = []
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (GREEN_MIN_W <= w <= GREEN_MAX_W and GREEN_MIN_H <= h <= GREEN_MAX_H):
            continue
        if cv2.contourArea(c) / max(w * h, 1) < 0.6:   # a filled pill, not a ring
            continue
        buttons.append({"box": [x, y, x + w, y + h], "area": int(w * h)})
    buttons.sort(key=lambda b: (b["box"][1], b["box"][0]))
    return buttons


def dot_near(dots, box, margin=90):
    """True when any red dot sits on or beside `box` (badges hang off corners)."""
    x1, y1, x2, y2 = box
    for d in dots:
        dx1, dy1, dx2, dy2 = d["box"]
        if (dx2 >= x1 - margin and dx1 <= x2 + margin
                and dy2 >= y1 - margin and dy1 <= y2 + margin):
            return True
    return False
