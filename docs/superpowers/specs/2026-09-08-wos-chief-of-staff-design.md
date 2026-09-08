# WoS Chief of Staff: design

Date: 2026-09-08. Branch: agent-autoplay. Status: draft for review.

## Purpose

Turn the two verified read/collect skills into a chief of staff for the main
Whiteout Survival account: it knows the game's numbers, knows the account,
proposes a daily plan toward an agreed goal, executes the part that only
spends basic resources, asks before anything strategic, and reports.

Decisions taken in brainstorming (2026-09-08):

| # | decision |
|---|---|
| D1 | Spend policy: `auto` for meat, wood, coal, iron, stamina, alliance coins; `ask` for gems, speedups, fire crystals, backpack items, hero shards/EXP, gear materials; `never` for real money, Escape, any screen showing a price, attacks on players |
| D2 | Goal for the next 90 days: growth first (furnace/city chain, research), SvS preparation from state day ~70 |
| D3 | Spend level: low (monthly cards, occasional small packs); plans count the card's gems and the daily deal chests |
| D4 | Interface: chat briefing on demand, approvals in the conversation; a scheduled routine later |
| D5 | Executable actions in v1: building upgrades, research, troop training |
| D6 | Knowledge: local knowledge base built first from online sources, refreshed on demand; web search only for what the tables cannot answer |
| D7 | Four sub-projects in order (knowledge base, planner, executor, briefing skill); rule-based planner with the assistant as advisor in chat; an LLM planner may be added later behind the same interface |
| D8 | Include wostools.net and whiteoutsurvival.wiki tables as cross-checks despite their terms (user decision over advice); they live in a gitignored `knowledge/local/` and are never committed or redistributed |

## What exists and is reused

- `wos-daily-collect`: free rewards, background input, money guard.
- `wos-chief-state` + `native/`: account sheet in `db/wos.sqlite` (snapshots,
  fields with provenance, `latest_static`, deltas), readers, `Screen` with the
  spend guards (`NEVER_RE`, `DANGER_RE`, `PRICE_RE`, pre-tap OCR), run lock,
  `timeline.py` (state-age milestones), `report.py`.
- Research of 2026-09-08 on community data sources (see Sub-project 1).

## Architecture

```
 online sources ──refresh_knowledge.py──▶ knowledge/*.json (+ _meta provenance)
                                               │
 game ──collect──▶ free rewards                │
 game ──snapshot─▶ db/wos.sqlite ──────────────┴─▶ native/plan.py ─▶ Plan (actions: auto | ask | never)
                                                                       │
                       briefing (chat) ◀── report ◀────────────────────┼──▶ native/actions/* run `auto`
                       user says yes in chat ──────────────────────────┼──▶ native/actions/* run approved `ask`
                                                                       │
                                        snapshot again ◀───────────────┘  verification + audit rows
```

Units and their contracts:

| unit | does | depends on | interface |
|---|---|---|---|
| `knowledge/` + `native/kb.py` | game tables and pure calculators | nothing at runtime | `kb.building_cost(...)`, `kb.research_path(...)`, `kb.training_cost(...)`, `kb.power_gain(...)`, `kb.days_to(...)` |
| `native/plan.py` | ranked daily plan | sheet (model), kb, policy | `plan(sheet, kb, goal, policy, calendar, today) -> Plan` |
| `native/actions/` | guarded spend routines | screen, model, kb, plan | `Action.run(screen) -> ActionResult` |
| `wos-chief-of-staff` skill | orchestration + briefing | the two skills, plan, actions | `scripts/brief.py`, `scripts/execute.py` |

## Sub-project 1: knowledge base

### Sources

Committed with attribution (community-published JSON; the site states "All
data is free to copy and use"; no LICENSE file, treated as revocable):

| table | source | covers |
|---|---|---|
| buildings | github.com/wosnerdwarriors/website-index `calculator/data/construction.json` | 16 buildings, levels 0-30 (+FC/RFC columns), meat/wood/coal/iron, seconds, prerequisites |
| troops | same repo `calculator/data/troops.json`; github.com/wosnerdwarriors/wos-data `data/troop-stats.json` | T1-T11 training cost/time/points; stats per tier and FC level |
| research | wos-data `data/research-upgrades.json` | Growth 45 / Economy 44 / Battle 102 nodes: per-level cost, time, prerequisites |
| gear, charms, hero gear, heroes, pets, dawn academy | website-index `calculator/data/*.json` | tiers, materials, stats |
| calendar | wos-data `data/calendar-data.json` + `native/timeline.py` | recurring events, state-age milestones |

Local only, gitignored under `knowledge/local/` (D8): whiteoutdata.com
furnace table (30-1 to FC10), wostools.net building/troop/research figures
(from the page bundle), whiteoutsurvival.wiki building and research pages.
Used as cross-checks; a disagreement between sources is recorded per row as
`disputed: {source: value}` and the committed value stays the wosnerds one
until the game screen settles it.

### Layout and provenance

- `knowledge/<table>.json`: normalised to the repo's slugs (`storehouse`,
  `research_center`, `infantry_camp`, ...), keys lower-case, costs as ints,
  times in seconds. Each file starts with `_meta`: `source_url`,
  `source_commit`, `fetched_at`, `licence`, `normaliser_version`.
- Per row: `verified_in_game: null | "<snapshot_id>"`, `disputed` (optional).
- `knowledge/README.md`: sources, licence notes, refresh procedure, what is
  local-only and why.
- `scripts/refresh_knowledge.py`: fetch, normalise, diff against the current
  files, print changed rows, write only with `--write`. A game patch appears
  as a diff, never as a silent overwrite. `--local` runs the gitignored
  cross-check fetchers (browser-like single page fetches, one per table, no
  crawling).

### Calculators (`native/kb.py`, pure, tested on the vendored data)

- `building_cost(name, from_level, to_level) -> Cost` (per resource, FC, RFC)
- `building_time(name, from_level, to_level, speed_bonus=0.0) -> seconds`
- `prerequisites(name, level, sheet) -> [(building, level)]` unresolved ones
- `research_path(node, level, sheet) -> [ResearchStep]` with prerequisites expanded and totals
- `training_cost(troop_type, tier, count) -> Cost`, `training_time(...)`
- `power_gain(action) -> int`
- `days_to(cost, income_per_day) -> float` using the sheet's per-day deltas
- `verify(kb_row, screen_cost) -> ok | mismatch(pct)` used by the executor

### Tests

Schema per file; one known value per table checked against the game (furnace
27 -> 28 cost and time as the furnace popup shows this week; research node
prerequisites; T9 infantry cost); prerequisite resolution against a recorded
sheet; refresh diff on a fixture with one changed row; `knowledge/local/`
absent does not break anything.

## Sub-project 2: planner

`plan(sheet, kb, goal, policy, calendar, today) -> Plan`, deterministic.

- **Inputs.** `sheet` = `latest_static` + dynamic collections; `goal` = growth
  (D2) with a `war_from_day` switch (70); `policy` = the D1 classes, per-run
  caps (max 6 actions, max 60% of any basic stock, never below protected
  amounts), spend level (D3); `calendar` = state age, next milestones, live
  events with timers.
- **Candidate generation.** For every building in the chain: the next level
  with its prerequisites; for research: the next level of nodes on the
  growth path (Tool Enhancement, Tooling Up, training capacity, march size,
  then economy); for training: fill each camp's queue with the top tier;
  healing when injured > 0.
- **Rules (scored, all explainable).** 1) keep every builder busy; 2) keep
  research busy; 3) furnace chain first (furnace, then its prerequisites);
  4) keep the T9 training queues busy without dropping any basic resource
  below its protected amount plus one day of the current training cost;
  5) from day `war_from_day`, weight troops and gear above buildings; 6) an
  action whose cost is not fully covered today is scheduled with `days_to`
  rather than dropped; 7) event bonuses (calendar) raise the score of the
  matching action class while the event runs.
- **Output.** `Plan{lines: [PlanLine]}`; `PlanLine{action, target, cost,
  yields, prerequisites_missing, klass: auto|ask|never, score, reasons: [str],
  eta}`. Every `ask` line carries the alternative of waiting (what the timer
  costs in days).
- **Tests.** Recorded sheets (fixtures) produce fixed plans; each rule has a
  test that flips one input and moves one line; caps are enforced; an `ask`
  line never becomes `auto` by any input.

## Sub-project 3: executor

- `native/actions/base.py`: `Action` = navigate, read the on-screen cost and
  target, `kb.verify` against the plan line and the sheet, press once, verify
  the screen shows the expected result, log an `actions` row (plan line, cost
  read, frames before/after, result). The press helper is the only code
  allowed to tap a label matching `NEVER_RE`, and only when the label equals
  the action's `press_label` and the verification passed.
- `native/actions/upgrade.py`: side-panel row or city-map building (from the
  buildings reader's popup path), popup `Upgrade` button, expected: queue row
  in the side panel with a timer.
- `native/actions/research.py`: Research Center -> tree tab -> node (canvas
  navigation from the TODO city-map spike, or the tree's own search if it
  exists: survey first), `Research` button, expected: `research.current`
  changes.
- `native/actions/train.py`: camp -> `Train` with the planned amount (slider
  read from the screen), expected: training row shows a timer.
- Approvals: `ask` lines execute only after the user's yes in chat; the
  briefing skill passes the approved line ids to `execute.py --approve <id>`.
- Safety (approved section 2): three classes fixed in code; read-compare-
  press-verify; per-run caps; money guard around every action; suspend list
  per action type in the DB, cleared by the user; `WOS_EXECUTE=0` kill switch;
  full audit trail.
- Tests: each action on recorded frame sequences with a mocked driver
  (happy, cost mismatch, prerequisite missing, verification failed); the
  guard refuses the press outside an Action; the suspend list blocks.

## Sub-project 4: briefing skill (`wos-chief-of-staff`)

- `scripts/brief.py`: collect (if not run today) -> snapshot -> plan ->
  execute `auto` lines within caps -> snapshot -> print the briefing: sheet
  deltas, what was done and verified, pending `ask` lines with reasons and
  the cost of waiting, calendar (state age, next milestones, live events),
  warnings.
- `scripts/execute.py --approve <ids>`: run approved lines, verify, report.
- SKILL.md: when to run, the approval protocol (the user answers in chat
  with line ids), what never happens, how to read the briefing, evals.
- Web research on demand: only for questions the knowledge base cannot
  answer (event rules, meta shifts); results are quoted with URLs and, when
  they change a rule, land in `knowledge/` with provenance.

## Error handling (all sub-projects)

Reader/action failures become statuses and briefing lines, never crashes;
the daily-collect chain boundary pattern applies to the executor. A cost
mismatch, a failed verification or a gem drop suspends the action type and
is the first line of the next briefing.

## Sequencing

1. Knowledge base (this spec's first implementation plan): sources fetched,
   normalised, tested; calculators; cross-checks local.
2. Planner: fixtures from the current sheet; first briefing is plan-only.
3. Executor: upgrade first (the furnace chain), then training, then research.
4. Briefing skill and the approval protocol; scheduled routine later.

## Out of scope

Real-money purchases, attacks on players, an LLM planner (later, behind
`plan()`), the scheduled routine (D4 later), hero/gear spending beyond the
`ask` class, redistribution of any local-only data.
