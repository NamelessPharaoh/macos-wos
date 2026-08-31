"""Drift measurement: the instrument that decides whether 355 ROIs move.

Every branch is exercised offline. measure_drift only consumes whatever req_text
hands it, so a synthetic full-frame read reaches cases that are awkward to stage
live -- a popup hiding the whole nav bar, two anchors reading the same string, a
rescale rather than a shift.
"""
import pytest

import core.anchor_drift as ad
from core.coord_utils import BASE_HEIGHT, box_percent_to_pixel


def _recorded_box(key):
    return box_percent_to_pixel(ad.text_area[key]["box"])


def _shift(box, dy_px):
    x1, y1, x2, y2 = box
    return [x1, y1 + dy_px, x2, y2 + dy_px]


def frame(shift_by=None, drop=(), extra=()):
    """A full-frame read with every anchor at its recorded spot, optionally moved.

    shift_by: callable(band, recorded_box) -> dy in pixels.
    """
    shift_by = shift_by or (lambda band, box: 0)
    read = []
    for key, band in ad.ANCHORS:
        if key in drop:
            continue
        box = _recorded_box(key)
        read.append([ad.text_area[key]["text"], _shift(box, shift_by(band, box))])
    read.extend(extra)
    return read


def arm(monkeypatch, read):
    monkeypatch.setattr(ad, "req_text", lambda *a, **k: read)


class TestReadFailures:
    """A broken reader must never look like a layout verdict."""

    def test_ocr_unavailable_is_not_a_layout_verdict(self, monkeypatch):
        monkeypatch.setattr(ad, "req_text", lambda *a, **k: None)
        r = ad.measure_drift()
        assert r.verdict == ad.OCR_UNAVAILABLE
        assert not r.measured and not r.drifted
        assert "layout" in r.reason.lower()

    def test_blank_frame_is_distinct_from_ocr_being_down(self, monkeypatch):
        monkeypatch.setattr(ad, "req_text", lambda *a, **k: [])
        r = ad.measure_drift()
        assert r.verdict == ad.NO_TEXT
        assert r.verdict != ad.OCR_UNAVAILABLE

    def test_drift_reads_are_never_tagged_as_value_reads(self, monkeypatch):
        # read_kind="value" buys a full-frame Paddle shadow-compare (961-2771ms)
        # and floods the burn-in ledger from a read that is positional, not numeric.
        seen = {}
        def spy(*a, **k):
            seen.update(kwargs=k, args=a)
            return []
        monkeypatch.setattr(ad, "req_text", spy)
        ad.measure_drift()
        assert seen["args"] == ()
        assert "read_kind" not in seen["kwargs"]


class TestQuorum:
    """One lucky match must never produce an authoritative number."""

    def test_refuses_when_the_upper_band_is_short(self, monkeypatch):
        arm(monkeypatch, frame(drop=("Home.VIPLevel", "Home.Events")))
        r = ad.measure_drift()
        assert r.verdict == ad.INCONCLUSIVE
        assert r.upper_dy_pct is None and r.bottom_dy_pct is None

    def test_refuses_when_the_nav_bar_is_covered(self, monkeypatch):
        arm(monkeypatch, frame(drop=("Home.World", "Home.Shop", "Home.Heroes",
                                     "Home.Alliance")))
        r = ad.measure_drift()
        assert r.verdict == ad.INCONCLUSIVE

    def test_names_the_anchors_it_could_not_find(self, monkeypatch):
        arm(monkeypatch, frame(drop=("Home.VIPLevel", "Home.Events")))
        r = ad.measure_drift()
        assert "Home.VIPLevel" in r.missing and "Home.Events" in r.missing
        assert "Home.VIPLevel" in r.reason

    def test_reports_once_both_bands_reach_quorum(self, monkeypatch):
        # One anchor short of the full set in each band, still enough to speak.
        arm(monkeypatch, frame(drop=("Home.Deal", "Home.Exploration")))
        r = ad.measure_drift()
        assert r.verdict == ad.OK


