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


# --- the shop price guard --------------------------------------------------
# claim_shop_freebies is the only routine that navigates a screen carrying
# real-money buttons, and req_detect finds ZERO green buttons there — so the
# colour money guard cannot vouch for anything on it. This text check IS the
# money guard for the shop, which makes it the most safety-critical function in
# the module.

from core.coord_utils import BASE_HEIGHT as _H, BASE_WIDTH as _W  # noqa: E402
from usecases.free_claims import (  # noqa: E402
    PRICE_EXCLUSION_PCT,
    PRICE_MARKER,
    _price_free_zone,
)


def _text_at(text, x_pct, y_pct, w=60, h=20):
    """A text box CENTRED on the given screen percentage.

    The guard measures from the box centre, so a helper that anchored the left
    edge would silently shift every case by half the box width.
    """
    cx, cy = x_pct / 100 * _W, y_pct / 100 * _H
    return [text, [int(cx - w / 2), int(cy - h / 2),
                   int(cx + w / 2), int(cy + h / 2)]]


@pytest.mark.parametrize("price", [
    "AED 74.99", "AED 17.99", "$4.99", "€9,99", "£19.99", "¥1200",
    "USD 1.00", "Purchase All Discount Packs", "Buy & Use", "21.97",
])
def test_price_shapes_are_recognised(price):
    assert PRICE_MARKER.search(price), f"{price!r} must read as a price"


@pytest.mark.parametrize("safe", ["Free", "Claim", "Daily Limit: 1", "Lv. 7",
                                  "100", "Dawn Fund", "1,543"])
def test_non_prices_are_not_flagged(safe):
    assert not PRICE_MARKER.search(safe), f"{safe!r} must not read as a price"


def test_a_tap_next_to_a_price_is_refused():
    """The exact failure this exists to prevent: a drifted tile position landing
    on 'AED 74.99' instead of the free reward."""
    texts = [_text_at("AED 74.99", 32.0, 55.0)]
    assert _price_free_zone((32.0, 51.0), texts) is False


def test_a_tap_far_from_any_price_is_allowed():
    texts = [_text_at("AED 74.99", 66.0, 36.6)]   # measured live, other column
    assert _price_free_zone((32.0, 51.0), texts) is True


def test_the_exclusion_radius_is_enforced_on_both_axes():
    inside = PRICE_EXCLUSION_PCT - 1
    outside = PRICE_EXCLUSION_PCT + 1
    assert _price_free_zone((50.0, 50.0), [_text_at("$1.99", 50.0, 50 + inside)]) is False
    assert _price_free_zone((50.0, 50.0), [_text_at("$1.99", 50 + inside, 50.0)]) is False
    assert _price_free_zone((50.0, 50.0), [_text_at("$1.99", 50.0, 50 + outside)]) is True


def test_an_empty_or_unreadable_screen_is_treated_as_safe_to_skip():
    """No text means no evidence. The caller only taps tiles it positively
    identified as 'Free', so an empty read yields no targets at all."""
    assert _price_free_zone((32.0, 51.0), []) is True
    assert _price_free_zone((32.0, 51.0), None) is True


# --- claim_shop_freebies actually runs -------------------------------------
# The guard tests above call _price_free_zone directly, so they never executed
# claim_shop_freebies' own body — and a missing `req_text` import survived all
# of them, surfacing only on a live run. Same class as the NameError in 210bbf5.


def _shop_stub(monkeypatch, screens):
    """Drive claim_shop_freebies with canned screens; record every tap."""
    import usecases.free_claims as fc
    taps = []
    seq = list(screens)

    monkeypatch.setattr(fc, "recalibrate", lambda *a, **k: None)
    monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: True)
    monkeypatch.setattr(fc, "tap_screen", lambda pt, *a, **k: taps.append(pt))
    monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(fc, "req_text",
                        lambda *a, **k: seq.pop(0) if seq else [])
    return fc, taps


def test_claim_shop_freebies_runs_end_to_end(monkeypatch):
    """Catches import and wiring errors the isolated guard tests cannot."""
    fc, taps = _shop_stub(monkeypatch, [[], []])
    assert fc.claim_shop_freebies() is True
    assert taps == [], "no 'Free' label means nothing is tapped"


def test_it_taps_below_a_free_label_when_no_price_is_near(monkeypatch):
    free = _text_at("Free", 27.0, 43.0)
    fc, taps = _shop_stub(monkeypatch, [[free], [["Claimed", [0, 0, 1, 1]]], []])
    fc.claim_shop_freebies()
    assert taps, "a clean Free tile must be tapped"
    tx, ty = taps[0]
    assert abs(tx - 27.0) < 1.0
    assert ty > 43.0, "the claimable tile sits below its label"


def test_it_refuses_a_free_label_sitting_next_to_a_price(monkeypatch):
    """The failure mode this whole guard exists for."""
    free = _text_at("Free", 27.0, 43.0)
    price = _text_at("AED 74.99", 27.0, 48.0)
    fc, taps = _shop_stub(monkeypatch, [[free, price], []])
    fc.claim_shop_freebies()
    assert taps == [], "a price within the exclusion radius must block the tap"
