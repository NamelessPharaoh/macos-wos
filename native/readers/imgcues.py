"""Pixel reads the OCR cannot give: star counts and colour tiers.

Calibrated on 2026-09-08 survey frames (tests/fixtures/local/frames):
hero-card star band cyan clusters, roster card hue, chief-gear slot stars and
border hue. Every threshold here has a fixture test behind it."""
import cv2
import numpy as np

CYAN = ((80, 120, 150), (100, 255, 255))
YELLOW = ((20, 120, 150), (35, 255, 255))


def _clusters(img, band, lo, hi, amin, merge_dx):
    """Connected blobs of a colour inside a fractional band (x0,y0,x1,y1),
    merged by x proximity: a star glyph splits into 2-3 components, so blobs
    closer than merge_dx (fraction of the band width) are one star."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = band
    crop = img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    if crop.size == 0:
        return []
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, lo, hi)
    n, _, stats, cent = cv2.connectedComponentsWithStats(m)
    blobs = sorted(((cent[i][0] / crop.shape[1], int(stats[i][4])) for i in range(1, n) if stats[i][4] >= amin),
                   key=lambda b: b[0])
    clusters = []
    for x, a in blobs:
        if clusters and x - clusters[-1][0] < merge_dx:
            cx, ca = clusters[-1]
            clusters[-1] = ((cx + x) / 2, ca + a)
        else:
            clusters.append((x, a))
    return clusters


def hero_card_stars(img, band=(0.28, 0.66, 0.72, 0.72), full_area=2000):
    """(full_stars, has_partial) from the star row of a hero detail card."""
    cl = _clusters(img, band, *CYAN, amin=150, merge_dx=0.08)
    full = sum(1 for _, a in cl if a >= full_area)
    partial = any(a < full_area for _, a in cl)
    return full, partial


def rarity_from_hue(img, fx, fy):
    """Roster card background: orange = SSR (mythic), purple = SR (epic), blue = R (rare)."""
    h, w = img.shape[:2]
    px = img[int(fy * h), int(fx * w)]
    hue, sat, _ = cv2.cvtColor(np.uint8([[px]]), cv2.COLOR_BGR2HSV)[0][0]
    if sat < 40:
        return None
    if hue < 30 or hue > 165:
        return "mythic"
    if 120 <= hue <= 145:
        return "epic"
    if 95 <= hue < 120:
        return "rare"
    return None


TIERS = (("green", 35, 85), ("gold", 12, 35), ("blue", 95, 119), ("purple", 120, 150))


def tier_from_hue(img, fx, fy):
    h, w = img.shape[:2]
    px = img[int(fy * h), int(fx * w)]
    hue, sat, val = cv2.cvtColor(np.uint8([[px]]), cv2.COLOR_BGR2HSV)[0][0]
    if sat < 40 and val < 120:
        return None
    if hue < 12 or hue > 165:
        return "red"
    for name, lo, hi in TIERS:
        if lo <= hue <= hi:
            return name
    return None


def gear_slot(img, cx, cy):
    """(tier, stars, charm_tiers) for a chief-gear slot centred at (cx, cy).
    Stars stack in the slot's left column (dx ~ -0.04, dy -0.01..0.03); the
    three charm triangles sit below the slot (dy ~ +0.052) at dx -0.034, 0.002,
    0.037, so the star band stops at dy 0.04 and the charms are sampled by hue."""
    cl = _clusters(img, (cx - 0.06, cy - 0.03, cx - 0.02, cy + 0.04), *YELLOW, amin=60, merge_dx=0.0)
    stars = len([c for c in cl if c[1] >= 150])
    charms = [tier_from_hue(img, cx + dx, cy + 0.052) for dx in (-0.034, 0.002, 0.037)]
    return tier_from_hue(img, cx - 0.055, cy), stars, charms
