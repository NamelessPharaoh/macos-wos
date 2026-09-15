"""Pixel cues: the glyphs and badges the navigation reads straight off a frame.

Split out of native/screen.py on 2026-09-11, when that file hit its 600-line
limit for the third time in two days and the limit hook refused the edit twice.
Everything here works on an image and nothing else -- no OCR items, no labels,
no driver -- which is why it is the clean seam. screen.py re-exports every name,
so `from native.screen import green_badges` and friends keep working.
"""
import os

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



def _glyph_contrast(img, fx, fy, r=0.022):
    """How much brighter the spot is than the darkest of its four neighbours.

    _is_dialog_glyph only asks whether a pixel is pale, and a dialog's white body
    is pale everywhere -- so on Ally Treasure (2026-09-11) three of the five spots
    "found" an x in the middle of the panel and go_home tapped all three before
    the retire reached the real one. A x is a pale glyph ISOLATED on a darker
    header: darker on every side. A tab edge or a panel seam is darker on one
    side only, which is why the minimum is taken rather than a ring mean -- a
    ring put the Events calendar's tab seam at 58.7 against a real-x floor of
    61.6, too thin to trust. On the four-sided minimum, five real x's measure
    70.7 to 110.8 and both known false positives are negative.
    """
    h, w = img.shape[:2]
    x, y, d = int(fx * w), int(fy * h), int(r * w * 0.8)
    core = float(np.mean(_rgb(img, fx, fy)))
    worst = None
    for dx, dy in ((-d, 0), (d, 0), (0, -d), (0, d)):
        px, py = x + dx, y + dy
        patch = img[max(0, py - 4):py + 5, max(0, px - 4):px + 5]
        if patch.size == 0:
            return 0.0
        side = core - float(patch.mean())
        worst = side if worst is None else min(worst, side)
    return 0.0 if worst is None else worst


GLYPH_MIN_CONTRAST = 35


def dialog_x_spot(img, blocked=()):
    """The (fx, fy) of a dialog × on this frame, or None.

    `blocked` holds "dialog-x(fx, fy)" keys that go_home already tried without
    the frame changing; those spots are skipped so a second candidate gets a
    turn instead of the first one being retried forever."""
    for spot in DIALOG_X_SPOTS:
        if f"dialog-x{spot}" in blocked:
            continue
        if (_is_dialog_glyph(_rgb(img, *spot))
                and _glyph_contrast(img, *spot) >= GLYPH_MIN_CONTRAST):
            return spot
    return None


BAND_MOVED_MIN = 2.0


def band_moved(a, b, ymin, ymax):
    """Did the view between ymin and ymax move from frame a to frame b?

    Grey, area-averaged to 64x64, mean absolute difference. Measured on the
    alliance tech tree, 2026-09-15: two captures of an unmoved tree 0.00 (the
    page has no animation), the 39px nudge at the bottom of Growth 7.5, a real
    half-page swipe 11.3-15.6. OCR text cannot answer this: signature() drops
    every string with a digit and ignores where a label sits."""
    h = a.shape[0]
    y0, y1 = int(ymin * h), int(ymax * h)
    small = [cv2.resize(cv2.cvtColor(f[y0:y1], cv2.COLOR_BGR2GRAY), (64, 64),
                        interpolation=cv2.INTER_AREA).astype(np.float32) for f in (a, b)]
    return bool(np.mean(np.abs(small[0] - small[1])) >= BAND_MOVED_MIN)


def is_selected_tab(img, fx, fy):
    """A selected page tab is pale, the others blue: rgb(219,229,232) against
    rgb(118,158,211) on all three alliance tech tabs, 2026-09-15. Sampled 0.08
    right of the tab's caption centre, off the caption's letters."""
    return min(_rgb(img, fx + 0.08, fy)) > 190


def green_badges(img, ymin=0.0, ymax=1.0):
    """Bright green blobs of badge size. Not specific: on Alliance → Tech this
    also returns the up-arrow and the chevrons in tech icons; thumbs_up_badges
    is the one that means "recommended"."""
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


THUMBS_UP_PNG = os.path.join(os.path.dirname(__file__), "thumbs_up.png")
THUMBS_UP_MIN_SCORE = 0.8
_thumbs_up = []


def thumbs_up_badges(img, ymin=0.0, ymax=1.0):
    """The green blobs that are the thumbs-up on the alliance's recommended tech.

    2026-09-14: a tech whose contribution bar is full wears a plain green
    UP-ARROW at its top-right instead, green_badges took it for the thumbs-up,
    and its dialog is "Research" -- starting the research on the alliance's
    resources -- not Contribute. Colour, fill and area overlap between the
    three greens on that page (arrow fill 0.59, thumb 0.70, icon chevrons
    0.64-0.76), so each blob is matched against the badge sprite, cut from a
    1284-wide frame: thumbs-up 0.94-1.00 on the page and in its dialog, the
    chevrons beside it at most 0.59, the arrow under 0.5."""
    if not _thumbs_up:
        tpl = cv2.imread(THUMBS_UP_PNG)
        if tpl is None:
            raise FileNotFoundError(THUMBS_UP_PNG)
        _thumbs_up.append(tpl)
    h, w = img.shape[:2]
    tpl = _thumbs_up[0]
    if w != 1284:
        tpl = cv2.resize(tpl, None, fx=w / 1284, fy=w / 1284)
    th, tw = tpl.shape[:2]
    out = []
    for fx, fy, a in green_badges(img, ymin, ymax):
        x, y = int(fx * w), int(fy * h)
        crop = img[max(0, y - th):y + th, max(0, x - tw):x + tw]
        if crop.shape[0] < th or crop.shape[1] < tw:
            continue
        if cv2.matchTemplate(crop, tpl, cv2.TM_CCOEFF_NORMED).max() >= THUMBS_UP_MIN_SCORE:
            out.append((fx, fy, a))
    return out