class TestClassification:
    def test_no_movement_reads_as_ok(self, monkeypatch):
        arm(monkeypatch, frame())
        r = ad.measure_drift()
        assert r.verdict == ad.OK
        assert not r.drifted

    def test_noise_under_tolerance_is_not_drift(self, monkeypatch):
        arm(monkeypatch, frame(shift_by=lambda band, box: 4))     # ~0.16%
        assert ad.measure_drift().verdict == ad.OK

    def test_uniform_shift_is_a_translation(self, monkeypatch):
        arm(monkeypatch, frame(shift_by=lambda band, box: -126))  # the RRO inset
        r = ad.measure_drift()
        assert r.verdict == ad.TRANSLATION
        assert r.upper_dy_pct == pytest.approx(-126 / BASE_HEIGHT * 100, abs=0.01)

    def test_top_moves_and_nav_holds_is_a_safe_area_relayout(self, monkeypatch):
        # The likeliest shape of removing a top cutout: Android pins the status
        # row to the top inset and the nav bar to the bottom.
        arm(monkeypatch, frame(
            shift_by=lambda band, box: -126 if band == ad.UPPER_BAND else 0))
        r = ad.measure_drift()
        assert r.verdict == ad.SAFE_AREA_RELAYOUT
        assert "slider" in r.reason.lower()

    def test_relayout_is_not_reported_as_rescale(self, monkeypatch):
        # A two-bucket classifier called this RESCALE and prescribed re-recording
        # all 355 ROIs for a case that needs the top band re-anchored.
        arm(monkeypatch, frame(
            shift_by=lambda band, box: -126 if band == ad.UPPER_BAND else 0))
        assert ad.measure_drift().verdict != ad.RESCALE

    def test_drift_growing_down_the_screen_is_a_rescale(self, monkeypatch):
        arm(monkeypatch, frame(shift_by=lambda band, box: (box[1] + box[3]) / 2 * 0.02))
        r = ad.measure_drift()
        assert r.verdict == ad.RESCALE
        assert "re-record" in r.reason.lower()
        assert abs(r.bottom_dy_pct) > abs(r.upper_dy_pct)


class TestMatching:
    def test_duplicate_text_resolves_to_the_nearest_candidate(self, monkeypatch):
        # A second 'World' elsewhere on the frame must not hijack the anchor.
        decoy = ["World", [0, 100, 200, 160]]
        arm(monkeypatch, frame(extra=(decoy,)))
        r = ad.measure_drift()
        world = next(m for m in r.matches if m.key == "Home.World")
        assert world.dy_pct == pytest.approx(0, abs=0.01)

    def test_ocr_noise_still_matches(self, monkeypatch):
        read = frame()
        read[0][0] = read[0][0] + "."          # 'VIP' -> 'VIP.'
        arm(monkeypatch, read)
        assert ad.measure_drift().verdict == ad.OK

    def test_deltas_are_percentages_not_pixels(self, monkeypatch):
        # BASE_HEIGHT doubles under the 2x render probe; pixels would silently
        # double with it, percentages would not.
        arm(monkeypatch, frame(shift_by=lambda band, box: -246))
        r = ad.measure_drift()
        assert r.upper_dy_pct == pytest.approx(-10.0, abs=0.01)
        assert abs(r.upper_dy_pct) < 100


class TestAnchorTable:
    def test_no_anchor_is_account_state(self):
        # Home.Gems / Home.Coal / Home.Power / Home.Survivor read as values that
        # change during play and on every account switch. Matching on those works
        # exactly once.
        volatile = {"Home.Gems", "Home.Coal", "Home.Power", "Home.Survivor"}
        assert not volatile & {key for key, _ in ad.ANCHORS}

    def test_every_anchor_has_a_recorded_box_and_text(self):
        for key, _ in ad.ANCHORS:
            assert ad.text_area.get(key, {}).get("box"), key
            assert ad.text_area[key].get("text"), key

    def test_bands_are_far_enough_apart_to_be_a_lever(self):
        def mid(key):
            b = ad.text_area[key]["box"]
            return (b[1] + b[3]) / 2
        upper = [mid(k) for k, band in ad.ANCHORS if band == ad.UPPER_BAND]
        bottom = [mid(k) for k, band in ad.ANCHORS if band == ad.BOTTOM_BAND]
        assert min(bottom) - max(upper) > 50     # >50% of frame height apart

    def test_each_band_can_actually_reach_its_quorum(self):
        for band, need in ad.QUORUM.items():
            assert sum(1 for _, b in ad.ANCHORS if b == band) >= need


class TestAnchorIsInPlace:
    """The cheap path recalibrate() uses before paying for a full frame."""

    def test_true_when_the_anchor_sits_where_it_was_recorded(self):
        assert ad.anchor_is_in_place("Home.World", [["World", _recorded_box("Home.World")]])

    def test_false_when_the_anchor_has_moved(self):
        moved = _shift(_recorded_box("Home.World"), -126)
        assert not ad.anchor_is_in_place("Home.World", [["World", moved]])

    def test_tolerates_sub_threshold_jitter(self):
        jittered = _shift(_recorded_box("Home.World"), 5)
        assert ad.anchor_is_in_place("Home.World", [["World", jittered]])

    def test_false_on_an_empty_or_malformed_read(self):
        assert not ad.anchor_is_in_place("Home.World", [])
        assert not ad.anchor_is_in_place("Home.World", None)
        assert not ad.anchor_is_in_place("Home.World", [[]])

    def test_false_for_an_unknown_key(self):
        assert not ad.anchor_is_in_place("Nope.NotReal", [["x", [0, 0, 10, 10]]])


