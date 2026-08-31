"""Burn-in verdict math on synthetic ledgers (scripts/burnin_report.py)."""
import json
import time

import pytest

from datetime import datetime, timezone

from scripts.burnin_report import (
    DAY_S,
    compute_verdict,
    load_epoch,
    load_records,
    load_waivers,
)


NOW = time.time()


def _read(day, decision, *, kind="value", fallback=0, mismatch=False, rss=400.0,
          expected=False):
    return {
        "ts": NOW - (10 - day) * DAY_S,
        "decision_id": decision,
        "read_kind": kind,
        "expected": expected,
        "fallback_hits": fallback,
        "digit_mismatch": mismatch,
        "rss_mb": rss,
    }


def _healthy_week(n_decisions=2500):
    records = []
    for i in range(n_decisions):
        day = i % 8  # spread across 8 days
        records.append(_read(day, f"d{i}"))
    return records


class TestExitCriteria:
    def test_healthy_week_passes(self):
        r = compute_verdict(_healthy_week())
        assert r["verdict"].startswith("EXIT: PASS")
        assert r["reasons"] == []

    def test_empty_log(self):
        assert compute_verdict([])["verdict"] == "NO DATA"

    def test_too_few_days_in_progress(self):
        records = [_read(day=8, decision=f"d{i}") for i in range(3000)]
        records += [_read(day=9, decision=f"e{i}") for i in range(10)]
        r = compute_verdict(records)
        assert r["verdict"] == "IN PROGRESS"
        assert any("days elapsed" in s for s in r["reasons"])

    def test_too_few_decisions_in_progress(self):
        records = [_read(day=i % 8, decision=f"d{i}") for i in range(100)]
        r = compute_verdict(records)
        assert r["verdict"] == "IN PROGRESS"
        assert any("decisions" in s for s in r["reasons"])

    def test_retries_collapse_into_one_decision(self):
        # 3000 reads but only 30 distinct decision ids.
        records = [_read(day=i % 8, decision=f"d{i % 30}") for i in range(3000)]
        r = compute_verdict(records)
        assert r["decisions"] == 30
        assert r["total_reads"] == 3000

    def test_expectation_free_reads_excluded_from_denominator(self):
        records = _healthy_week()
        records += [_read(day=i % 8, decision=f"free{i}", kind=None) for i in range(500)]
        r = compute_verdict(records)
        assert r["decisions"] == 2500

    def test_expected_text_reads_count_as_expectation(self):
        records = [_read(day=i % 8, decision=f"d{i}", kind=None, expected=True)
                   for i in range(2500)]
        r = compute_verdict(records)
        assert r["decisions"] == 2500


class TestFailureCriteria:
    def test_fallback_rate_over_one_percent_fails(self):
        records = _healthy_week(2000)
        records += [_read(day=i % 8, decision=f"fb{i}", fallback=1) for i in range(30)]
        r = compute_verdict(records)
        assert any("template-digit" in s for s in r["reasons"])

    def test_unwaived_mismatch_blocks(self):
        records = _healthy_week()
        records.append(_read(day=3, decision="bad1", mismatch=True))
        r = compute_verdict(records)
        assert any("DIGIT_MISMATCH" in s for s in r["reasons"])

    def test_waived_mismatch_does_not_block(self):
        records = _healthy_week()
        records.append(_read(day=3, decision="bad1", mismatch=True))
        r = compute_verdict(records, waivers={"bad1"})
        assert r["verdict"].startswith("EXIT: PASS")
        assert r["mismatched"] == ["bad1"] and r["unwaived_mismatches"] == []

    def test_rss_growth_fails(self):
        records = []
        for i in range(2500):
            day = i % 8
            records.append(_read(day, f"d{i}", rss=300.0 + day * 40))  # +280MB across window
        r = compute_verdict(records)
        assert any("RSS growth" in s for s in r["reasons"])
        assert any("RAM-cap" in s for s in r["reasons"])


class TestFourteenDayCap:
    def test_cap_reached_low_volume_decides_on_data(self):
        records = [_read(day=0, decision=f"d{i}") for i in range(50)]
        records += [_read(day=10, decision=f"e{i}") for i in range(50)]
        # spread first/last 15 days apart
        records[0]["ts"] = NOW - 15 * DAY_S
        r = compute_verdict(records)
        assert "14-day cap" in r["verdict"]

    def test_cap_reached_with_failing_criteria_exits_fail(self):
        # Past the cap, a failing criterion is a hard FAIL, not IN PROGRESS.
        records = _healthy_week()
        records.append(_read(day=3, decision="bad1", mismatch=True))
        records.append(_read(day=10, decision="recent"))
        records[0]["ts"] = NOW - 15 * DAY_S
        r = compute_verdict(records)
        assert r["verdict"].startswith("EXIT: FAIL")
        assert "14-day cap" in r["verdict"]


class TestLedgerIO:
    def test_load_records_skips_torn_lines(self, tmp_path):
        # A process kill mid-append leaves a torn JSON line; the reader must
        # keep every intact record around it.
        p = tmp_path / "log.jsonl"
        good = json.dumps({"ts": 1.0})
        p.write_text(good + "\n" + '{"ts": 2.0, "trunca' + "\n\n" + good + "\n")
        assert load_records(p) == [{"ts": 1.0}, {"ts": 1.0}]

    def test_load_waivers_missing_file_is_empty(self, tmp_path):
        assert load_waivers(tmp_path / "nope.txt") == set()

    def test_load_waivers_strips_and_drops_blank_lines(self, tmp_path):
        w = tmp_path / "burnin_waivers.txt"
        w.write_text("abc\n\n  def \n")
        assert load_waivers(w) == {"abc", "def"}


