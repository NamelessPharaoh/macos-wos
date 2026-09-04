# DIRECTORY_MAP — /Users/melsawah1/Developer/wos-bot
**Generated:** 2026-09-03 | **Audited by:** Claude (directory-map skill)
**Items:** 38 directories · 229 files (`.venv`, `.git`, `__pycache__` excluded) | **Live projects:** 0

> Refresh this file by running `python update-index.py` from this root.
> Do not edit manually — treat as a generated artefact updated by the script.

**What this is:** a single-project Python repo, not a portfolio directory. `wos` v0.9.0.0
is a macOS/Apple-Silicon fork of a Whiteout Survival game-automation bot, driving
MuMuPlayer Pro over `adb`, reading the screen with Apple Vision OCR (PaddleOCR as the
guarded fallback) and OpenCV template matching. Fork of `github.com/AminulIslamSifat/wos`
pinned at upstream `c7951f19` (0.8.0); origin is `github.com/NamelessPharaoh/macos-wos`,
branch `main`, 75 commits.

**Start here:** `README.md` → `docs/port/INDEX.md` (the full port decision record) →
`Main/main.py` (entry point). Run with `./run.sh`. Tests: `uv run pytest tests/ -q`
(392 tests, 23s, no emulator or network needed).

---

## System Graph

```
wos-bot/
│
├── [A] LIVE / PUBLISHED ──────────── 0  (local CLI tool; nothing deployed or on a registry)
├── [B] ACTIVE DEVELOPMENT ─────────── 5 modules · ~11.1k Python lines · 392 tests · v0.9.0.0
├── [C] CONCEPT / PLANNING ──────────── 1 docs tree (port record, designs, unlock knowledge)
├── [D] DATA & RESEARCH PIPELINES ──── 3 (burn-in telemetry · analysis scripts · state store)
├── [E] SHARED INFRASTRUCTURE ──────── uv/pyproject · run.sh · VERSION · .gstack QA output
├── [F] CONTENT & CREATIVE ASSETS ───── 61 icon templates · 35 ROI definitions · frames
└── [G] REFERENCE & ARCHIVED ──────── pre-port scratch scripts · pre-Vision OCR backups
```

---

## Domain A — Live / Published

*Deployed to production or published as a package.*

**None.** No `Dockerfile`, no `.github/workflows/`, no hosting config, no package-registry
publish. The bot is run locally by `./run.sh` against a local emulator; `core/ocr.py` binds
a FastAPI server on `localhost:8210` for the process itself, which is not a deployment.

---

## Domain B — Active Development

*Code exists, not yet deployed or published.*

| Project | Path | Stack | Status | Description |
|---------|------|-------|--------|-------------|
| **wos** | `./` | Python 3.12 · uv · FastAPI · OpenCV · PaddleOCR · Apple Vision · rapidfuzz · rich | v0.9.0.0, v1 daily loop live-verified | The whole bot. One package, five modules below. |

### Modules

| Module | Path | Lines | Role |
|--------|------|-------|------|
| **Main** | `Main/` | 762 | Entry point (`main.py`, run as `python -m Main.main`): launches the game, reads the active player from Chief Profile, runs selected tasks, cycles siblings then accounts. `task_menu.py` is the interactive task selector (~20 tasks: Mail, World Gather, VIP, Arena, Labyrinth, Alliance Help/Tech/Chests, Pet Exploration, Train Troops, Free Claims Sweep, Chief Order …). |
| **core** | `core/` | 3,804 (11 files, excl. `backup/`) | The engine. `core.py` (35KB) device/tap/template matching; `ocr.py` (43KB) the FastAPI OCR service; `vision_engine.py` Apple Vision path (revision pinned); `capability.py` per-account feature gating; `player_profile.py` per-player persisted state; `anchor_drift.py`, `visual_cues.py`, `recalibrate.py`, `coord_utils.py` (1080×2460 canonical base). |
| **usecases** | `usecases/` | 2,089 | One file per in-game routine — `gather.py`, `intel.py`, `pet.py`, `training_troops.py`, `alliance.py`, `vip.py`, `arena.py`, `labyrinth.py`, `free_claims.py`, `lock_evidence.py`, `collect.py`, `mail.py`, `heal.py`, `exploration.py`, `chief_order.py`, `bear_trap.py`, `hunting.py`, `sunfire_castle.py`, `anchor_drift.py`. |
| **cmd_program** | `cmd_program/` | 590 | Device I/O: `screen_action.py` (taps/swipes over adb), `screen_stream.py` (scrcpy/v4l2 path — Linux-only, bypassed on macOS via `OCR_CAPTURE_TOOL=adb`; slated for deletion in TODOS). |
| **tests** | `tests/` | 3,891 | 392 offline tests across 26 files + `conftest.py`. `fixtures/` holds 6 golden PNGs (home badges/night, four VIP states) plus `crop_manifest.json` and `paddle_parity_baseline.json`. |

