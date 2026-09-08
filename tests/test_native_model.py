"""native/model.py: schema, flatten/slug/rank helpers, OV1 validators, and the
sqlite writers/readers (write_snapshot, write_abort, write_operator, latest,
latest_dynamic, deltas, series, prune_runs).

Every DB test opens its own tmp_path file via model.connect(path=...) so
tests never touch the real db/wos.sqlite and never share state with each
other. Timestamps are fixed strings, never datetime.now(), so a rerun can't
flip a day-boundary assertion.
"""
import os

import pytest

from native import model


def _conn(tmp_path, name="wos.sqlite"):
    return model.connect(str(tmp_path / name))


# A run of fixed UTC stamps, one per synthetic "day", so allowance math
# (ceil(days_since_prev)) is exact instead of clock-dependent.
DAY1_ID, DAY1_AT = "20260101T000000Z", "2026-01-01T00:00:00Z"
DAY2_ID, DAY2_AT = "20260102T000000Z", "2026-01-02T00:00:00Z"
DAY1H_ID, DAY1H_AT = "20260101T120000Z", "2026-01-01T12:00:00Z"  # between day1/day2
DAY4_ID, DAY4_AT = "20260104T000000Z", "2026-01-04T00:00:00Z"  # 3 days after day1


def _identity_doc(power=1000000, gems=500, furnace_ordinal=27, vip=7):
    return {
        "identity": {"id": "P1", "name": "Bob", "state": 100, "state_age_days": 5},
        "progress": {
            "furnace": {"level": 27, "fc": 0, "sub": 0, "ordinal": furnace_ordinal,
                        "upgrading": {"to": 28, "remaining_s": 100}},
            "vip": {"level": vip},
            "power": power,
            "kills": 0,
        },
        "economy": {"gems": gems},
    }


ALL_OK_SECTIONS = {
    "profile": "ok", "hud": "ok", "resources": "ok", "buildings": "ok",
    "research": "ok", "troops": "ok", "heroes": "ok", "gear": "ok",
    "backpack": "ok", "events": "ok", "alliance": "ok",
}


def _write(conn, snapshot_id, taken_at, doc, provenance=None, sections=None, **overrides):
    kwargs = dict(
        player={"id": "P1", "name": "Bob", "state": 100},
        snapshot_id=snapshot_id,
        taken_at=taken_at,
        source="native-app",
        run_dir=f"/tmp/{snapshot_id}",
        status="ok",
        sections=sections or ALL_OK_SECTIONS,
        duration_s=10,
        gems_before=500,
        gems_after=500,
        power_before=1000000,
        power_after=1000000,
        power_rose=False,
        doc=doc,
        provenance=provenance or {},
    )
    kwargs.update(overrides)
    return model.write_snapshot(conn, **kwargs)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_ensure_schema_idempotent_and_user_version(tmp_path):
    conn = _conn(tmp_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == model.SCHEMA_VERSION
    model.ensure_schema(conn)  # second call must not raise or change anything
    assert conn.execute("PRAGMA user_version").fetchone()[0] == model.SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"players", "snapshots", "fields"} <= tables
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert "latest_static" in views


# ---------------------------------------------------------------------------
# flatten / slug / ranks
# ---------------------------------------------------------------------------


def test_flatten_arrays_nesting_none():
    doc = {"a": {"b": [10, 20, None]}, "c": None, "d": {"e": {"f": "x"}}}
    flat = model.flatten(doc)
    assert flat == {"a.b.0": 10, "a.b.1": 20, "a.b.2": None, "c": None, "d.e.f": "x"}


def test_slug():
    assert model.slug("Hello, World!!") == "hello_world"
    assert model.slug("  Storehouse  ") == "storehouse"
    assert model.slug("t1") == "t1"


def test_rank_of_tier():
    assert model.rank_of_tier("green") == 0
    assert model.rank_of_tier("Blue") == 1
    assert model.rank_of_tier("purple") == 2
    assert model.rank_of_tier("Gold") == 3
    assert model.rank_of_tier("red") == 4
    assert model.rank_of_tier("Red T1") == 4
    assert model.rank_of_tier("RedT6") == 9
    assert model.rank_of_tier("nonsense") is None
    assert model.rank_of_tier(None) is None


