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
    """Drive claim_shop_freebies with canned screens; record every input."""
    import usecases.free_claims as fc
    taps, swipes, text_taps = [], [], []
    seq = list(screens)

    monkeypatch.setattr(fc, "recalibrate", lambda *a, **k: None)
    monkeypatch.setattr(fc, "tap_on_text",
                        lambda name, *a, **k: text_taps.append(name) or True)
    monkeypatch.setattr(fc, "tap_screen", lambda pt, *a, **k: taps.append(pt))
    monkeypatch.setattr(fc, "swipe_screen",
                        lambda a, b, **k: swipes.append((a, b)))
    monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(fc, "req_text",
                        lambda *a, **k: seq.pop(0) if seq else [])
    return fc, taps, swipes, text_taps


def _tile_taps(fc, taps):
    """Only the taps aimed at rewards: drop the cart and the tab slots."""
    return [pt for pt in taps
            if pt != fc.SHOP_ENTRY and pt[1] != fc.SHOP_TAB_ROW_Y_PCT]


def test_claim_shop_freebies_runs_end_to_end(monkeypatch):
    """Catches import and wiring errors the isolated guard tests cannot."""
    fc, taps, _swipes, _texts = _shop_stub(monkeypatch, [])
    assert fc.claim_shop_freebies() is True
    assert _tile_taps(fc, taps) == [], "no Free label means nothing is tapped"


def test_the_shop_is_entered_by_the_cart_not_the_bottom_nav(monkeypatch):
    """Home.Shop is the BOTTOM NAV button, and it opens the VIP/Gem shop --
    every tile gem-priced or VIP-locked, nothing free anywhere on it. Trying
    it first with SHOP_ENTRY as the fallback meant the routine reached the
    right screen only when OCR happened to MISS the bottom-nav text: 200 gems
    on 2026-09-03 (missed), nothing on 2026-09-04 (hit), no error either time.
    """
    fc, taps, _swipes, text_taps = _shop_stub(monkeypatch, [])
    fc.claim_shop_freebies()
    assert taps and taps[0] == fc.SHOP_ENTRY, "the cart is the only entry"
    assert "Home.Shop" not in text_taps, "the bottom-nav Shop is a trap"


def test_every_tab_is_visited_not_just_the_one_the_shop_opens_on(monkeypatch):
    """The shop opens on Dawn Market; the daily free chest is on Daily Deals.
    Without traversal the routine reads one tab and reports success."""
    fc, taps, swipes, _texts = _shop_stub(monkeypatch, [])
    fc.claim_shop_freebies()
    slots = [pt for pt in taps if pt[1] == fc.SHOP_TAB_ROW_Y_PCT]
    assert len(slots) == len(fc.SHOP_TAB_SLOTS_PCT) * fc.SHOP_TAB_PAGES
    assert len(swipes) == fc.SHOP_TAB_PAGES - 1, "one swipe between pages"


def test_the_upward_offset_is_probed_before_the_downward_one(monkeypatch):
    free = _text_at("Free", 27.0, 43.0)
    fc, taps, _s, _t = _shop_stub(
        monkeypatch, [[free], [["Claimed", [0, 0, 1, 1]]]])
    fc.claim_shop_freebies()
    tile = _tile_taps(fc, taps)
    assert tile, "a clean Free tile must be tapped"
    tx, ty = tile[0]
    assert abs(tx - 27.0) < 1.0
    assert ty < 43.0, "the first probe sits above the label"


def test_a_free_label_below_its_tile_is_still_reached(monkeypatch):
    """Dawn Fund's "Free" is a COLUMN HEADER with the tile below it, so the
    downward candidate must survive -- it is only ever second, never dropped.
    """
    free = _text_at("Free", 31.3, 43.7)
    fc, taps, _s, _t = _shop_stub(
        monkeypatch, [[free], [free], [["Claimed", [0, 0, 1, 1]]]])
    fc.claim_shop_freebies()
    tile = _tile_taps(fc, taps)
    assert len(tile) == 2, "up first, then down"
    assert tile[0][1] < 43.7 < tile[1][1]


def test_a_daily_deals_layout_never_taps_the_purchase_banner(monkeypatch):
    """The bug this ordering exists for, measured live 2026-09-04.

    The Free chest's caption sits at y=32.1% with the chest ABOVE it, while
    "Purchase All Discount Packs / AED 17.99" spans y 33.5-40.7%. The old
    +7.8% offset landed at 39.9% -- inside that banner -- and _price_free_zone
    did NOT refuse it, because the price TEXT is about 70% of the screen away
    horizontally. Probing up first claims the chest and ends the target.
    """
    free = _text_at("Free", 11.6, 32.1)
    price = _text_at("AED 17.99", 82.0, 37.4)
    fc, taps, _s, _t = _shop_stub(
        monkeypatch, [[free, price], [["Claimed", [0, 0, 1, 1]]]])
    fc.claim_shop_freebies()
    tile = _tile_taps(fc, taps)
    assert len(tile) == 1, "the chest is claimed on the first probe"
    assert all(ty < 33.5 for _tx, ty in tile), \
        "no tap may land in the purchase banner"


def test_it_refuses_a_free_label_sitting_next_to_a_price(monkeypatch):
    """The failure mode the text guard exists for: both candidates blocked."""
    free = _text_at("Free", 27.0, 43.0)
    price = _text_at("AED 74.99", 27.0, 45.0)
    fc, taps, _s, _t = _shop_stub(monkeypatch, [[free, price], []])
    fc.claim_shop_freebies()
    assert _tile_taps(fc, taps) == [], \
        "a price within the exclusion radius must block every candidate"


