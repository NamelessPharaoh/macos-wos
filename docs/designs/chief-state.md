# Chief State: the account model and its readers

Promoted from the CEO plan of 2026-09-08 (`/plan-ceo-review`, SELECTIVE EXPANSION) and the eng review of the same day; implementation record in the commit history (`native/`).


## Problem

The daily-collect skill reads gems and power on every item and throws the
numbers away after the money guard. The repo profile (`db/players/<id>.json`)
persists name, state, furnace and alliance only; `gems`, `power`, `stamina`,
`resources.*` exist in the schema and are never written. Nothing records
troops, backpack, heroes, buildings, research, gear or live events. Strategy
advice is therefore from memory, not from the account.

## Vision

### 10x check

One account model, written by whichever product sees the screen (native-app
skill today, emulator bot tomorrow), with a daily history, so a strategy skill
can answer "what should I do this week" from real numbers: troops by tier,
speedups by type, FC stock, hero investment, building chain, live events.

### Baseline (approach B, accepted D1)

New skill `wos-chief-state`. One reader per screen; a reader is a fixed
navigation path, parse rules and validators (the daily-collect pattern: the
screen is read for numbers, never to decide what to press). Shared native-app
helpers and the readers live in the repo package `native/` (D4) so both skills
import one copy. Model = SQLite database with a JSON document per snapshot plus
a flattened field table (user decision 2026-09-08: "use a nosql or a sqlite
database"). Terminal chief-sheet report with deltas; HTML dashboard from
history.

## Scope Decisions

| # | Proposal | Effort | Decision | Reasoning |
|---|---|---|---|---|
| D1 | Approach B: new skill + shared helpers + versioned model | L (see sizing) | ACCEPTED | Captures the fields strategy needs; planner stays a separate skill |
| D2 | Review mode | - | SELECTIVE EXPANSION | Greenfield model, small independent add-ons |
| D3.1 | Gift-code API identity cross-check | S | SKIPPED | User: no third-party call |
| D3.2 | Snapshot chained at the end of every wos-daily-collect run | S | ACCEPTED | Free daily time series |
| D3.3 | Write the fields core/capability.py account_state() reads | S | ACCEPTED (forward-looking, see note) | One shared model for bot and skill |
| D3.4 | Full backpack ledger (every tab, scrolled) | M | ACCEPTED, phased | Strategic tabs first, remaining tabs in the same plan |
| D3.5 | Events calendar reader (name + remaining time) | M | ACCEPTED | SvS/event timing is a strategy input |
| D3.6 | Chief-sheet dashboard page from history | M | ACCEPTED, last phase | Visual trends; built once history has rows |
| D4 | Shared helpers + readers as repo package `native/` | S | ACCEPTED | One tested copy under git; collect.py re-verified after the import swap |
| D5 | Storage: SQLite (stdlib `sqlite3`, JSON1) instead of JSON files | S | USER DECISION | History and trends become queries; no new dependency |

## Hard rules (inherited from wos-daily-collect, applied to reading)

1. **Main account only.** The HUD reader runs first (no taps): power in the
   tens of millions and `VIP 7` mean main; a five-digit power means the
   tutorial account and the run stops before any tap. The first navigating
   reader is the Chief Profile: player id, name, state. The confirmed main id
   is stored once in `players.is_main` (or given as `WOS_MAIN_ID`); an OCR'd id
   that differs from it aborts the run. No confirmed id = nothing written.
2. **Navigation includes opening an entity's detail view** (a hero card, a
   building, a backpack item, an event tab). Every such panel is a spend surface
   (Upgrade / Ascend / Use / Train / Buy). The only presses allowed anywhere in
   this skill are: bottom-bar labels, fixed HUD entry spots, tab labels, list
   rows/tiles that open a read-only detail, scroll swipes, and close controls.
   `DANGER_RE`/`PRICE_RE` from the shared helpers run on every frame; a matched
   label is never pressed and the frame is still read.
