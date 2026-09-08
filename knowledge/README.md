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
`--local`. Their terms restrict reproduction, so they stay on this machine
and only annotate committed rows as `disputed` when they disagree.

Refresh prints a diff and writes nothing without `--write`. A patch on one
table never stops the others: each table's fetch and normalise step is
guarded, prints its own failure line (`FETCH FAILED` / `NORMALISE FAILED`)
and the run moves on. `verified_in_game` marks carried over from a previous
fetch survive a refresh whenever the row's own costs did not change.

## Regenerating the integration fixtures

`tests/test_knowledge_integration.py` proves `SOURCES`, `NORMALISERS` and
`native/kb.py`'s `TABLES` agree by running the refresh against real cached
copies of the five source files. Those copies live in
`tests/fixtures/local/knowledge/sources/` (gitignored, so the test skips on
a fresh clone); to regenerate them:

```bash
mkdir -p tests/fixtures/local/knowledge/sources && cd $_ && \
for u in \
  https://raw.githubusercontent.com/wosnerdwarriors/website-index/main/calculator/data/construction.json \
  https://raw.githubusercontent.com/wosnerdwarriors/website-index/main/calculator/data/troops.json \
  https://raw.githubusercontent.com/wosnerdwarriors/wos-data/main/data/research-upgrades.json \
  https://raw.githubusercontent.com/wosnerdwarriors/wos-data/main/data/troop-stats.json \
  https://raw.githubusercontent.com/wosnerdwarriors/wos-data/main/data/calendar-data.json ; \
do curl -sSLO "$u"; done
```