class TestFormatReport:
    @pytest.mark.parametrize("shift", [
        None,
        lambda band, box: -126,
        lambda band, box: -126 if band == ad.UPPER_BAND else 0,
    ])
    def test_renders_every_measured_verdict(self, monkeypatch, shift):
        arm(monkeypatch, frame(shift_by=shift))
        out = ad.format_report(ad.measure_drift())
        assert "Anchor drift:" in out and "band means" in out

    def test_renders_a_refusal_without_band_means(self, monkeypatch):
        arm(monkeypatch, frame(drop=("Home.VIPLevel", "Home.Events")))
        out = ad.format_report(ad.measure_drift())
        assert ad.INCONCLUSIVE in out and "band means" not in out


class TestRecalibrateDiagnostics:
    """'Homepage Not found' used to mean three different things."""

    def _report(self, verdict, **kw):
        return ad.DriftReport(verdict=verdict, reason="because", **kw)

    def test_ocr_down_blames_the_reader_not_the_layout(self, monkeypatch):
        import core.recalibrate as rc
        monkeypatch.setattr(rc, "measure_drift",
                            lambda: self._report(ad.OCR_UNAVAILABLE))
        msg = rc._diagnose_missing_homepage()
        assert "OCR is unreachable" in msg and "READER is down" in msg
        assert "moved" not in msg.lower()

    def test_drift_blames_the_layout_and_shows_the_numbers(self, monkeypatch):
        import core.recalibrate as rc
        monkeypatch.setattr(rc, "measure_drift", lambda: self._report(
            ad.TRANSLATION, upper_dy_pct=-5.1, bottom_dy_pct=-5.1))
        msg = rc._diagnose_missing_homepage()
        assert "UI has MOVED" in msg
        assert "-5.10" in msg          # the measurement, not just a claim

    def test_blank_frame_blames_the_emulator(self, monkeypatch):
        import core.recalibrate as rc
        monkeypatch.setattr(rc, "measure_drift", lambda: self._report(ad.NO_TEXT))
        assert "asleep, black, or mid-transition" in rc._diagnose_missing_homepage()

    def test_too_few_anchors_admits_it_cannot_tell(self, monkeypatch):
        import core.recalibrate as rc
        monkeypatch.setattr(rc, "measure_drift", lambda: self._report(
            ad.INCONCLUSIVE, missing=["Home.Deal"]))
        msg = rc._diagnose_missing_homepage()
        assert "too few anchors" in msg
        assert "exactly where they should be" not in msg

    def test_healthy_layout_means_the_bot_is_genuinely_stuck(self, monkeypatch):
        import core.recalibrate as rc
        monkeypatch.setattr(rc, "measure_drift", lambda: self._report(
            ad.OK, upper_dy_pct=0.0, bottom_dy_pct=0.0))
        msg = rc._diagnose_missing_homepage()
        assert "genuinely stuck" in msg
        assert "Stopping the Bot" in msg

    def test_the_three_causes_never_share_a_message(self, monkeypatch):
        import core.recalibrate as rc
        seen = set()
        for verdict in (ad.OCR_UNAVAILABLE, ad.NO_TEXT, ad.TRANSLATION,
                        ad.INCONCLUSIVE, ad.OK):
            monkeypatch.setattr(rc, "measure_drift", lambda v=verdict: self._report(
                v, upper_dy_pct=-5.1, bottom_dy_pct=-5.1))
            seen.add(rc._diagnose_missing_homepage())
        assert len(seen) == 5

    def test_anchor_in_place_costs_no_extra_read(self, monkeypatch):
        import core.recalibrate as rc
        monkeypatch.setattr(rc, "anchor_is_in_place", lambda k, r: True)
        monkeypatch.setattr(rc, "measure_drift",
                            lambda: pytest.fail("must not pay for a full frame"))
        rc._warn_if_anchor_moved([["World", [0, 0, 1, 1]]])

    def test_moved_anchor_warns_with_the_drift_table(self, monkeypatch, capsys):
        import core.recalibrate as rc
        monkeypatch.setattr(rc, "anchor_is_in_place", lambda k, r: False)
        monkeypatch.setattr(rc, "measure_drift", lambda: self._report(
            ad.TRANSLATION, upper_dy_pct=-5.1, bottom_dy_pct=-5.1))
        rc._warn_if_anchor_moved([["World", [0, 0, 1, 1]]])
        out = capsys.readouterr().out
        assert "WARNING" in out and "anchor_drift" in out

    def test_a_miss_that_is_not_drift_stays_quiet(self, monkeypatch, capsys):
        # OCR flaked on one loop. Nothing moved; do not cry drift.
        import core.recalibrate as rc
        monkeypatch.setattr(rc, "anchor_is_in_place", lambda k, r: False)
        monkeypatch.setattr(rc, "measure_drift", lambda: self._report(ad.OCR_UNAVAILABLE))
        rc._warn_if_anchor_moved([])
        assert capsys.readouterr().out == ""
