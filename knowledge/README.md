# Game knowledge base

Tables the planner computes with. The four required tables -- `buildings`,
`troops`, `troop_stats`, `research` -- come from the game client's own
config tables, via wos-mcp's `extract_knowledge` (see "Client-sourced
tables" below), not from the web. `items.json` is built entirely from
in-game backpack tooltips (see "Item catalogue" below). Only the optional
`calendar` table and the gitignored local cross-checks (`knowledge/local/`,
below) are fetched from the web. The readers in `native/` still verify
rows against the screen (`verified_in_game`), whatever a table's source.

| file | source | refresh |
|---|---|---|
| buildings.json | wos-mcp `extract_knowledge` (decompiled client config) | see "Client-sourced tables" below |
| troops.json | wos-mcp `extract_knowledge` (decompiled client config) | see "Client-sourced tables" below |
| troop_stats.json | wos-mcp `extract_knowledge` (decompiled client config) | see "Client-sourced tables" below |
| research.json | wos-mcp `extract_knowledge` (decompiled client config) | see "Client-sourced tables" below |
| unlocks.json | community guides, seeded 2026-09-01 (see docs/designs/adaptive-automation.md) | hand-maintained; observation overrides |
| items.json | in-game backpack tooltips (see "Item catalogue" below) | `native.kb.record_item`, called by the backpack reader on every tooltip it reads |

## Client-sourced tables

`buildings.json`, `troops.json`, `troop_stats.json` and `research.json` are
produced by the private wos-mcp repo, not fetched from wosnerds:

```sh
python -m research.decompiled.extract_knowledge --out /Users/melsawah1/Developer/wos-bot/knowledge --write
```

It reads the decompiled client's own config tables (patch 1.33.9-304) --
the same numbers the game client uses, not a community transcription -- and
writes each table with `_meta.source == "client-config"`,
`client_version`, `client_patch`, `sources` (which client tables were
read) and `source_commit` (the sha of those source files, not a wosnerds
repo commit); `normaliser_version` is 2 for these tables. `verified_in_game`
marks reset to `null` on a client-sourced table the first time it is
written this way (a wosnerds-carried mark predates the client numbers and
is not evidence for them).

Furnace rows now run ordinal 0..80 (31..34 are Fire Crystal levels 30-1..
30-4, 35 is FC1, up to 80 for FC10) -- the terms-restricted
`knowledge/local/overlay.json` Fire Crystal merge (below) is no longer
needed for furnace and never shadows a committed row (Task 4).
`buildings.json` rows carry both `power` (the gain for that level) and
`power_total` (cumulative through that level). `troops.json` rows no
longer carry `points`. `research.json` levels carry `stat_total`
(cumulative displayed stat) next to `stat_addition` (the per-level
increment), plus the same `power`/`power_total` split as buildings;
research node ids are unchanged (191 nodes).

`scripts/refresh_knowledge.py` still exists for two things: the wosnerds
cross-check diff (`--crosscheck`, below -- useful even though buildings.json
is no longer written from wosnerds) and the optional `calendar` table,
which has no client-config source. With `--write`, `refresh()` refuses to
overwrite a table whose committed `_meta.source == "client-config"` --
it still fetches and diffs against the wosnerds source and prints
`== <table>: committed table is client-sourced; pass --replace-client-tables
to overwrite it with the wosnerds fetch` -- unless `--replace-client-tables`
is also passed.

Required (fetched by a plain `refresh_knowledge.py` run, no `--table`
given): `buildings`, `troops`, `troop_stats`, `research`. These are still
the four tables `refresh_knowledge.py`'s `SOURCES` fetches from wosnerds
for the cross-check diff -- not the source of truth for the committed
files any more (see "Client-sourced tables" above) -- so a failure on any
of these still ends the run with exit code 1 after every table has been
attempted, and `--write` on them now refuses to land without
`--replace-client-tables`; the diff and the exit code are unaffected by
the guard.

Optional (fetched only with `--table <name>`, a failure never changes the
exit code): `calendar` (`wos-data` `data/calendar-data.json`) -- its five
events all ship `available-after-age: "unknown"`, so it moves with the
events reader in a later milestone rather than blocking this one.

