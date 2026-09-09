# Game knowledge base

Tables the planner computes with. Nothing here is read from the game; the
readers in `native/` verify rows against the screen (`verified_in_game`).

| file | source | refresh |
|---|---|---|
| buildings.json | wosnerdwarriors/website-index `calculator/data/construction.json` | `uv run python scripts/refresh_knowledge.py --table buildings` |
| troops.json | website-index `calculator/data/troops.json` + wos-data `data/troop-stats.json` | `--table troops --table troop_stats` |
| research.json | wos-data `data/research-upgrades.json` | `--table research` |

Required (fetched by a plain `refresh_knowledge.py` run, no `--table` given):
`buildings`, `troops`, `troop_stats`, `research`. A failure on any of these
ends the run with exit code 1 after every table has been attempted.

Optional (fetched only with `--table <name>`, a failure never changes the
exit code): `calendar` (`wos-data` `data/calendar-data.json`) -- its five
events all ship `available-after-age: "unknown"`, so it moves with the
events reader in a later milestone rather than blocking this one.

Licence: wosnerds.com states "All data is free to copy and use"; the repos
carry no LICENSE file, so every `_meta` records the source URL, commit and
fetch date and the data is treated as revocable.

`knowledge/local/` (gitignored, never committed): cross-check tables fetched
from whiteoutdata.com, whiteoutsurvival.wiki and wostools.net with
`--local` (`knowledge/local_sources.py`). Their terms restrict reproduction,
so they stay on this machine. `--crosscheck` diffs them against the
committed furnace rows and writes `knowledge/local/overlay.json` -- it never
edits `knowledge/buildings.json` itself (spec D8). The overlay holds only
`power` (copied from whiteoutdata alone) and `disputed: {source: {field:
value}}` for levels the committed table already has, plus full Fire
Crystal rows (ordinal > 30, `"source": "whiteoutdata"`) for levels it
lacks. `native/kb.py::load` merges it at read time; without it,
`power_gain("building", ...)` is `None` and FC rows are absent.

Refresh prints a diff and writes nothing without `--write`. A patch on one
table never stops the others: each table's fetch and normalise step is
guarded, prints its own failure line (`FETCH FAILED` / `NORMALISE FAILED`)
and the run moves on. `verified_in_game` marks carried over from a previous
fetch survive a refresh whenever the row's own costs did not change.

## The registry/table linkage is tested, not just documented

The chain is: a normaliser's output key (`knowledge/normalise.py`) becomes a
committed `<table>.json`'s top-level key (`scripts/refresh_knowledge.py`
writes it), which becomes a `native/kb.py` `TABLES` key that `native/kb.py`
reads back as `doc[key]`. Two tests together prove the whole chain holds:
`tests/test_refresh_knowledge.py::test_registry_shape_holds_now_and_after_task2`
proves `SOURCES | OPTIONAL_SOURCES == NORMALISERS`; `tests/test_knowledge_integration.py`
proves that every `native/kb.py` `TABLES` key matches the single non-`_meta`
top-level key of its committed file. Neither test needs fixtures -- the four
required tables are committed in this repo, and the optional `events`
(`calendar.json`, not shipped in M1) is skipped rather than failed when its
file is absent.