3. **Money guard around the whole snapshot**: gems and power read on the HUD
   before and after. A gem drop aborts and marks the snapshot `aborted`;
   nothing from an aborted run is written. A power rise is legitimate during a
   read-only run (a timer completes) and is recorded as `power_rose = 1` with a
   warning in the report; the sheet is still written.
4. **No Escape key.**

## Saved data format (SQLite)

One database, `db/wos.sqlite`, local-only; `.gitignore` gains `db/*.sqlite*`
in phase 1 (today only `db/players/*.json` is ignored). Opened with
`sqlite3` from the standard library, WAL mode, one writer (the snapshot).
Frames stay on disk in `~/wos-chief/<stamp>/` and are referenced by path.

```sql
CREATE TABLE players (
  id            TEXT PRIMARY KEY,           -- in-game player id from the Chief Profile
  name          TEXT, state INTEGER,
  is_main       INTEGER NOT NULL DEFAULT 0, -- exactly one row may be 1
  state_opened_on TEXT,                     -- ISO date, operator input (--state-opened)
  first_seen    TEXT NOT NULL, last_seen TEXT NOT NULL);

CREATE TABLE snapshots (
  id            TEXT PRIMARY KEY,           -- "20260908-1251"
  player_id     TEXT NOT NULL REFERENCES players(id),
  taken_at      TEXT NOT NULL,              -- ISO 8601 with offset
  source        TEXT NOT NULL,              -- "native-app" | "emulator"
  run_dir       TEXT NOT NULL,
  status        TEXT NOT NULL,              -- "ok" | "partial" | "aborted"
  sections      TEXT NOT NULL,              -- JSON {"hud":"ok","troops":"partial",...}
  duration_s    INTEGER,
  gems_before   INTEGER, gems_after INTEGER,
  power_before  INTEGER, power_after INTEGER, power_rose INTEGER NOT NULL DEFAULT 0,
  schema_version INTEGER NOT NULL,
  doc           TEXT NOT NULL);             -- the full sheet as JSON (JSON1-queryable)

CREATE TABLE fields (                       -- one row per leaf value in doc
  snapshot_id   TEXT NOT NULL REFERENCES snapshots(id),
  player_id     TEXT NOT NULL,
  path          TEXT NOT NULL,              -- dotted, e.g. "troops.by_tier.infantry.t9"
  value_num     REAL, value_text TEXT,      -- exactly one is non-null
  exact         INTEGER NOT NULL DEFAULT 1, -- 0 when parsed from "36.30M"
  raw           TEXT, frame TEXT, score REAL, method TEXT,
  status        TEXT NOT NULL,              -- "ok" | "rejected: <reason>" | "carried"
  PRIMARY KEY (snapshot_id, path));
CREATE INDEX fields_series ON fields(player_id, path, snapshot_id);

CREATE VIEW latest AS                       -- the current sheet, one row per field
  SELECT f.* FROM fields f JOIN snapshots s ON s.id = f.snapshot_id
  WHERE s.status != 'aborted'
    AND s.taken_at = (SELECT MAX(taken_at) FROM snapshots s2
                      WHERE s2.player_id = f.player_id AND s2.status != 'aborted');
```

Sheet document (`schema_version` 1; the same object is stored in
`snapshots.doc` and flattened into `fields`):

