"""Hero level-up: what it reads, what it refuses, and when it stops.

The spend guard is the point of this file. This is the one routine that presses
a button on a screen where things cost something, so every refusal path gets a
test of its own -- an unreadable price is as good as a price.
"""
import pytest

import usecases.heroes as heroes
from core.core import text_area


def _read(text, box=None):
    return {"text": text, "score": 0.99, "box": box or [0, 0, 10, 10]}


class TestRoiNames:
    def test_every_roi_the_module_names_is_recorded(self):
        names = list(heroes.ROSTER_LEVEL_ROIS) + [
            heroes.DETAIL_LEVEL_ROI,
            heroes.DETAIL_COST_ROI,
            heroes.DETAIL_UPGRADE_ROI,
        ]
        missing = [n for n in names if n not in text_area]
        assert not missing, f"unrecorded ROI names: {missing}"


class TestParseLevel:
    @pytest.mark.parametrize("text,expected", [
        ("Lv. 6", 6),
        ("Lv.6", 6),
        ("Lv 80", 80),
        ("7", 7),
        ("  12 ", 12),
    ])
    def test_reads_a_level(self, text, expected):
        assert heroes._parse_level(text) == expected

    @pytest.mark.parametrize("text", ["15/10", "0/20", "2/10"])
    def test_shard_counter_is_not_a_level(self, text):
        """The Recruit card sits in a roster slot and shows shards where a level

        would be. Reading '15/10' as level 15 makes it the highest hero on the
        roster and sends the routine into the recruit screen.
        """
        assert heroes._parse_level(text) is None

    @pytest.mark.parametrize("text", [None, "", "Recruit", "Lv."])
    def test_no_level_reads_none(self, text):
        assert heroes._parse_level(text) is None


class TestParseCost:
    @pytest.mark.parametrize("text", ["216,922/2,200", "216.922/2.200", "216922/2200"])
    def test_have_over_need(self, text):
        assert heroes._parse_cost(text) == (216922, 2200)

    @pytest.mark.parametrize("text", [None, "", "Upgrade", "Max Level", "2,200"])
    def test_anything_else_is_a_refusal(self, text):
        assert heroes._parse_cost(text) is None


class TestPickLeadHero:
    def test_picks_the_highest_level(self):
        reads = {
            "Home.Heroes.FirstHeroLevel": [_read("15/10")],
            "Home.Heroes.SecondHeroLevel": [_read("Lv. 6", [300, 590, 400, 626])],
            "Home.Heroes.ThirdHeroLevel": [_read("Lv. 5")],
            "Home.Heroes.FourthHeroLevel": [_read("Lv. 1")],
        }
        name, level, box = heroes._pick_lead_hero(reads)
        assert (name, level) == ("Home.Heroes.SecondHeroLevel", 6)
        assert box == [300, 590, 400, 626]

    def test_recruit_slot_never_wins(self):
        reads = {
            "Home.Heroes.FirstHeroLevel": [_read("15/10")],
            "Home.Heroes.SecondHeroLevel": [_read("Lv. 1")],
        }
        assert heroes._pick_lead_hero(reads)[1] == 1

    @pytest.mark.parametrize("reads", [None, {}, {"Home.Heroes.FirstHeroLevel": []}])
    def test_no_levels_is_none(self, reads):
        assert heroes._pick_lead_hero(reads) is None


class TestSpendRefusal:
    def test_affordable_exp_is_allowed(self):
        reads = {
            heroes.DETAIL_COST_ROI: [_read("216,922/2,200")],
            heroes.DETAIL_UPGRADE_ROI: [_read("Upgrade")],
        }
        assert heroes._spend_refusal(reads) is None

    def test_insufficient_exp_refuses(self):
        """Below the cost the game offers to top EXP up with gems. Stopping here

        means the routine never sees that offer at all.
        """
        reads = {
            heroes.DETAIL_COST_ROI: [_read("1,200/2,200")],
            heroes.DETAIL_UPGRADE_ROI: [_read("Upgrade")],
        }
        assert "not enough hero EXP" in heroes._spend_refusal(reads)

    @pytest.mark.parametrize("text", ["AED 17.99", "$4.99", "Buy 2,200", "Purchase"])
    def test_price_marker_refuses(self, text):
        reads = {
            heroes.DETAIL_COST_ROI: [_read("216,922/2,200"), _read(text)],
            heroes.DETAIL_UPGRADE_ROI: [_read("Upgrade")],
        }
        assert "real-money marker" in heroes._spend_refusal(reads)

    def test_truncated_resource_count_is_not_a_price(self):
        """Regression, live 2026-09-04: OCR read "205,522/4,200" as "205,22/

        4,200" and free_claims.PRICE_MARKER's decimal clause matched the
        truncated count, refusing a free upgrade four levels into the run.
        Under-reading `have` is the safe direction; calling it money is not.
        """
        reads = {
            heroes.DETAIL_COST_ROI: [_read("205,22/4,200")],
            heroes.DETAIL_UPGRADE_ROI: [_read("Upgrade")],
        }
        assert heroes._spend_refusal(reads) is None

    def test_missing_cost_refuses(self):
        reads = {heroes.DETAIL_COST_ROI: [], heroes.DETAIL_UPGRADE_ROI: [_read("Upgrade")]}
        assert "no cost readout" in heroes._spend_refusal(reads)

    def test_unparseable_cost_refuses(self):
        reads = {
            heroes.DETAIL_COST_ROI: [_read("Max Level")],
            heroes.DETAIL_UPGRADE_ROI: [_read("Upgrade")],
        }
        assert "did not parse" in heroes._spend_refusal(reads)