def test_rank_of_rarity():
    assert model.rank_of_rarity("rare") == 0
    assert model.rank_of_rarity("Epic") == 1
    assert model.rank_of_rarity("MYTHIC") == 2
    assert model.rank_of_rarity("legendary") is None
    assert model.rank_of_rarity(None) is None


# ---------------------------------------------------------------------------
# validate() per class (OV1)
# ---------------------------------------------------------------------------


def test_validate_identity_raises():
    with pytest.raises(ValueError):
        model.validate("identity.id", "P1", "P1", None)
    with pytest.raises(ValueError):
        model.validate("identity.state", 100, 100, None)


def test_validate_none_is_unread_for_every_class():
    assert model.validate("economy.gems", None, 500, None) == (None, "unread")
    assert model.validate("progress.power", None, 1000, None) == (None, "unread")
    assert model.validate("progress.kills", None, 5, None) == (None, "unread")
    assert model.validate("progress.furnace.ordinal", None, 27, 1) == (None, "unread")


def test_validate_first_read_accepts_regardless_of_prev_none():
    assert model.validate("progress.kills", 0, None, None) == (0, "ok")
    assert model.validate("progress.power", 1, None, None) == (1, "ok")
    assert model.validate("progress.furnace.ordinal", 27, None, None) == (27, "ok")


def test_validate_monotonic_step_furnace_ordinal_rollover():
    # 34 -> 35 at 1 day: step 1 <= allowance max(1, ceil(1)) == 1 -> accepted.
    assert model.validate("progress.furnace.ordinal", 35, 34, 1) == (35, "ok")
    # 34 -> 37 at 1 day: step 3 > allowance 1 -> rejected, prior value kept.
    value, status = model.validate("progress.furnace.ordinal", 37, 34, 1)
    assert value == 34
    assert status.startswith("rejected")
    # 34 -> 37 at 3 days: allowance max(1, ceil(3)) == 3 -> accepted.
    assert model.validate("progress.furnace.ordinal", 37, 34, 3) == (37, "ok")


def test_validate_monotonic_step_vip_level():
    assert model.validate("progress.vip.level", 8, 7, 1) == (8, "ok")
    value, status = model.validate("progress.vip.level", 9, 7, 1)
    assert value == 7 and status.startswith("rejected")


def test_validate_monotonic_never_decreases_no_step_cap():
    # A big jump with no elapsed-days argument at all is still fine: monotonic
    # (not monotonic_step) has no allowance to exceed.
    assert model.validate("city.buildings.furnace", 30, 5, None) == (30, "ok")
    value, status = model.validate("city.buildings.furnace", 4, 5, None)
    assert value == 5
    assert status == "rejected: decreased 5 -> 4"


def test_validate_bounded_power_edges():
    assert model.validate("progress.power", 1500, 1000, None) == (1500, "ok")  # 1.5x edge
    assert model.validate("progress.power", 500, 1000, None) == (500, "ok")  # 0.5x edge
    value, status = model.validate("progress.power", 1501, 1000, None)
    assert value == 1000 and status.startswith("rejected")
    value, status = model.validate("progress.power", 499, 1000, None)
    assert value == 1000 and status.startswith("rejected")


def test_validate_volatile_negative_rejected_and_no_prev_comparison():
    value, status = model.validate("economy.gems", -5, 100, None)
    assert value == 100 and status == "rejected: negative value"
    # Volatile never compares to prev otherwise -- a big drop is fine.
    assert model.validate("economy.gems", 0, 100000, None) == (0, "ok")


def test_validate_volatile_text_requires_non_empty():
    assert model.validate("alliance.name", "Foo", None, None) == ("Foo", "ok")
    value, status = model.validate("alliance.name", "", "Foo", None)
    assert value == "Foo" and status == "rejected: empty text"


def test_validate_unknown_path_raises():
    with pytest.raises(ValueError):
        model.validate("not.a.real.path", 1, None, None)


# ---------------------------------------------------------------------------
# write_snapshot
# ---------------------------------------------------------------------------


