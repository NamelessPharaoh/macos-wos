"""The home-screen entry points must actually contain their dots.

Each ENTRY_POINTS row is a hardcoded screen position, so a banner that moves
silently stops being swept: no dot falls in the box, the sweep skips it without
comment, and the reward sits there forever. That is exactly what happened to
the daily sign-in — measured live 2026-09-03 with its dot at (96.2%, 29.8%)
while the recorded box covered [72, 10, 84, 18], nowhere near it.
"""
import pytest

from usecases.free_claims import ENTRY_POINTS, _pct_box_to_pixels
from core.coord_utils import BASE_HEIGHT, BASE_WIDTH


def _dot_at(x_pct, y_pct, radius_px=18):
    """A detector-shaped dot centred on a screen percentage."""
    cx, cy = x_pct / 100 * BASE_WIDTH, y_pct / 100 * BASE_HEIGHT
    return {"box": [int(cx - radius_px), int(cy - radius_px),
                    int(cx + radius_px), int(cy + radius_px)], "kind": "dot"}


def _entry_for(dot):
    """Which entry point claims this dot, mirroring sweep_free_claims."""
    from core.visual_cues import dot_near
    for label, _centre, search_box in ENTRY_POINTS:
        if dot_near([dot], _pct_box_to_pixels(search_box), margin=0):
            return label
    return None


# Positions measured live on 2026-09-03 (Furnace 7 home screen).
MEASURED_DOTS = {
    "daily sign-in": (96.2, 29.8),
    "events":        (96.2, 12.2),
    "heroes":        (30.1, 94.4),
    "backpack":      (46.1, 94.4),
}


def test_the_daily_signin_dot_is_reachable():
    """The regression. This dot had a claimable Daily Sign-in Gift behind it and
    no entry point covered it, so the sweep walked past it every run."""
    assert _entry_for(_dot_at(*MEASURED_DOTS["daily sign-in"])) is not None, (
        "no entry point covers the daily sign-in dot — it can never be followed"
    )


@pytest.mark.parametrize("name", ["events", "heroes", "backpack"])
def test_the_other_measured_dots_stay_reachable(name):
    """These three were already covered; the fix must not move them out."""
    assert _entry_for(_dot_at(*MEASURED_DOTS[name])) is not None, name


def test_entry_point_boxes_do_not_overlap():
    """Overlapping boxes make one dot trigger two navigations, and the second
    runs against whatever screen the first left behind."""
    seen = []
    for label, _centre, box in ENTRY_POINTS:
        x1, y1, x2, y2 = box
        for other_label, ox1, oy1, ox2, oy2 in seen:
            overlap = not (x2 <= ox1 or ox2 <= x1 or y2 <= oy1 or oy2 <= y1)
            assert not overlap, f"{label} overlaps {other_label}"
        seen.append((label, x1, y1, x2, y2))


def test_every_tap_centre_sits_inside_its_own_search_box():
    """A centre outside its box means the sweep taps somewhere the dot is not."""
    for label, (cx, cy), (x1, y1, x2, y2) in ENTRY_POINTS:
        assert x1 <= cx <= x2 and y1 <= cy <= y2, (
            f"{label}: tap centre ({cx}, {cy}) is outside its box {[x1, y1, x2, y2]}"
        )
