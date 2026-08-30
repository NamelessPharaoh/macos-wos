"""Red-dot and green-button detection, pinned to real VIP-screen captures.

Fixtures are live 1080x2460 frames taken 2026-08-30 (no player IDs on any VIP
screen). They cover the exact confusions that broke the first detector pass:
a pink discount starburst, an orange price CTA, and red price digits sitting
inside orange Buy & Use buttons.
"""
from pathlib import Path

import cv2
import pytest

from core.visual_cues import dot_near, find_green_buttons, find_red_dots

FIX = Path(__file__).parent / "fixtures"


def load(name):
    img = cv2.imread(str(FIX / name))
    assert img is not None, f"missing fixture {name}"
    return img


class TestRedDots:
    def test_locked_screen_has_one_dot_on_the_unlock_cta(self):
        dots = find_red_dots(load("vip_locked.png"))
        assert len(dots) == 1
        x1, y1, x2, y2 = dots[0]["box"]
        assert 700 < x1 < 800 and 2200 < y1 < 2320      # bottom-right of Unlock

    def test_active_screen_has_one_dot_on_the_claim_button(self):
        img = load("vip_active_claim.png")
        dots, greens = find_red_dots(img), find_green_buttons(img)
        assert len(dots) == 1 and len(greens) == 1
        assert dot_near(dots, greens[0]["box"])

    def test_pink_starburst_and_orange_cta_are_not_dots(self):
        # The '2321%' discount badge and the 'AED 17.99' button are the two
        # false positives a naive red hue window produces on this screen.
        for d in find_red_dots(load("vip_locked.png")):
            x1, y1, _, _ = d["box"]
            assert not (850 < x1 < 1010 and 1850 < y1 < 2000), "starburst matched"
            assert not (700 < x1 < 1000 and 2150 < y1 < 2230), "price CTA matched"

    def test_red_price_digits_inside_buy_buttons_are_not_dots(self):
        # 'Buy & Use 1,000/3,000/10,000' renders red glyphs on orange.
        assert find_red_dots(load("vip_obtain_more.png")) == []
        assert find_red_dots(load("vip_obtain_more_used.png")) == []

    def test_empty_input_is_safe(self):
        import numpy as np
        assert find_red_dots(None) == []
        assert find_red_dots(np.zeros((0, 0, 3), dtype=np.uint8)) == []


class TestGreenButtons:
    def test_finds_the_free_use_button(self):
        greens = find_green_buttons(load("vip_obtain_more.png"))
        assert len(greens) == 1
        x1, y1, x2, y2 = greens[0]["box"]
        assert x2 - x1 > 200 and y2 - y1 > 60           # a real button, not a badge

    def test_no_green_once_the_free_item_is_consumed(self):
        # Only gem-priced Buy & Use rows remain: nothing free to press.
        assert find_green_buttons(load("vip_obtain_more_used.png")) == []

    def test_no_green_on_the_locked_screen(self):
        assert find_green_buttons(load("vip_locked.png")) == []

    def test_new_badges_and_benefit_text_are_not_buttons(self):
        # The active screen carries green 'NEW' chips and '+2.0%' text; only
        # the Claim pill may qualify.
        greens = find_green_buttons(load("vip_active_claim.png"))
        assert len(greens) == 1

    def test_empty_input_is_safe(self):
        assert find_green_buttons(None) == []


class TestDotNear:
    def test_matches_badge_hanging_off_a_corner(self):
        dots = [{"box": [976, 1723, 998, 1745]}]
        assert dot_near(dots, [742, 1729, 1003, 1826])

    def test_rejects_distant_dot(self):
        dots = [{"box": [100, 100, 120, 120]}]
        assert not dot_near(dots, [742, 1729, 1003, 1826])

    def test_no_dots_is_false(self):
        assert not dot_near([], [0, 0, 10, 10])
