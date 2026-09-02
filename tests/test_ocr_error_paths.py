"""Error-path and boundary tests from the ship pre-landing review.

Covers: run_ocr's capture-failure contract and full-frame leg, the shadow-read
exception guard (instrumentation must never kill a real read), the pure
_paddle_lines_to_items transform, take_screenshot's error branches, the
example.json fail-loud contract, and tap_on_text's shared decision id.
"""
import json
import subprocess

import numpy as np
import pytest

import core.ocr as ocr_mod
import cmd_program.screen_action as sa

IMG = np.zeros((40, 40, 3), dtype=np.uint8)
ITEM = {"text": "hello", "score": 0.95, "box": [1, 2, 3, 4]}


@pytest.fixture
def clean_engine_state(monkeypatch):
    saved = (ocr_mod._resolved_engine, ocr_mod._vision_engine, ocr_mod._paddle_engine,
             ocr_mod._vision_exc_streak, ocr_mod._vision_disabled_session)
    yield monkeypatch
    (ocr_mod._resolved_engine, ocr_mod._vision_engine, ocr_mod._paddle_engine,
     ocr_mod._vision_exc_streak, ocr_mod._vision_disabled_session) = saved


class TestRunOcrErrorPaths:
    def test_capture_failure_raises_runtime_error(self, clean_engine_state):
        clean_engine_state.setattr(
            ocr_mod, "_capture_frame",
            lambda *a, **k: (_ for _ in ()).throw(OSError("adb gone")))
        with pytest.raises(RuntimeError, match="OCR capture failed"):
            ocr_mod.run_ocr(rois=[[0, 0, 10, 10]])

    def test_capture_returning_none_yields_empty(self, clean_engine_state):
        clean_engine_state.setattr(ocr_mod, "_capture_frame", lambda *a, **k: None)
        assert ocr_mod.run_ocr(rois=[[0, 0, 10, 10]]) == []

    def test_full_frame_leg_passes_items_through_unoffset(self, clean_engine_state):
        clean_engine_state.setattr(ocr_mod, "_capture_frame", lambda *a, **k: IMG.copy())
        clean_engine_state.setattr(
            ocr_mod, "_recognize_crop_unlocked",
            lambda img, expected_text=None, read_kind=None: ([dict(ITEM)], "vision", False))
        results = ocr_mod.run_ocr(rois=None)
        assert results == [ITEM]  # no ROI offsets on the full-frame leg


class TestShadowReadGuard:
    def test_paddle_exception_in_shadow_keeps_vision_result(self, clean_engine_state):
        # Instrumentation must never kill a real read (pre-landing review fix).
        clean_engine_state.setattr(ocr_mod, "_paddle_models_present", lambda: True)
        clean_engine_state.setattr(
            ocr_mod, "_paddle_recognize_unlocked",
            lambda img: (_ for _ in ()).throw(RuntimeError("paddle blew up")))
        assert ocr_mod._shadow_compare_unlocked(IMG, [dict(ITEM)]) is None


class TestPaddleLinesToItems:
    def test_none_and_empty_outputs(self):
        assert ocr_mod._paddle_lines_to_items(None) == []
        assert ocr_mod._paddle_lines_to_items([]) == []
        assert ocr_mod._paddle_lines_to_items([[]]) == []

    def test_malformed_lines_skipped(self):
        output = [[None, ["short"], "not-a-list",
                   [[[0, 0], [4, 0], [4, 2], [0, 2]], ("ok", 0.99)]]]
        items = ocr_mod._paddle_lines_to_items(output)
        assert [i["text"] for i in items] == ["ok"]
        assert items[0]["box"] == [0, 0, 4, 2]

    def test_score_floor_is_exclusive_at_exactly_0_8(self):
        line = lambda score: [[[0, 0], [4, 0], [4, 2], [0, 2]], ("t", score)]
        assert ocr_mod._paddle_lines_to_items([[line(0.8)]]) == []
        assert len(ocr_mod._paddle_lines_to_items([[line(0.8001)]])) == 1


class TestTakeScreenshotErrorPaths:
    def _with_device(self, monkeypatch):
        monkeypatch.setattr(sa, "resolve_device", lambda force=False: "dev-1")

    def test_called_process_error_invalidates_cache_and_carries_stderr(self, monkeypatch):
        self._with_device(monkeypatch)
        invalidated = []
        monkeypatch.setattr(sa, "invalidate_device", lambda: invalidated.append(True))
        err = subprocess.CalledProcessError(1, "adb", stderr=b"device offline")
        monkeypatch.setattr(sa.subprocess, "check_output",
                            lambda *a, **k: (_ for _ in ()).throw(err))
        with pytest.raises(RuntimeError, match="device offline"):
            sa.take_screenshot()
        assert invalidated == [True]

    def test_missing_adb_binary_raises_clearly(self, monkeypatch):
        self._with_device(monkeypatch)
        monkeypatch.setattr(sa.subprocess, "check_output",
                            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("adb")))
        with pytest.raises(RuntimeError, match="adb binary not found"):
            sa.take_screenshot()

    def test_undecodable_bytes_report_length(self, monkeypatch):
        self._with_device(monkeypatch)
        monkeypatch.setattr(sa.subprocess, "check_output", lambda *a, **k: b"garbage")
        with pytest.raises(RuntimeError, match="bytes_received=7"):
            sa.take_screenshot()


