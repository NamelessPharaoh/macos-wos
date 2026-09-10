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
    # Tile centre measured from the fixture's own visible sibling tiles
    # (col 0.404, row fy=0.2624 -> cy = 0.2624 - 0.045 = 0.217): the tapped
    # tile's own count is covered by the tooltip, same as every real shape
    # A/C sighting, so cy comes from the surrounding grid, not this frame's
    # own tile_targets() (which can no longer see the covered tile).
    got = backpack.read_tooltip(titems, h, w, 0.404, 0.217)
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


def test_read_tooltip_shape_a_resources_with_slider():
    """Live sweep 2026-09-09 found at least three tooltip layouts, and a
    fixed offset from the Use button cannot fit all of them (round 1 of
    this fix tried exactly that and still failed shapes B and C). The fix
    anchors on the tapped tile's centre (cx, cy) instead -- the one thing
    read() already knows before it taps.

    Shape A (Resources, with a quantity slider between the description and
    the buttons): real measured offsets from cy, Chief Stamina tooltip
    (~/wos-chief/20260909T120815Z/010-tile.png, tile at cx=0.216, cy=0.218):

        cy+0.117  "Chief Stamina"                                  name
        cy+0.151  "Restores 10 Chief Stamina. Used for daily ..."  desc line 1
        cy+0.174  "troop deployment."                              desc line 2
        cy+0.226  quantity slider ("00", "+", count)
        cy+0.315  Source / Use

    Reproduced here at cy=0.300 (h=w=1000, so 1 fy unit = 1000px). The
    quantity slider and the buttons must both be excluded from the
    description: the old fixed-offset code (and an earlier round of this
    same fix that only handled shape A) both stored "troop deployment." --
    the tail of a sentence -- because the first description line landed in
    what used to be the fixed name band."""
    from native.readers import backpack

    def item(text, cx, cy):
        return {"text": text, "score": 1.0, "box": [cx - 10, cy - 5, cx + 10, cy + 5]}

    cy = 0.300
    items = [
        item("Chief Stamina", 500, int((cy + 0.117) * 1000)),
        item("Restores 10 Chief Stamina. Used for daily events like", 500, int((cy + 0.151) * 1000)),
        item("troop deployment.", 500, int((cy + 0.174) * 1000)),
        item("00", 566, int((cy + 0.226) * 1000)),
        item("+", 632, int((cy + 0.227) * 1000)),
        item("220", 798, int((cy + 0.228) * 1000)),
        item("Source", 333, int((cy + 0.315) * 1000)),
        item("Use", 666, int((cy + 0.315) * 1000)),
    ]
    got = backpack.read_tooltip(items, 1000, 1000, 0.216, cy)
    assert got[0] == "Chief Stamina"
    assert got[2] == ("Restores 10 Chief Stamina. Used for daily events like "
                      "troop deployment.")


def test_read_tooltip_shape_b_speedup_has_no_buttons_at_all():
    """Shape B (Speedup): no Use/Source button exists at all -- the old
    Use-anchored read_tooltip returned None for every one of these, which
    is why the 2026-09-09 sweep captured zero speedups out of ~20+ tiles
    tapped in that tab. Real measured offsets from cy, 1m Construction
    Speedup (~/wos-chief/20260909T120815Z/086-tile.png, tile at cx=0.404,
    cy=0.218):

        cy+0.120  "1m Construction Speedup"                            name
        cy+0.150  "Speeds up your [Construction] queue by 1 minute."   desc

    Reproduced here at cy=0.300. With no button to anchor on, this shape
    can *only* be read via the tapped-tile position."""
    from native.readers import backpack

    def item(text, cx, cy):
        return {"text": text, "score": 1.0, "box": [cx - 10, cy - 5, cx + 10, cy + 5]}

    cy = 0.300
    items = [
        item("1m Construction Speedup", 500, int((cy + 0.120) * 1000)),
        item("Speeds up your [Construction] queue by 1 minute.", 500, int((cy + 0.150) * 1000)),
    ]
    got = backpack.read_tooltip(items, 1000, 1000, 0.404, cy)
    assert got[0] == "1m Construction Speedup"
    assert got[2] == "Speeds up your [Construction] queue by 1 minute."


