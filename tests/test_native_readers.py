"""Reader parse functions against survey frames of the main account (local,
gitignored fixture: the frames carry the player id and alliance chat)."""
import json
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIX = os.path.join(REPO, "tests", "fixtures", "local", "reader_frames.json")


def _frame(reader):
    if not os.path.exists(FIX):
        pytest.skip("local reader frames not present")
    import cv2
    rec = json.load(open(FIX))[reader]
    img = cv2.imread(os.path.join(REPO, rec["frame"]))
    return img, rec["items"], rec["frame"]


def test_hud_reads_power_gems_coal_survivors_vip_and_timer():
    from native.readers import hud, ReaderResult
    img, items, path = _frame("hud")
    r = hud.parse(ReaderResult("hud"), img, items, path)
    d = r.doc
    assert d["progress"]["power"] == 35_020_652
    assert d["economy"]["gems"] == 1423
    assert d["economy"]["resources"]["coal"] == 8_900_000
    assert r.provenance["economy.resources.coal"]["exact"] == 0
    assert d["city"]["survivors"] == {"value": 32, "cap": 32}
    assert d["progress"]["vip"]["level"] == 7
    assert d["progress"]["furnace"]["upgrading"]["remaining_s"] == 9 * 86400 + 9 * 3600 + 37 * 60 + 27
    assert r.settle(hud.EXPECTED).status == "ok"


def test_profile_reads_identity_furnace_kills_stamina_alliance():
    from native.readers import profile, ReaderResult
    img, items, path = _frame("profile")
    r = profile.parse(ReaderResult("profile"), img, items, path)
    d = r.doc
    assert d["identity"]["id"] == "821103058"
    assert d["identity"]["name"] == "NamelessPharaoh"
    assert d["identity"]["state"] == 4562
    assert d["progress"]["furnace"]["level"] == 27
    assert d["progress"]["kills"] == 671_553
    assert d["economy"]["stamina"] == {"value": 700, "cap": 200}
    assert d["alliance"]["tag"] == "ACE"
    assert r.settle(profile.EXPECTED).status == "ok"


def test_troops_reads_totals_queue_injured_and_per_type():
    from native.readers import troops, ReaderResult
    img, items, path = _frame("troops")
    r = troops.parse(ReaderResult("troops"), img, items, path)
    d = r.doc
    assert d["troops"]["total"] == {"value": 231_300, "cap": 231_300}
    assert d["troops"]["march_queue"] == {"used": 6, "cap": 6}
    assert d["troops"]["wounded"] == {"value": 0, "cap": 89_300}
    assert d["troops"]["by_tier"]["infantry"]["t9"] == 76_227
    assert d["troops"]["by_tier"]["lancer"]["t9"] == 77_849
    assert d["troops"]["by_tier"]["marksman"]["t9"] == 77_232
    assert d["troops"]["totals"] == {"infantry": 76_227, "lancer": 77_849, "marksman": 77_232}
    assert d["troops"]["by_tier"]["infantry"]["t8"] == 0 and d["troops"]["by_tier"]["marksman"]["t10"] == 0
    assert r.provenance["troops.by_tier.infantry.t8"]["method"] == "absent"
    assert r.settle(troops.EXPECTED).status == "ok"


def test_resources_reads_owned_output_protected_in_order():
    from native.readers import resources, ReaderResult
    img, items, path = _frame("resources")
    r = resources.parse(ReaderResult("resources"), img, items, path)
    d = r.doc["economy"]
    assert d["resources"] == {"meat": 37_800_000, "wood": 33_000_000, "coal": 8_900_000, "iron": 1_900_000}
    assert d["protected"] == {"meat": 28_400_000, "wood": 22_000_000, "coal": 6_800_000, "iron": 1_300_000}
    assert d["output"] == {"meat": 90_200, "wood": 29_500, "coal": 4_800, "iron": 671}
    assert all(r.provenance[f"economy.resources.{k}"]["exact"] == 0 for k in ("meat", "wood", "coal", "iron"))
    assert r.settle(resources.EXPECTED).status == "ok"


