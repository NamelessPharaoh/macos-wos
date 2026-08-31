"""Measure how far the rendered UI has moved away from the recorded ROIs.

Why this exists
---------------
Three things push this game's content down the screen, and only one of them has
a UI:

    MuMu's own cutout option ....... off
    cutout.emulation.tall RRO ...... 126px, enabled over adb, no UI at all
    in-game "Non-standard Screen Adaptation" ... 77

Every box in references/TextArea was recorded with that stack in place. Change
any layer and all 355 of them are wrong at once, which surfaces six screens deep
as a clipped read rather than as an obvious failure. This module turns that into
a number you can act on.

Why a FULL-FRAME read
---------------------
req_text(name) looks up the recorded box (core/core.py:628) and hands it to the
server as an ROI (core/core.py:643), which crops to it (core/core.py:142). If
drift moved the text OUT of that box, the read returns nothing -- indistinguishable
from OCR being broken, and the bigger the drift the more certain you are to get
nothing. A measuring instrument cannot be built on the crop it is measuring. So
this reads the whole frame (req_text() with no args) and finds each anchor by its
text.

read_kind stays UNSET here on purpose: read_kind="value" asks the server for a
burn-in Paddle shadow-compare (core/core.py:137-140). On a full frame that is a
961-2771ms second pass, and it would fill logs/ocr_burnin.jsonl with
DIGIT_MISMATCH noise from a read that is positional, not numeric.
"""
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from core.core import req_text, text_area
from core.coord_utils import BASE_HEIGHT, BASE_WIDTH, box_percent_to_pixel


# Anchors are LABEL text, never value text. The obvious top-of-screen candidates
# in Home.json are account state -- Home.Gems '326,248', Home.Coal '937.2M',
# Home.Power '143,488,645', Home.Survivor '32/32' -- which change during play and
# again on every account switch (core/change_player.py). Matching on those would
# work once and then quietly stop.
#
# Two bands, deliberately far apart: the vertical lever arm between them is what
# tells a whole-screen shift apart from a safe-area relayout or a rescale. A
# single band, however many anchors it holds, cannot distinguish those.
UPPER_BAND = "UPPER"
BOTTOM_BAND = "BOTTOM"

ANCHORS = [
    # key                     band
    ("Home.VIPLevel",         UPPER_BAND),    # 'VIP'          y  8.21- 9.88
    ("Home.Events",           UPPER_BAND),    # 'Events'       y 15.73-16.87
    ("Home.Deal",             UPPER_BAND),    # 'Deals'        y 21.59-22.64
    ("Home.World",            BOTTOM_BAND),   # 'World'        y 97.76-99.23
    ("Home.Shop",             BOTTOM_BAND),   # 'Shop'         y 97.80-99.47
    ("Home.Heroes",           BOTTOM_BAND),   # 'Heroes'       y 97.85-99.19
    ("Home.Alliance",         BOTTOM_BAND),   # 'Alliance'     y 97.85-99.23
    ("Home.Backpack",         BOTTOM_BAND),   # 'Backpack'     y 97.97-99.27
    ("Home.Exploration",      BOTTOM_BAND),   # 'Exploration'  y 98.01-99.15
]

# Enough anchors per band that one bad match cannot carry the verdict. Below
# these counts the module refuses to report rather than averaging whatever it
# happened to find -- a confident-looking number from one lucky match is how you
# end up retuning 355 ROIs against noise.
QUORUM = {UPPER_BAND: 2, BOTTOM_BAND: 3}

# Percent of frame height. 0.30% of 2460px is ~7px, comfortably inside a text
# box's own height, so anything under this is measurement noise rather than drift.
TOLERANCE_PCT = 0.30

# Same bar ensure_screen uses (core/core.py:262), for the same reason: OCR
# routinely returns a character or two off on this game's font.
TEXT_MATCH_THRESHOLD = 80