class TestRotatedSegments:
    def test_main_folds_rotated_segments_into_verdict(self, tmp_path, monkeypatch, capsys):
        import sys as _sys
        import scripts.burnin_report as br
        live = tmp_path / "ocr_burnin.jsonl"
        rotated = tmp_path / "ocr_burnin.1756500000.jsonl"
        rotated.write_text(json.dumps(_read(0, "d-old")) + "\n")
        live.write_text(json.dumps(_read(8, "d-new")) + "\n")
        monkeypatch.setattr(_sys, "argv", ["burnin_report.py", str(live)])
        assert br.main() == 0
        out = json.loads(capsys.readouterr().out)
        # Both segments counted: rotation must not reset measured progress.
        assert out["total_reads"] == 2
        assert out["decisions"] == 2


class TestCoordinateFrameEpoch:
    """A layout change makes accuracy data either side of it incomparable.

    Removing the cutout overlay or retuning the in-game screen-adaptation slider
    moves all 355 recorded ROIs at once. A clipped box afterwards reads as a
    DIGIT_MISMATCH, and days of clean pre-change data would average it away.
    """

    def _epoch(self, day):
        return NOW - (10 - day) * DAY_S

    def _after_epoch(self, n=2500):
        """A healthy week whose decisions all land AFTER a day-5 epoch.

        _healthy_week() spreads across days 0-7, so an epoch mid-week would
        strand most of it on the wrong side and the volume criterion — not the
        thing under test — would drive the verdict.
        """
        return [_read(6 + (i % 4), f"d{i}") for i in range(n)]

    def test_no_epoch_is_unchanged_behaviour(self):
        records = _healthy_week()
        assert compute_verdict(records) == compute_verdict(records, epoch_ts=None)

    def test_pre_epoch_mismatches_do_not_fail_the_post_epoch_verdict(self):
        # The calibration FIXED the clipping. Old mismatches must not veto that.
        records = self._after_epoch()
        records += [_read(1, f"old{i}", mismatch=True) for i in range(20)]
        # Without the epoch the old mismatches veto the exit.
        assert compute_verdict(records)["unwaived_mismatches"]
        r = compute_verdict(records, epoch_ts=self._epoch(5))
        assert r["unwaived_mismatches"] == []
        assert r["verdict"].startswith("EXIT: PASS")

    def test_pre_epoch_evidence_is_reported_not_discarded(self):
        # A pile of mismatches on the old layout is the evidence the calibration
        # was worth doing. Losing it silently would hide that.
        records = self._after_epoch()
        records += [_read(1, f"old{i}", mismatch=True) for i in range(20)]
        r = compute_verdict(records, epoch_ts=self._epoch(5))
        assert len(r["pre_epoch_unwaived_mismatches"]) == 20
        assert r["pre_epoch_reads"] == 20
        assert r["pre_epoch_decisions"] == 20
        assert r["epoch"] is not None

    def test_post_epoch_mismatches_still_fail(self):
        records = self._after_epoch()
        records += [_read(7, "fresh", mismatch=True)]
        r = compute_verdict(records, epoch_ts=self._epoch(5))
        assert r["unwaived_mismatches"] == ["fresh"]

    def test_the_clock_is_not_reset_by_the_epoch(self):
        # The whole point of annotating rather than truncating: elapsed days and
        # RSS growth do not depend on where the text sits.
        records = _healthy_week()
        assert compute_verdict(records, epoch_ts=self._epoch(5))["days"] == \
               compute_verdict(records)["days"]

    def test_rss_growth_spans_the_whole_ledger(self):
        records = [_read(0, "a", rss=300.0), _read(9, "b", rss=900.0)]
        r = compute_verdict(records, epoch_ts=self._epoch(5))
        assert r["rss_growth_mb"] == 600.0

    def test_an_epoch_with_no_reads_after_it_says_so(self):
        r = compute_verdict(_healthy_week(), epoch_ts=NOW + DAY_S)
        assert r["verdict"] == "IN PROGRESS"
        assert any("no reads since the coordinate-frame change" in x
                   for x in r["reasons"])

    def test_waivers_apply_to_the_pre_epoch_half_too(self):
        records = _healthy_week() + [_read(1, "old0", mismatch=True)]
        r = compute_verdict(records, waivers={"old0"}, epoch_ts=self._epoch(5))
        assert r["pre_epoch_unwaived_mismatches"] == []


class TestLoadEpoch:
    def test_missing_file_is_no_epoch(self, tmp_path):
        assert load_epoch(tmp_path / "nope.txt") is None

    def test_reads_an_iso_timestamp(self, tmp_path):
        p = tmp_path / "e.txt"
        p.write_text("2026-08-31T12:00:00+00:00\n")
        assert load_epoch(p) == pytest.approx(
            datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc).timestamp())

    def test_accepts_a_trailing_z(self, tmp_path):
        p = tmp_path / "e.txt"
        p.write_text("2026-08-31T12:00:00Z\n")
        assert load_epoch(p) is not None

    def test_comments_and_blanks_are_ignored(self, tmp_path):
        p = tmp_path / "e.txt"
        p.write_text("# removed the cutout RRO, slider 77 -> 91\n\n"
                     "2026-08-31T12:00:00Z  # measured dy -5.12%\n")
        assert load_epoch(p) is not None

    def test_a_malformed_epoch_is_ignored_not_guessed(self, tmp_path, capsys):
        # Guessing here would silently drop half the ledger.
        p = tmp_path / "e.txt"
        p.write_text("yesterday afternoon\n")
        assert load_epoch(p) is None
        assert "not an ISO-8601 timestamp" in capsys.readouterr().out
