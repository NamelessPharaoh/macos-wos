"""Pixel cues: the glyphs and badges the navigation reads straight off a frame.

Split out of native/screen.py on 2026-09-11, when that file hit its 600-line
limit for the third time in two days and the limit hook refused the edit twice.
Everything here works on an image and nothing else -- no OCR items, no labels,
no driver -- which is why it is the clean seam. screen.py re-exports every name,
so `from native.screen import green_badges` and friends keep working.
"""
import cv2
import numpy as np


def _rgb(img, fx, fy):
    h, w = img.shape[:2]
    x, y = int(fx * w), int(fy * h)
    b, g, r = img[y - 3:y + 4, x - 3:x + 4].reshape(-1, 3).mean(0)
    return int(r), int(g), int(b)


def _is_icy(rgb):
    """The game's back arrow and modal × are the same icy white-cyan glyph."""
    r, g, b = rgb
    return r > 190 and g > 225 and b > 225


def has_back_arrow(img):
    return _is_icy(_rgb(img, 0.148, 0.063))


def has_modal_x(img):
    return _is_icy(_rgb(img, 0.868, 0.128))


# Both trailing spots were measured on 2026-09-10, each after go_home burned all
# nine steps on a dialog it could not close: "Welcome back!" offline income at
# (0.836, 0.224), and the scallop-topped pack offers ("Charm Master Pack", €5,99)
# at (0.779, 0.166), near-white rgb(255,253,247). That pixel reads 84-238 blue on
# home, VIP, Deals, the cart, Intel and the City tab, so it does not false-fire.
# Five spots now, each added after go_home spent its whole budget on a dialog it
# could not close. A scan of this band instead of a list was tried and rejected
# on 2026-09-11: the glyph colour also appears 9-40 times on ordinary pages
# (home, Deals, the Events calendar), so a scan would tap white pixels at random.
# Each spot below was checked against home, VIP, Deals, the cart, Intel, Sign-in
# and the City tab before being added.
DIALOG_X_SPOTS = ((0.814, 0.182), (0.815, 0.257), (0.836, 0.224), (0.779, 0.166),
                  (0.843, 0.150))   # Ally Treasure, from pet_adventure


def _is_dialog_glyph(rgb):
    r, g, b = rgb
    return r > 180 and g > 200 and b > 235


def has_dialog_x(img):
    """Centred dialogs (Tech contribute, Tips, Daily Rewards) draw their × at
    ~(0.814, 0.182); taller card dialogs (Gear Details) at ~(0.815, 0.257).
    It is a bluer white than the page-header glyph — measured rgb(201,219,252)
    — so the icy test is loosened here."""
    return _is_dialog_glyph(_rgb(img, *DIALOG_X_SPOTS[0]))


def dialog_x_spot(img, blocked=()):
    """The (fx, fy) of a dialog × on this frame, or None.

    `blocked` holds "dialog-x(fx, fy)" keys that go_home already tried without
    the frame changing; those spots are skipped so a second candidate gets a
    turn instead of the first one being retried forever."""
    for spot in DIALOG_X_SPOTS:
        if f"dialog-x{spot}" in blocked:
            continue
        if _is_dialog_glyph(_rgb(img, *spot)):
            return spot
    return None


def green_badges(img, ymin=0.0, ymax=1.0):
    """Bright green blobs of badge size. On Alliance → Tech the alliance's
    recommended tech carries a green thumbs-up at the hexagon's top-left."""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (40, 120, 120), (80, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _, stats, cent = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        x, y, bw, bh, a = stats[i]
        fy = cent[i][1] / h
        if 400 <= a <= 6000 and 0.6 <= bw / max(bh, 1) <= 1.6 and ymin <= fy <= ymax:
            out.append((cent[i][0] / w, fy, int(a)))
    return out