---

## Domain C — Concept / Planning

*Strategy documents, PRDs, and specs — no runnable code.*

| Concept | Path | Format | Status | Description |
|---------|------|--------|--------|-------------|
| **Mac port record** | `docs/port/` | 14 `.md` + 4 `.jsonl` | Complete, authoritative | The whole port history: `00-original-brief.md` (three of its technical claims are wrong — flagged in `INDEX.md`), `01-plan-reviewed.md`, `02-ceo-review.md` (account-ban risk finding), `03-test-plan.md`, `04-execution-ledger.md`, `05-final-review-findings.md`, plus `briefs/` (4), `reports/` (5) and `decisions/` (`decisions.jsonl`, `learnings.jsonl`, `reviews.jsonl`, `timeline.jsonl`). **Read `INDEX.md` before touching the code.** |
| **Vision/OCR swap design** | `docs/designs/vision-ocr-swap.md` | `.md` | Implemented, burn-in open | Design + burn-in exit criteria for retiring PaddleOCR. |
| **Feature-unlock knowledge** | `docs/knowledge/feature-unlocks.json` | `.json` | Active data | Deterministic game unlock rules backing `core/capability.py`. |
| **Adaptive Automation Design** | `# Adaptive Automation Design.md` | `.md` | Untracked draft | Root-level design record for making the bot state-aware instead of running routines blindly; carries scope + acceptance criteria because GitHub Issues are disabled on this repo. |
| **TODOS** | `TODOS.md` | `.md` | Live backlog | Deferred work with enough context to pick up cold: OCR lock removal, template-digit fallback, Linux capture removal, 2× render probe. |

---

## Domain D — Data & Research Pipelines

*Scripts, notebooks, ETL, and data assets.*

| Pipeline | Path | Tech | Scripts | Data | Entry Point |
|----------|------|------|---------|------|-------------|
| **OCR burn-in telemetry** | `logs/` + `scripts/burnin_report.py` | Python, JSONL | 1 (382 lines with capability_report) | `ocr_burnin.jsonl` (560KB), `ocr_burnin.run0.jsonl` (12KB), `burnin_epoch.txt`, `burnin_waivers.txt` | `uv run python scripts/burnin_report.py [jsonl]` |
| **Capability report** | `scripts/capability_report.py` | Python | 1 | reads `docs/knowledge/feature-unlocks.json` + player profiles | `uv run python scripts/capability_report.py` |
| **Runtime state store** | `db/` | JSON + flat file | — | `players/<id>.json` (per-player name, state, furnace level, adaptive gather node level), `completion_log.txt` (3h skip window), `account.json` (+ `.example`) | seeded from `db/players/example.json` |
| **Coordinate migration** | root | Python | 2 | — | `convert_textarea_to_percent.py` (pixel→percent for `references/TextArea/*.json`), `coordinate_conversion_reference.py` (same for hardcoded pixels in `.py`) |
| **OCR sample dump** | `Home.json` | JSON | — | 3.5KB captured OCR result set (text/score/box) | reference sample, not loaded at runtime |

> Personal data is gitignored: `db/account.json`, `db/players/*.json` (except `example.json`),
> and `logs/` — deliberately, because the upstream repo committed a real player ID.

---

## Domain E — Shared Infrastructure

*Root-level config, CI/CD, agent memory, and shared tooling.*

