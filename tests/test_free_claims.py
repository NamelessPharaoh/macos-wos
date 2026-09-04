"""Free-claim sweep: entry-point gating and the free-only guarantee."""
import pytest

import usecases.free_claims as fc


class TestEntryPoints:
    def test_every_entry_has_icon_and_search_box(self):
        for label, centre, box in fc.ENTRY_POINTS:
            assert label
            assert 0 < centre[0] < 100 and 0 < centre[1] < 100
            assert len(box) == 4 and box[0] < box[2] and box[1] < box[3]

    def test_search_box_contains_its_icon_centre(self):
        for label, (cx, cy), box in fc.ENTRY_POINTS:
            assert box[0] <= cx <= box[2], f"{label}: x centre outside search box"
            assert box[1] <= cy <= box[3], f"{label}: y centre outside search box"

    def test_pct_box_converts_to_base_resolution_pixels(self):
        assert fc._pct_box_to_pixels([0, 0, 100, 100]) == [0, 0, 1080, 2460]
        assert fc._pct_box_to_pixels([50, 50, 100, 100]) == [540, 1230, 1080, 2460]


class TestDotFiltering:
    def test_only_solid_dots_count_as_actions(self, monkeypatch):
        # Numbered badges are counts (33 alliance requests); they must not
        # trigger a screen visit on their own.
        monkeypatch.setattr(fc, "req_detect", lambda *a, **k: [
            {"box": [0, 0, 10, 10], "area": 100, "kind": "dot"},
            {"box": [20, 0, 60, 30], "area": 400, "kind": "badge"},
        ])
        assert [d["kind"] for d in fc._home_dots()] == ["dot"]

    def test_no_detections_is_empty(self, monkeypatch):
        monkeypatch.setattr(fc, "req_detect", lambda *a, **k: None)
        assert fc._home_dots() == []


class TestSweep:
    def test_no_dots_detected_still_visits_every_screen(self, monkeypatch):
        """This asserted the opposite until 2026-09-03, and that was the bug.

        Zero dots DETECTED is not zero dots PRESENT. A dot on a red icon merges
        with it and is rejected by req_detect -- measured live, the Trials blob
        is area 4179 against a 2600 cap and the Deals blob circularity 0.24
        against a 0.70 floor. So "no dots" is precisely what a home screen full
        of unclaimed rewards looks like to the detector, and returning early
        there meant claiming nothing while reporting success.
        """
        monkeypatch.setattr(fc, "recalibrate", lambda: None)
        # A drawn screen. Without this the settle gate polls for its
        # full timeout on every entry point and the suite crawls.
        monkeypatch.setattr(fc, "req_text", lambda *a, **k: [["x", [0, 0, 1, 1]]])
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        monkeypatch.setattr(fc, "req_detect", lambda *a, **k: [])
        taps = []
        monkeypatch.setattr(fc, "tap_screen", lambda c: taps.append(c))
        monkeypatch.setattr(fc, "tap_on_green_button", lambda **k: False)
        monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: False)
        monkeypatch.setattr(fc, "tap_on_template", lambda *a, **k: True)

        assert fc.sweep_free_claims() is True
        for label, centre, _box in fc.ENTRY_POINTS:
            assert centre in taps, f"{label} went unvisited on a dotless read"

    def test_every_entry_point_is_visited_regardless_of_dots(self, monkeypatch):
        """The dot USED to gate this, and that silently cost whole screens.

        Trials is a red shield and Deals a red gift box, so their dots merge
        with the icon: measured live, the Trials blob is area 4179 against a
        2600 cap and the Deals blob circularity 0.24 against a 0.70 floor.
        Both are invisible to req_detect, so both were never visited. Skipping
        the gate is safe because the CLAIM is guarded -- _claim_here presses
        green only -- so a screen with nothing free costs seconds, not risk.
        """
        first_box = fc._pct_box_to_pixels(fc.ENTRY_POINTS[0][2])
        dot = {"box": first_box, "area": 400, "kind": "dot"}
        monkeypatch.setattr(fc, "recalibrate", lambda: None)
        # A drawn screen. Without this the settle gate polls for its
        # full timeout on every entry point and the suite crawls.
        monkeypatch.setattr(fc, "req_text", lambda *a, **k: [["x", [0, 0, 1, 1]]])
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        monkeypatch.setattr(fc, "req_detect", lambda *a, **k: [dot])
        taps = []
        monkeypatch.setattr(fc, "tap_screen", lambda c: taps.append(c))
        monkeypatch.setattr(fc, "tap_on_green_button", lambda **k: False)
        monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: False)
        monkeypatch.setattr(fc, "tap_on_template", lambda *a, **k: True)
        fc.sweep_free_claims()

        centres = [centre for _l, centre, _b in fc.ENTRY_POINTS]
        for centre in centres:
            assert centre in taps, f"{centre} was never visited"

    def test_a_screen_with_nothing_free_is_still_safe_to_visit(self, monkeypatch):
        """Visiting costs navigation, never a press.

        With no dots at all the sweep still opens every entry point and still
        asks tap_on_green_button on each -- that ask IS the money guard, so the
        thing worth asserting is that it happens once per screen and that
        nothing is claimed when it always says no.
        """
        monkeypatch.setattr(fc, "recalibrate", lambda: None)
        # A drawn screen. Without this the settle gate polls for its
        # full timeout on every entry point and the suite crawls.
        monkeypatch.setattr(fc, "req_text", lambda *a, **k: [["x", [0, 0, 1, 1]]])
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        monkeypatch.setattr(fc, "req_detect", lambda *a, **k: [])
        asked = []
        monkeypatch.setattr(fc, "tap_screen", lambda c: None)
        monkeypatch.setattr(fc, "tap_on_green_button",
                            lambda **k: (asked.append(k), False)[1])
        monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: False)
        monkeypatch.setattr(fc, "tap_on_template", lambda *a, **k: True)

        assert fc.sweep_free_claims() is True
        assert len(asked) >= len(fc.ENTRY_POINTS), (
            f"green was checked {len(asked)} times for "
            f"{len(fc.ENTRY_POINTS)} entry points — a screen went unchecked"
        )


