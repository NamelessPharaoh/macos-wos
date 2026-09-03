"""Observed locks: only text actually read off the screen may become one.

The danger this whole path guards against is recording a lock from ABSENCE.
usecases/arena.py find_arena() and usecases/labyrinth.py go_to_labyrinth()
scroll the Daily Missions list for a specific row, and that row disappears both
when the feature is locked AND when the daily is simply finished. Recording
from those bails would permanently disable a working task on the first day it
succeeded — strictly worse than the wasted navigation the gate exists to
remove.
"""
import json

import pytest

import core.core as cc
from core import capability
from core import player_profile as pp
from usecases import lock_evidence


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    d = tmp_path / "players"
    d.mkdir()
    (d / "example.json").write_text(json.dumps({"id": "0", "furnace_level": None}))
    (d / "846646676.json").write_text(json.dumps({
        "id": "846646676", "furnace_level": 7,
    }))
    monkeypatch.setattr(pp, "PLAYERS_DIR", str(d))
    monkeypatch.setattr(pp, "EXAMPLE_PATH", str(d / "example.json"))
    monkeypatch.delenv("WOS_FURNACE_RESET", raising=False)
    return d


def _screen(monkeypatch, lines):
    """Fake a full-frame read returning these text lines."""
    monkeypatch.setattr(cc, "req_text",
                        lambda *a, **k: [[t, [0, 0, 1, 1]] for t in lines])


# --- read_lock_marker ------------------------------------------------------


@pytest.mark.parametrize("line,expected", [
    ("Locked", True),
    ("VIP 1 Benefits(Locked)", True),
    ("Unlocked at Furnace 18", False),   # says the OPPOSITE
    ("unlocked", False),
    ("Blocked", False),                  # not a lock marker either
    ("Pet Adventure", False),
])
def test_lock_marker_matching(monkeypatch, line, expected):
    _screen(monkeypatch, [line])
    assert (cc.read_lock_marker() is not None) is expected


def test_lock_marker_returns_the_text_as_evidence(monkeypatch):
    _screen(monkeypatch, ["Pet Adventure", "Beast Cage (Locked)"])
    assert cc.read_lock_marker() == "Beast Cage (Locked)"


def test_ocr_failure_reads_as_not_locked(monkeypatch):
    """Fail open: never stop doing work on the strength of a read that failed."""
    monkeypatch.setattr(cc, "req_text", lambda *a, **k: None)
    assert cc.read_lock_marker() is None


# --- record_lock -----------------------------------------------------------


def test_evidence_is_a_required_positional(profile_dir):
    """Not a validated keyword: Main/task_menu.py:195 catches bare Exception, so
    a runtime raise would be reported as a task crash instead."""
    with pytest.raises(TypeError):
        pp.record_lock({"id": "846646676"}, "beast_cage", "reason")


def test_falsy_evidence_is_refused_without_raising(profile_dir, capsys):
    profile = pp.load_profile("846646676")
    assert pp.record_lock(profile, "beast_cage", "reason", "") is False
    assert pp.record_lock(profile, "beast_cage", "reason", None) is False
    assert "observed_locks" not in profile
    assert "no evidence" in capsys.readouterr().out


def test_recorded_lock_is_stamped_with_the_observing_furnace(profile_dir):
    profile = pp.load_profile("846646676")
    assert pp.record_lock(profile, "beast_cage", "arrival check", "Locked")
    saved = json.loads((profile_dir / "846646676.json").read_text())
    record = saved["observed_locks"]["beast_cage"]
    assert record["furnace_at_observation"] == 7
    assert record["evidence"] == "Locked"
    assert record["observed_at"]


# --- observed_lock: the stamp self-invalidates ------------------------------


def test_lock_applies_at_the_furnace_that_saw_it():
    profile = {"furnace_level": 7,
               "observed_locks": {"beast_cage": {"furnace_at_observation": 7}}}
    assert capability.observed_lock("beast_cage", profile) is not None


def test_lock_is_dropped_once_the_account_levels_past_it():
    """Otherwise a lock seen at Furnace 7 would keep Pets skipped forever on an
    account that reached 18 — worse than the problem the gate solves."""
    profile = {"furnace_level": 8,
               "observed_locks": {"beast_cage": {"furnace_at_observation": 7}}}
    assert capability.observed_lock("beast_cage", profile) is None


def test_malformed_lock_records_are_ignored():
    for junk in ("a string", 42, None, []):
        assert capability.observed_lock(
            "beast_cage", {"observed_locks": {"beast_cage": junk}}) is None


def test_gate_skips_on_an_observed_lock_the_table_would_have_run():
    table = {"features": {"beast_cage": {
        "label": "Pets",
        "conditions": [{"state_key": "furnace_level", "op": ">=", "value": 4}],
    }}}
    profile = {"furnace_level": 7, "observed_locks": {
        "beast_cage": {"furnace_at_observation": 7, "evidence": "Locked"}}}
    verdict = capability.evaluate("beast_cage", profile, table=table)
    assert verdict.decision == capability.SKIP
    assert verdict.source == "observed", "in-game evidence outranks the table"


def test_a_lock_is_keyed_by_feature_so_both_pet_tasks_skip():
    """pet_treasure and pet_exploration both gate on beast_cage."""
    from Main import task_menu
    gates = {t.key: t.gate for t in task_menu.TASKS}
    assert gates["pet_treasure"] == gates["pet_exploration"] == "beast_cage"


# --- note_if_locked, and the regression that matters ------------------------


def test_no_player_id_is_a_no_op(monkeypatch):
    _screen(monkeypatch, ["Locked"])
    assert lock_evidence.note_if_locked(None, "beast_cage", "ctx") is False


def test_records_when_the_screen_actually_says_locked(profile_dir, monkeypatch):
    _screen(monkeypatch, ["Beast Cage (Locked)"])
    assert lock_evidence.note_if_locked("846646676", "beast_cage", "ctx") is True
    saved = json.loads((profile_dir / "846646676.json").read_text())
    assert saved["observed_locks"]["beast_cage"]["evidence"] == "Beast Cage (Locked)"


def test_a_finished_daily_records_nothing(profile_dir, monkeypatch):
    """THE regression. Arena's row is gone because today's challenges are done;
    the screen shows the Daily Missions list with no lock anywhere. Recording
    here would disable arena until the next furnace level-up."""
    _screen(monkeypatch, [
        "Daily Missions", "Gather resources 3 times", "Go",
        "Train 10 troops", "Claim",
    ])
    assert lock_evidence.note_if_locked("846646676", "arena_of_glory",
                                        "daily mission row absent") is False
    saved = json.loads((profile_dir / "846646676.json").read_text())
    assert "observed_locks" not in saved, "absence is not evidence"


def test_an_unlocked_message_records_nothing(profile_dir, monkeypatch):
    """"Unlocked at Furnace 18" contains the substring "locked". The old
    substring check would have recorded a lock from a message saying the
    feature is open."""
    _screen(monkeypatch, ["Beast Cage", "Unlocked at Furnace 18"])
    assert lock_evidence.note_if_locked("846646676", "beast_cage", "ctx") is False
    saved = json.loads((profile_dir / "846646676.json").read_text())
    assert "observed_locks" not in saved