| File / Directory | Purpose |
|-----------------|---------|
| `pyproject.toml` | Project `wos` v0.9.0, Python ≥3.12, deps: adbutils, adbnativeblitz, fastapi 0.135.3, opencv-python, paddleocr 2.10.0, paddlepaddle 3.2.0, uvicorn, rapidfuzz, rich, psutil, pyobjc-framework-vision (darwin only). Dev group: pytest. |
| `uv.lock` | uv lockfile (312KB, gitignored). |
| `.python-version` | `3.12`. |
| `VERSION` | `0.9.0.0` — source of truth for `MAJOR.MINOR.PATCH.MICRO`. |
| `run.sh` | The launcher, and the most opinionated file here: pins `WOS_ADB_SERIAL`, forces `OCR_CAPTURE_TOOL=adb`, gates on a real 1080×2460 framebuffer (not `wm size`), pre-checks port 8210 for orphan/foreign servers, boots `core.ocr`, waits for readiness, then runs `python -m Main.main`. Every guard has a comment naming the incident that produced it. |
| `README.md` | Primary entry doc: architecture, requirements, run instructions, rollback runbook (`OCR_ENGINE=paddle ./run.sh`). |
| `CHANGELOG.md` | Keep a Changelog format; `[0.9.0.0] - 2026-08-30` is the first versioned release of the macOS fork. |
| `.gitignore` | Notable: excludes `db/players/*.json`, `logs/`, `.gstack/`, `.claude/`, `frames/`, `uv.lock`. |
| `.gstack/qa-reports/` | Generated QA output (gitignored): `qa-report-wos-bot-2026-08-29.md`, `live-smoke-single-account.log`, `screenshots/`. |
| `.claude/` | Empty — Ralph-loop scratch state, gitignored. |
| `DIRECTORY_MAP.md` · `update-index.py` · `index.json` | This map and its regenerator. |

---

## Domain F — Content & Creative Assets

*Runtime visual assets consumed by the vision pipeline.*

| Asset | Path | Format | Contents |
|-------|------|--------|---------|
| **Icon templates** | `references/icon/` | 61 PNG | OpenCV template-match targets by screen: `intel/` (14), `home/` (11), `exploration/` (8), `arena/` (7), `global/` (5), `world/` (5), `pet/` (4), `alliance/` (3), `labyrinth/`, `tundra_trek/`, `vip/` (1 each), plus `template_config.json`. |
| **Text ROIs** | `references/TextArea/` | 35 JSON | Percentage-based screen regions against the 1080×2460 base — `Home.Alliance.*`, `Home.Exploration.*`, `Home.Labyrinth`, `Home.Arena`, `ChiefProfile*`, `Global.SidePanel`, etc. Not pixel-hardcoded; the emulator must still present 1080×2460. |
| **Captured frame** | `frames/` | 1 PNG (2.6MB) | `frame_20260427_064253.png` — scratch capture, gitignored. |

---

## Domain G — Reference & Archived

*Third-party sources, legacy code, and dormant scratch.*

| Asset | Path | Source / Notes | Status |
|-------|------|----------------|--------|
| **Pre-port scratch** | `test/` | 5 manual scripts (392 lines) + `test.png` (2MB): `ocr-2.8.0.py`, `save_coord.py`, `scrcpy_capture.py`, `screen_capture.py`, `test.py`. Superseded by `tests/`. | Dormant — do not confuse with `tests/` |
| **Pre-Vision OCR copies** | `core/backup/` | `ocr.py.backup`, `ocr1.py` — the PaddleOCR-only engine before the Apple Vision swap. | Dormant |
| **Upstream** | *(remote only)* | `github.com/AminulIslamSifat/wos`, pinned `c7951f19e8cdb7fadcdf947a0bc0c6e74b6b951f` (2026-05-02, tagged 0.8.0). Upstream `main` has not moved since May 2026. | Reference pin |
| **Pytest cache** | `.pytest_cache/` | Generated, gitignored. | Ignore |

---

<!-- ============================================================
     AGENT INGRESS BLOCK — machine-readable, regenerated by update-index.py
     Do not edit manually.
     ============================================================ -->