def test_read_tooltip_shape_c_other_no_slider_buttons_sit_closer():
    """Shape C (Other): Source/Use exist but there is no quantity slider,
    so the buttons sit much closer to the description than in shape A --
    close enough that shape A's own floor (DESC_FLOOR) would have clipped
    it if the floor were set from shape A's Use offset (0.315) instead of
    a value that works for both. Real measured offsets from cy, Mystery
    Badge (~/wos-chief/20260909T120815Z/110-tile.png, tile at cx=0.593,
    cy=0.257):

        cy+0.121  "Mystery Badge"                                        name
        cy+0.151  "Mystery Badge can be used for trading in the ..."     desc line 1
        cy+0.174  "Shop."                                                desc line 2
        cy+0.242  Source / Use  (no slider -- shape A has one at cy+0.226)

    Reproduced here at cy=0.300: the full two-line description must come
    back, and Source/Use (only 0.058 below the last description line, far
    closer than shape A's 0.089 gap) must not leak into it."""
    from native.readers import backpack

    def item(text, cx, cy):
        return {"text": text, "score": 1.0, "box": [cx - 10, cy - 5, cx + 10, cy + 5]}

    cy = 0.300
    items = [
        item("Mystery Badge", 500, int((cy + 0.121) * 1000)),
        item("Mystery Badge can be used for trading in the Mystery", 500, int((cy + 0.151) * 1000)),
        item("Shop.", 500, int((cy + 0.174) * 1000)),
        item("Source", 333, int((cy + 0.242) * 1000)),
        item("Use", 666, int((cy + 0.242) * 1000)),
    ]
    got = backpack.read_tooltip(items, 1000, 1000, 0.593, cy)
    assert got[0] == "Mystery Badge"
    assert got[2] == "Mystery Badge can be used for trading in the Mystery Shop."


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


def test_queues_reads_the_tech_under_the_tech_research_header():
    """2026-09-10: the panel's research row is a SECTION HEADER ("Tech Research")
    with the tech's own name below it ("Weapons Prep IV" / "08:52:49"). The
    parser looked for a row that named itself "... Research" and excluded
    anything containing "tech", so research.current.name -- the reader's only
    expected path -- was never produced and the whole reader reported failed
    while the entire panel had OCR'd cleanly."""
    import cv2
    from core.vision_engine import VisionEngine
    from native.readers import queues, ReaderResult
    p = os.path.join(REPO, "tests", "fixtures", "local", "frames", "reader-queues-tech-research-header.png")
    if not os.path.exists(p):
        pytest.skip("local frame not present")
    img = cv2.imread(p)
    items = VisionEngine().recognize(img)
    r = queues.parse(ReaderResult("queues"), img, items, p)
    assert r.doc["research"]["current"]["name"] == "weapons_prep_iv"
    assert r.doc["research"]["current"]["remaining_s"] == 8 * 3600 + 52 * 60 + 49
    # The rest of the panel still parses: two builds and three training states.
    assert r.doc["city"]["queues"]["1"]["building"] == "furnace"
    assert r.doc["city"]["queues"]["2"]["building"] == "lancer_camp"
    assert r.doc["city"]["queues"]["2"]["remaining_s"] == 23 * 3600 + 10 * 60 + 47
    assert r.doc["troops"]["training"]["infantry"]["state"] == "completed"
    assert r.settle(queues.EXPECTED).status == "ok"


def test_buildings_is_ok_when_the_panel_simply_had_no_row_for_a_building():
    """The City tab lists only what is queued right now. On 2026-09-10 it offered
    Furnace and Lancer Camp; research_center had no row, and the reader reported
    failed while holding a good furnace 27. Absent rows are unavailability, not
    failure -- only a row whose popup will not parse is a partial."""
    import cv2
    from core.vision_engine import VisionEngine
    from native.readers import buildings, ReaderResult
    p = os.path.join(REPO, "tests", "fixtures", "local", "frames", "reader-buildings-furnace-popup.png")
    if not os.path.exists(p):
        pytest.skip("local frame not present")
    img = cv2.imread(p)
    items = VisionEngine().recognize(img)
    assert buildings.popup_level(items, *img.shape[:2])[:2] == ("furnace", 27)

    res = ReaderResult("buildings")
    res.put("city.buildings.furnace", 27, frame=p)
    missing = [k for k in buildings.WANTED if f"city.buildings.{k}" not in res.provenance]
    assert "research_center" in missing and "furnace" not in missing


def test_buildings_stays_failed_when_it_never_reached_the_city_tab(monkeypatch):
    """2026-09-10: the handle tap landed on the city map, so the frame captured
    as "city-tab" was really the Research Center popup. No ROWS labels matched,
    the reader read nothing, and an unguarded status called that ok. An empty
    read on the wrong screen is a failure, not an idle city."""
    import cv2
    from core.vision_engine import VisionEngine
    from native.readers import buildings
    from native.screen import norm
    p = os.path.join(REPO, "tests", "fixtures", "local", "frames", "reader-buildings-wrong-screen.png")
    if not os.path.exists(p):
        pytest.skip("local frame not present")
    img = cv2.imread(p)
    items = VisionEngine().recognize(img)
    # The popup is perfectly legible: this is the wrong screen, not bad OCR.
    assert buildings.popup_level(items, *img.shape[:2])[:2] == ("research_center", 27)
    assert not any("building queue" in norm(i["text"]) for i in items)

    class Stub:
        """Every navigation call succeeds; every frame is the wrong screen."""
        def frame(self, tag):
            return img, items, p

        def at_home(self, *a, **k):
            return True

        def go_home(self, *a, **k):
            return True

        def tapf(self, *a, **k):
            return True

        def tap_item(self, *a, **k):
            return True

        def find(self, *a, **k):
            return None

    monkeypatch.setattr(buildings.time, "sleep", lambda n: None)
    res = buildings.read(Stub())
    assert res.status == "failed"
    assert "City tab did not show the Building Queue" in res.notes
    assert res.doc == {}
