"""knowledge/local_sources.py: HTML/JS parsers for the terms-restricted
cross-check sites and the report-only `crosscheck()` (D-T2, binding over the
earlier A5/B10 overlay-annotation design).

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


def test_parse_amount_distinguishes_a_missing_column_from_a_real_zero():
    """Finding 1 (2026-09-09 fix round): None is what a row dict actually
    hands parse_amount when a column's header was never found on the page at
    all (`_table_rows` only populates keys it matched) -- that must come
    back None, never 0, so it can't be mistaken for a genuinely parsed zero
    (a present cell reading '-'/'--'/''  , which test_parse_amount_and_time
    above already pins at 0)."""
    assert ls.parse_amount(None) is None


def test_whiteoutdata_furnace_rows_by_ordinal():
    doc = ls.whiteoutdata_furnace(_read_local("whiteoutdata_furnace_excerpt.html"))
    f = doc["furnace"]
    assert f["28"]["meat"] == 190_000_000 and f["28"]["seconds"] == 29 * 86400 + 2 * 3600 + 52 * 60 and f["28"]["power"] == 1_213_100
    assert "prerequisites" not in f["28"]  # D-T2: no prerequisite parsing at all
    assert f["31"]["label"] == "30-1" and f["31"]["fire_crystals"] == 132 and f["31"]["refined_fire_crystals"] == 0
    assert f["80"]["label"] == "FC 10" and f["80"]["refined_fire_crystals"] == 140


def test_whiteoutdata_furnace_applies_the_scope_floor():
    """D-T2: only ordinal >= FLOOR_ORDINAL (26) is parsed at all. The fixture
    carries a level 20 row precisely below the floor (finding 4, 2026-09-09
    fix round -- without it every other row is already >= 26, so this
    assertion could never fail even with the floor check deleted)."""
    assert ls.FLOOR_ORDINAL == 26
    doc = ls.whiteoutdata_furnace(_read_local("whiteoutdata_furnace_excerpt.html"))
    assert "20" not in doc["furnace"]
    assert all(int(o) >= ls.FLOOR_ORDINAL for o in doc["furnace"])


def test_wiki_furnace_row():
    doc = ls.wiki_furnace(_read_local("wiki_furnace_excerpt.html"))
    r = doc["furnace"]["28"]
    assert (r["meat"], r["wood"], r["coal"], r["iron"]) == (190_000_000, 190_000_000, 39_000_000, 9_900_000)
    assert "prerequisites" not in r  # D-T2: no prerequisite parsing at all


def test_wiki_furnace_all_zero_rows_are_dropped():
    """Finding 2 (2026-09-09 fix round): a source whose every parsed row has
    zero cost/time (a header-label mismatch on the live page, not real data)
    is dropped like an unrecognised bundle shape, not reported as a pile of
    disagreements."""
    html = ('<table><tr><th>Level</th><th>Nonsense</th></tr>'
            '<tr><td>28</td><td>whatever</td></tr></table>')
    assert ls.wiki_furnace(html) == {"furnace": {}}


def test_whiteoutdata_furnace_drops_the_source_when_an_overlay_only_column_vanishes():
    """Finding 1 (2026-09-09 fix round): if whiteoutdata renames just its
    Fire Crystal column, meat/wood/coal/iron/power still parse fine for
    every row -- the pre-existing all-fields-all-rows-zero check never fires
    for that. fire_crystals is None (column not found) for every row here,
    which is exactly the per-field signal _drop_if_all_zero now checks; the
    source must be dropped rather than shipping power/fire_crystals: 0 into
    the overlay (the two fields nothing else in the system can contradict)."""
    html = ('<table><tr><th>Level</th><th>Wood</th><th>Meat</th><th>Coal</th><th>Iron</th>'
            '<th>Upgrade Time</th><th>Power</th></tr>'
            '<tr><td>30-1</td><td>67M</td><td>67M</td><td>13M</td><td>3.3M</td><td>7d</td><td>1,580,900</td></tr></table>')
    assert ls.whiteoutdata_furnace(html) == {"furnace": {}}


def test_wostools_buildings_from_chunk():
    doc = ls.wostools_buildings(_read_local("wostools_chunk_excerpt.js"))
    assert doc["buildings"]["furnace"]["28"]["meat"] == 190_000_000 and doc["buildings"]["furnace"]["28"]["seconds"] == 2_515_920
    assert "embassy" in doc["buildings"]
    assert ls.wostools_buildings("var x = 1;") == {}


def _committed_row(**overrides):
    row = {"meat": 190_000_000, "wood": 190_000_000, "coal": 39_000_000, "iron": 9_900_000,
           "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_515_920,
           "prerequisites": {}, "verified_in_game": None}
    row.update(overrides)
    return row


def test_crosscheck_reports_in_scope_disagreements_without_touching_committed_shape():
    """D-T2: a level the committed table has (>= floor) gets a report entry
    with every source's costs/times and a disagrees list -- no `disputed` or
    `power` is ever written back onto that level (that lived in the
    superseded A5 overlay design)."""
    committed = {"furnace": {"28": _committed_row()}}
    wd = {"furnace": {"28": {"label": "28", "meat": 190_000_000, "wood": 190_000_000, "coal": 40_000_000, "iron": 9_900_000,
                              "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_515_920, "power": 1_213_100}}}
    report, overlay, lines = ls.crosscheck(committed, {"whiteoutdata": wd})
    entry = report["furnace"]["28"]
    assert entry["committed"]["coal"] == 39_000_000
    assert entry["whiteoutdata"]["coal"] == 40_000_000
    assert entry["disagrees"] == ["whiteoutdata.coal"]
    assert "power" not in entry and "disputed" not in entry
    assert overlay["buildings"] == {}  # nothing to add to the overlay -- level 28 is already committed
    assert any("furnace.28 coal" in l for l in lines)


def test_crosscheck_appends_fc_rows_to_the_overlay_only():
    """The one thing D-T2 keeps from A5: FC rows (ordinal > 30) the
    committed table lacks entirely go into the overlay, full row, source
    marked, prerequisites hard-coded empty (no parsing, D-T2)."""
    committed = {"furnace": {"28": _committed_row()}}
    wd = {"furnace": {"31": {"label": "30-1", "meat": 67_000_000, "wood": 67_000_000, "coal": 13_000_000, "iron": 3_300_000,
                              "fire_crystals": 132, "refined_fire_crystals": 0, "seconds": 604_800, "power": 1_580_900}}}
    report, overlay, lines = ls.crosscheck(committed, {"whiteoutdata": wd})
    row = overlay["buildings"]["furnace"]["31"]
    assert row["source"] == "whiteoutdata" and row["fire_crystals"] == 132 and row["prerequisites"] == {}
    assert "31" not in report["furnace"]  # FC rows are overlay-only, never in the report
    assert any("furnace.31 added from whiteoutdata" in l for l in lines)


def test_crosscheck_applies_the_scope_floor():
    """D-T2: furnace levels below ordinal 26 are out of scope entirely --
    the two known whiteoutdata/wosnerds disagreements at levels 11 and 17
    (C6) must never appear."""
    committed = {"furnace": {"11": _committed_row(coal=20_000), "28": _committed_row()}}
    wd = {"furnace": {"11": {"label": "11", "meat": 1_300_000, "wood": 1_300_000, "coal": 260_000, "iron": 65_000,
                              "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 27_000, "power": 0}}}
    report, overlay, lines = ls.crosscheck(committed, {"whiteoutdata": wd})
    assert "11" not in report["furnace"]
    assert not any(l.startswith("furnace.11") for l in lines)


def test_crosscheck_without_local_dir():
    """B10 (kept): no local docs at all -> an empty report/overlay and zero
    findings, not an error (this is what `--crosscheck` sees on a fresh
    clone or before `--local` has run)."""
    report, overlay, lines = ls.crosscheck({"furnace": {}}, {})
    assert report["furnace"] == {}
    assert overlay["buildings"] == {}
    assert lines == []