```json
{
  "identity": {"id": "12345678", "name": "...", "state": 1234, "state_age_days": 434},
  "progress": {"furnace": {"level": 27, "fc": 0, "sub": 0, "ordinal": 27,
                           "upgrading": {"to": 28, "remaining_s": 818552}},
               "vip": {"level": 7}, "power": 35020652, "kills": 0},
  "economy": {"gems": 1423,
              "resources": {"meat": 36300000, "wood": null, "coal": 8800000, "iron": null},
              "stamina": {"value": 71, "cap": 200}},
  "city": {"survivors": {"value": 32, "cap": 32},
           "buildings": {"furnace": 27, "embassy": null, "command_center": null,
                         "research_center": null, "infantry_camp": null,
                         "lancer_camp": null, "marksman_camp": null,
                         "infirmary": null, "storehouse": 26},
           "queues": [{"builder": 1, "building": "storehouse", "to": 27, "remaining_s": 0}]},
  "research": {"current": {"name": "Lance Upgrade V", "remaining_s": 0},
               "tabs": {"growth": {"done": 12, "total": 40}}},
  "troops": {"by_tier": {"infantry": {"t9": 0}, "lancer": {}, "marksman": {}},
             "totals": {"infantry": 0, "lancer": 0, "marksman": 0},
             "wounded": {"value": 0, "cap": 0},
             "capacity": {"deploy": 0, "rally": 0},
             "training": {"infantry": {"tier": 9, "amount": 0, "remaining_s": 0}}},
  "heroes": {"Jessie": {"rarity": "epic", "level": 60, "stars": 3,
                        "gear": {"goggles": null, "gloves": null, "belt": null, "boots": null}}},
  "gear": {"chief": {"helmet": {"tier": "purple", "stars": 2}, "watch": {}, "jacket": {},
                     "pants": {}, "ring": {}, "cane": {}},
           "charms": {"helmet": [4, 4, 3]}},
  "backpack": {"speedups": {"general": {"1m": 0, "5m": 0, "1h": 0, "3h": 0, "8h": 0, "24h": 0},
                            "construction": {}, "research": {}, "training": {}, "healing": {}},
               "resource_boxes": {}, "fire_crystals": 0, "refined_fire_crystals": 0,
               "hero_shards": {}, "gear_materials": {}, "charm_materials": {},
               "items": {"Other/Advanced Teleport": 3}},
  "events": {"State of Power": {"remaining_s": 172800, "tab": 2}},
  "alliance": {"name": "...", "members": 52, "cap": 100, "rank": "R1"}
}
```

Rules of the format:

- **Missing is `null`, not 0.** A reader that did not run leaves `null` and
  `sections.<reader>` is `skipped` (not attempted), `partial` (entered, some
  fields failed validation or OCR), `failed` (entry not found or left early),
  `ok`.
- **Rejected reads carry the previous value.** The `fields` row for that
  snapshot has `status = 'rejected: decreased 27 -> 17'` and `value_*` copied
  from the last accepted row; a value that was never read is `status =
  'carried'` when copied forward, so a flat series is distinguishable from an
  unreadable one.
- **Abbreviated numbers** ("36.30M") are stored parsed with `exact = 0` and the
  display string in `raw`; a later reader that sees the exact figure (resource
  panel, backpack) writes `exact = 1`.
- **Furnace ordinal**: `level` 1-30, `fc` 0-10, `sub` 0-4 (5 values), so
  `ordinal = level + 5*fc + sub`. The step rule applies to `level`, `fc`, `sub`
  individually (each may move by at most 1 per day elapsed), not to the ordinal.
  Today's account is base level (27 on the HUD tags), so `fc = 0`.
- **Operator escape**: `snapshot.py --set progress.furnace.level=27` writes a
  value directly with `method = 'operator'`, clearing the rejection; the step
  allowance scales with days since the previous snapshot so a multi-day gap does
  not reject a legitimate jump.
