"""knowledge/local_sources.py: HTML/JS parsers for the terms-restricted
cross-check sites and the overlay-producing `crosscheck()` (A5/B10).

The three site excerpts (whiteoutdata.com, whiteoutsurvival.wiki, wostools.net)
are terms-restricted and never committed (B5): they live in
tests/fixtures/local/knowledge/ (gitignored) and their tests skip when the
fixture is absent, the same way the screen goldens skip on a fresh clone.
Regenerate them with the excerpts in .superpowers/sdd/2026-09-08-wos-knowledge-base/
task-4-brief-full.md, Task 4 Step 1.
"""
import os

import pytest

from knowledge import local_sources as ls

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "local", "knowledge")


def _read_local(name):
    path = os.path.join(FIX, name)
    if not os.path.exists(path):
        pytest.skip(f"{path} not present (terms-restricted fixture kept off the repo, B5)")
    with open(path) as f:
        return f.read()


def test_parse_amount_and_time():
    assert ls.parse_amount("140M") == 140_000_000 and ls.parse_amount("7.4M") == 7_400_000
    assert ls.parse_amount("1,213,100") == 1_213_100 and ls.parse_amount("–") == 0 and ls.parse_amount("132") == 132
    assert ls.parse_time("29d 2h 52m") == 29 * 86400 + 2 * 3600 + 52 * 60
    assert ls.parse_time("7d") == 7 * 86400 and ls.parse_time("") == 0


def test_whiteoutdata_furnace_rows_by_ordinal():
    doc = ls.whiteoutdata_furnace(_read_local("whiteoutdata_furnace_excerpt.html"))
    f = doc["furnace"]
    assert f["28"]["meat"] == 190_000_000 and f["28"]["seconds"] == 29 * 86400 + 2 * 3600 + 52 * 60 and f["28"]["power"] == 1_213_100
    assert f["28"]["prerequisites"] == {"embassy": 27, "research_center": 27}
    assert f["31"]["label"] == "30-1" and f["31"]["fire_crystals"] == 132 and f["31"]["refined_fire_crystals"] == 0
    assert f["80"]["label"] == "FC 10" and f["80"]["refined_fire_crystals"] == 140 and f["80"]["prerequisites"] == {"embassy": 75, "marksman_camp": 75}


def test_wiki_furnace_row():
    doc = ls.wiki_furnace(_read_local("wiki_furnace_excerpt.html"))
    r = doc["furnace"]["28"]
    assert (r["meat"], r["wood"], r["coal"], r["iron"]) == (190_000_000, 190_000_000, 39_000_000, 9_900_000)
    assert r["prerequisites"]["research_center"] == 27


def test_wostools_buildings_from_chunk():
    doc = ls.wostools_buildings(_read_local("wostools_chunk_excerpt.js"))
    assert doc["buildings"]["furnace"]["28"]["meat"] == 190_000_000 and doc["buildings"]["furnace"]["28"]["seconds"] == 2_515_920
    assert "embassy" in doc["buildings"]
    assert ls.wostools_buildings("var x = 1;") == {}


def test_crosscheck_marks_disputed_and_appends_fc_rows():
    """A5's rewritten version of this test (binding over the task body):
    crosscheck() never touches the committed dict, it returns an overlay
    shaped exactly like native/kb.py::_apply_overlay consumes."""
    committed = {"furnace": {"28": {"meat": 190_000_000, "wood": 190_000_000, "coal": 39_000_000, "iron": 9_900_000,
                                     "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_515_920,
                                     "prerequisites": {}, "verified_in_game": None}}}
    wd = {"furnace": {"28": {"label": "28", "meat": 190_000_000, "wood": 190_000_000, "coal": 40_000_000, "iron": 9_900_000,
                              "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_515_920, "power": 1_213_100, "prerequisites": {}},
                       "31": {"label": "30-1", "meat": 67_000_000, "wood": 67_000_000, "coal": 13_000_000, "iron": 3_300_000,
                              "fire_crystals": 132, "refined_fire_crystals": 0, "seconds": 604_800, "power": 1_580_900, "prerequisites": {}}}}
    overlay, lines = ls.crosscheck(committed, {"whiteoutdata": wd})
    assert overlay["buildings"]["furnace"]["28"] == {"power": 1_213_100, "disputed": {"whiteoutdata": {"coal": 40_000_000}}}
    assert overlay["buildings"]["furnace"]["31"]["source"] == "whiteoutdata" and overlay["buildings"]["furnace"]["31"]["fire_crystals"] == 132
    assert any("furnace.28 coal" in l for l in lines) and any("furnace.31 added from whiteoutdata" in l for l in lines)


def test_crosscheck_ignores_wiki_and_wostools_power():
    """B10: power is copied from whiteoutdata only; other sources' power
    fields (and wostools, which carries none) never reach the overlay."""
    committed = {"furnace": {"28": {"meat": 190_000_000, "wood": 190_000_000, "coal": 39_000_000, "iron": 9_900_000,
                                     "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_515_920,
                                     "prerequisites": {}, "verified_in_game": None}}}
    wiki = {"furnace": {"28": {"label": "28", "meat": 190_000_000, "wood": 190_000_000, "coal": 39_000_000, "iron": 9_900_000,
                                "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_515_920, "power": 999, "prerequisites": {}}}}
    overlay, lines = ls.crosscheck(committed, {"wiki": wiki})
    assert overlay["buildings"] == {}
    assert lines == []


def test_crosscheck_without_local_dir():
    """B10: no local docs at all -> an empty overlay and zero findings, not
    an error (this is what `--crosscheck` sees on a fresh clone)."""
    overlay, lines = ls.crosscheck({"furnace": {}}, {})
    assert overlay["buildings"] == {}
    assert lines == []
