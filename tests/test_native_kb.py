import json
import os
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from native import kb, model

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "knowledge", "kbdir")


@pytest.fixture(scope="module")
def k():
    return kb.load(FIX)


def test_load_reads_every_table(k):
    assert set(k) >= {"buildings", "training", "stats", "research", "events"}


def test_building_cost_and_time(k):
    c = kb.building_cost("furnace", 27, 28, kb=k)
    assert c["meat"] == 190_000_000 and c["iron"] == 9_900_000
    assert c.get("fire_crystals", 0) == 0
    assert kb.building_cost("furnace", 27, 27, kb=k) == {}
    assert kb.building_time("furnace", 27, 28, kb=k) == 2_515_920
    assert kb.building_time("furnace", 27, 28, speed_bonus=1.0, kb=k) == 1_257_960
    with pytest.raises(KeyError):
        kb.building_cost("furnace", 27, 99, kb=k)


def test_building_cost_names_the_building_absent_upstream(k):
    """D-T1: storehouse/warehouse/war_academy aren't in wosnerds at all --
    the KeyError must name the building and point at the popup, not just
    say 'not in the knowledge base' like an ordinary missing level."""
    with pytest.raises(KeyError) as exc:
        kb.building_cost("storehouse", 26, 27, kb=k)
    msg = str(exc.value)
    assert "storehouse" in msg and "popup" in msg
    with pytest.raises(KeyError) as exc2:
        kb.building_time("warehouse", 1, 2, kb=k)
    assert "popup" in str(exc2.value)


def test_prerequisites_unmet_only(k):
    sheet = {"embassy": 26, "research_center": 27, "infantry_camp": 24, "lancer_camp": 26, "marksman_camp": 25,
             "command_center": 1, "coal_mine": 3, "hunters_hut": 6, "infirmary": 1, "iron_mine": 5, "sawmill": 1, "shelter": 3}
    unmet, assumed = kb.prerequisites("furnace", 28, sheet, kb=k)
    assert ("embassy", 27, 26) in unmet
    assert not any(b == "research_center" for b, _, _ in unmet)
    unmet2, _ = kb.prerequisites("furnace", 28, {**sheet, "infirmary": None}, kb=k)
    assert ("infirmary", 1, None) in unmet2
    assert assumed == []  # every UNTRACKED_ASSUMED_MET building is tracked (and satisfied) in this sheet


def test_prerequisites_assumes_untracked_buildings_met(k):
    """D-T1: a building the sheet has no reading for at all (not merely
    None) is dropped from `unmet` and reported in `assumed` instead, so the
    planner prints one line rather than five permanent unmet rows."""
    sheet = {"embassy": 27, "research_center": 27, "infantry_camp": 24, "lancer_camp": 26, "marksman_camp": 25,
             "command_center": 1, "infirmary": 1}  # coal_mine, hunters_hut, iron_mine, sawmill, shelter absent
    unmet, assumed = kb.prerequisites("furnace", 28, sheet, kb=k)
    assert unmet == []
    assert assumed == [("coal_mine", 3), ("hunters_hut", 6), ("iron_mine", 5), ("sawmill", 1), ("shelter", 3)]
    # with the override off, the same gaps show up as ordinary unmet rows with have=None
    unmet_strict, assumed_strict = kb.prerequisites("furnace", 28, sheet, kb=k, assume_untracked_met=False)
    assert assumed_strict == []
    assert ("coal_mine", 3, None) in unmet_strict


def test_prerequisites_on_an_overlay_row_reports_unknown():
    """D-T2 1e: a Fire Crystal furnace row from the local cross-check
    overlay carries a `source` marker and no real prerequisite data (D-T2
    hard-codes `prerequisites: {}` there rather than parsing text that
    breaks on the live pages). `([], [])` would read as "all met", which is
    not known -- the overlay branch must say so instead of going silent."""
    overlay_kb = {"buildings": {"furnace": {"31": {"source": "whiteoutdata", "prerequisites": {}, "meat": 1}}}}
    unmet, assumed = kb.prerequisites("furnace", 31, {}, kb=overlay_kb)
    assert unmet == [] and assumed == ["unknown: overlay row"]