- **Legacy profile write-through** to `db/players/<id>.json` (the bot's file),
  only keys that already exist or the gate reads: `name`, `state`,
  `furnace_level` (base level, via `validate_furnace_read`), `gems`, `power`,
  `stamina`, `resources.{meat,wood,coal,iron}`, `vip.level`,
  `alliance.name`/`member_count` (via `set_alliance_state`), plus two new keys
  `command_center_level` and `state_opened_on`. `core/capability.account_state()`
  reads those two; `state_age_days` = today minus `state_opened_on`, an operator
  input entered once; no online lookup. `PLAYERS_DIR` is cwd-relative, so
  `snapshot.py` does `os.chdir(REPO)` before any profile call.

## Validators (per field class)

| Class | Fields | Rule | On reject |
|---|---|---|---|
| identity | id, state | id must equal the stored main id; state must equal the player's stored state | abort run, write nothing |
| monotonic | furnace level/fc/sub, vip level, building levels, hero level/stars, gear tiers, research done counts, kills | never decrease; furnace components and vip step <= 1 per elapsed day; buildings step <= 2 per elapsed day | keep old, mark rejected |
| bounded | power | within 0.5x-1.5x of previous unless previous is null | keep old, mark rejected |
| volatile | gems, resources, stamina, troops, wounded, backpack counts, queues, events | non-negative int; parses; troop totals > deploy capacity is a warning, not a reject | keep old, mark rejected |

## Readers, screens and sizing

Screen survey first: every target screen captured once, path recorded as
`entry`/`path`/`tabs` in `references/screen-map.md` with the frame that proved
it. Readers are then encoded against those frames.

| Reader | Screen (to confirm in survey) | Fields | Size (human / CC) |
|---|---|---|---|
| hud | home, no taps | power, gems, coal, survivors, VIP, furnace timer | S (2h / 15m) |
| profile | avatar -> Chief Profile | id, name, state, furnace, stamina, kills, power | S (2h / 20m) |
| alliance | bottom bar Alliance | name, members, rank | S (1h / 10m) |
| resources | HUD coal icon -> resource panel (exact meat/wood/coal/iron) | exact resources | S (1h / 15m) |
| troops | Chief Profile "Troops" button, else each camp's Troops tab | counts per type and tier, wounded, capacities, training queue | M (1d / 45m) |
| buildings | building quick-list (top-left construction icon) if it exists, else side panel queues + furnace Upgrade prerequisites; city-map panning is a spike, not v1 | core chain levels, queues | M (1d / 1h), survey-gated |
| research | side panel Research row -> Research Center: current research + per-tab completion figure on the tab header; per-node levels are a canvas read, deferred | current, tabs done/total | S (3h / 30m) |
| heroes | bottom bar Heroes roster; detail view per hero (read-only, close only) | name, rarity, level, stars, gear | M (1d / 1h) |
| gear | Chief Profile -> Chief Gear; Charms tab | six slots tier/stars; 18 charm levels | M (0.5d / 40m) |
| backpack | bottom bar Backpack, each tab, scrolled to end (frame-signature stop) | speedups by type/duration, resource boxes, FC/RFC, shards, materials; then every tab | M (1d / 1h) |
| events | HUD Events; walk the tab strip the way the cart walk does (tap each visible label, re-read, nudge when nothing new), de-dupe by page title | event name, remaining time | M (1d / 45m) |

Phases: (1) `native/` package + `db/wos.sqlite` model + hud/profile/alliance/
resources + chain + report; (2) troops/buildings/research/heroes/gear;
(3) backpack all tabs, events, dashboard, gate wiring.

## Entry points

- Run: `cd ~/Developer/wos-bot && uv run python ~/.claude/skills/wos-chief-state/scripts/snapshot.py [--readers hud,profile,...] [--dry-run] [--no-write] [--set path=value] [--state-opened YYYY-MM-DD] [--report DIR]`
  - `--dry-run` = no taps and no writes (hud reader only), mirrors collect.py.
  - `--no-write` = navigate and read, persist nothing (survey and debugging).
- Report: `uv run python ~/.claude/skills/wos-chief-state/scripts/report.py [--since 7d] [--html OUT]` prints the chief sheet with deltas vs the previous snapshot; `--html` writes the dashboard page for the Artifact tool to publish.
- Chain: `collect.py` imports `native.snapshot` from `REPO` (the same path it already uses for `core.vision_engine`) and runs it after its last item unless the run `aborted` or logged `home-failed`; a chained snapshot's own abort is its own report row.
- Code layout: `native/drive.py` (from wos_drive.py), `native/screen.py` (frame, find, is_enabled, go_home, enter, close-control probes, DANGER/PRICE guards), `native/readers/<name>.py`, `native/model.py` (schema, validators, sqlite writes), `native/snapshot.py` (orchestration). Skill `scripts/` are thin CLIs.

## Accepted Scope

- Screen survey of the native app (`references/screen-map.md`)
- Repo package `native/` as above; `collect.py` re-verified with
  `--dry-run --items mail` and one live `--items mail` run after the import swap
- Readers listed above, model, validators, provenance
- Terminal chief sheet with deltas; HTML dashboard
- Chain and gate wiring as above. Note: the bot's on-disk profile (`846646676`,
  furnace 7) is the tutorial account; the main account gets its own row and its
  own `<id>.json`. The gate benefits only when the bot runs the main account;
  accepted as forward-looking.