def test_write_snapshot_writes_a_row_for_every_static_path(tmp_path):
    conn = _conn(tmp_path)
    res = _write(conn, DAY1_ID, DAY1_AT, _identity_doc())
    assert res["written"] == len(model.static_paths())
    rows = conn.execute(
        "SELECT COUNT(*) FROM fields WHERE snapshot_id = ? AND kind = 'static'", (DAY1_ID,)
    ).fetchone()[0]
    assert rows == len(model.static_paths())


def test_write_snapshot_ok_then_rejected_carries_prior_value(tmp_path):
    conn = _conn(tmp_path)
    _write(conn, DAY1_ID, DAY1_AT, _identity_doc(furnace_ordinal=27))
    res = _write(conn, DAY2_ID, DAY2_AT, _identity_doc(furnace_ordinal=20))  # decrease
    assert res["rejected"] >= 1
    row = conn.execute(
        "SELECT value_num, status FROM fields WHERE snapshot_id = ? AND path = ?",
        (DAY2_ID, "progress.furnace.ordinal"),
    ).fetchone()
    assert row["value_num"] == 27  # prior value carried into value_num
    assert row["status"].startswith("rejected")
    # latest_static still reports the last *accepted* value (27), not the
    # rejected reading -- OV2 excludes nothing here since the row's status
    # is 'rejected: ...', not 'unread', so it IS the newest row for the path.
    latest = model.latest(conn, "P1")
    assert latest["progress.furnace.ordinal"]["value_num"] == 27


def test_write_snapshot_carried_vs_unread_depends_on_section_status(tmp_path):
    conn = _conn(tmp_path)
    doc1 = _identity_doc()
    doc1["city"] = {"buildings": {"embassy": 5}}
    _write(conn, DAY1_ID, DAY1_AT, doc1, provenance={"city.buildings.embassy": {"method": "ocr"}})

    # Day 2: buildings reader skipped entirely -> carried, value stays 5.
    doc2 = _identity_doc()
    sections2 = dict(ALL_OK_SECTIONS, buildings="skipped")
    _write(conn, DAY2_ID, DAY2_AT, doc2, sections=sections2)
    row2 = conn.execute(
        "SELECT value_num, status FROM fields WHERE snapshot_id = ? AND path = ?",
        (DAY2_ID, "city.buildings.embassy"),
    ).fetchone()
    assert (row2["value_num"], row2["status"]) == (5, "carried")

    # Day 4: buildings reader ran fully ('ok') but embassy just wasn't in the
    # doc this time -> genuinely 'unread', not carried, no value.
    doc4 = _identity_doc()
    _write(conn, DAY4_ID, DAY4_AT, doc4, sections=ALL_OK_SECTIONS)
    row4 = conn.execute(
        "SELECT value_num, status FROM fields WHERE snapshot_id = ? AND path = ?",
        (DAY4_ID, "city.buildings.embassy"),
    ).fetchone()
    assert (row4["value_num"], row4["status"]) == (None, "unread")


def test_write_snapshot_dynamic_never_carried_and_vanishes(tmp_path):
    conn = _conn(tmp_path)
    doc1 = _identity_doc()
    doc1["events"] = {"state_of_power": {"remaining_s": 1000}}
    _write(conn, DAY1_ID, DAY1_AT, doc1, sections=ALL_OK_SECTIONS)
    dyn1 = model.latest_dynamic(conn, "P1", "events")
    assert "events.state_of_power.remaining_s" in dyn1

    # Day 2: events reader ran ok again but the event ended -- doc has no
    # events.* leaf at all. It must not be carried forward.
    doc2 = _identity_doc()
    _write(conn, DAY2_ID, DAY2_AT, doc2, sections=ALL_OK_SECTIONS)
    row = conn.execute(
        "SELECT COUNT(*) FROM fields WHERE snapshot_id = ? AND kind = 'dynamic'", (DAY2_ID,)
    ).fetchone()[0]
    assert row == 0
    dyn2 = model.latest_dynamic(conn, "P1", "events")
    assert dyn2 == {}  # vanished, not carried


def test_write_snapshot_dynamic_unmatched_name_status(tmp_path):
    conn = _conn(tmp_path)
    doc = _identity_doc()
    doc["heroes"] = {"unknown_hero_x": {"level": 1}}
    prov = {"heroes.unknown_hero_x.level": {"method": "ocr", "status": "unmatched-name"}}
    _write(conn, DAY1_ID, DAY1_AT, doc, provenance=prov)
    row = conn.execute(
        "SELECT status FROM fields WHERE snapshot_id = ? AND path = ?",
        (DAY1_ID, "heroes.unknown_hero_x.level"),
    ).fetchone()
    assert row["status"] == "unmatched-name"