def test_research_path_expands_prerequisites_once(k):
    steps = kb.research_path("tooling_up_i", 2, {}, kb=k)
    assert [(s.node, s.level) for s in steps] == [("tooling_up_i", 1), ("tooling_up_i", 2)]
    assert steps[1].cost["steel"] == 220 and steps[1].seconds == 40
    assert kb.research_path("tooling_up_i", 2, {"tooling_up_i": 2}, kb=k) == []


def test_research_path_cycle_guard():
    """B8: a two-node fixture where each requires the other must raise
    ValueError naming the node and level it re-entered, not recurse forever."""
    cyclic_kb = {
        "research": {
            "a": {"levels": {"1": {"cost": {}, "seconds": 1, "requires_research": {"b": 1}}}},
            "b": {"levels": {"1": {"cost": {}, "seconds": 1, "requires_research": {"a": 1}}}},
        }
    }
    with pytest.raises(ValueError, match=r"research prerequisite cycle at (a|b)@1"):
        kb.research_path("a", 1, {}, kb=cyclic_kb)


def test_training_cost_time_and_power(k):
    c = kb.training_cost("infantry", 9, 100, kb=k)
    assert c == {"meat": 139_400, "wood": 104_600, "coal": 24_400, "iron": 5_100}
    assert kb.training_time("infantry", 9, 100, kb=k) == 13_100
    assert kb.troop_power("infantry", 9, kb=k) == 50
    assert kb.power_gain("training", troop_type="infantry", tier=9, count=100, kb=k) == 5_000
    assert kb.power_gain("research", node="tooling_up_i", level=2, kb=k) == 2_000


def test_power_gain_building_is_none_without_overlay(k):
    """A5/B12: no open source carries building power outside the M2
    overlay, so this must be None -- never a silently-wrong 0."""
    assert kb.power_gain("building", name="furnace", from_level=27, to_level=28, kb=k) is None


def test_power_gain_building_is_none_for_a_row_with_unknown_power():
    """Finding 1 (2026-09-09 fix round): a whiteoutdata column rename would
    have left an overlay row with power=0 before this fix; now a row that
    genuinely doesn't know its power carries power=None, and power_gain must
    return None for it rather than treating the unknown as a real 0 (the
    contract native/kb.py's own docstring states twice)."""
    broken_kb = {"buildings": {"furnace": {"31": {"meat": 1, "wood": 1, "coal": 1, "iron": 1,
                                                   "fire_crystals": 132, "refined_fire_crystals": 0,
                                                   "seconds": 1, "power": None}}}}
    assert kb.power_gain("building", name="furnace", from_level=30, to_level=31, kb=broken_kb) is None


def test_building_cost_raises_rather_than_dropping_an_unknown_resource():
    """Finding 1 (2026-09-09 fix round): before this fix, an overlay row
    with fire_crystals=None (the parser never found the column) simply
    vanished from the cost dict -- days_to() then reported a fraction of a
    day for an upgrade that actually needs 132 Fire Crystals. An unknown
    resource must be a loud failure, not a silently cheaper plan."""
    broken_kb = {"buildings": {"furnace": {"31": {"meat": 1, "wood": 1, "coal": 1, "iron": 1,
                                                   "fire_crystals": None, "refined_fire_crystals": 0,
                                                   "seconds": 1, "power": 1_580_900}}}}
    with pytest.raises(ValueError, match="fire_crystals"):
        kb.building_cost("furnace", 30, 31, kb=broken_kb)


def test_days_to_and_verify():
    assert kb.days_to({"meat": 100, "wood": 50}, {"meat": 10, "wood": 100}) == 10.0
    assert kb.days_to({"meat": 100}, {"meat": 10}, stock={"meat": 100}) == 0.0
    assert kb.days_to({"meat": 100}, {"meat": 0}) is None
    ok, why = kb.verify({"meat": 190_000_000, "wood": 190_000_000}, {"meat": 190_000_000})
    assert ok and why == "ok"
    ok, why = kb.verify({"meat": 190_000_000}, {"meat": 100_000_000})
    assert not ok and "meat" in why


def test_furnace_ordinal():
    assert [kb.furnace_ordinal(x) for x in ("27", "30-3", "FC1", "FC 10", "FC9-4")] == [27, 33, 35, 80, 79]
    with pytest.raises(ValueError):
        kb.furnace_ordinal("27-3")
    with pytest.raises(ValueError):
        kb.furnace_ordinal("FC1-5")