## Deferred to TODOS.md

- Retain a chief-sheet summary in Hindsight after each snapshot (S)
- City-map panning reader for buildings not visible in any list surface (M)
- Research per-node levels (canvas read) (M)
- Strategy planner skill consuming the model (separate plan)

## NOT in scope

- Gift-code API cross-check (skipped)
- Any press that spends: Upgrade, Train, Buy, Use, Ascend, Spin, Fight
- Emulator (adb) path for the readers; the bot keeps its own ROI tables

## Spec review round 3 amendments (binding; supersede the text above where they differ)

1. `snapshot.py --confirm-main <id>` writes `players.is_main = 1` (unique partial
   index `one_main`); `WOS_MAIN_ID` seeds it on the first run. Before that, no write.
2. `core/capability.account_state()` edit + tests are phase 3 scope; `today` is
   injectable to keep the module pure.
3. `--set path=value` creates a new snapshot row with `source = 'operator'`,
   one field row, every section `skipped`.
4. Monotonic rule for the furnace applies to `ordinal` only (never decreases,
   step <= allowance); `level/fc/sub` are derived from it. Gear tiers and hero
   rarity store a rank int alongside the text: green 0 < blue 1 < purple 2 <
   gold 3 < red-T1 4 ... red-T6 9; rare 0 < epic 1 < mythic 2.
5. Every known schema path gets one `fields` row per snapshot: `ok`, `rejected:
   <reason>` (carries last accepted value), `carried` (reader skipped, value
   copied), `unread` (both value columns null, never read). The "exactly one
   non-null" claim is dropped.
6. `taken_at` is UTC (`...Z`); `snapshots.id` is `YYYYMMDDTHHMMSSZ`; `latest`
   orders by `id`.
7. Arrays of scalars flatten to `path.0`, `path.1`; `city.queues` becomes a dict
   keyed by builder (`city.queues.1.building`); charms `gear.charms.helmet.0`.
8. Dict keys are slugs `[a-z0-9_]+`; hero and event names are canonicalised by
   fuzzy match (rapidfuzz, already a dependency, score >= 85) against
   `references/names.json`; an unmatched OCR name is slugged and its row gets
   `status = 'unmatched-name'`. The display name is kept in the doc.
9. Step allowance = `max(1, ceil(days_since_previous))`.
10. `DB_PATH = os.path.join(REPO, "db", "wos.sqlite")`; `os.chdir(REPO)` is
    scoped to the legacy write-through only.
11. The chain passes `dry_run` through; a chained dry-run snapshot runs `hud` only.
12. `--set identity.state=<n>` is the operator escape for a state transfer.

## Reviewer Concerns (spec review, kept as user decisions)

- Dashboard before history exists (YAGNI): user accepted it; sequenced last.
- Full backpack ledger day 1: user accepted it; strategic tabs land first.