# ---------------------------------------------------------------------------
# latest_static, operator rows, abort rows (OV2)
# ---------------------------------------------------------------------------


def test_latest_static_survives_an_operator_row(tmp_path):
    conn = _conn(tmp_path)
    _write(conn, DAY1_ID, DAY1_AT, _identity_doc(furnace_ordinal=27, gems=500))
    # vip.level is monotonic_step: an operator write is still validated by
    # its class (only identity.state overrides outright), so the step stays
    # within the same day-scaled allowance a real read would face.
    model.write_operator(conn, player_id="P1", path="progress.vip.level", value=8,
                          taken_at=DAY1H_AT, snapshot_id=DAY1H_ID)

    latest = model.latest(conn, "P1")
    # The operator's own field wins latest_static...
    assert latest["progress.vip.level"]["value_num"] == 8
    assert latest["progress.vip.level"]["method"] == "operator"
    # ...and every other path from the real snapshot is still there.
    assert latest["progress.furnace.ordinal"]["value_num"] == 27
    assert latest["economy.gems"]["value_num"] == 500
    assert latest["identity.id"]["value_text"] == "P1"


def test_write_abort_has_no_fields_and_is_excluded_from_latest(tmp_path):
    conn = _conn(tmp_path)
    _write(conn, DAY1_ID, DAY1_AT, _identity_doc(gems=500))
    model.write_abort(
        conn, player_id="P1", snapshot_id=DAY2_ID, taken_at=DAY2_AT, source="native-app",
        run_dir="/tmp/aborted", reason="gems dropped", sections={"hud": "ok"},
        gems_before=500, power_before=1000000,
    )
    field_count = conn.execute(
        "SELECT COUNT(*) FROM fields WHERE snapshot_id = ?", (DAY2_ID,)
    ).fetchone()[0]
    assert field_count == 0
    snap = conn.execute("SELECT status, abort_reason, doc FROM snapshots WHERE id = ?", (DAY2_ID,)).fetchone()
    assert snap["status"] == "aborted"
    assert snap["abort_reason"] == "gems dropped"
    assert snap["doc"] == "{}"
    # latest_static must still report the pre-abort value, unaffected.
    latest = model.latest(conn, "P1")
    assert latest["economy.gems"]["value_num"] == 500


def test_write_operator_rejects_unknown_path_and_bad_value(tmp_path):
    conn = _conn(tmp_path)
    _write(conn, DAY1_ID, DAY1_AT, _identity_doc())
    with pytest.raises(ValueError):
        model.write_operator(conn, player_id="P1", path="not.a.real.path", value=1,
                              taken_at=DAY2_AT, snapshot_id=DAY2_ID)
    with pytest.raises(ValueError):
        model.write_operator(conn, player_id="P1", path="heroes.jessie.level", value=1,
                              taken_at=DAY2_AT, snapshot_id=DAY2_ID)
    with pytest.raises(ValueError):
        model.write_operator(conn, player_id="P1", path="economy.gems", value=-5,
                              taken_at=DAY2_AT, snapshot_id=DAY2_ID)
    # None of the rejected calls should have left a row behind.
    assert conn.execute("SELECT COUNT(*) FROM snapshots WHERE id = ?", (DAY2_ID,)).fetchone()[0] == 0


def test_write_operator_state_transfer_escape(tmp_path):
    conn = _conn(tmp_path)
    _write(conn, DAY1_ID, DAY1_AT, _identity_doc())
    model.write_operator(conn, player_id="P1", path="identity.state", value="9999",
                          taken_at=DAY2_AT, snapshot_id=DAY2_ID)
    latest = model.latest(conn, "P1")
    assert latest["identity.state"]["value_num"] == 9999


# ---------------------------------------------------------------------------
# deltas / series
# ---------------------------------------------------------------------------


