"""Client-side OCR request plumbing (core/core.py req_ocr / req_text).

Every OCR request must carry a decision_id — a fresh UUID when the caller
doesn't pass one, the caller's shared id when it does (tap_on_text passes one
id across its retried reads so burn-in rates count decisions, not attempts).
read_kind must reach the server payload untouched: the server's zero-item
fallback and shadow-compare key on it.
"""
import pytest

import core.core as cc


def _capture_payloads(monkeypatch):
    payloads = []

    def fake_post(url, payload, request_name, wait_sec=None):
        payloads.append(payload)
        return {"success": True, "count": 0, "results": []}

    monkeypatch.setattr(cc, "_post_json_with_replay", fake_post)
    return payloads


def test_auto_decision_ids_are_fresh_per_call(monkeypatch):
    payloads = _capture_payloads(monkeypatch)
    cc.req_ocr(name="a")
    cc.req_ocr(name="b")
    ids = [p["decision_id"] for p in payloads]
    assert all(ids), "every request must carry a decision_id"
    assert ids[0] != ids[1], "unrelated calls must not share a decision"


def test_explicit_decision_id_and_read_kind_pass_through(monkeypatch):
    payloads = _capture_payloads(monkeypatch)
    cc.req_ocr(name="a", read_kind="value", decision_id="shared-1")
    assert payloads[0]["decision_id"] == "shared-1"
    assert payloads[0]["read_kind"] == "value"


def test_read_kind_defaults_to_none_for_label_reads(monkeypatch):
    payloads = _capture_payloads(monkeypatch)
    cc.req_ocr(name="a", expected_text="Read & Claim")
    assert payloads[0]["read_kind"] is None
    assert payloads[0]["expected_text"] == "Read & Claim"


# --- req_text ROI resolution (load_config) ---------------------------------
# Both behaviours below sit directly under the Chief Profile furnace read that
# the capability gate depends on, and both were latent: no caller has ever
# passed rois=, and no caller has ever used a name missing from text_area.


def test_unknown_roi_name_raises_instead_of_reading_the_full_screen(monkeypatch):
    """A typo used to become box [0,0,100,100] — a silent full-screen read
    returned under the typo'd name. With the score now surfaced to callers that
    is confident garbage, so it has to fail loudly instead."""
    _capture_payloads(monkeypatch)
    with pytest.raises(KeyError, match="Unknown ROI name"):
        cc.req_text("ChiefProfile.FurnaceLevl")  # typo: ...FurnaceLevel


def test_explicit_rois_override_names_and_do_not_touch_the_callers_list(monkeypatch):
    """load_config used to rebind its accumulator to the caller's own list and
    then append, so passing rois returned rois + one box per name AND grew the
    caller's list. That offset would shift any roi_index -> name mapping."""
    payloads = _capture_payloads(monkeypatch)
    caller_rois = [[10.0, 10.0, 20.0, 20.0]]
    before = [list(b) for b in caller_rois]

    cc.req_text(["Home.World", "Home.Alliance.Title"], rois=caller_rois)

    assert caller_rois == before, "req_text must not mutate the caller's rois list"
    assert len(payloads[0]["rois"]) == 1, (
        "rois overrides the named boxes; the old code sent rois + one per name"
    )


def test_named_rois_resolve_one_box_per_name_in_order(monkeypatch):
    payloads = _capture_payloads(monkeypatch)
    cc.req_text(["Home.World", "Home.Alliance.Title"])
    assert len(payloads[0]["rois"]) == 2
