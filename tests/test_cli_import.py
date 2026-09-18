"""native/cli_import.py: live `wos` CLI output -> chief sheet snapshot.

Synthetic, account-free inputs shaped like `wos profile --out` and
`wos hospital status`; every DB test uses its own tmp_path database.
"""
import json
from datetime import datetime

import pytest

from native import cli_import, model
from native.report import doctor
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


def _hospital(player_id=7, finished_at="2026-01-02T00:00:40+00:00", mode="live", healing=None):
    return {"observation": {"mode": mode, "player_id": player_id, "finished_at": finished_at},
            "gems": 9073, "wounded": {"10900": 119, "30500": 4}, "healing": healing,
            "resources": {"102": 5, "192": 50}}


def _conn(tmp_path):
    return model.connect(str(tmp_path / "wos.sqlite"))


def _field(conn, snapshot_id, path):
    return conn.execute("SELECT * FROM fields WHERE snapshot_id = ? AND path = ?", (snapshot_id, path)).fetchone()


def test_build_maps_same_meaning_fields_only():
    player, doc, prov, _, gems, notes = cli_import.build(_profile(), "p.json", _hospital(), "h.json")
    flat = model.flatten(doc)
    assert player == {"id": "7", "name": "Bob", "state": 100}
    assert flat["progress.furnace.level"] == flat["city.buildings.furnace"] == 28
    assert flat["economy.stamina.value"] == 183 and flat["economy.stamina.cap"] == 200
    assert flat["alliance.rank"] == "R4" and flat["alliance.state_rank"] == 3
    assert flat["troops.wounded.value"] == 123 and gems == flat["economy.gems"] == 9073
    assert (flat["gear.chief.pants.tier"], flat["gear.chief.pants.rank"], flat["gear.chief.pants.stars"]) == ("purple", 2, 0)
    # an unverified rarity leaves the whole slot alone: fresh stars under a
    # carried old tier would read as a regression
    assert not any(p.startswith("gear.chief.cane.") for p in flat)
    assert notes == ["gear cane: rarity 'Mythic' has no verified colour, slot not written"]
    # different meaning on the sheet: not mapped
    assert not any(p.startswith(("economy.resources.", "gear.charms.")) for p in flat)
    assert prov["gear.chief.pants.stars"]["frame"] == "p.json#op10476"
    assert all(v["method"] == "protocol" and v["exact"] == 1 for v in prov.values())


@pytest.mark.parametrize("profile, hospital, match", [
    (_profile(mode="offline"), None, "profile observation mode"),
    (_profile(finished_at=None), None, "no player_id or finished_at"),
    (_profile(finished_at="2026-01-02T00:00:00"), None, "no UTC offset"),
    (_profile(), _hospital(player_id=8), "different player"),
    (_profile(), _hospital(mode="offline"), "hospital observation mode"),
    (_profile(), _hospital(finished_at="2026-01-01T00:00:00+00:00"), "not the same moment"),
])
def test_build_refusals(profile, hospital, match):
    with pytest.raises(cli_import.ImportRefused, match=match):
        cli_import.build(profile, "p.json", hospital, "h.json")


def test_build_skips_fire_crystal_furnace_and_wounded_mid_heal():
    _, doc, _, observed_at, _, notes = cli_import.build(
        _profile(furnace=31, finished_at="2026-01-02T04:00:00+04:00"), "p.json",
        _hospital(healing={"soldiers": {"10900": 24}}), "h.json")
    assert "furnace" not in doc.get("progress", {})
    assert "troops" not in doc and "a heal was running" in notes[-1]
    assert observed_at.isoformat() == "2026-01-02T00:00:00+00:00"


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
    with pytest.raises(cli_import.ImportRefused, match="no main account"):
        cli_import.write(conn, _profile(), "p.json")
    confirm_main(conn, "9")
    with pytest.raises(cli_import.ImportRefused, match="not the confirmed main"):
        cli_import.write(conn, _profile(), "p.json")
    confirm_main(conn, "7")
    cli_import.write(conn, _profile(), "p.json")
    with pytest.raises(cli_import.ImportRefused, match="not newer"):
        cli_import.write(conn, _profile(), "p.json")