class TestSubtabDescent:
    """One level deep only, bounded, each dot visited once."""

    def _arm(self, mp, dots, green_results, drawn=True):
        mp.setattr(fc, "req_detect", lambda *a, **k: list(dots))
        # Sub-tabs go through the same settle gate as the entry points.
        mp.setattr(fc, "req_text",
                   lambda *a, **k: [["x", [0, 0, 1, 1]]] if drawn else [])
        mp.setattr(fc.time, "sleep", lambda *a, **k: None)
        mp.setattr(fc, "tap_screen", lambda c: None)
        mp.setattr(fc, "tap_on_text", lambda *a, **k: True)
        mp.setattr(fc, "tap_on_template", lambda *a, **k: True)
        it = iter(green_results)
        mp.setattr(fc, "tap_on_green_button", lambda **k: next(it, False))

    def test_descends_when_top_level_offers_nothing(self, monkeypatch):
        dots = [{"box": [10, 10, 30, 30], "area": 400, "kind": "dot"}]
        # top-level press fails, the sub-tab press succeeds
        self._arm(monkeypatch, dots, [False, True])
        assert fc._claim_here("Heroes") == 1

    def test_does_not_descend_when_top_level_already_paid_out(self, monkeypatch):
        dots = [{"box": [10, 10, 30, 30], "area": 400, "kind": "dot"}]
        self._arm(monkeypatch, dots, [True, False])
        # Claimed at the top level, so the descent must not run.
        assert fc._claim_here("Heroes") == 1

    def test_each_dot_visited_once_even_if_it_never_clears(self, monkeypatch):
        # A dot that stays put must not be followed forever.
        dots = [{"box": [10, 10, 30, 30], "area": 400, "kind": "dot"}]
        self._arm(monkeypatch, dots, [False] * 20)
        taps = []
        monkeypatch.setattr(fc, "tap_screen", lambda c: taps.append(c))
        assert fc._claim_here("Heroes") == 0
        assert len(taps) == 1          # one dot, visited exactly once

    def test_descent_is_bounded_by_max_subtabs(self, monkeypatch):
        dots = [{"box": [i * 100, 10, i * 100 + 20, 30], "area": 400, "kind": "dot"}
                for i in range(10)]
        self._arm(monkeypatch, dots, [False] * 40)
        taps = []
        monkeypatch.setattr(fc, "tap_screen", lambda c: taps.append(c))
        fc._descend_into_subtabs("Backpack", max_subtabs=3)
        assert len(taps) == 3

    def test_badges_are_not_descended_into(self, monkeypatch):
        # Counts are not buttons.
        dots = [{"box": [10, 10, 50, 30], "area": 400, "kind": "badge"}]
        self._arm(monkeypatch, dots, [False] * 5)
        taps = []
        monkeypatch.setattr(fc, "tap_screen", lambda c: taps.append(c))
        assert fc._descend_into_subtabs("Events") == 0
        assert taps == []

    def test_claim_loop_stops_when_nothing_green_remains(self, monkeypatch):
        presses = iter([True, True, False])
        monkeypatch.setattr(fc, "tap_on_green_button", lambda **k: next(presses))
        monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: True)
        assert fc._claim_here("test") == 2

    def test_claim_loop_is_bounded(self, monkeypatch):
        # A screen whose green button never clears must not spin forever.
        monkeypatch.setattr(fc, "tap_on_green_button", lambda **k: True)
        monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: True)
        assert fc._claim_here("stuck", max_rounds=3) == 3


    def test_it_reaches_a_reward_two_levels_down(self, monkeypatch):
        """The live case: Events -> Lucky Wheel -> "Lucky Chip Supply", behind
        which sits a "Free Lucky Chip Pack" with an ordinary GREEN Free button.
        tap_on_green_button would take it happily; a one-level descent simply
        never arrived, so the pack went unclaimed every single run."""
        level1 = [{"box": [10, 10, 30, 30], "area": 400, "kind": "dot"}]
        level2 = [{"box": [500, 700, 520, 720], "area": 400, "kind": "dot"}]
        screens = [level1, level1, level2, level2]
        monkeypatch.setattr(
            fc, "req_detect",
            lambda *a, **k: list(screens.pop(0)) if screens else [])
        monkeypatch.setattr(fc, "req_text",
                            lambda *a, **k: [["x", [0, 0, 1, 1]]])
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        monkeypatch.setattr(fc, "tap_screen", lambda c: None)
        monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: True)
        monkeypatch.setattr(fc, "tap_on_template", lambda *a, **k: True)
        greens = iter([False, True])   # nothing at level 1, the pack at level 2
        monkeypatch.setattr(fc, "tap_on_green_button",
                            lambda **k: next(greens, False))

        assert fc._descend_into_subtabs("Events") == 1

    def test_it_does_not_descend_past_the_depth_limit(self, monkeypatch):
        """Every extra level multiplies navigation time by the dot count, so
        the bound is a deliberate choice rather than an accident of the data."""
        counter = [0]

        def endless_fresh_dots(*a, **k):
            counter[0] += 1
            i = counter[0]
            return [{"box": [i * 40, 10, i * 40 + 20, 30], "area": 400,
                     "kind": "dot"}]

        monkeypatch.setattr(fc, "req_detect", endless_fresh_dots)
        monkeypatch.setattr(fc, "req_text",
                            lambda *a, **k: [["x", [0, 0, 1, 1]]])
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        monkeypatch.setattr(fc, "tap_screen", lambda c: None)
        monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: True)
        monkeypatch.setattr(fc, "tap_on_template", lambda *a, **k: True)
        monkeypatch.setattr(fc, "tap_on_green_button", lambda **k: False)

        depths = []
        real = fc._descend_into_subtabs

        def spy(label, max_subtabs=None, depth=1, seen=None, budget=None):
            depths.append(depth)
            return real(label, max_subtabs, depth, seen, budget)

        monkeypatch.setattr(fc, "_descend_into_subtabs", spy)
        spy("Events", max_subtabs=6)

        assert depths, "the descent never ran"
        assert max(depths) <= fc.SUBTAB_MAX_DEPTH, \
            f"descended to level {max(depths)}, limit is {fc.SUBTAB_MAX_DEPTH}"