# Verdicts
OK = "OK"
TRANSLATION = "TRANSLATION"
SAFE_AREA_RELAYOUT = "SAFE_AREA_RELAYOUT"
RESCALE = "RESCALE"
INCONCLUSIVE = "INCONCLUSIVE"
OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
NO_TEXT = "NO_TEXT"

# Verdicts that carry a usable measurement.
MEASURED = (OK, TRANSLATION, SAFE_AREA_RELAYOUT, RESCALE)


@dataclass
class AnchorMatch:
    key: str
    band: str
    expected: str
    found: str
    dx_pct: float
    dy_pct: float


@dataclass
class DriftReport:
    verdict: str
    reason: str = ""
    matches: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    upper_dy_pct: float = None
    bottom_dy_pct: float = None

    @property
    def measured(self):
        return self.verdict in MEASURED

    @property
    def drifted(self):
        return self.verdict in (TRANSLATION, SAFE_AREA_RELAYOUT, RESCALE)


def _centre(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _expected(key):
    """(expected text, expected centre in pixels) for a recorded anchor."""
    entry = text_area.get(key)
    if not entry or not entry.get("box"):
        return None, None
    return entry.get("text", ""), _centre(box_percent_to_pixel(entry["box"]))


def _best_match(expected_text, expected_centre, read):
    """The read item whose text matches, nearest to where the anchor should be.

    Nearest-position rather than first-hit: 'Shop' and 'Shop' can both appear on
    one frame, and picking the wrong one injects a fake delta the size of the
    screen.
    """
    candidates = [
        item for item in read
        if fuzz.ratio(str(item[0]).lower(), expected_text.lower()) >= TEXT_MATCH_THRESHOLD
    ]
    if not candidates:
        return None

    def distance(item):
        cx, cy = _centre(item[1])
        return ((cx - expected_centre[0]) ** 2 + (cy - expected_centre[1]) ** 2) ** 0.5

    return min(candidates, key=distance)


def _band_mean(matches, band):
    values = [m.dy_pct for m in matches if m.band == band]
    if not values:
        return None
    return sum(values) / len(values)


def _classify(upper_dy, bottom_dy):
    """Name the failure mode, because each one has a different remedy."""
    upper_moved = abs(upper_dy) > TOLERANCE_PCT
    bottom_moved = abs(bottom_dy) > TOLERANCE_PCT

    if not upper_moved and not bottom_moved:
        return OK, "Anchors are where the recorded ROIs expect them."

    if abs(upper_dy - bottom_dy) <= TOLERANCE_PCT:
        return TRANSLATION, (
            f"Whole screen shifted {upper_dy:+.2f}%. A single inset change explains "
            f"this; retuning one knob (or one global offset) fixes every ROI."
        )

    # Drift growing with distance from the top is a scale about y=0, not a shift.
    if abs(bottom_dy) > abs(upper_dy):
        return RESCALE, (
            f"Drift grows down the screen (upper {upper_dy:+.2f}%, bottom "
            f"{bottom_dy:+.2f}%). The viewport was rescaled, not moved -- no single "
            f"offset or slider value corrects it. Re-record the ROIs."
        )

    # Top moved, bottom pinned. Android anchors the status row to the top inset
    # and the nav bar to the bottom, so this is the expected shape of removing a
    # top cutout -- and the one a two-bucket classifier would misread as RESCALE
    # and over-prescribe a full re-record for.
    return SAFE_AREA_RELAYOUT, (
        f"Top band moved {upper_dy:+.2f}% while the bottom nav held at "
        f"{bottom_dy:+.2f}%. That is a safe-area relayout, not a shift: no slider "
        f"value fixes it. Re-anchor the top-band ROIs only."
    )


def measure_drift():
    """Compare where anchor text actually lands against where it was recorded.

    Deltas are reported as a PERCENTAGE of frame height, not pixels: that is how
    all 355 ROIs are already stored, and it survives the 2x render probe in
    TODOS.md doubling BASE_HEIGHT under us.
    """
    read = req_text()

    if read is None:
        return DriftReport(
            verdict=OCR_UNAVAILABLE,
            reason="OCR returned nothing at all -- the server is down or unreachable. "
                   "This says nothing about the layout.",
        )
    if not read:
        return DriftReport(
            verdict=NO_TEXT,
            reason="OCR ran but found no text on the frame (blank or mid-transition). "
                   "Retry on a settled screen.",
        )

    matches, missing = [], []
    for key, band in ANCHORS:
        expected_text, expected_centre = _expected(key)
        if not expected_text:
            missing.append(f"{key} (no recorded box)")
            continue

        hit = _best_match(expected_text, expected_centre, read)
        if hit is None:
            missing.append(key)
            continue

        found_centre = _centre(hit[1])
        matches.append(AnchorMatch(
            key=key,
            band=band,
            expected=expected_text,
            found=str(hit[0]),
            dx_pct=(found_centre[0] - expected_centre[0]) / BASE_WIDTH * 100,
            dy_pct=(found_centre[1] - expected_centre[1]) / BASE_HEIGHT * 100,
        ))

    short = [
        f"{band} {sum(1 for m in matches if m.band == band)}/{need}"
        for band, need in QUORUM.items()
        if sum(1 for m in matches if m.band == band) < need
    ]
    if short:
        return DriftReport(
            verdict=INCONCLUSIVE,
            reason=(
                f"Not enough anchors matched to trust a delta ({', '.join(short)}). "
                f"Missing: {', '.join(missing) or 'none'}. Both bands are needed to "
                f"tell a shift apart from a relayout, so no number is reported."
            ),
            matches=matches,
            missing=missing,
        )

    upper_dy = _band_mean(matches, UPPER_BAND)
    bottom_dy = _band_mean(matches, BOTTOM_BAND)
    verdict, reason = _classify(upper_dy, bottom_dy)

    return DriftReport(
        verdict=verdict,
        reason=reason,
        matches=matches,
        missing=missing,
        upper_dy_pct=upper_dy,
        bottom_dy_pct=bottom_dy,
    )


def anchor_is_in_place(key, read_result):
    """Cheap check: did an already-performed cropped read land where it should?

    Takes the result the caller ALREADY has rather than reading again. recalibrate()
    reads Home.World every loop anyway (core/recalibrate.py:20); a cropped read is
    ~17ms against a full frame's 86-376ms, so the expensive path is worth reserving
    for the case where this one fails.

    Range: a cropped read is confined to its own ROI plus the 50px of padding
    core/ocr.py:995 adds and :1023 subtracts back off, so this can only see drift
    up to roughly that much. Larger drift pushes the text clean out of the box and
    the read simply misses -- which is the caller's cue to escalate to
    measure_drift(). Both halves are needed; neither covers the other's range.
    """
    if not read_result:
        return False
    try:
        box = read_result[0][1]
        _, expected_centre = _expected(key)
    except (IndexError, TypeError):
        return False
    if expected_centre is None:
        return False
    dy_pct = (_centre(box)[1] - expected_centre[1]) / BASE_HEIGHT * 100
    return abs(dy_pct) <= TOLERANCE_PCT


def format_report(report):
    """Human-readable drift table. Percentages, because pixels lie at 2x."""
    lines = [f"Anchor drift: {report.verdict}", report.reason]
    if report.matches:
        lines.append("")
        lines.append(f"  {'anchor':<22} {'band':<7} {'dy%':>8} {'dx%':>8}  read as")
        for m in sorted(report.matches, key=lambda m: m.dy_pct):
            lines.append(
                f"  {m.key:<22} {m.band:<7} {m.dy_pct:>+8.2f} {m.dx_pct:>+8.2f}  {m.found!r}"
            )
    if report.missing:
        lines.append("")
        lines.append(f"  not found: {', '.join(report.missing)}")
    if report.upper_dy_pct is not None:
        lines.append("")
        lines.append(
            f"  band means: UPPER {report.upper_dy_pct:+.2f}%  "
            f"BOTTOM {report.bottom_dy_pct:+.2f}%  (tolerance +/-{TOLERANCE_PCT}%)"
        )
    return "\n".join(lines)
