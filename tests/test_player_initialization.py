"""Regression cover for Main.main.player_initialization's parse and persist.

This function had ZERO test coverage: every test that touched it replaced it
wholesale (tests/test_run_bot.py fakes it at the boundary). It is also the only
writer of profile["furnace_level"], the number the capability gate reads — so
the parse at Main/main.py:302-311 and the persist at :335-354 were both
unguarded while being modified.

Everything the emulator would supply is faked at the seam: _open_chief_profile
returns True, and req_text is scripted per call (title read, then the
four-field value read).
"""
import json

import pytest

import Main.main as mm
from core import player_profile as pp


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    d = tmp_path / "players"
    d.mkdir()
    (d / "example.json").write_text(json.dumps({
        "id": "00000000", "name": "example", "state": 3429,
        "furnace_level": None,
    }))
    monkeypatch.setattr(pp, "PLAYERS_DIR", str(d))
    monkeypatch.setattr(pp, "EXAMPLE_PATH", str(d / "example.json"))
    monkeypatch.delenv("WOS_FURNACE_RESET", raising=False)
    return d


def _arm(monkeypatch, name="[LAT]lord846646676", pid="846646676",
         furnace="7", state="#4653"):
    """Script the two req_text calls player_initialization makes."""
    monkeypatch.setattr(mm, "_open_chief_profile", lambda *a, **k: True)
    monkeypatch.setattr(mm.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(mm, "player_data", [
        ("burner@example.com", {"player": [{"name": "lord", "id": "846646676"}]}),
    ], raising=False)

    calls = {"n": 0}

    def fake_req_text(names, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:                       # ChiefProfile.Title
            return [["Chief Profile", [0, 0, 1, 1]]]
        return [                                   # the four-field value read
            [name, [0, 0, 1, 1]],
            [pid, [0, 0, 1, 1]],
            [furnace, [0, 0, 1, 1]],
            [state, [0, 0, 1, 1]],
        ]

    monkeypatch.setattr(mm, "req_text", fake_req_text)
    return calls


def _saved(profile_dir, pid="846646676"):
    return json.loads((profile_dir / f"{pid}.json").read_text())


def test_parses_and_persists_a_good_read(profile_dir, monkeypatch):
    _arm(monkeypatch)
    player = mm.player_initialization()
    assert player is not None, "a clean read must return the Player"
    assert player.id == "846646676"

    saved = _saved(profile_dir)
    assert saved["furnace_level"] == 7, "the gate reads this key"
    assert saved["state"] == "4653", "state is split on '#'"
    assert saved["name"] == "lord846646676", "name is split on ']'"


def test_furnace_misread_high_is_rejected_and_the_good_value_survives(
        profile_dir, monkeypatch):
    _arm(monkeypatch, furnace="7")
    mm.player_initialization()
    assert _saved(profile_dir)["furnace_level"] == 7

    _arm(monkeypatch, furnace="17")   # 7 misread as 17
    mm.player_initialization()
    assert _saved(profile_dir)["furnace_level"] == 7, (
        "a jump of more than one level must not persist — it would mark Pets "
        "and Arena unlocked and send the bot into locked screens"
    )


def test_furnace_misread_low_is_rejected(profile_dir, monkeypatch):
    _arm(monkeypatch, furnace="7")
    mm.player_initialization()
    _arm(monkeypatch, furnace="1")
    mm.player_initialization()
    assert _saved(profile_dir)["furnace_level"] == 7


def test_zero_furnace_read_is_rejected_not_silently_skipped(
        profile_dir, monkeypatch):
    _arm(monkeypatch, furnace="7")
    mm.player_initialization()
    _arm(monkeypatch, furnace="0")
    mm.player_initialization()
    assert _saved(profile_dir)["furnace_level"] == 7


def test_non_numeric_furnace_read_does_not_raise(profile_dir, monkeypatch):
    _arm(monkeypatch, furnace="~~~")
    player = mm.player_initialization()
    assert player is not None, "int() used to raise here and end the whole pass"
    assert _saved(profile_dir)["furnace_level"] is None


def test_furnace_reset_env_overrides_a_stuck_value(profile_dir, monkeypatch):
    _arm(monkeypatch, furnace="17")
    mm.player_initialization()
    assert _saved(profile_dir)["furnace_level"] == 17, "first read has no floor"

    monkeypatch.setenv("WOS_FURNACE_RESET", "7")
    _arm(monkeypatch, furnace="7")
    mm.player_initialization()
    assert _saved(profile_dir)["furnace_level"] == 7