class TestScreenSettle:
    """A blank frame is not an empty screen.

    Trials cost 1,500 gems to this distinction on 2026-09-04. The sweep read
    zero green buttons, zero red dots AND zero OCR lines off a screen carrying
    four full green Claim buttons ("Log in for 2/3/4/5 day(s)"), then reported
    a clean sweep. Deals, read moments later, returned red_dot: 1 hit -- the
    detectors were fine, the frame simply had not drawn.
    """

    def test_a_drawn_screen_is_accepted_immediately(self, monkeypatch):
        monkeypatch.setattr(fc, "req_text",
                            lambda *a, **k: [["Claim", [0, 0, 1, 1]]])
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        assert fc._wait_for_screen("Trials") is True

    def test_a_screen_that_draws_late_is_still_read(self, monkeypatch):
        """Giving up on frame one is exactly the bug: the transition is the
        first thing you see, never the last."""
        frames = [[], [], [["Claim", [0, 0, 1, 1]]]]
        monkeypatch.setattr(fc, "req_text", lambda *a, **k: frames.pop(0))
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        assert fc._wait_for_screen("Trials") is True
        assert frames == [], "it must keep looking past the blank frames"

    def test_a_screen_that_never_draws_says_so(self, monkeypatch, capsys):
        monkeypatch.setattr(fc, "req_text", lambda *a, **k: [])
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        assert fc._wait_for_screen("Trials", timeout=0) is False
        out = capsys.readouterr().out
        assert "Trials" in out and "NOT read" in out, \
            "an unread screen must be reported, never silently counted clean"

    def test_a_blank_sub_tab_is_never_pressed(self, monkeypatch):
        """Pressing green on a frame that has not drawn is a blind tap."""
        monkeypatch.setattr(fc, "req_detect", lambda *a, **k: [
            {"box": [10, 10, 30, 30], "area": 400, "kind": "dot"}])
        monkeypatch.setattr(fc, "req_text", lambda *a, **k: [])
        monkeypatch.setattr(fc, "SCREEN_SETTLE_TIMEOUT_S", 0)
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        monkeypatch.setattr(fc, "tap_screen", lambda c: None)
        monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: True)
        monkeypatch.setattr(fc, "tap_on_template", lambda *a, **k: True)
        pressed = []
        monkeypatch.setattr(fc, "tap_on_green_button",
                            lambda **k: pressed.append(1) or True)
        fc._descend_into_subtabs("Heroes", max_subtabs=1)
        assert pressed == [], "a blank sub-tab must not be pressed blind"

    def test_the_sweep_names_every_screen_it_could_not_read(
            self, monkeypatch, capsys):
        """The whole point: turn a silent miss into a loud one."""
        monkeypatch.setattr(fc, "recalibrate", lambda: None)
        monkeypatch.setattr(fc, "req_detect", lambda *a, **k: [])
        monkeypatch.setattr(fc, "req_text", lambda *a, **k: [])
        monkeypatch.setattr(fc, "SCREEN_SETTLE_TIMEOUT_S", 0)
        monkeypatch.setattr(fc.time, "sleep", lambda *a, **k: None)
        monkeypatch.setattr(fc, "tap_screen", lambda c: None)
        monkeypatch.setattr(fc, "tap_on_text", lambda *a, **k: False)
        monkeypatch.setattr(fc, "tap_on_template", lambda *a, **k: True)
        pressed = []
        monkeypatch.setattr(fc, "tap_on_green_button",
                            lambda **k: pressed.append(1) or False)

        assert fc.sweep_free_claims() is True
        out = capsys.readouterr().out
        assert "never drew" in out
        for label, _centre, _box in fc.ENTRY_POINTS:
            assert label in out, f"{label} went unreported"
        assert pressed == [], "an undrawn screen must not be pressed blind"