def test_speed_bonus_from_sheet():
    assert kb.speed_bonus_from_sheet({"progress.bonus.construction_speed": 128}, "construction") == 1.28
    assert kb.speed_bonus_from_sheet({}, "training") == 0.0


def test_speed_bonus_from_sheet_rejects_an_unrecognised_dict_shape():
    """Fix round 2, item 2: a shape this function can't interpret must be
    loud, not silently read as "bonus not applied"."""
    with pytest.raises(TypeError):
        kb.speed_bonus_from_sheet({"progress.bonus.construction_speed": {"unexpected": 1}}, "construction")


def test_speed_bonus_from_sheet_reads_model_latest_row_dicts(tmp_path, k):
    """Fix round 2, item 2 (the deliverable, not a nicety): native.model.latest
    is documented, in its own docstring, as "the current sheet", but it
    returns {path: row_dict}, not {path: value} -- the shape the old test
    above hand-builds and which nothing in this repo actually produces.
    Before this fix, `float(row_dict)` raised TypeError; a caller that
    defensively caught that got the same 0.0 this function returns for
    "the stats reader never ran", indistinguishable from Task 7 never
    having shipped.

    Built end to end through the real model functions -- write_snapshot()
    writes the three bonus paths the normal way, model.latest() reads them
    back -- rather than a hand-built row-dict fixture, so a wrong guess
    about the shape model.latest() hands back can't hide behind a
    convenient stand-in."""
    conn = model.connect(str(tmp_path / "wos.sqlite"))
    doc = {
        "identity": {"id": "P1", "name": "Bob", "state": 100, "state_age_days": 1},
        "progress": {"bonus": {"construction_speed": 128, "research_speed": 50, "training_speed": 30}},
    }
    model.write_snapshot(
        conn, player={"id": "P1", "name": "Bob", "state": 100},
        snapshot_id="20260101T000000Z", taken_at="2026-01-01T00:00:00Z",
        source="native-app", run_dir="/tmp/run", status="ok", sections={},
        duration_s=1, gems_before=0, gems_after=0, power_before=0, power_after=0,
        power_rose=False, doc=doc, provenance={},
    )
    sheet = model.latest(conn, "P1")
    assert isinstance(sheet["progress.bonus.construction_speed"], dict)  # the row-dict shape, not a bare value
    assert kb.speed_bonus_from_sheet(sheet, "construction") == 1.28
    assert kb.speed_bonus_from_sheet(sheet, "research") == 0.5
    assert kb.speed_bonus_from_sheet(sheet, "training") == 0.3

    # the buffed number a real caller wants: base furnace 27->28 time with
    # the account's own construction bonus applied, not the unbuffed base
    # time (which would read as if Task 7 had never shipped, F-a).
    base = kb.building_time("furnace", 27, 28, kb=k)
    buffed = kb.building_time(
        "furnace", 27, 28, speed_bonus=kb.speed_bonus_from_sheet(sheet, "construction"), kb=k
    )
    assert buffed == kb.building_time("furnace", 27, 28, speed_bonus=1.28, kb=k)
    assert buffed != base


def test_cache_is_read_only():
    """E8: a dict load()/building_row() hands back is owned by the cache.
    Mutating it must not leak into a fresh load of the same directory.
    Uses its own cache entry (popped before and after) so it neither reads
    a stale cache from an earlier test nor pollutes the shared `k` fixture
    or any other directory's cache entry."""
    kb._CACHE.pop(FIX, None)
    first = kb.load(FIX)
    row = kb.building_row("furnace", 28, kb=first)
    original_meat = row["meat"]
    row["meat"] = -1
    kb._CACHE.pop(FIX, None)
    fresh = kb.load(FIX)
    assert kb.building_row("furnace", 28, kb=fresh)["meat"] == original_meat
    kb._CACHE.pop(FIX, None)


def test_load_missing_required_table_raises(tmp_path):
    (tmp_path / "buildings.json").write_text(json.dumps({"_meta": {}, "buildings": {}}))
    # training/stats/research absent -> required table missing
    with pytest.raises(FileNotFoundError, match="troops.json"):
        kb.load(str(tmp_path))


