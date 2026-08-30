"""Color-based UI cue detection: red-dot badges and green (free-action) buttons.

Two cues carry meaning uniformly across every WOS screen, independent of the
text inside them:

  RED DOT     — "there is something actionable behind this". The game paints it
                on any icon/row/button with pending work. Cheaper and far more
                general than a per-screen template.

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

# Free-action green. Bounds sized for real buttons so the small "NEW" badges
# and green benefit text ("+2.0%") never qualify.
GREEN_BAND = ((38, 90, 90), (85, 255, 255))
GREEN_MIN_W, GREEN_MAX_W = 150, 520
GREEN_MIN_H, GREEN_MAX_H = 45, 150


def _mask(hsv, band):
    lo, hi = band
    return cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))


def find_red_dots(img):
    """Notification badges. Returns [{'box': [x1,y1,x2,y2], 'area': int}]."""
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
        if not (0.6 <= w / max(h, 1) <= 1.8):
            continue
        if area / max(w * h, 1) < 0.55:
            continue
        peri = cv2.arcLength(c, True)
        if 4 * np.pi * area / max(peri * peri, 1) < RED_CIRCULARITY:
            continue
        if mask[y:y + h, x:x + w].mean() / 255.0 < RED_MASK_FILL:
            continue
        b, g, r = img[y:y + h, x:x + w].reshape(-1, 3).mean(axis=0)
        if not (r > 120 and g < 0.62 * r and b < 0.62 * r):
            continue
        dots.append({"box": [x, y, x + w, y + h], "area": int(area)})
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