<AgentIngress version="1.0" generated="2026-09-03" root="/Users/melsawah1/Developer/wos-bot" refresh="run python update-index.py from this directory">
  <Stack lang="Python_3.12" pkg="uv" framework="FastAPI" vision="AppleVision|PaddleOCR|OpenCV" device="adb|MuMuPlayerPro" platform="macOS_AppleSilicon" hosting="none" />
  <Repo origin="github.com/NamelessPharaoh/macos-wos" branch="main" commits="75" version="0.9.0.0" upstream="github.com/AminulIslamSifat/wos" upstream_pin="c7951f19" />
  <Entry run="./run.sh" module="python -m Main.main" ocr_service="core/ocr.py:8210" tests="uv run pytest tests/ -q" test_count="392" test_runtime_s="24" />
  <Env OCR_ENGINE="vision|paddle" OCR_PORT="8210" OCR_CAPTURE_TOOL="adb" WOS_ADB_PORT="16384" WOS_ADB_SERIAL="127.0.0.1:PORT" rollback="OCR_ENGINE=paddle ./run.sh" />
  <Constraints framebuffer="1080x2460" coord_system="percent_of_base" base_file="core/coord_utils.py" note="emulator_must_present_1080x2460" />
  <SharedMemory>
    <File seq="1" id="readme"       path="README.md"                      role="architecture_and_run_instructions" always="true" />
    <File seq="2" id="port-index"   path="docs/port/INDEX.md"             role="port_decision_record_router"       always="true" />
    <File seq="3" id="todos"        path="TODOS.md"                       role="deferred_work_backlog"             when="planning" />
    <File seq="4" id="changelog"    path="CHANGELOG.md"                   role="release_history"                   when="release" />
    <File seq="5" id="adaptive"     path="# Adaptive Automation Design.md" role="state_aware_scheduling_design"    when="scheduler_work" />
    <File seq="6" id="ocr-design"   path="docs/designs/vision-ocr-swap.md" role="burnin_exit_criteria"             when="ocr_work" />
  </SharedMemory>
  <Projects>
    <Project id="wos" domain="B" status="dev" path="." stack="Python3.12 · FastAPI · OpenCV · AppleVision" version="0.9.0.0"
             notes="local_cli_bot_no_deployment; v1_daily_loop_live_verified" />
    <Module id="main-loop"   domain="B" path="Main"        lines="762"  entry="Main/main.py" role="task_runner_account_cycler" />
    <Module id="core"        domain="B" path="core"        lines="3804" role="device_ocr_vision_capability_profile" />
    <Module id="usecases"    domain="B" path="usecases"    lines="2089" files="19" role="one_file_per_ingame_routine" />
    <Module id="cmd-program" domain="B" path="cmd_program" lines="590"  role="adb_taps_and_scrcpy_stream" notes="scrcpy_path_unused_on_macos" />
    <Module id="tests"       domain="B" path="tests"       lines="3891" tests="392" files="26" role="offline_suite_no_emulator" />
  </Projects>
  <DataPipelines>
    <Pipeline id="ocr-burnin"  domain="D" path="logs"    tech="Python,JSONL" entry="scripts/burnin_report.py"
              data="ocr_burnin.jsonl" size_kb="560" status="active" purpose="paddle_shadow_check_go_nogo" />
    <Pipeline id="capability"  domain="D" path="scripts" tech="Python" entry="scripts/capability_report.py"
              reads="docs/knowledge/feature-unlocks.json" status="active" />
    <Pipeline id="state-store" domain="D" path="db"      tech="JSON" status="active"
              files="players/&lt;id&gt;.json,completion_log.txt,account.json" gitignored="true"
              purpose="per_player_profile_and_3h_skip_window" />
    <Pipeline id="coord-migrate" domain="D" path="." tech="Python" entry="convert_textarea_to_percent.py"
              status="one_shot" purpose="pixel_to_percent_conversion" />
  </DataPipelines>
  <Assets>
    <Asset id="icon-templates" domain="F" path="references/icon"     count="61" format="png"  role="opencv_template_match" />
    <Asset id="text-rois"      domain="F" path="references/TextArea" count="35" format="json" role="percent_screen_regions" />
    <Asset id="test-fixtures"  domain="B" path="tests/fixtures"      count="6"  format="png"  role="golden_screens_home_and_vip" />
  </Assets>
  <Reference>
    <Repo id="upstream"    domain="G" source="github.com/AminulIslamSifat/wos" pin="c7951f19" tag="0.8.0" purpose="fork_origin" />
    <Repo id="legacy-test" domain="G" path="test"        purpose="pre_port_manual_scripts" status="dormant" />
    <Repo id="ocr-backup"  domain="G" path="core/backup" purpose="pre_vision_swap_ocr" status="dormant" />
  </Reference>
  <ExcludeFromScan>
    <Pattern>.venv</Pattern>
    <Pattern>.git</Pattern>
    <Pattern>__pycache__</Pattern>
    <Pattern>.pytest_cache</Pattern>
    <Pattern>node_modules</Pattern>
    <Pattern>*.lock</Pattern>
  </ExcludeFromScan>
</AgentIngress>