def test_imports_never_count_as_reader_runs_for_doctor(tmp_path):
    conn = _conn(tmp_path)
    confirm_main(conn, "7")
    for second in ("00", "01", "02"):
        cli_import.write(conn, _profile(finished_at=f"2026-01-02T00:00:{second}+00:00"), "p.json")
    assert doctor(conn, "7", kb_freshness=[]).startswith("doctor: only 0 snapshot(s)")


def _heroes(player_id=7, finished_at="2026-01-03T00:00:00+00:00", mode="live"):
    return {"format": "wos-hero-roster-v1", "source": "live",
            "observation": {"mode": mode, "player_id": player_id, "finished_at": finished_at},
            "heroes": [{"id": 50007, "name": "Ling Xue", "rarity": "epic", "level": 71, "star_row": 23,
                        "stars": 3, "star_step": 5, "exploration_skills": [3, 4, 3],
                        "expedition_skills": [4, 3, None]}]}


def test_build_heroes_maps_protocol_roster_and_refuses_offline_reads():
    player, doc, prov, _ = cli_import.build_heroes(_heroes(), "r.json")
    hero = doc["heroes"]["ling_xue"]
    assert player == {"id": "7"}
    assert (hero["level"], hero["stars"], hero["star_step"], hero["rarity"]) == (71, 3, 5, "epic")
    assert (hero["exploration_skill_2"], hero["expedition_skill_1"]) == (4, 4)
    assert "expedition_skill_3" not in hero  # not configured for this hero
    assert prov["heroes.ling_xue.level"]["method"] == "protocol"
    with pytest.raises(cli_import.ImportRefused, match="not 'live'"):
        cli_import.build_heroes(_heroes(mode=None), "r.json")


def test_write_heroes_is_its_own_snapshot_with_only_heroes_ok(tmp_path):
    conn = _conn(tmp_path)
    confirm_main(conn, "7")
    cli_import.write(conn, _profile(), "p.json")
    summary = cli_import.write_heroes(conn, _heroes(), "r.json")
    sid = summary["snapshot_id"]
    snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (sid,)).fetchone()
    assert (sid, snap["source"]) == ("20260103T000000Z", "wos-cli")
    sections = json.loads(snap["sections"])
    assert sections["heroes"] == "ok" and {v for k, v in sections.items() if k != "heroes"} == {"skipped"}
    assert _field(conn, sid, "progress.furnace.level")["status"] == "carried"
    latest = model.latest_dynamic(conn, "7", "heroes")
    assert latest["heroes.ling_xue.expedition_skill_1"]["value_num"] == 4


def test_main_needs_exactly_one_of_profile_or_heroes(tmp_path):
    with pytest.raises(SystemExit):
        cli_import.main(["--db", str(tmp_path / "x.sqlite")])
    with pytest.raises(SystemExit):
        cli_import.main(["--heroes", "r.json", "--hospital", "h.json", "--db", str(tmp_path / "x.sqlite")])


# The wos CLI's store table, reduced to the columns the importer reads.
_CLI_READS = ("CREATE TABLE cli_reads (id TEXT PRIMARY KEY, command TEXT, kind TEXT, player_id TEXT, "
              "mode TEXT, finished_at TEXT, doc TEXT)")


