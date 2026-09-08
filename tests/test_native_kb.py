import json
import os

import pytest

from native import kb

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