def test_load_missing_optional_table_is_skipped(tmp_path, capsys):
    for key, fname, payload in (("buildings", "buildings.json", {"buildings": {}}),
                                 ("training", "troops.json", {"training": {}}),
                                 ("stats", "troop_stats.json", {"stats": {}}),
                                 ("research", "research.json", {"research": {}})):
        (tmp_path / fname).write_text(json.dumps({"_meta": {}, **payload}))
    result = kb.load(str(tmp_path))
    assert "events" not in result
    assert "not available" in capsys.readouterr().out
    kb._CACHE.pop(str(tmp_path), None)


# ----------------------------------------------------------------------------- Task 5: labels, verification, freshness
_FC31_ROW = {"source": "whiteoutdata", "label": "30-1", "meat": 67_000_000, "wood": 67_000_000,
             "coal": 13_000_000, "iron": 3_300_000, "fire_crystals": 132, "refined_fire_crystals": 0,
             "seconds": 604800, "power": 1_580_900, "prerequisites": {}}


def _kbdir_with_overlay(tmp_path):
    d = tmp_path / "kbdir"
    shutil.copytree(FIX, d)
    overlay = {"_meta": {"sources": ["whiteoutdata"]}, "buildings": {"furnace": {"31": dict(_FC31_ROW)}}}
    (d / "local").mkdir()
    (d / "local" / "overlay.json").write_text(json.dumps(overlay))
    return d


def test_building_row_accepts_furnace_labels_and_next_label(k):
    assert kb.building_row("furnace", "28", kb=k)["meat"] == 190_000_000
    assert kb.building_row("furnace", 99, kb=k) is None
    assert [kb.next_level_label(x) for x in (27, 30, 34, 35, 79)] == ["28", "30-1", "FC1", "FC1-1", "FC10"]
    # Finding 3 (2026-09-09 fix round): "28" and 99 above are both digit
    # strings, so the label branch (`not key.isdigit()`) never runs -- this
    # test stayed green with that branch deleted entirely. A real label form
    # ("30-1", not a plain ordinal) must resolve through furnace_ordinal to
    # the matching row, and an unparseable one must return None rather than
    # raise (the documented contract building_row's docstring makes).
    label_kb = {"buildings": {"furnace": {"31": {"meat": 67_000_000}}}}
    assert kb.building_row("furnace", "30-1", kb=label_kb)["meat"] == 67_000_000
    assert kb.building_row("furnace", "not-a-level", kb=label_kb) is None


def test_mark_verified_writes_and_invalidates_cache(tmp_path):
    d = tmp_path / "kbdir"
    shutil.copytree(FIX, d)
    kb.load(str(d))
    assert kb.mark_verified("buildings", "furnace", 28, "20260908T122223Z", directory=str(d))
    assert kb.load(str(d))["buildings"]["furnace"]["28"]["verified_in_game"] == "20260908T122223Z"
    assert not kb.mark_verified("buildings", "furnace", 999, "x", directory=str(d))


def test_mark_verified_writes_research_levels_and_survives_reload(tmp_path):
    """The research branch nests one level deeper than buildings
    (doc["research"][key]["levels"][level] vs doc["buildings"][key][level]);
    the two shapes are the one place mark_verified's logic diverges by
    table, so it needs its own test rather than riding on the buildings
    coverage. Asserted through the public functions (mark_verified,
    kb.load) so a future rename of the "levels" key would fail this."""
    d = tmp_path / "kbdir"
    shutil.copytree(FIX, d)
    assert kb.mark_verified("research", "tooling_up_i", 1, "sidX", directory=str(d))
    reloaded = kb.load(str(d))
    assert reloaded["research"]["tooling_up_i"]["levels"]["1"]["verified_in_game"] == "sidX"


def test_mark_verified_names_an_unsupported_table(tmp_path):
    d = tmp_path / "kbdir"
    shutil.copytree(FIX, d)
    with pytest.raises(KeyError) as exc:
        kb.mark_verified("training", "infantry", 9, "sid", directory=str(d))
    msg = str(exc.value)
    assert "training" in msg and "buildings" in msg and "research" in msg


