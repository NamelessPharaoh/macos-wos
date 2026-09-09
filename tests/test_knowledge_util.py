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


def test_slugify_and_normalise_slug_agree_on_apostrophes():
    """Fix round 2, item 3: knowledge/util.py::slugify (backpack items,
    other screen-read names) and knowledge/normalise.py::slug (the
    vendored tables' own slugger, used by knowledge/buildings.json's keys
    and native/kb.py::UNTRACKED_ASSUMED_MET) used to disagree on
    apostrophes -- "Hunter's Hut" was 'hunter_s_hut' from one and
    'hunters_hut' from the other. Unreachable only because no reader read
    a building whose screen name has an apostrophe yet; the day one does,
    a `prerequisites()` lookup under the wrong slug would find nothing,
    fall through to the assumed-met list, and report a now-tracked
    building as merely "assumed met" -- the exact silent-satisfaction bug
    that list exists to prevent. Both must produce the same slug for every
    apostrophe-bearing name, not just for "Hunter's Hut"."""
    from knowledge import normalise

    for name in ("Hunter's Hut", "Chief's Charm", "O'Brien's Camp", "Marksman's Guild"):
        assert ku.slugify(name) == normalise.slug(name), name


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


def test_classify_kind_rules_are_fingerprinted_by_the_current_version():
    """Fix round 2, item 1: the version gate only helps if something forces
    CLASSIFIER_VERSION to move whenever classify_kind's rules actually
    change. A test pinning four literal name -> kind pairs did not: a
    wholly new rule for a wholly new family (e.g. the "resource box"/"chest"
    rule this module's docstring used to invite) touches none of the four
    pinned names, so that suite stayed green with the version left stale.

    This pins a fingerprint of the RULES themselves (SPEEDUP_RE's pattern
    plus the source of speedup_duration and classify_kind), not a sample of
    their outputs, keyed by CLASSIFIER_VERSION. Editing a rule -- for any
    input, not just a pre-selected few -- moves the fingerprint; the lookup
    then finds the OLD fingerprint still pinned under the unbumped version
    number and this test goes red. The only way back to green is to bump
    CLASSIFIER_VERSION to a version this dict has no entry for and add one.

    Verified by reproducing the original failure (2026-09-09): added the
    exact docstring-invited rule
        if "resource box" in n or "chest" in n:
            return "resource_box"
    to classify_kind, left CLASSIFIER_VERSION at 1, and reran the suite --
    this test failed (fingerprint mismatch) while the old four-pair test
    still passed, which is exactly the gap this test closes."""
    fp = ku._classifier_fingerprint()
    expected = ku.CLASSIFIER_RULES_FINGERPRINTS.get(ku.CLASSIFIER_VERSION)
    assert expected is not None, (
        f"CLASSIFIER_VERSION {ku.CLASSIFIER_VERSION!r} has no pinned fingerprint in "
        "CLASSIFIER_RULES_FINGERPRINTS -- add one for the rules as they stand now"
    )
    assert fp == expected, (
        "classify_kind's rules changed (SPEEDUP_RE, speedup_duration or classify_kind "
        "itself) but CLASSIFIER_VERSION was not bumped -- bump it in knowledge/util.py "
        "and pin a new fingerprint for the new version in CLASSIFIER_RULES_FINGERPRINTS"
    )


def test_speedup_names_use_the_order_the_game_actually_prints():
    """Live sweep 2026-09-09. The game names these "1m Construction Speedup":
    duration FIRST, then the queue. SPEEDUP_RE originally only allowed
    "Construction 1m Speedup", so on every real name the type group failed to
    match, the duration group (which had to sit immediately before "speedup")
    failed too, and classify_kind returned "other" for all eight speedups on
    the account. The consequence was invisible in the database rather than
    loud: `backpack.speedups.<type>.<duration>` had 45 rows, every one null,
    because fold never routed a single speedup to them.

    Names below are verbatim from the sweep. The reversed order and the
    bare/trailing forms are kept so a future rename in either direction stays
    covered."""
    from knowledge.util import classify_kind, speedup_duration

    real = {
        "1m Construction Speedup": ("construction", "1m"),
        "5m Construction Speedup": ("construction", "5m"),
        "1h Construction Speedup": ("construction", "1h"),
        "1m Training Speedup": ("training", "1m"),
        "5m Training Speedup": ("training", "5m"),
        "1h Training Speedup": ("training", "1h"),
        "1m Healing Speedup": ("healing", "1m"),
        "5m Healing Speedup": ("healing", "5m"),
    }
    for name, want in real.items():
        assert classify_kind(name) == "speedup", name
        assert speedup_duration(name) == want, name

    # the other orders and forms must keep working
    assert speedup_duration("Construction 5m Speedup") == ("construction", "5m")
    assert speedup_duration("1m Speedup") == ("general", "1m")
    assert speedup_duration("Speedup (5m)") == ("general", "5m")

    # and nothing else becomes a speedup
    for name in ("Fire Crystal", "Refined Fire Crystal", "Mystery Badge", "1K Meat"):
        assert speedup_duration(name) is None, name
        assert classify_kind(name) != "speedup", name