def test_claimable_counts_as_a_free_reward(monkeypatch):
    """Dawn Market's chest says "Claimable", not "Free", and it is free."""
    claimable = _text_at("Claimable", 86.7, 22.8)
    fc, taps, _s, _t = _shop_stub(
        monkeypatch, [[claimable], [["Claimed", [0, 0, 1, 1]]]])
    fc.claim_shop_freebies()
    assert _tile_taps(fc, taps), "a Claimable chest must be tapped"


def test_a_claimable_label_is_not_itself_proof_of_a_claim(monkeypatch, capsys):
    """"Claimable" contains "claim". Matching bare "claim" on the read-back --
    which is what the old success test did -- would count an untouched tile as
    claimed the moment "Claimable" became a target label."""
    claimable = _text_at("Claimable", 86.7, 22.8)
    fc, _taps, _s, _t = _shop_stub(monkeypatch, [[claimable]] * 60)
    fc.claim_shop_freebies()
    assert "0 reward(s) claimed" in capsys.readouterr().out


# --- sub-tab descent budget ------------------------------------------------
# A fixed budget of 4 left six free hero recruits unclaimed every run: the
# Heroes screen carries five dots and "Recruit Hero" is last in the list.


def _descent_stub(monkeypatch, dot_count, green_after=0):
    import usecases.free_claims as fc
    visited = []

    def dots(feature, *a, **k):
        if feature != "red_dot":
            return []
        return [{"box": [i * 100, 500, i * 100 + 30, 530], "kind": "dot"}
                for i in range(dot_count)]

    monkeypatch.setattr(fc, "req_detect", dots)
    # A drawn screen carrying no claimable label. Without this the settle gate
    # polls its full timeout on every dot and the suite crawls.
    monkeypatch.setattr(fc, "req_text", lambda *a, **k: [["x", [0, 0, 1, 1]]])
    monkeypatch.setattr(fc, "tap_screen", lambda pt, *a, **k: visited.append(pt))
    monkeypatch.setattr(fc, "tap_on_green_button", lambda *a, **k: False)
    monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: False)
    monkeypatch.setattr(fc, "tap_on_template", lambda *a, **k: True)
    monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
    return fc, visited


def test_every_dot_on_a_five_dot_screen_gets_visited(monkeypatch):
    """The regression. Heroes has five dots; the free recruits are behind the
    last one, so a budget of four never reached them."""
    fc, visited = _descent_stub(monkeypatch, dot_count=5)
    fc._descend_into_subtabs("Heroes")
    assert len(visited) >= 5, f"only visited {len(visited)} of 5 dots"


def test_the_budget_is_hard_capped(monkeypatch):
    """A screen that keeps producing dots must not hold the sweep forever."""
    fc, visited = _descent_stub(monkeypatch, dot_count=50)
    fc._descend_into_subtabs("Runaway")
    assert len(visited) <= fc.SUBTAB_HARD_CAP


def test_each_dot_is_visited_at_most_once(monkeypatch):
    fc, visited = _descent_stub(monkeypatch, dot_count=3)
    fc._descend_into_subtabs("Events")
    assert len(visited) == len(set(visited)), "a dot was revisited"


def test_an_explicit_budget_still_wins(monkeypatch):
    fc, visited = _descent_stub(monkeypatch, dot_count=9)
    fc._descend_into_subtabs("Bounded", max_subtabs=2)
    assert len(visited) == 2


# --- free rewards that are not buttons -------------------------------------


def test_a_free_tile_is_claimed_when_the_screen_has_no_green_button(monkeypatch):
    """Deals' login track hands out rewards as icon tiles under a "Free"
    column header, with no green button anywhere on the screen.

    The sweep used to visit it, log "No free (green) button found" and move on
    -- correct by its own lights, there genuinely was no green button. An SR
    hero was sitting there unclaimed on 2026-09-04. Column header measured
    live at (19.4%, 34.1%), the Day 1 tile 9.0% below it.
    """
    import usecases.free_claims as fc
    free = _text_at("Free", 19.4, 34.1)
    # Three reads: the spend-screen check, the tile scan, then the read-back
    # that confirms the payout.
    screens = [[free], [free], [["Claimed", [0, 0, 1, 1]]], []]
    monkeypatch.setattr(fc, "req_text",
                        lambda *a, **k: screens.pop(0) if screens else [])
    monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(fc, "req_detect", lambda *a, **k: [])
    monkeypatch.setattr(fc, "tap_on_green_button", lambda **k: False)
    monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: True)
    taps = []
    monkeypatch.setattr(fc, "tap_screen", lambda pt, *a, **k: taps.append(pt))

    assert fc._claim_here("Deals", descend=False) == 1
    assert taps, "the tile must actually be tapped"
    assert abs(taps[0][0] - 19.4) < 1.0, "tapped the wrong column"


def test_a_screen_with_neither_button_nor_free_tile_claims_nothing(monkeypatch):
    """The guard has to stay a guard: no green button and no "Free" label
    means nothing is pressed, however many other words are on screen."""
    import usecases.free_claims as fc
    monkeypatch.setattr(fc, "req_text", lambda *a, **k: [
        _text_at("Hall of Chiefs", 30.0, 20.0),
        _text_at("AED 17.99", 60.0, 40.0),
        _text_at("Claim", 80.0, 50.0),      # a GREY, disabled Claim button
    ])
    monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(fc, "req_detect", lambda *a, **k: [])
    monkeypatch.setattr(fc, "tap_on_green_button", lambda **k: False)
    monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: True)
    taps = []
    monkeypatch.setattr(fc, "tap_screen", lambda pt, *a, **k: taps.append(pt))

    assert fc._claim_here("Events", descend=False) == 0
    assert taps == [], "nothing free on screen, so nothing may be tapped"
