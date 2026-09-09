"""knowledge/util.py: the layering home (B9) for the moved parsers, the
furnace/Embassy/camp ordinal convention (B6/B7) and the atomic table writer.
tests/test_native_screen.py proves the moved parsers still behave through
native.screen's re-export; these are direct spot checks that the module is
self-sufficient (it imports nothing from native/, per E1)."""
import os

import pytest

from knowledge import util as ku


def test_parse_number_moved_intact():
    assert ku.parse_number("36.30M") == (36_300_000, False)
    assert ku.parse_number("1,423") == (1423, True)
    assert ku.parse_number("x") == (None, True)


def test_parse_ratio_moved_intact():
    assert ku.parse_ratio("71/200") == (71, 200)
    assert ku.parse_ratio("a/b") is None


def test_parse_duration_moved_intact():
    assert ku.parse_duration("1h 20m") == 4800
    assert ku.parse_duration("soon") is None


@pytest.mark.parametrize("label,ordinal", [
    ("27", 27), ("1", 1), ("30", 30),
    ("30-3", 33), ("30-1", 31), ("30-4", 34),
    ("FC1", 35), ("FC 10", 80), ("FC9", 75), ("FC10", 80),
    ("FC9-4", 79), ("FC1-1", 36),
])
def test_furnace_ordinal(label, ordinal):
    assert ku.furnace_ordinal(label) == ordinal


@pytest.mark.parametrize("label", [
    "27-3",      # N-sub only valid with N == 30
    "FC1-5",     # sub out of 1..4
    "FC11",      # fc out of 1..10
    "FC0",       # fc out of 1..10
    "31-1",      # N-sub only valid with N == 30
    "not a level",
    "",
])
def test_furnace_ordinal_is_strict(label):
    with pytest.raises(ValueError):
        ku.furnace_ordinal(label)


def test_next_level_label_matches_the_furnace_chain():
    assert [ku.next_level_label(x) for x in (27, 30, 34, 35, 79)] == ["28", "30-1", "FC1", "FC1-1", "FC10"]


def test_furnace_ordinal_and_next_level_label_are_inverses_across_the_chain():
    # every label next_level_label can produce must round-trip through
    # furnace_ordinal back to the ordinal that produced it (30 -> 80)
    for ordinal in range(29, 80):
        label = ku.next_level_label(ordinal)
        assert ku.furnace_ordinal(label) == ordinal + 1, label


def test_write_table_is_atomic_and_pretty(tmp_path):
    p = tmp_path / "t.json"
    ku.write_table(str(p), {"_meta": {"x": 1}, "b": {"1": {"meat": 2}}})
    text = p.read_text()
    assert text.startswith('{\n  "_meta"') and text.endswith("}\n")
    assert not os.path.exists(str(p) + ".tmp")


def test_write_table_creates_missing_directories(tmp_path):
    p = tmp_path / "nested" / "dir" / "t.json"
    ku.write_table(str(p), {"a": {"1": {"v": 1}}})
    assert p.exists()


def test_slugify_moved_intact():
    assert ku.slugify("Supreme Infantry") == "supreme_infantry"
    assert ku.slugify("") == "unnamed"


def test_speedup_duration_extracts_kind_and_duration():
    assert ku.speedup_duration("Construction Speedup 1h") == ("construction", "1h")
    assert ku.speedup_duration("5m Speedup") == ("general", "5m")
    assert ku.speedup_duration("Fire Crystal") is None


def test_classify_kind_reuses_speedup_duration_and_the_fire_crystal_keyword():
    assert ku.classify_kind("5m Speedup") == "speedup"
    assert ku.classify_kind("Fire Crystal") == "fire_crystal"
    assert ku.classify_kind("Refined Fire Crystal") == "fire_crystal"
    assert ku.classify_kind("1 Gems") == "other"


def test_classifier_version_is_a_plain_int():
    """native/kb.py::record_item stamps every catalogue row with this, and
    native/readers/backpack.py::fold compares against it to decide whether
    a stored kind is still trustworthy (fix round 1, item 2). A future
    classifier fix bumps this constant; nothing else should have to change
    for that bump to reach every already-catalogued item."""
    assert isinstance(ku.CLASSIFIER_VERSION, int) and ku.CLASSIFIER_VERSION >= 1