Licence: wosnerds.com states "All data is free to copy and use"; the repos
carry no LICENSE file, so every table fetched FROM WOSNERDS records the
source URL, commit and fetch date in `_meta` and the data is treated as
revocable. This does not apply to the client-sourced tables above, whose
`_meta.source_commit` points at the decompiled client's own source files,
not a wosnerds repo. The other exception is `unlocks.json`: it is
hand-maintained (community guides, not a fetch at all), so its `_meta` has
no `source_commit` -- there is no commit to point at. It is not checked by
a refresh; its accuracy is instead the capability reporter's job, via each
feature's `last_verified` date and the STALE flag
`scripts/capability_report.py` raises past 180 days.

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

## In-game verification

Rows carry `verified_in_game: null | "<snapshot_id>"`. The executor calls
`native.kb.mark_verified("buildings", "furnace", 28, snapshot_id)` when the
cost it read on the Upgrade popup matched the table within 2%
(`native.kb.verify`). A row's mark survives a refresh whenever that row's
own costs did not change (`carry_marks`); it is cleared back to `null` when
they did.

`mark_verified` opens the committed table file directly -- never the
merged, cached table `kb.load()` returns. This means a Fire Crystal furnace
level (ordinal > 30) that exists only via `knowledge/local/overlay.json`
can never be marked verified: the committed file has no row for it, so
`mark_verified` returns `False`, exactly like any other level that doesn't
exist. This is intended, not a bug (C12) -- overlay data is
terms-restricted and must never be written into a committed file, and
`mark_verified` and `record_item` (below) are the only two places in the
knowledge base that write to disk. It stays irrelevant until the tracked
furnace actually passes level 30.

## Item catalogue

`items.json` is built from in-game backpack tooltips, not fetched: the
wiki's item index has no tables, only 421 individual pages, and crawling
them all is the crawling the constraints forbid. The backpack reader
(`native/readers/backpack.py`) already opens every tile's tooltip to read
its name; `native.kb.record_item(name, tab, description, snapshot_id)`
upserts a row per item (`first_seen` set once; every other field refreshes
on every sighting, except `description`, which keeps its existing value
when a later read comes back empty rather than blanking a good read with
a worse one) and `native.kb.items()` reads the catalogue back. `kind`
comes from `knowledge.util.classify_kind` -- the same SPEEDUP_RE match and
keyword rules `fold` uses to route ledger writes, so classification can't
drift between the ledger and the catalogue.

`fold` consults the catalogue first by exact slug match, but only trusts
its stored `kind` when the row's `classifier_version` matches
`knowledge.util.CLASSIFIER_VERSION`; a name the catalogue hasn't seen at
all, or a row stamped with an older (or missing) version, both fall back
to a fresh `classify_kind(name)` call. This is what makes a classify_kind
rule change reach every already-catalogued item at once, rather than only
the next time the backpack reader happens to see that exact tile live
again.

Unlike every vendored table, `items.json` IS committed even though it has
no `source_commit`: its source is this account's own in-game tooltips,
which carry no terms restriction (contrast `knowledge/local/`, above,
whose sources' terms keep it off this repo entirely).

It reads the committed file, mutates the row and writes the whole document
back; the write itself is atomic (`write_table`), but the read-modify-write
is not a transaction. A `scripts/refresh_knowledge.py --write` landing
between the read and the write here is silently clobbered -- don't run a
refresh while marking a row verified.

## Knowledge freshness

`native.kb.freshness(kb=None, now=None, stale_days=30)` returns
`[(table, age_days, stale)]` computed from each table's `_meta.fetched_at`;
a table with no `_meta` (an optional table not yet fetched, or a hand-built
test kb) is omitted rather than reported as fresh. The chief report prints
it under Warnings (`knowledge: buildings 12 d, research 12 d`) and, for any
table past the threshold, a one-line refresh hint
(`knowledge table buildings is 40 days old: uv run python
scripts/refresh_knowledge.py --table buildings`); `native.report.doctor`
prints the same hint.