def test_alliance_reads_name_tag_power_rank_members_level():
    from native.readers import alliance, ReaderResult
    img, items, path = _frame("alliance")
    r = alliance.parse(ReaderResult("alliance"), img, items, path)
    d = r.doc["alliance"]
    assert d["tag"] == "ACE" and d["name"] == "ArabChampEmpire"
    assert d["power"] == 1_846_641_461
    assert d["state_rank"] == 3
    assert d["members"] == 99 and d["cap"] == 100
    assert d["level"] == 11
    assert r.settle(alliance.EXPECTED).status == "ok"


def test_reader_result_put_and_settle():
    from native.readers import ReaderResult
    r = ReaderResult("x")
    r.put("a.b.c", 1, raw="1", frame="/tmp/f/001-x.png")
    assert r.doc == {"a": {"b": {"c": 1}}}
    assert r.provenance["a.b.c"]["frame"] == "001-x.png"
    assert r.settle(["a.b.c", "a.b.d"]).status == "partial"
    assert r.settle(["a.b.c"]).status == "ok"
    assert ReaderResult("y").settle(["p"]).status == "failed"


def test_derive_furnace_fills_fc_sub_ordinal():
    from native.snapshot import derive_furnace, deep_merge
    doc = {"progress": {"furnace": {"level": 27}}}
    prov = {"progress.furnace.level": {"raw": "# Lv. 27", "frame": "002-profile.png", "score": 1.0, "method": "ocr", "exact": 1}}
    derive_furnace(doc, prov)
    assert doc["progress"]["furnace"] == {"level": 27, "fc": 0, "sub": 0, "ordinal": 27}
    assert prov["progress.furnace.ordinal"]["method"] == "derived"
    assert deep_merge({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}}
    derive_furnace({"progress": {}}, {})  # no level: no-op


def test_queues_reads_build_training_and_research():
    from native.readers import queues, ReaderResult
    img, items, path = _frame("queues")
    r = queues.parse(ReaderResult("queues"), img, items, path)
    d = r.doc
    assert d["city"]["queues"]["1"] == {"building": "furnace", "remaining_s": 9 * 86400 + 9 * 3600 + 23 * 60 + 44}
    assert d["city"]["queues"]["2"]["building"] == "storehouse"
    assert d["troops"]["training"]["infantry"] == {"state": "completed", "remaining_s": 0}
    assert d["research"]["current"] == {"name": "idle", "remaining_s": 0}
    assert r.settle(queues.EXPECTED).status == "ok"


def test_building_popup_level():
    from native.readers import buildings
    img, items, path = _frame("building_popup")
    h, w = img.shape[:2]
    got = buildings.popup_level(items, h, w)
    assert got is not None and got[0] == "research_center" and got[1] == 27


def test_gear_slots_tier_stars_charms():
    from native.readers import gear, ReaderResult
    img, items, path = _frame("profile")
    r = gear.parse(ReaderResult("gear"), img, items, path)
    chief = r.doc["gear"]["chief"]
    assert {k: v["stars"] for k, v in chief.items()} == {"helmet": 1, "watch": 1, "jacket": 0, "pants": 3, "ring": 3, "cane": 2}
    assert chief["jacket"]["tier"] == "purple" and chief["jacket"]["rank"] == 2
    assert chief["helmet"]["tier"] == "blue" and chief["helmet"]["rank"] == 1
    assert len(r.doc["gear"]["charms"]["ring"]) == 3
    assert r.settle(gear.EXPECTED).status == "ok"


