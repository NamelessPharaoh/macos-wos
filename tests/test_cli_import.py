"""native/cli_import.py: live `wos` CLI output -> chief sheet snapshot.

Synthetic, account-free inputs shaped like `wos profile --out` and
`wos hospital status`; every DB test uses its own tmp_path database.
"""
import pytest

from native import cli_import, model
from native.snapshot import confirm_main


def _profile(player_id=7, finished_at="2026-01-02T00:00:00+00:00", furnace=28, mode="live", gear=None):
    return {
        "observation": {"mode": mode, "player_id": player_id, "finished_at": finished_at},
        "profile": {"name": "Bob", "player_id": player_id, "state": 100, "furnace_level": furnace,
                    "power": {"total": 1_200_000}},
        "alliance": {"rank": 4, "evidence": {"operation": 5171},
                     "alliance": {"name": "Club", "abbr": "CLB", "leader_name": "Ann", "power": 9_000_000,
                                  "power_rank": 3, "lv": 11, "count": 99, "member_max": 100}},
        "chief_gear": {"evidence": {"operation": 10476}, "items": gear if gear is not None else [
            {"slot": "pants", "rarity": "Epic", "stars": 0, "name": "Frost Pacer",
             "charms": [{"name": "Protection Charm", "level": 2}]},
            {"slot": "cane", "rarity": "Mythic", "stars": 1, "name": "Cudgel"},
        ]},
        "recoverable_resources": {"evidence": {"operation": 1401},
                                  "stamina": {"current": 183, "recovery_cap": 200}},
    }


def _hospital(player_id=7):
    return {"observation": {"player_id": player_id}, "gems": 9073,
            "wounded": {"10900": 119, "30500": 4}, "resources": {"102": 5, "192": 50}}


def _conn(tmp_path):
    return model.connect(str(tmp_path / "wos.sqlite"))


def _field(conn, snapshot_id, path):
    return conn.execute("SELECT * FROM fields WHERE snapshot_id = ? AND path = ?", (snapshot_id, path)).fetchone()


def test_build_maps_same_meaning_fields_only():
    player, doc, prov, _, gems = cli_import.build(_profile(), "p.json", _hospital(), "h.json")
    flat = model.flatten(doc)
    assert player == {"id": "7", "name": "Bob", "state": 100}
    assert flat["progress.furnace.level"] == flat["city.buildings.furnace"] == 28
    assert flat["economy.stamina.value"] == 183 and flat["economy.stamina.cap"] == 200
    assert flat["alliance.rank"] == "R4" and flat["alliance.state_rank"] == 3
    assert flat["troops.wounded.value"] == 123 and gems == flat["economy.gems"] == 9073
    assert (flat["gear.chief.pants.tier"], flat["gear.chief.pants.rank"], flat["gear.chief.pants.stars"]) == ("purple", 2, 0)
    # an unverified rarity keeps its stars but never guesses a colour
    assert flat["gear.chief.cane.stars"] == 1 and "gear.chief.cane.tier" not in flat
    # different meaning on the sheet: not mapped
    assert not any(p.startswith(("economy.resources.", "gear.charms.")) for p in flat)
    assert prov["gear.chief.pants.stars"]["frame"] == "p.json#op10476"
    assert all(v["method"] == "protocol" and v["exact"] == 1 for v in prov.values())


def test_build_refusals_and_fire_crystal_furnace():
    with pytest.raises(cli_import.ImportRefused):
        cli_import.build(_profile(mode="offline"), "p.json")
    with pytest.raises(cli_import.ImportRefused):
        cli_import.build(_profile(), "p.json", _hospital(player_id=8), "h.json")
    _, doc, _, _, _ = cli_import.build(_profile(furnace=31), "p.json")
    assert "furnace" not in doc.get("progress", {})


def test_write_carries_unmapped_and_accepts_tier_up_star_reset(tmp_path):
    conn = _conn(tmp_path)
    confirm_main(conn, "7")
    model.write_snapshot(
        conn, player={"id": "7"}, snapshot_id="20260101T000000Z", taken_at="2026-01-01T00:00:00Z",
        source="native-app", run_dir=None, status="ok", sections={"hud": "ok", "gear": "ok"},
        duration_s=1, gems_before=1, gems_after=1, power_before=1_000_000, power_after=1_000_000,
        power_rose=False, provenance={},
        doc={"progress": {"power": 1_000_000, "vip": {"level": 8}},
             "gear": {"chief": {"pants": {"tier": "blue", "rank": 1, "stars": 3}}}})

    summary = cli_import.write(conn, _profile(), "p.json", _hospital(), "h.json")

    sid = summary["snapshot_id"]
    assert sid == "20260102T000000Z" and summary["counts"]["rejected"] == 0
    snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (sid,)).fetchone()
    assert (snap["source"], snap["gems_after"], snap["power_after"]) == ("wos-cli", 9073, 1_200_000)
    assert _field(conn, sid, "progress.vip.level")["status"] == "carried"
    stars = _field(conn, sid, "gear.chief.pants.stars")
    assert (stars["value_num"], stars["status"], stars["method"]) == (0, "ok", "protocol")
    assert _field(conn, sid, "progress.furnace.ordinal")["method"] == "derived"


def test_write_refuses_other_account_and_stale_observation(tmp_path):
    conn = _conn(tmp_path)
    confirm_main(conn, "9")
    with pytest.raises(cli_import.ImportRefused, match="not the confirmed main"):
        cli_import.write(conn, _profile(), "p.json")
    confirm_main(conn, "7")
    cli_import.write(conn, _profile(), "p.json")
    with pytest.raises(cli_import.ImportRefused, match="not newer"):
        cli_import.write(conn, _profile(), "p.json")