class TestPlayerProfileFailLoud:
    def test_missing_example_json_raises(self, monkeypatch, tmp_path):
        import core.player_profile as pp
        monkeypatch.setattr(pp, "PLAYERS_DIR", tmp_path / "players")
        monkeypatch.setattr(pp, "EXAMPLE_PATH", tmp_path / "players" / "example.json")
        (tmp_path / "players").mkdir()
        # example.json missing is repo damage — must raise, never absorb.
        with pytest.raises(FileNotFoundError):
            pp.load_profile("someone")


class TestTapOnTextDecisionSharing:
    def test_all_retried_reads_share_one_decision_id(self, monkeypatch):
        import core.core as cc
        payloads = []

        def fake_post(url, payload, name, wait_sec=None):
            payloads.append(payload)
            return {"success": True, "results": []}

        monkeypatch.setattr(cc, "_post_json_with_replay", fake_post)
        monkeypatch.setattr(cc.time, "sleep", lambda s: None)
        cc.tap_on_text("NoSuchLabelAnywhere")
        assert len(payloads) >= 2  # retries happened
        ids = {p["decision_id"] for p in payloads}
        assert len(ids) == 1  # one decision across all retried reads


# --- req_text_named: ROI attribution survives a batched read ---------------
# The three cases below are the entire justification for roi_index. Case 2 is
# the documented bug: core/ocr.py skips an ROI that read nothing, so a
# positional index slides into the next ROI's first line (usecases/pet.py:25-31).


def _named_response(monkeypatch, results):
    """Arm req_ocr's transport with a canned server response."""
    import core.core as cc
    captured = {}

    def fake_post(url, payload, request_name, wait_sec=None):
        captured["payload"] = payload
        return {"success": True, "count": len(results), "results": results}

    monkeypatch.setattr(cc, "_post_json_with_replay", fake_post)
    return cc, captured


def test_named_read_maps_each_line_to_its_own_roi(monkeypatch):
    cc, _ = _named_response(monkeypatch, [
        {"text": "lord", "score": 0.9, "box": [0, 0, 1, 1], "roi_index": 0},
        {"text": "7", "score": 0.8, "box": [2, 2, 3, 3], "roi_index": 1},
    ])
    out = cc.req_text_named(["ChiefProfile.PlayerName", "ChiefProfile.FurnaceLevel"])
    assert out["ChiefProfile.PlayerName"][0]["text"] == "lord"
    assert out["ChiefProfile.FurnaceLevel"][0]["text"] == "7"
    assert out["ChiefProfile.FurnaceLevel"][0]["score"] == 0.8


def test_roi_that_read_nothing_does_not_shift_the_others(monkeypatch):
    """The server drops an empty ROI entirely, so index 1 is simply absent.
    Positionally, "4653" would have become the furnace level."""
    cc, _ = _named_response(monkeypatch, [
        {"text": "lord", "score": 0.9, "box": [0, 0, 1, 1], "roi_index": 0},
        {"text": "4653", "score": 0.7, "box": [4, 4, 5, 5], "roi_index": 2},
    ])
    out = cc.req_text_named([
        "ChiefProfile.PlayerName",
        "ChiefProfile.FurnaceLevel",
        "ChiefProfile.State",
    ])
    assert out["ChiefProfile.FurnaceLevel"] == [], "empty ROI must stay empty"
    assert out["ChiefProfile.State"][0]["text"] == "4653"


def test_roi_returning_two_lines_keeps_both_under_its_own_name(monkeypatch):
    cc, _ = _named_response(monkeypatch, [
        {"text": "7", "score": 0.9, "box": [0, 0, 1, 1], "roi_index": 0},
        {"text": "/10", "score": 0.6, "box": [1, 0, 2, 1], "roi_index": 0},
        {"text": "4653", "score": 0.8, "box": [4, 4, 5, 5], "roi_index": 1},
    ])
    out = cc.req_text_named(["ChiefProfile.FurnaceLevel", "ChiefProfile.State"])
    assert [r["text"] for r in out["ChiefProfile.FurnaceLevel"]] == ["7", "/10"]
    assert out["ChiefProfile.State"][0]["text"] == "4653"


def test_named_read_rejects_an_unknown_roi_name(monkeypatch):
    cc, _ = _named_response(monkeypatch, [])
    with pytest.raises(KeyError, match="Unknown ROI name"):
        cc.req_text_named("ChiefProfile.FurnaceLevl")