class TestReadDetailRetry:
    def test_retries_once_when_the_cost_does_not_parse(self, monkeypatch):
        """One corrupted crop must not end a run.

        The EXP bottle icon sat inside the cost ROI until 2026-09-04 and ate the
        slash, so the same screen read as a price on one call and as an EXP
        receipt on the next.
        """
        calls = []

        def fake_read(names, **kwargs):
            calls.append(names)
            if len(calls) == 1:
                return {heroes.DETAIL_COST_ROI: [_read("0 205,22214,200")]}
            return {heroes.DETAIL_COST_ROI: [_read("205,222/4,200")]}

        monkeypatch.setattr(heroes, "req_text_named", fake_read)
        monkeypatch.setattr(heroes.time, "sleep", lambda _s: None)
        reads = heroes._read_detail()
        assert len(calls) == 2
        assert heroes._spend_refusal(reads) is None

    def test_gives_up_after_the_retry_budget(self, monkeypatch):
        calls = []
        monkeypatch.setattr(heroes, "req_text_named", lambda names, **k: (
            calls.append(names), {heroes.DETAIL_COST_ROI: [_read("Max Level")]})[1])
        monkeypatch.setattr(heroes.time, "sleep", lambda _s: None)
        heroes._read_detail()
        assert len(calls) == heroes.COST_READ_RETRIES + 1


class _Screen:
    """A fake hero detail screen: each press raises the level until it stops."""

    def __init__(self, start=7, cap=None, have=216922, need=2200):
        self.level = start
        self.cap = cap
        self.have = have
        self.need = need
        self.taps = []

    def reads(self):
        return {
            heroes.DETAIL_LEVEL_ROI: [_read(str(self.level))],
            heroes.DETAIL_COST_ROI: [_read(f"{self.have:,}/{self.need:,}")],
            heroes.DETAIL_UPGRADE_ROI: [_read("Upgrade")],
        }

    def tap(self, *args):
        self.taps.append(args)
        if self.cap is None or self.level < self.cap:
            self.level += 1


@pytest.fixture
def wired(monkeypatch):
    """Wire upgrade_hero to a fake screen with no adb and no OCR."""
    def _wire(screen):
        monkeypatch.setattr(heroes, "_open_lead_hero", lambda: screen.reads())
        monkeypatch.setattr(heroes, "_read_detail", screen.reads)
        monkeypatch.setattr(heroes, "tap_screen", screen.tap)
        monkeypatch.setattr(heroes, "tap_on_template", lambda *a, **k: True)
        monkeypatch.setattr(heroes.time, "sleep", lambda _s: None)
        return screen
    return _wire


class TestUpgradeLoop:
    def test_raises_exactly_the_levels_asked_for(self, wired):
        screen = wired(_Screen(start=7))
        result = heroes.upgrade_hero(levels=5)
        assert (result["start"], result["end"], result["gained"]) == (7, 12, 5)
        assert result["stopped"] is None
        assert len(screen.taps) == 5

    def test_stops_at_the_level_cap(self, wired):
        """Affordable and still refusing to move is what the Furnace cap looks

        like -- nothing on screen announces it.
        """
        screen = wired(_Screen(start=7, cap=9))
        result = heroes.upgrade_hero(levels=5)
        assert result["end"] == 9
        assert result["gained"] == 2
        assert "cap reached" in result["stopped"]
        # One wasted press to discover the cap, never a second.
        assert len(screen.taps) == 3

    def test_never_presses_when_exp_is_short(self, wired):
        screen = wired(_Screen(start=7, have=100, need=2200))
        result = heroes.upgrade_hero(levels=5)
        assert screen.taps == []
        assert result["gained"] == 0
        assert "not enough hero EXP" in result["stopped"]

    def test_gain_is_measured_on_screen_not_counted_from_presses(self, wired):
        screen = wired(_Screen(start=7, cap=7))
        result = heroes.upgrade_hero(levels=5)
        assert result["gained"] == 0

    def test_unopenable_hero_reports_instead_of_pressing(self, monkeypatch):
        monkeypatch.setattr(heroes, "_open_lead_hero", lambda: None)
        pressed = []
        monkeypatch.setattr(heroes, "tap_screen", lambda *a: pressed.append(a))
        result = heroes.upgrade_hero(levels=5)
        assert pressed == []
        assert result["stopped"] == "could not open the lead hero"
