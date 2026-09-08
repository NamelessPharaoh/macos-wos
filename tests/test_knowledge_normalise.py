"""knowledge/normalise.py: wosnerds source shape -> knowledge-base schema
(Task 2). Fixtures under tests/fixtures/knowledge/ are real rows copied from
the sources on 2026-09-08; the duplicate-id and missing-field cases below
are small synthetic dicts (A3, E3) rather than fixture files, since the
brief names exactly five fixture files for this task."""
import copy
import json
import os

import pytest

from knowledge import normalise as n

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "knowledge")


def _load(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def test_slug():
    assert n.slug("Hunter's Hut") == "hunters_hut"
    assert n.slug("Research Center") == "research_center"
    assert n.slug("research-center") == "research_center"
    assert n.slug("tooling-up-i") == "tooling_up_i"


def test_slug_drops_the_curly_apostrophe_too():
    # B1: the source occasionally uses a typographic right single quote.
    assert n.slug("Hunter’s Hut") == "hunters_hut"


def test_buildings_furnace_rows_and_prerequisites():
    doc = n.buildings(_load("construction_excerpt.json"))
    f = doc["buildings"]["furnace"]
    assert set(f) == {"0", "27", "30"}
    r27 = f["27"]
    assert (r27["meat"], r27["wood"], r27["coal"], r27["iron"]) == (140_000_000, 140_000_000, 24_000_000, 7_400_000)
    assert r27["seconds"] == 2_187_780 and r27["fire_crystals"] == 0 and r27["refined_fire_crystals"] == 0
    assert r27["prerequisites"]["embassy"] == 26 and r27["prerequisites"]["hunters_hut"] == 6
    assert r27["verified_in_game"] is None
    assert "hunters_hut" in doc["buildings"]


def test_buildings_level_zero_row_of_zeros_normalises_fine():
    # E3: a present 0 is legal, not "missing".
    doc = n.buildings(_load("construction_excerpt.json"))
    r0 = doc["buildings"]["furnace"]["0"]
    assert (r0["meat"], r0["wood"], r0["coal"], r0["iron"], r0["seconds"]) == (0, 0, 0, 0, 0)


def test_buildings_missing_required_field_raises_naming_building_and_level():
    # E3: a dropped upstream field must become a loud failure.
    raw = copy.deepcopy(_load("construction_excerpt.json"))
    del raw["buildingLevels"]["Furnace"][1]["meat"]  # level 27 row
    with pytest.raises(ValueError) as exc:
        n.buildings(raw)
    assert "furnace" in str(exc.value) and "27" in str(exc.value) and "meat" in str(exc.value)


def test_troops_training_rows():
    doc = n.troops(_load("troops_excerpt.json"))
    t9 = doc["training"]["infantry"]["9"]
    assert t9 == {"points": 45, "seconds": 131, "meat": 1394, "wood": 1046, "coal": 244, "iron": 51}
    assert set(doc["training"]) == {"infantry", "lancer", "marksman"}


def test_troops_missing_required_field_raises():
    raw = copy.deepcopy(_load("troops_excerpt.json"))
    del raw["troopCosts"]["infantry"][1]["points"]  # tier 9 row
    with pytest.raises(ValueError) as exc:
        n.troops(raw)
    assert "infantry" in str(exc.value) and "9" in str(exc.value) and "points" in str(exc.value)


def test_troop_stats_rows():
    doc = n.troop_stats(_load("troop_stats_excerpt.json"))
    s = doc["stats"]["infantry"]["9-fc0"]
    assert s["name"] == "Supreme" and s["power"] == 50 and s["lethality"] == 9 and s["load"] == 330


def test_troop_stats_missing_power_raises_naming_troop_type_and_tier():
    # E3: power feeds native/kb.py's troop_power and power_gain("training")
    # directly -- a dropped upstream field must not become a silent 0.
    raw = copy.deepcopy(_load("troop_stats_excerpt.json"))
    del raw["troop-stats"]["infantry"][1]["power"]  # the "9-fc0" row
    with pytest.raises(ValueError) as exc:
        n.troop_stats(raw)
    assert "infantry" in str(exc.value) and "9-fc0" in str(exc.value) and "power" in str(exc.value)


def test_troop_stats_zero_power_is_legal():
    # E3: a present 0 is legal, not "missing" -- mirrors the buildings L0 case.
    raw = {"troop-stats": {"infantry": [
        {"Troop Type": "infantry", "Troop Level": 1, "troop level name": "Rookie", "FC level": 0,
         "power": 0, "defense": 4, "lethality": 1, "load": 108, "attack": 1, "health": 6, "speed": 11}],
        "lancer": [], "marksman": []}}
    doc = n.troop_stats(raw)
    assert doc["stats"]["infantry"]["1-fc0"]["power"] == 0


def test_research_nodes_levels_and_requirements():
    doc = n.research(_load("research_excerpt.json"))
    node = doc["research"]["tooling_up_i"]
    assert node["tree"] == "growth" and node["name"] == "Tooling Up I" and node["stat"] == "construction_speed"
    lv2 = node["levels"]["2"]
    assert lv2["cost"] == {"meat": 3700, "wood": 3700, "coal": 750, "iron": 180, "steel": 220}
    assert lv2["seconds"] == 40 and lv2["power"] == 2000
    assert lv2["requires_research"] == {"tooling_up_i": 1}
    assert lv2["requires_buildings"] == {"research_center": 2}


def test_research_verified_in_game_lives_on_the_level_row_not_the_node():
    # C4: the mark is per-level evidence from the screen, not per-node.
    doc = n.research(_load("research_excerpt.json"))
    node = doc["research"]["tooling_up_i"]
    assert "verified_in_game" not in node
    assert node["levels"]["1"]["verified_in_game"] is None
    assert node["levels"]["2"]["verified_in_game"] is None


def test_research_missing_cost_raises():
    raw = copy.deepcopy(_load("research_excerpt.json"))
    del raw["Growth"]["tooling-up-i"]["levels"]["2"]["cost"]
    with pytest.raises(ValueError) as exc:
        n.research(raw)
    assert "tooling_up_i" in str(exc.value) and "2" in str(exc.value) and "cost" in str(exc.value)


def test_research_missing_time_raises():
    raw = copy.deepcopy(_load("research_excerpt.json"))
    del raw["Growth"]["tooling-up-i"]["levels"]["1"]["research-time-seconds"]
    with pytest.raises(ValueError) as exc:
        n.research(raw)
    assert "tooling_up_i" in str(exc.value) and "1" in str(exc.value) and "research-time-seconds" in str(exc.value)


def test_research_null_cost_raises_attribute_error():
    # scripts/refresh_knowledge.py's per-table guard must catch this: `cost`
    # is present (so the "missing cost" ValueError check doesn't fire) but
    # reshaped to null upstream, so `lv["cost"].items()` raises
    # AttributeError, not ValueError/KeyError/TypeError.
    raw = copy.deepcopy(_load("research_excerpt.json"))
    raw["Growth"]["tooling-up-i"]["levels"]["2"]["cost"] = None
    with pytest.raises(AttributeError):
        n.research(raw)


def test_buildings_reshaped_building_levels_raises_attribute_error():
    # Same reshape class at the other end of the chain: `buildingLevels`
    # turning into a list (or any non-dict) breaks `raw["buildingLevels"]
    # .items()` with AttributeError, which the refresh guard must also catch.
    raw = copy.deepcopy(_load("construction_excerpt.json"))
    raw["buildingLevels"] = list(raw["buildingLevels"].values())
    with pytest.raises(AttributeError):
        n.buildings(raw)


def test_research_center_lv_alias_maps_to_research_center():
    # C5: the real source spells "research-center-lv" in two rows
    # (coal_mining_iii L3, marksman_armor_iii L4); both must resolve to the
    # same building the buildings table uses.
    raw = {"Economy": {"coal-mining-iii": {"row": 3, "name": "Coal Mining III", "stat": "coal-production",
                                            "troop-type": None, "levels": {
        "3": {"stat-addition": 0.1, "power": 100, "cost": {"meat": 100}, "research-time-seconds": 10,
              "requirements": {"research-items": {}, "buildings": {"research-center-lv": 20}}}}}}}
    doc = n.research(raw)
    assert doc["research"]["coal_mining_iii"]["levels"]["3"]["requires_buildings"] == {"research_center": 20}


def test_research_duplicate_node_id_across_trees_raises():
    # A3: the same node id appearing under two trees is a data-integrity
    # error the refresh must surface, not silently overwrite.
    node = {"row": 1, "name": "Tooling Up I", "stat": "construction-speed", "troop-type": None,
            "levels": {"1": {"stat-addition": 0.4, "power": 1, "cost": {"meat": 1},
                              "research-time-seconds": 1, "requirements": {}}}}
    raw = {"Growth": {"tooling-up-i": node}, "Economy": {"tooling-up-i": node}}
    with pytest.raises(ValueError) as exc:
        n.research(raw)
    assert "growth" in str(exc.value) and "economy" in str(exc.value)


def test_calendar_events():
    doc = n.calendar(_load("calendar_excerpt.json"))
    e = doc["events"]["svs_castle"]
    assert e["name"] == "SVS Castle Battle" and e["repeat_every_days"] == 28 and e["length_hours"] == 6
    assert e["anchor"] == "2024-10-12T12:00:00Z" and e["available_after_age"] is None


def test_normalisers_registers_all_required_and_optional_tables():
    assert set(n.NORMALISERS) == {"buildings", "troops", "troop_stats", "research", "calendar"}
