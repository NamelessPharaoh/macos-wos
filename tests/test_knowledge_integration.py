"""M1 final review, Finding 1: the milestone's contract chain is a normaliser's
output key (Task 2, `knowledge/normalise.py`) becoming a committed
`<table>.json`'s top-level key (Task 1's `scripts/refresh_knowledge.py`
writes it), which becomes a `native/kb.py` `TABLES` key that `native/kb.py`
`load()` reads back as `doc[key]` (Task 3).

`tests/test_refresh_knowledge.py::test_registry_shape_holds_now_and_after_task2`
already proves the first link: `SOURCES | OPTIONAL_SOURCES == NORMALISERS`.
This module proves the second and third: for every entry in `native/kb.py`'s
`TABLES`, the corresponding committed file's single non-`_meta` top-level key
equals the `TABLES` key. No gitignored fixtures are needed -- the four
required tables are committed in this repo. `events` (`calendar.json`) is
deliberately not shipped in M1 (see knowledge/README.md), so it is skipped,
not failed, when its file is absent.
"""
import json
import os

import pytest

from native import kb


@pytest.mark.parametrize("key, fname", sorted(kb.TABLES.items()))
def test_table_file_top_level_key_matches_TABLES_key(key, fname):
    path = os.path.join(kb.KNOWLEDGE_DIR, fname)
    if not os.path.exists(path):
        if key in kb.REQUIRED_TABLES:
            pytest.fail(f"{fname} is missing but {key!r} is a required table")
        pytest.skip(f"{fname} not shipped in M1 (optional table {key!r})")
    with open(path) as f:
        doc = json.load(f)
    non_meta_keys = [k for k in doc if k != "_meta"]
    assert non_meta_keys == [key], f"{fname}: expected top-level key {key!r}, found {non_meta_keys}"


def test_every_required_table_is_committed():
    for key in kb.REQUIRED_TABLES:
        path = os.path.join(kb.KNOWLEDGE_DIR, kb.TABLES[key])
        assert os.path.exists(path), f"required table {key!r} ({kb.TABLES[key]}) is not committed"
