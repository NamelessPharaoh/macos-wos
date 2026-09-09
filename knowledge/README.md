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
so they stay on this machine. `--crosscheck` is report-only (D-T2, a
user decision that overrides the earlier A5 "annotate the committed table"
design) and never edits `knowledge/buildings.json` (spec D8). It only parses
and compares furnace levels with ordinal >= 26 (the account is upgrading
26 -> 27; two known whiteoutdata/wosnerds disagreements below that floor,
at levels 11 and 17, are out of scope by design). It writes two gitignored
files:

- `knowledge/local/crosscheck.json` -- per furnace level >= 26 the
  committed table has, every source's costs/times plus a `disagrees` list
  naming which "source.field" pairs differ by more than 2%. Report only:
  nothing here is ever written back onto the committed table.
- `knowledge/local/overlay.json` -- the Fire Crystal furnace rows (ordinal
  31-80) that whiteoutdata carries and the committed table lacks entirely,
  each a full row marked `"source": "whiteoutdata"` with `prerequisites: {}`
  (no prerequisite parsing at all -- it is the part that breaks on the live
  pages). `native/kb.py::load` merges this one at read time; without it,
  `power_gain("building", ...)` is `None` and FC rows are absent.
  `prerequisites()` on an overlay row returns `([], ["unknown: overlay
  row"])` rather than reading empty prerequisites as satisfied.

A local source whose every parsed row comes back with zero cost and time
(a header-label mismatch on the live page, not real data) is dropped with
one printed line naming it, the same way `wostools_buildings` drops an
unrecognised bundle shape -- never reported as a pile of disagreements.

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