def test_mark_verified_returns_false_for_overlay_only_level(tmp_path):
    """C12: mark_verified opens the committed file directly, never the
    merged in-memory table, so an overlay-only level (ordinal > 30, added
    only by _apply_overlay from the gitignored, terms-restricted local
    overlay) always returns False. Writing it would put whiteoutdata's data
    into a committed file, which is exactly what the overlay design (A5) is
    there to prevent."""
    d = _kbdir_with_overlay(tmp_path)
    merged = kb.load(str(d))
    assert merged["buildings"]["furnace"]["31"]["meat"] == 67_000_000  # confirms the overlay actually merged
    assert kb.mark_verified("buildings", "furnace", 31, "sid", directory=str(d)) is False
    kb._CACHE.pop(str(d), None)


def test_load_with_real_overlay_file_reaches_the_prerequisites_guard(tmp_path):
    """Closes the Task 4 coverage gap: the existing overlay-row test for
    prerequisites() builds its row as a hand-written dict, so nothing
    proves kb.load() merging a REAL knowledge/local/overlay.json file on
    disk produces a row shaped the way that guard expects. This round-trips
    it end to end: write the overlay fixture, kb.load() it for real, and
    exercise both building_row's furnace-label lookup and prerequisites()'s
    overlay guard on the merged row."""
    d = _kbdir_with_overlay(tmp_path)
    merged = kb.load(str(d))
    assert kb.building_row("furnace", "30-1", kb=merged)["meat"] == 67_000_000
    unmet, assumed = kb.prerequisites("furnace", 31, {}, kb=merged)
    assert unmet == [] and assumed == ["unknown: overlay row"]
    kb._CACHE.pop(str(d), None)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_freshness_flags_tables_older_than_the_threshold():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    stale_kb = {
        "_meta_buildings": {"fetched_at": _iso(now - timedelta(days=40))},
        "_meta_research": {"fetched_at": _iso(now - timedelta(days=5))},
    }
    out = {t: (age, stale) for t, age, stale in kb.freshness(kb=stale_kb, now=now)}
    assert out["buildings"] == (40, True)
    assert out["research"] == (5, False)
    # training/stats/events have no _meta_* entry at all -> omitted, not a false "fresh"
    assert "training" not in out and "stats" not in out and "events" not in out


def test_freshness_uses_the_default_stale_threshold_of_30_days():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    edge_kb = {"_meta_buildings": {"fetched_at": _iso(now - timedelta(days=30))}}
    assert kb.freshness(kb=edge_kb, now=now) == [("buildings", 30, False)]


def test_freshness_omits_a_table_with_an_unparseable_fetched_at():
    """Finding 2 (2026-09-09 fix round): a bare date ("2026-09-01", the
    shape knowledge/unlocks.json used to ship, every other table uses
    "%Y-%m-%dT%H:%M:%SZ") raises TypeError subtracting offset-naive from
    offset-aware once `now` is timezone-aware; a plain garbage string raises
    ValueError out of fromisoformat. Neither may propagate -- this is a
    report line (native/report.py calls it inside build()), and a bad
    timestamp on one table must never take the whole report down."""
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    bad_kb = {
        "_meta_buildings": {"fetched_at": "2026-09-01"},  # bare date, offset-naive
        "_meta_training": {"fetched_at": "not-a-timestamp"},
        "_meta_research": {"fetched_at": _iso(now - timedelta(days=5))},
    }
    out = {t: (age, stale) for t, age, stale in kb.freshness(kb=bad_kb, now=now)}
    assert "buildings" not in out and "training" not in out
    assert out["research"] == (5, False)


# ----------------------------------------------------------------------------- Task 8: item catalogue (A11)
def test_items_returns_empty_dict_when_the_catalogue_file_is_absent(tmp_path):
    assert kb.items(directory=str(tmp_path)) == {}


def test_record_item_upsert_sets_first_seen_once_and_advances_last_seen(tmp_path):
    d = str(tmp_path)
    row1 = kb.record_item("1 Gems", "Resources", "Grants 1 Gems.", "sidA", directory=d)
    assert row1["first_seen"] == "sidA" and row1["last_seen"] == "sidA"
    assert row1["slug"] == "1_gems" and row1["tab"] == "Resources"

    row2 = kb.record_item("1 Gems", "Resources", "Grants 1 Gems.", "sidB", directory=d)
    assert row2["first_seen"] == "sidA"  # never overwritten
    assert row2["last_seen"] == "sidB"   # always advances

    # the upsert round-trips through kb.items(), the file record_item wrote
    catalogue = kb.items(directory=d)
    assert catalogue["1_gems"] == row2


