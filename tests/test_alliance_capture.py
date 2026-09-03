"""Alliance snapshot: wiring two ROIs that existed unused since the port.

Fixtures use invented alliance names on purpose. A real one names other
players in a public repo, which is the norm .gitignore:14 sets for player
ids ("do not repeat").

core/capability.py already declared alliance_member_count as a state key and
treated it as unreadable, so every condition on it failed open. This is what
makes it readable — and it runs inside player_initialization, so the one thing
it must never do is raise.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from core import capability
from core import player_profile as pp
from usecases import alliance as al


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    d = tmp_path / "players"
    d.mkdir()
    (d / "example.json").write_text(json.dumps(
        {"id": "0", "alliance": {"name": "xxx"}, "furnace_level": None}))
    monkeypatch.setattr(pp, "PLAYERS_DIR", str(d))
    monkeypatch.setattr(pp, "EXAMPLE_PATH", str(d / "example.json"))
    return d


def _ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat(timespec="seconds")


# --- staleness -------------------------------------------------------------


@pytest.mark.parametrize("alliance,stale,why", [
    ({}, True, "never captured"),
    ({"name": "xxx"}, True, "example.json's seed value, not a real alliance"),
    ({"name": ""}, True, "blank"),
    ({"name": "[TST]FakeAlliance"}, True, "no timestamp means unknown age"),
    ({"name": "[TST]FakeAlliance", "last_verified": "not-a-date"}, True, "unparseable"),
    ({"name": "[TST]FakeAlliance", "last_verified": _ago(hours=1)}, False, "fresh"),
    ({"name": "[TST]FakeAlliance", "last_verified": _ago(hours=25)}, True, "past the window"),
])
def test_alliance_staleness(alliance, stale, why):
    assert pp.alliance_state_is_stale({"alliance": alliance}) is stale, why


# --- persistence -----------------------------------------------------------


def test_blank_name_never_overwrites_a_good_one(profile_dir):
    """The capture path yields None when the screen would not read. Writing that
    would make the gate forget an alliance the account is still in."""
    profile = pp.load_profile("111")
    pp.set_alliance_state(profile, "[TST]FakeAlliance", 52)
    assert pp.set_alliance_state(profile, None, 9) is False
    assert pp.set_alliance_state(profile, "", 9) is False
    assert profile["alliance"]["name"] == "[TST]FakeAlliance"


def test_snapshot_round_trips(profile_dir):
    profile = pp.load_profile("111")
    assert pp.set_alliance_state(profile, "[TST]FakeAllianceName", 52)
    saved = json.loads((profile_dir / "111.json").read_text())
    assert saved["alliance"]["name"] == "[TST]FakeAllianceName"
    assert saved["alliance"]["member_count"] == 52
    assert saved["alliance"]["last_verified"]


# --- capture ---------------------------------------------------------------


def _arm(monkeypatch, named, arrived=True):
    monkeypatch.setattr(al, "open_alliance", lambda: arrived)
    monkeypatch.setattr(al, "ensure_screen", lambda *a, **k: arrived)
    monkeypatch.setattr(al, "req_text_named", lambda *a, **k: named)


def test_no_player_id_is_a_no_op(profile_dir, monkeypatch):
    _arm(monkeypatch, {})
    assert al.capture_alliance_state(None) is False


def test_a_fresh_snapshot_is_not_re_read(profile_dir, monkeypatch):
    """Costs a recalibrate + tap + verify round-trip, for data that moves monthly."""
    profile = pp.load_profile("111")
    pp.set_alliance_state(profile, "[TST]FakeAlliance", 52)

    def explode(*a, **k):
        raise AssertionError("must not navigate when the snapshot is fresh")

    monkeypatch.setattr(al, "open_alliance", explode)
    assert al.capture_alliance_state("111") is False


def test_captures_name_and_current_member_count(profile_dir, monkeypatch):
    _arm(monkeypatch, {
        "Home.Alliance.Name": [{"text": "[TST]FakeAllianceName", "score": 1.0}],
        "Home.Alliance.MemberCount": [{"text": "52/52", "score": 1.0}],
    })
    assert al.capture_alliance_state("111") is True
    saved = json.loads((profile_dir / "111.json").read_text())
    assert saved["alliance"]["name"] == "[TST]FakeAllianceName"
    assert saved["alliance"]["member_count"] == 52, "current, not the capacity"


def test_unreadable_name_keeps_the_stored_snapshot(profile_dir, monkeypatch):
    profile = pp.load_profile("111")
    pp.set_alliance_state(profile, "[TST]FakeAlliance", 52)
    _arm(monkeypatch, {"Home.Alliance.Name": [], "Home.Alliance.MemberCount": []})
    assert al.capture_alliance_state("111", force=True) is False
    saved = json.loads((profile_dir / "111.json").read_text())
    assert saved["alliance"]["name"] == "[TST]FakeAlliance"


def test_a_failed_read_never_raises(profile_dir, monkeypatch):
    """This runs inside player_initialization. A blown alliance read is not a
    reason to abandon the whole pass."""
    def boom(*a, **k):
        raise RuntimeError("OCR server died")

    monkeypatch.setattr(al, "open_alliance", boom)
    assert al.capture_alliance_state("111") is False


def test_unreachable_screen_keeps_the_stored_snapshot(profile_dir, monkeypatch):
    _arm(monkeypatch, {}, arrived=False)
    assert al.capture_alliance_state("111") is False


# --- the gate now sees it --------------------------------------------------


def test_member_count_reaches_the_gate():
    """feature-unlocks.json gates Alliance Mobilization on >= 15 members. Before
    this, that condition was permanently UNKNOWN and failed open."""
    captured = capability.account_state(
        {"alliance": {"name": "[TST]FakeAlliance", "member_count": 52}})
    assert captured["alliance_member_count"] == 52
    assert captured["alliance_name"] == "[TST]FakeAlliance"

    seed = capability.account_state({"alliance": {"name": "xxx"}})
    assert seed["alliance_member_count"] is None
    assert seed["alliance_name"] is None, "the seed literal is not an alliance"
