import json
import os

import pytest

from core import player_profile as pp


@pytest.fixture
def players_dir(tmp_path, monkeypatch):
    d = tmp_path / "players"
    d.mkdir()
    example = {
        "id": "00000000",
        "name": "example",
        "state": 3429,
        "gather": {"current_march": 0, "possible_march": 4, "priority": None},
    }
    (d / "example.json").write_text(json.dumps(example))
    monkeypatch.setattr(pp, "PLAYERS_DIR", str(d))
    monkeypatch.setattr(pp, "EXAMPLE_PATH", str(d / "example.json"))
    return d


def test_load_missing_profile_seeds_from_example(players_dir):
    profile = pp.load_profile("555000111")
    assert profile["id"] == "555000111"
    assert profile["name"] is None
    assert profile["gather"]["possible_march"] == 4


def test_save_and_reload_roundtrip(players_dir):
    profile = pp.load_profile("555000111")
    profile["name"] = "lord555000111"
    pp.save_profile(profile)
    again = pp.load_profile("555000111")
    assert again["name"] == "lord555000111"
    assert os.path.exists(players_dir / "555000111.json")


def test_existing_profile_is_reused_not_reseeded(players_dir):
    (players_dir / "555000111.json").write_text(
        json.dumps({"id": "555000111", "name": "kept", "gather": {"node_level": 3}})
    )
    profile = pp.load_profile("555000111")
    assert profile["name"] == "kept"
    assert pp.get_gather_node_level(profile) == 3


def test_node_level_defaults_to_8(players_dir):
    profile = pp.load_profile("555000111")
    assert pp.get_gather_node_level(profile) == 8


def test_node_level_set_persists_and_clamps(players_dir):
    profile = pp.load_profile("555000111")
    pp.set_gather_node_level(profile, 0)
    assert pp.get_gather_node_level(pp.load_profile("555000111")) == 1
    pp.set_gather_node_level(profile, 99)
    assert pp.get_gather_node_level(pp.load_profile("555000111")) == 8
    pp.set_gather_node_level(profile, 5)
    assert pp.get_gather_node_level(pp.load_profile("555000111")) == 5


def test_node_level_garbage_falls_back_to_default(players_dir):
    profile = {"id": "x", "gather": {"node_level": "not-a-number"}}
    assert pp.get_gather_node_level(profile) == 8


def test_corrupt_profile_reseeds_instead_of_crashing(players_dir):
    (players_dir / "555000111.json").write_text("")  # upstream shipped a zero-byte file
    profile = pp.load_profile("555000111")
    assert profile["id"] == "555000111"
    assert profile["gather"]["possible_march"] == 4

    (players_dir / "555000111.json").write_text("{not json")
    profile = pp.load_profile("555000111")
    assert profile["id"] == "555000111"


# --- furnace validation ----------------------------------------------------
# The capability gate reads furnace_level, so a misread is a silent behaviour
# change rather than an error. int() alone accepted 7 -> 17 (unlocks Pets and
# Arena for a gate that then walks into locked screens) and 7 -> 1 (gates
# almost everything off).


def test_get_furnace_level_rejects_unusable_values():
    assert pp.get_furnace_level({"furnace_level": 7}) == 7
    assert pp.get_furnace_level({"furnace_level": "7"}) == 7
    assert pp.get_furnace_level({}) is None
    assert pp.get_furnace_level({"furnace_level": None}) is None
    assert pp.get_furnace_level({"furnace_level": "seven"}) is None
    assert pp.get_furnace_level({"furnace_level": 0}) is None
    assert pp.get_furnace_level({"furnace_level": 245}) is None


@pytest.mark.parametrize("raw,expected_level,reason_fragment", [
    ("8", 8, "ok"),                       # normal one-level climb from 7
    ("7", 7, "ok"),                       # same level re-read
    ("17", None, "jumped"),               # the misread that unlocks Pets/Arena
    ("1", None, "decreased"),             # the misread that gates everything off
    ("0", None, "out-of-range"),          # `if furnace:` used to swallow this
    ("31", None, "out-of-range"),         # past base max; FC is not an int
    ("seven", None, "not-a-number"),
    ("", None, "no-read"),
    (None, None, "no-read"),
])
def test_validate_furnace_read(raw, expected_level, reason_fragment):
    profile = {"furnace_level": 7}
    level, reason = pp.validate_furnace_read(profile, raw)
    assert level == expected_level
    assert reason_fragment in reason


def test_first_read_has_no_floor_and_is_flagged_unconfirmed():
    level, reason = pp.validate_furnace_read({"furnace_level": None}, "17")
    assert level == 17, "nothing to compare against, so the read is accepted"
    assert reason == "unconfirmed-first-read"


def test_rejected_read_leaves_the_stored_value_untouched():
    profile = {"furnace_level": 7}
    level, _ = pp.validate_furnace_read(profile, "17")
    assert level is None
    assert profile["furnace_level"] == 7, "validation must not write"


# --- WOS_FURNACE_RESET -----------------------------------------------------
# The monotonic rule means a too-high value can never be corrected downward by
# another read, so there has to be a way out.


def test_furnace_reset_corrects_a_stuck_value_and_clears_stale_locks(
        players_dir, monkeypatch):
    monkeypatch.setenv("WOS_FURNACE_RESET", "7")
    profile = pp.load_profile("555000111")
    profile["furnace_level"] = 17
    profile["observed_locks"] = {
        "pet_treasure": {"furnace_at_observation": 17},   # stamped at the bad value
        "arena": {"furnace_at_observation": 3},           # genuinely below, keep
    }
    assert pp.apply_furnace_reset(profile) == 7
    assert profile["furnace_level"] == 7
    assert "pet_treasure" not in profile["observed_locks"]
    assert "arena" in profile["observed_locks"]


def test_furnace_reset_is_a_no_op_when_unset_or_invalid(players_dir, monkeypatch):
    monkeypatch.delenv("WOS_FURNACE_RESET", raising=False)
    assert pp.apply_furnace_reset({"furnace_level": 7}) is None
    monkeypatch.setenv("WOS_FURNACE_RESET", "banana")
    assert pp.apply_furnace_reset({"furnace_level": 7}) is None
    monkeypatch.setenv("WOS_FURNACE_RESET", "99")
    assert pp.apply_furnace_reset({"furnace_level": 7}) is None


# --- per-account gather flags ----------------------------------------------
# Replaces a hardcoded player id in the task dispatcher.


def test_gather_flags_default_to_todays_else_branch():
    assert pp.get_gather_flags({}) == (False, True)
    assert pp.get_gather_flags({"gather": {}}) == (False, True)


def test_gather_flags_read_from_the_profile():
    profile = {"gather": {"remove_hero": True, "equalize": False}}
    assert pp.get_gather_flags(profile) == (True, False)