class TestMultipleRewardsOnOneScreen:
    """One screen, several free buttons. Both live cases were under-claimed."""

    def _arm(self, mp, greens):
        mp.setattr(fc, "req_text", lambda *a, **k: [["x", [0, 0, 1, 1]]])
        mp.setattr(fc.time, "sleep", lambda *a, **k: None)
        mp.setattr(fc, "tap_on_text", lambda *a, **k: True)
        mp.setattr(fc, "tap_on_template", lambda *a, **k: True)
        mp.setattr(fc, "tap_screen", lambda *a, **k: None)
        it = iter(greens)
        mp.setattr(fc, "tap_on_green_button", lambda **k: next(it, False))

    def test_a_screen_with_four_green_buttons_gives_up_four(self, monkeypatch):
        """Trials: "Log in for 2/3/4/5 day(s)", all four at full progress,
        300+300+400+500 gems. The old cap of 3 would have left the 5-day row."""
        self._arm(monkeypatch, [True] * 4 + [False])
        assert fc._claim_here("Trials", descend=False) == 4

    def test_pressing_stops_the_moment_a_button_is_no_longer_green(
            self, monkeypatch):
        """Hero Recruitment turns the free button ORANGE ("Recruit once, key
        x1") once the free one is spent. The colour guard is the only thing
        between a repeated press and a spent key."""
        self._arm(monkeypatch, [True, True, False, True])
        assert fc._claim_here("Heroes", descend=False) == 2, \
            "it must stop at the first non-green, not resume after it"

    def test_a_sub_tab_with_two_free_buttons_gives_up_both(self, monkeypatch):
        """Hero Recruitment sits one level down and shows Advanced Free and
        Epic Free at once. A single press per dot took one and left the other.
        """
        dots = [{"box": [10, 10, 30, 30], "area": 400, "kind": "dot"}]
        monkeypatch.setattr(fc, "req_detect", lambda *a, **k: list(dots))
        # top level offers nothing, the sub-tab offers two
        self._arm(monkeypatch, [False, True, True, False])
        assert fc._claim_here("Heroes") == 2

    def test_the_press_budget_is_capped(self, monkeypatch):
        """A button that never clears must not hold the sweep forever."""
        self._arm(monkeypatch, [True] * 100)
        assert fc._claim_here("Heroes", descend=False) == \
            fc.MAX_GREEN_PRESSES_PER_SCREEN