def _stored(conn, command, doc):
    obs = doc["observation"]
    read_id = f"{datetime.fromisoformat(obs['finished_at']).strftime('%Y%m%dT%H%M%SZ')}-{command.replace(' ', '-')}"
    conn.execute(_CLI_READS.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
    conn.execute("INSERT INTO cli_reads VALUES (?, ?, 'read', ?, ?, ?, ?)",
                 (read_id, command, str(obs["player_id"]), obs["mode"], obs["finished_at"], json.dumps(doc)))
    conn.commit()
    return read_id


def test_from_db_matches_the_file_import(tmp_path):
    files = model.connect(str(tmp_path / "files.sqlite"))
    stored = model.connect(str(tmp_path / "stored.sqlite"))
    for conn in (files, stored):
        confirm_main(conn, "7")
    by_file = cli_import.write(files, _profile(), "p.json", _hospital(), "h.json")
    _stored(stored, "profile", _profile())
    hospital_id = _stored(stored, "hospital status", _hospital())
    by_db = cli_import.write_from_db(stored, "profile")
    assert (by_db["snapshot_id"], by_db["doc"]) == (by_file["snapshot_id"], by_file["doc"])
    frame = _field(stored, by_db["snapshot_id"], "economy.gems")["frame"]
    assert frame == f"db:cli_reads/{hospital_id}"


def test_from_db_leaves_out_a_hospital_read_from_another_moment(tmp_path):
    conn = _conn(tmp_path)
    confirm_main(conn, "7")
    _stored(conn, "profile", _profile())
    _stored(conn, "hospital status", _hospital(finished_at="2026-01-02T01:00:00+00:00"))
    summary = cli_import.write_from_db(conn, "profile")
    assert "economy" not in summary["doc"] or "gems" not in summary["doc"]["economy"]
    assert summary["notes"][0].startswith("newest hospital read ")


def test_from_db_heroes_and_refusals(tmp_path):
    conn = _conn(tmp_path)
    confirm_main(conn, "7")
    with pytest.raises(cli_import.ImportRefused, match="cli_reads is missing"):
        cli_import.write_from_db(conn, "heroes")
    _stored(conn, "profile", _profile())
    with pytest.raises(cli_import.ImportRefused, match="no stored live heroes read"):
        cli_import.write_from_db(conn, "heroes")
    _stored(conn, "heroes", _heroes())
    summary = cli_import.write_from_db(conn, "heroes")
    assert summary["snapshot_id"] == "20260103T000000Z"


def test_from_db_profile_takes_a_same_moment_heroes_read_into_one_snapshot(tmp_path):
    # One `wos snapshot` login: profile and heroes finish in the same second, so a
    # second chief-sheet snapshot for heroes could never be newer.
    conn = _conn(tmp_path)
    confirm_main(conn, "7")
    _stored(conn, "profile", _profile())
    heroes_id = _stored(conn, "heroes", _heroes(finished_at="2026-01-02T00:00:00+00:00"))
    summary = cli_import.write_from_db(conn, "profile")
    sid = summary["snapshot_id"]
    assert sid == "20260102T000000Z" and summary["doc"]["heroes"]["ling_xue"]["level"] == 71
    sections = json.loads(conn.execute("SELECT sections FROM snapshots WHERE id = ?", (sid,)).fetchone()["sections"])
    assert sections["heroes"] == "ok"
    assert _field(conn, sid, "heroes.ling_xue.level")["frame"] == f"db:cli_reads/{heroes_id}"
    assert model.latest_dynamic(conn, "7", "heroes")["heroes.ling_xue.level"]["value_num"] == 71


def test_from_db_profile_leaves_out_a_heroes_read_from_another_moment(tmp_path):
    conn = _conn(tmp_path)
    confirm_main(conn, "7")
    _stored(conn, "profile", _profile())
    _stored(conn, "heroes", _heroes(finished_at="2026-01-03T00:00:00+00:00"))
    summary = cli_import.write_from_db(conn, "profile")
    assert "heroes" not in summary["doc"]
    assert any(note.startswith("newest heroes read ") for note in summary["notes"])


def test_heroes_from_another_player_are_refused():
    with pytest.raises(cli_import.ImportRefused, match="different player"):
        cli_import.build(_profile(), "p.json", heroes=_heroes(player_id=8), heroes_path="r.json")


def test_main_from_db_excludes_file_inputs(tmp_path):
    with pytest.raises(SystemExit):
        cli_import.main(["--from-db", "profile", "--profile", "p.json", "--db", str(tmp_path / "x.sqlite")])