def test_hero_card_and_roster():
    from native.readers import heroes, ReaderResult
    from native.readers.imgcues import hero_card_stars, rarity_from_hue
    img, items, path = _frame("hero_card")
    r = ReaderResult("heroes")
    assert heroes.parse_card(r, img, items, path, "mythic") == "molly"
    m = r.doc["heroes"]["molly"]
    assert m["name"] == "Molly" and m["rarity"] == "mythic" and m["level"] == 70
    assert m["power"] == 1_034_920 and m["troops_capacity"] == 13_070 and m["escorts"] == 10
    assert (m["exp"], m["exp_next"]) == (177_269, 870_000) and m["stars"] == 4
    assert hero_card_stars(img) == (4, True)
    rimg, ritems, _ = _frame("heroes_roster")
    h, w = rimg.shape[:2]
    cards = heroes.roster_cards(ritems, h, w)
    assert len(cards) == 12 and all(c[2] == 70 for c in cards)
    assert [rarity_from_hue(rimg, cx - 0.05, cy - 0.07) for cx, cy, _ in cards][:5] == ["mythic"] * 4 + ["epic"]


def test_events_page_and_backpack_tooltip_parsers():
    from native.readers import events, backpack, ReaderResult
    img, items, path = _frame("events")
    r = ReaderResult("events")
    assert events.parse_page(r, "Endless Trial", img, items, path) == "endless_trial"
    e = r.doc["events"]["endless_trial"]
    assert e["name"] == "Endless Trial" and e["remaining_s"] == 12 * 3600 + 38 * 60 + 17 and e["attempts_left"] == 30
    timg, titems, tpath = _frame("backpack_tile")
    h, w = timg.shape[:2]
    assert backpack.read_tooltip(titems, h, w) == ("1 Gems", None)
    bimg, bitems, _ = _frame("backpack")
    h, w = bimg.shape[:2]
    tiles = backpack.tile_targets(bitems, h, w)
    assert len(tiles) >= 30 and tiles[0][0] in backpack.TILE_COLS
    r2 = ReaderResult("backpack")
    backpack.fold(r2, "Speedup", "5m Speedup", 220, "220", tpath, 1.0, True)
    backpack.fold(r2, "Speedup", "Construction Speedup 1h", 3, "3", tpath, 1.0, True)
    backpack.fold(r2, "Resources", "Fire Crystal", 12, "12", tpath, 1.0, True)
    assert r2.doc["backpack"]["speedups"]["general"]["5m"] == 220
    assert r2.doc["backpack"]["speedups"]["construction"]["1h"] == 3
    assert r2.doc["backpack"]["fire_crystals"] == 12
    assert r2.doc["backpack"]["items"]["speedup/5m_speedup"] == 220


def test_is_hero_card_rejects_other_screens():
    from native.readers import heroes
    img, items, path = _frame("hero_card")
    h, w = img.shape[:2]
    assert heroes.is_hero_card(items, h, w)
    bimg, bitems, _ = _frame("backpack")
    assert not heroes.is_hero_card(bitems, *bimg.shape[:2])


def test_backpack_tab_active_by_pixel():
    from native.readers import backpack as bp
    img, items, _ = _frame("backpack")
    active = {i["text"]: bp.tab_active(img, i) for i in items if i["text"] in ("Resources", "Speedup", "Bonus", "Gear", "Other")}
    assert active == {"Resources": True, "Speedup": False, "Bonus": False, "Gear": False, "Other": False}


def test_resources_assigns_by_row_so_a_dropped_bullet_cannot_become_iron():
    import cv2
    from core.vision_engine import VisionEngine
    from native.readers import resources, ReaderResult
    p = os.path.join(REPO, "tests", "fixtures", "local", "frames", "reader-resources-bullet-misread.png")
    if not os.path.exists(p):
        pytest.skip("local frame not present")
    img = cv2.imread(p)
    items = VisionEngine().recognize(img)
    r = resources.parse(ReaderResult("resources"), img, items, p)
    d = r.doc["economy"]
    assert d["resources"] == {"meat": 39_300_000, "wood": 34_000_000, "coal": 9_100_000, "iron": 1_900_000}
    assert d["protected"] == {"meat": 30_000_000, "wood": 23_000_000, "coal": 7_000_000, "iron": 1_300_000}
    assert r.settle(resources.EXPECTED).status == "ok"
