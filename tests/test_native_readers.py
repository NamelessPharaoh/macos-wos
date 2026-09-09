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
    got = backpack.read_tooltip(titems, h, w)
    assert got[:2] == ("1 Gems", None)
    assert isinstance(got[2], str) and got[2] and got[2] != got[0]
    assert got[2] == "Grants 1 Gems."
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


def test_read_tooltip_joins_a_two_line_description():
    """Fix round 1, item 3: the only real fixture on hand has a single-line
    description, so nothing exercised a description that wraps across more
    than one OCR line. Built synthetically (h=w=1000): a Use button at
    fy=0.5, a name in the 0.15-0.24 band above it, and two description
    lines in the 0.10-0.15 band, top line first. read_tooltip must return
    both lines joined top-to-bottom with a space, not just the first."""
    from native.readers import backpack

    def item(text, cx, cy):
        return {"text": text, "score": 1.0, "box": [cx - 10, cy - 5, cx + 10, cy + 5]}

    items = [
        item("Use", 500, 500),                          # uy = 0.5
        item("Wonder Box", 500, 300),                    # fy = 0.30, in the 0.26-0.35 name band
        item("Contains a random reward.", 500, 360),     # fy = 0.36, in the 0.35-0.40 description band
        item("Open it to find out!", 500, 385),          # fy = 0.385, same band, below the first line
    ]
    got = backpack.read_tooltip(items, 1000, 1000)
    assert got[0] == "Wonder Box"
    assert got[2] == "Contains a random reward. Open it to find out!"


def test_classify_kind_is_reexported_from_knowledge_util():
    """Fix round 1, item 1: classify_kind (and SPEEDUP_RE, speedup_duration)
    moved to knowledge/util.py so native/kb.py::record_item can import it
    without dragging native.screen's cv2/numpy/native.drive imports in.
    backpack.py must hand back the exact same function object, not a
    second copy, since it is `fold`'s own classifier too."""
    from knowledge import util as ku
    from native.readers import backpack
    assert backpack.classify_kind is ku.classify_kind


def test_fold_prefers_the_item_catalogue_over_its_own_regex_at_the_current_classifier_version(tmp_path):
    """A11 step 3: fold consults native.kb.items() by exact slug match
    before falling back to classify_kind's SPEEDUP_RE/keyword rules. A name
    that classify_kind alone would call "other" (no speedup shape, no "fire
    crystal" keyword) must still route to the fire_crystals ledger path
    once the catalogue -- built from the game's own tooltip -- has already
    classified that exact slug as a fire_crystal, stamped with today's
    CLASSIFIER_VERSION (fix round 1, item 2's gate)."""
    from knowledge.util import CLASSIFIER_VERSION, write_table
    from native import kb
    from native.readers import backpack, ReaderResult
    import os

    assert backpack.classify_kind("Mystery Shard") == "other"
    d = str(tmp_path)
    kb.record_item("Mystery Shard", "Other", "A rare crystal fragment.", "sid", directory=d)
    catalogue = kb.items(directory=d)
    # simulate a keyword rule catching it later, at the current version
    catalogue["mystery_shard"]["kind"] = "fire_crystal"
    catalogue["mystery_shard"]["classifier_version"] = CLASSIFIER_VERSION
    write_table(os.path.join(d, "items.json"), {"_meta": {"source": "in-game backpack tooltips"}, "items": catalogue})

    r = ReaderResult("backpack")
    backpack.fold(r, "Other", "Mystery Shard", 7, "7", "frame.png", 1.0, True, directory=d)
    assert r.doc["backpack"]["fire_crystals"] == 7

    # an uncatalogued slug still falls back to classify_kind, unchanged
    r2 = ReaderResult("backpack")
    backpack.fold(r2, "Other", "Totally Unknown Thing", 3, "3", "frame.png", 1.0, True, directory=d)
    assert "fire_crystals" not in r2.doc.get("backpack", {})


def test_fold_recomputes_kind_when_the_catalogues_classifier_version_is_stale(tmp_path):
    """Fix round 1, item 2: before this fix, fold trusted a catalogued
    `kind` forever, so a classify_kind rule change never reached an item
    already recorded until the backpack reader saw that exact tile live
    again -- a fix that silently did nothing for the whole existing
    catalogue. Now a row whose `classifier_version` does not match
    knowledge.util.CLASSIFIER_VERSION is treated as uncatalogued: this
    plants a row for "Fire Crystal" with a wrong, stale `kind` ("other")
    under an old version number and proves fold ignores it and recomputes
    the correct "fire_crystal" from classify_kind(name) instead -- no live
    re-sighting required. The companion assertion (current version, same
    wrong kind) proves the gate is the version stamp specifically, not
    fold simply ignoring the catalogue altogether."""
    from knowledge.util import CLASSIFIER_VERSION, classify_kind, write_table
    from native import kb
    from native.readers import backpack, ReaderResult
    import os

    assert classify_kind("Fire Crystal") == "fire_crystal"
    d = str(tmp_path)
    kb.record_item("Fire Crystal", "Resources", "A rare crystal.", "sid", directory=d)
    catalogue = kb.items(directory=d)
    catalogue["fire_crystal"]["kind"] = "other"  # deliberately wrong, to detect which value fold used
    catalogue["fire_crystal"]["classifier_version"] = CLASSIFIER_VERSION - 1  # stale
    write_table(os.path.join(d, "items.json"), {"_meta": {"source": "in-game backpack tooltips"}, "items": catalogue})

    stale = ReaderResult("backpack")
    backpack.fold(stale, "Resources", "Fire Crystal", 12, "12", "frame.png", 1.0, True, directory=d)
    assert stale.doc["backpack"]["fire_crystals"] == 12  # recomputed, not the stale "other"

    catalogue["fire_crystal"]["classifier_version"] = CLASSIFIER_VERSION  # now current
    write_table(os.path.join(d, "items.json"), {"_meta": {"source": "in-game backpack tooltips"}, "items": catalogue})
    current = ReaderResult("backpack")
    backpack.fold(current, "Resources", "Fire Crystal", 12, "12", "frame.png", 1.0, True, directory=d)
    assert "fire_crystals" not in current.doc.get("backpack", {})  # trusted the (wrong) stored "other"


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


def test_stats_reads_construction_research_and_training_speed_across_scrolls():
    """Bonus Overview is one long scrollable list: Training Speed sits in the
    Military section, Construction/Research Speed in the Growth section
    further down, never together in one frame -- so parse() accumulates into
    the same ReaderResult across the frames the reader visits while scrolling
    (native/readers/stats.py::read), exactly like this test does."""
    from native.readers import stats, ReaderResult
    r = ReaderResult("stats")
    for name in ("stats", "stats_scroll1", "stats_scroll2"):
        img, items, path = _frame(name)
        stats.parse(r, img, items, path)
    d = r.doc["progress"]["bonus"]
    assert d == {"construction_speed": 51, "research_speed": 37, "training_speed": 156}
    assert r.settle(stats.EXPECTED).status == "ok"


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