def test_record_item_updates_tab_and_description_on_a_later_sighting(tmp_path):
    """Every field but first_seen refreshes to the latest sighting -- a
    later, better OCR read of the description must not be stuck behind an
    earlier, worse one."""
    d = str(tmp_path)
    kb.record_item("1 Gems", "Resources", "Grants 1 Gem.", "sidA", directory=d)
    row = kb.record_item("1 Gems", "Other", "Grants 1 Gems.", "sidB", directory=d)
    assert row["tab"] == "Other" and row["description"] == "Grants 1 Gems."


def test_record_item_kind_reuses_backpacks_classification_rules(tmp_path):
    """kind must come from native.readers.backpack.classify_kind -- the
    same SPEEDUP_RE/keyword logic `fold` uses -- not a second copy."""
    d = str(tmp_path)
    speedup = kb.record_item("5m Speedup", "Speedup", "Reduces a queue by 5 minutes.", "sid", directory=d)
    fc = kb.record_item("Fire Crystal", "Resources", "A rare crystal.", "sid", directory=d)
    other = kb.record_item("1 Gems", "Resources", "Grants 1 Gems.", "sid", directory=d)
    assert speedup["kind"] == "speedup"
    assert fc["kind"] == "fire_crystal"
    assert other["kind"] == "other"


def test_record_item_row_has_exactly_its_own_fields_with_a_real_overlay_present(tmp_path):
    """Fix round 1, item 4: a substring search for "overlay" would not
    catch overlay-sourced data landing under a different key name -- the
    real guarantee is that record_item has no overlay code path at all
    (it never calls load()/_kb()/_apply_overlay), so plant a REAL,
    distinctive overlay.json (the exact shape _apply_overlay merges into
    kb.load()'s buildings table) next to items.json and prove two things
    a leak would break: the row record_item writes has EXACTLY the eight
    fields the upsert sets -- no "power"/"source"/"disputed" field the
    overlay carries could sneak in unnoticed -- and none of the overlay's
    own distinctive values appear anywhere in the file."""
    d = tmp_path
    (d / "local").mkdir()
    (d / "local" / "overlay.json").write_text(json.dumps({
        "_meta": {"sources": ["whiteoutdata"]},
        "buildings": {"furnace": {"31": {
            "source": "whiteoutdata", "power": 1_580_900, "meat": 67_000_000,
            "prerequisites": {}, "disputed": {"whiteoutdata": {"coal": 40_000_000}},
        }}},
    }))
    row = kb.record_item("1 Gems", "Resources", "Grants 1 Gems.", "sid", directory=str(d))
    assert set(row) == {"name", "slug", "tab", "description", "kind",
                         "classifier_version", "first_seen", "last_seen"}
    with open(d / "items.json") as f:
        doc = json.load(f)
    assert doc["_meta"] == {"source": "in-game backpack tooltips"}
    written = json.dumps(doc)
    for sentinel in ("whiteoutdata", "power", "disputed", "1580900", "67000000"):
        assert sentinel not in written, f"overlay data ({sentinel!r}) leaked into items.json"


def test_record_item_does_not_import_the_game_facing_stack(tmp_path):
    """Fix round 1, item 1: native/kb.py's module docstring opens with "No
    game access, no database." Before this fix that was true only because
    the backpack reader (the one caller so far) had already paid the
    native.screen import cost -- record_item's own deferred `from
    native.readers.backpack import classify_kind` meant a fresh process
    calling only native.kb.record_item would transitively import cv2,
    numpy and native.drive to resolve a two-line string classifier.
    Run in a clean subprocess (this test process has already imported cv2
    for other tests) and assert none of that stack ever loads."""
    import subprocess
    import sys

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = f"""
import sys
sys.path.insert(0, {repo_root!r})
from native import kb
kb.record_item("1 Gems", "Resources", "Grants 1 Gems.", "sid", directory={str(tmp_path)!r})
leaked = [m for m in ("cv2", "numpy", "native.drive", "native.screen", "native.readers.backpack")
          if m in sys.modules]
assert not leaked, f"record_item pulled in: {{leaked}}"
print("OK")
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr
