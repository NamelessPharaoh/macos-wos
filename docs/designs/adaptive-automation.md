# Adaptive Automation Design

Design record for making The Bot state-aware instead of executing routines
blindly. Because GitHub Issues are disabled on this repository, this document
also carries the scope and acceptance criteria that would normally live on the
project workboard.

## Problem

The Bot schedules routines purely by priority and timer. Two failure modes
follow:

1. **Locked features.** Routines look for screens or items the account has not
   unlocked yet (level-gated features), fail on screen, and retry every cycle.
2. **No strategy.** Whiteout Survival is a strategy game; executing every
   enabled routine unconditionally wastes stamina, marches, and resources that
   a player would allocate deliberately.

These are different problems with different solutions. Feature availability is
**deterministic** — the game's unlock rules are fixed, known facts — and must
be solved with data and code, not with a model. Strategy is a judgment
problem where a small local LLM can help, but only as an optional advisor.

## Current Behavior (evidence)

- `TaskQueue` selects tasks by priority and schedule only; there is no
  scheduler-level notion of "this profile cannot run this yet".
- Locked-feature handling is per-task and defensive. Example:
  `PetSkillsRoutine` template-matches unlock text and logs "Pets feature is
  unavailable" *after already navigating there*. Each such check burns
  emulator time every run and nothing persists the result.
- Useful hooks already exist: `TaskFailureIncidentService` (failure streaks),
  `ActionRequiredIncidentService`, per-profile persistence via
  `ProfileService`/`ConfigService`, and the design-guideline rule that unknown
  states exit conservatively.

## Design: Three Layers

### Layer 1 — Account capability model (no LLM; biggest ROI)

- **Static rules** live in the curated knowledge base under `docs/knowledge/`
  (`feature-unlocks.json`): unlock requirements and fixed schedule facts for
  every `TpDailyTaskEnum` routine, with per-entry confidence and source
  provenance. The file graduates to a module resource once an engine service
  consumes it.
- **Observed account state** (current furnace level, alliance membership,
  unlocked features, trek count) is captured through the existing OCR and
  template helpers — once at `INITIALIZE`, refreshed opportunistically — and
  persisted with the profile. No website can supply this; only the bot's own
  vision can.
- **Gating**: `DelayedTask` gains an availability hook (e.g.
  `isAvailableFor(accountState)`) consulted by `TaskQueue` before dispatch.
  A gated routine is skipped with an explainable log line and backed off until
  its gate can be met, instead of navigating into a locked screen.
- **Feedback loop**: when a routine still encounters a lock screen, it records
  the observation ("unavailable until furnace N"). In-game evidence always
  overrides the static data for that profile; stable contradictions flow back
  into the knowledge base with the evidence noted.

### Layer 2 — Rule-based prioritization

Most genre "strategy" is expressible as utility rules over the capability
model: do not train when the infirmary is full, prioritize construction
speedups before construction-scoring events, keep marches free for rallies.
A deterministic scoring function stays fully explainable — which matters when
a wrong move spends real, irreversible resources.

### Layer 3 — Optional local LLM advisor

Where fuzzy judgment genuinely helps, and only there:

- **Planning cadence, not control loop.** Every N minutes or on state change,
  the advisor receives compact JSON (account state + goals) and returns
  schema-constrained priority/schedule adjustments. Deterministic code still
  executes everything; per-frame or per-tap LLM decisions are ruled out.
- **Unknown-screen recovery**: classify an unrecognized state from the OCR
  dump/screenshot and suggest a safe exit for `NavigationHelper`.
- **Failure triage**: turn `TaskFailureIncidentService` streaks into
  explanations and suspension proposals.
- **Natural-language configuration**: user goals ("focus hero growth, never
  spend gems") translated into reviewable priority config.

Hard constraints:

- **Safety rules live in code, not in the prompt.** Advisor output is
  validated against non-negotiable rules (never spend paid currency, never
  attack players, resource-spend caps) before anything applies.
- **The bot must run fully without the model.** The advisor sits behind an
  interface in its own module (or an automation service depending only on
  `modules/api`), as an optional plug-in; a multi-GB model is not packaged
  into the MSI. Local serving via Ollama/llama.cpp over localhost HTTP; a hosted API remains
  an alternative.
- **Facts are not fine-tuned into the model.** Small models recall facts
  poorly; unlock tables, event calendars, and costs stay structured data that
  both code and prompts read. The model judges over the data.

## Knowledge Base Summary

Seeded on 2026-09-01 from community references (schema and trust rules in
`docs/knowledge/README.md`). Confirmed gates worth designing around:

| Gate | Requirement |
| --- | --- |
| Hero Hall / Exploration | Furnace 4 |
| Chief's House (Chief Orders) | Furnace 6 |
| Lighthouse (Intel) | Furnace 7 |
| Arena, Embassy (rallies) | Furnace 8 |
| Research Center, Storehouse | Furnace 9 |
| Alliance Mobilization | Furnace 10 + alliance ≥ 15 members, biweekly |
| Pets (Beast Cage) | Furnace 18 + state ~60 days (sources: 55–60) |
| Labyrinth | Furnace 19 + Command Center L1; 5 attempts/day, reset 00:00 UTC |
| Experts / Tundra Trek | Furnace ~25 + state stage; supplies 20/10/10 at 00:00/08:00/16:00 UTC; auto-trek after 50 treks |
| Crystal Laboratory | Furnace 30 + Fire Crystal age |
| War Academy | Furnace FC5 + state ~220 days |
| Bear Trap | Alliance HQ (≥ 20 members), R4+ sets trap, 46 h membership to join, ~47 h cadence |

Unconfirmed online, recorded as `unknown` pending in-game verification: Bank,
Mystery Shop, Nomadic Merchant, Life Essence, several event entry gates, and
the exact early-game levels for alliance access and world-map marches.

## Sequencing

1. **Layer 1** — capability model + gating (this design's first
   implementation unit).
2. **Layer 2** — rule-based prioritization over that state.
3. **Layer 3** — LLM advisor for the fuzzy remainder, if still needed once
   Layers 1–2 have removed the deterministic failures.

## Acceptance Criteria (Layer 1)

- Every built-in routine has a defined availability answer (gated or
  always-available); `CUSTOM_TASK` is exempt.
- A profile below a known gate (e.g. furnace < 18 for pet routines) never
  navigates into the locked feature; the skip decision and reason appear in
  the account log.
- A routine that still encounters a lock screen records the observation so
  subsequent cycles skip without navigation.
- Knowledge-base entries keep confidence and source provenance; recorded
  discrepancies are only removed with evidence.

Verification expectation per project rules: automated tests for the gating
logic plus live account-log confirmation; community-sourced values remain
marked unverified until confirmed in game.

## Status

- Seed knowledge base committed (`docs/knowledge/`,
  branch `claude/game-script-llm-strategy-5ab7a7`).
- Capability model, OCR state capture, and queue gating: not started.
- Risks: unlock values drift with game patches (mitigated by
  observation-overrides-data); several gates start observation-only.