def test_deltas_skip_operator_snapshots(tmp_path):
    conn = _conn(tmp_path)
    doc1 = _identity_doc()
    doc1["city"] = {"buildings": {"furnace": 10}}
    _write(conn, DAY1_ID, DAY1_AT, doc1, provenance={"city.buildings.furnace": {"method": "ocr"}})

    model.write_operator(conn, player_id="P1", path="city.buildings.furnace", value=12,
                          taken_at=DAY1H_AT, snapshot_id=DAY1H_ID)

    doc2 = _identity_doc()
    doc2["city"] = {"buildings": {"furnace": 15}}
    _write(conn, DAY2_ID, DAY2_AT, doc2, provenance={"city.buildings.furnace": {"method": "ocr"}})

    result = model.deltas(conn, "P1", DAY2_ID)
    prev, cur, delta = result["city.buildings.furnace"]
    assert (prev, cur, delta) == (10, 15, 5)  # compares against day1, not the operator row


def test_series_ordering_across_two_ids(tmp_path):
    conn = _conn(tmp_path)
    doc1 = _identity_doc()
    doc1["city"] = {"buildings": {"furnace": 10}}
    _write(conn, DAY1_ID, DAY1_AT, doc1, provenance={"city.buildings.furnace": {"method": "ocr"}})
    doc2 = _identity_doc()
    doc2["city"] = {"buildings": {"furnace": 11}}
    _write(conn, DAY2_ID, DAY2_AT, doc2, provenance={"city.buildings.furnace": {"method": "ocr"}})

    points = model.series(conn, "P1", "city.buildings.furnace")
    assert [p[0] for p in points] == [DAY1_ID, DAY2_ID]
    assert [p[2] for p in points] == [10, 11]


# ---------------------------------------------------------------------------
# prune_runs (pure filesystem)
# ---------------------------------------------------------------------------


def test_prune_runs_keeps_current_and_recent_removes_old_both_styles(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    old_new_style = root / "20200101T000000Z"
    old_legacy_style = root / "2020-01-01-1200"
    recent = root / model.new_snapshot_id()
    current_but_old = root / "20200101T010000Z"
    not_a_run = root / "notes.txt"
    for path in (old_new_style, old_legacy_style, recent, current_but_old):
        path.mkdir()
    not_a_run.write_text("keep me")

    removed = model.prune_runs(str(root), keep_days=2, current="20200101T010000Z")

    assert sorted(removed) == sorted([old_new_style.name, old_legacy_style.name])
    remaining = set(os.listdir(root))
    assert remaining == {recent.name, current_but_old.name, not_a_run.name}


def test_prune_runs_missing_root_returns_empty(tmp_path):
    assert model.prune_runs(str(tmp_path / "does-not-exist")) == []


def test_manual_paths_carry_forward_across_snapshots(tmp_path):
    from native import model
    conn = model.connect(str(tmp_path / "t.sqlite"))
    conn.execute("INSERT INTO players (id, is_main, first_seen, last_seen) VALUES ('p', 1, 't', 't')")
    model.write_operator(conn, player_id="p", path="manual.hero_generation", value="2",
                         taken_at="2026-09-08T12:00:00Z", snapshot_id="20260908T120000Z")
    model.write_snapshot(conn, player={"id": "p"}, snapshot_id="20260909T120000Z", taken_at="2026-09-09T12:00:00Z",
                         source="native-app", run_dir="r", status="ok", sections={"hud": "ok"}, duration_s=1,
                         gems_before=1, gems_after=1, power_before=1, power_after=1, power_rose=0,
                         doc={"progress": {"power": 1}}, provenance={"progress.power": {"raw": "1"}})
    row = conn.execute("SELECT status, value_num FROM fields WHERE snapshot_id='20260909T120000Z' AND path='manual.hero_generation'").fetchone()
    assert (row["status"], row["value_num"]) == ("carried", 2)
    assert model.latest(conn, "p")["manual.hero_generation"]["value_num"] == 2


def test_new_snapshot_id_is_strictly_increasing_within_a_second():
    from datetime import datetime, timezone
    from native import model
    t = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    model._LAST_ID["value"] = None   # earlier tests in this process issued real-clock ids
    a, b, c = model.new_snapshot_id(t), model.new_snapshot_id(t), model.new_snapshot_id(t)
    assert (a, b, c) == ("20260908T120000Z", "20260908T120001Z", "20260908T120002Z")
