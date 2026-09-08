# TODOS

Deferred work with context. Each entry carries enough reasoning to pick up cold.

## OCR lock removal (post-burn-in)
- **What:** Drop the global `_ocr_lock` for vision-path reads (Vision is
  thread-safe; the lock exists for Paddle).
- **Why:** Multi-ROI requests currently serialize; parallel vision reads are a
  free ~2x on batch reads.
- **Context:** Deliberately kept in v1 (decision 1A, 2026-08-29 eng review) so
  the engine swap and a concurrency change never share a diff. Do it only
  after the burn-in exit criteria pass (`scripts/burnin_report.py`).
- **Where to start:** `core/ocr.py` `_ocr_lock` usage in `run_ocr`; keep the
  lock for paddle-active sessions.
- **Effort:** S (CC ~15min). **Priority:** P3.

## Template-digit fallback -> full Paddle removal
- **What:** Replace the lazy-Paddle zero-item fallback with a small digit
  template matcher, then drop `paddleocr`/`paddlepaddle` from dependencies.
- **Why:** ~1GB lighter install and one engine total.
- **Context:** Rejected for v1 (D4.3): the Paddle fallback is proven and the
  only known zero-item case is isolated badge digits. REOPEN TRIGGER: burn-in
  FAILS its fallback-rate criterion (>=1% of decisions), or a clean burn-in
  exit reopens the broader Paddle-removal decision with data.
- **Where to start:** `_recognize_crop_unlocked` in `core/ocr.py`;
  `tests/test_fixture_crops.py` FurnaceLevel case is the acceptance test.
- **Effort:** M (CC ~1h). **Priority:** P3.

## Linux capture path removal
- **What:** Delete the scrcpy/v4l2loopback capture path (`_try_start_stream`,
  `cmd_program/screen_stream.py` usage) that macOS bypasses via
  `OCR_CAPTURE_TOOL=adb`.
- **Why:** Dead weight on this fork; the fork is macOS-only in practice.
- **Context:** Deferred twice (CEO review item 5): aesthetics-only on
  upstream-diverged code; nothing breaks by keeping it.
- **Effort:** S (CC ~15min). **Priority:** P3.

## 2x render probe (post-burn-in, in plan)
- **What:** Bounded probe of MuMu at 2160x4920 @ 840dpi (same dp layout as the
  1080x2460 base, so ROIs/taps/templates survive the uniform 0.5x normalize).
- **Why:** Vision reads sharper input; may fix badge digits natively.
- **Context:** ACCEPTED scope, sequenced post-burn-in with explicit
  adopt/reject criteria — see docs/designs/vision-ocr-swap.md. Ships with a
  `WOS_FRAMEBUFFER` parameterization of the run.sh gate (default stays strict).
- **Effort:** M. **Priority:** P2 (after burn-in exit).

## Fresh ID-free fixture captures
- **What:** Capture Home/World screens at the restored 1080x2460 config, mask
  any chat/name surfaces, freeze expected text, add to
  tests/fixtures/crop_manifest.json (committed) and tests/fixtures/local/
  (gitignored, ID-bearing screens).
- **Context:** E-3A oracle; emulator was offline at implementation time, so
  v1 ships with the committed legacy frame as the oracle. Do on next bot run.
- **Effort:** S (CC ~30min with emulator up). **Priority:** P2.

## Pin an unreachable OCR endpoint in tests/conftest.py

- **What:** Point the OCR base URL at a dead port during tests so any unmocked
  HTTP fails in milliseconds instead of retrying against whatever is listening.
- **Why:** `tests/conftest.py` sets `OCR_CAPTURE_TOOL` and `OCR_BURNIN` but never
  pins the port, so a test that misses a mock talks to `127.0.0.1:8000`. Observed
  once this session: a full-suite run spent 35s on template-replay retries and
  reported 2 failures that did not reproduce. `run.sh:11` already documents that a
  foreign dev server on 8000 answers `/docs` while 404-ing `/ocr`, so a missed
  mock can get *plausible-looking* answers from a stranger.
- **Context:** the free-claims sub-tab descent added in `ea32aa2` is the path that
  reached out. Suite timing should not depend on what else is running on the box.
- **Where to start:** `tests/conftest.py`; set the env var `core/core.py` reads for
  `ocr_url`. Any test that genuinely wants a live server then opts out explicitly.
- **Effort:** S (CC ~5min). **Priority:** P3.

## Reclaim the ~118px in-game letterbox band

- **What:** Set the in-game Non-standard Screen Adaptation distance to 0 and shift
  every ROI recorded above y~25% up by 4.80%, verified screen by screen.
- **Why:** distance 70 buys ROI compatibility with a permanent ~118px black band
  across the top of the game — about 5% of the screen, and visually it is the
  notch this work set out to remove. At 0 there is no band at all.
- **Context:** deliberate call on 2026-08-31 to take the band rather than re-anchor
  (see `docs/port/INDEX.md`). The drift is a clean block translation, so a flat
  -4.80% is the correct correction; the blocker is identification, not maths.
  ~87 ROIs sit above y=25%, and cross-screen text matching cannot pick them out
  (`Home.Alliance.Title` false-matches the bottom-nav `Alliance` label at +91%),
  so each screen has to be measured with the `anchor_drift` task. The boundary
  between the translating top group and the pinned bottom nav is also unmeasured —
  nothing static exists between 22% and 98% on the home screen.
- **Where to start:** `usecases/anchor_drift.py`; add a second anchor set for a
  screen with vertically-spread static text to find the boundary first.
- **Effort:** M (CC ~2-3h with the emulator up). **Priority:** P3 — cosmetic; the
  bot is fully working at 70.

## City-map reader for building levels (wos-chief-state)
- **What:** Read Embassy, Command Center, Infirmary and the three camp levels
  by panning the city and opening each building's popup (read only).
- **Why:** The side panel only exposes Furnace, Storehouse and Research
  Center rows; camp rows must not be tapped (a tap on the centred camp
  collects completed training, +32,700 power observed 2026-09-08).
- **Context:** `native/readers/buildings.py` reads the popup shape
  ('27' beside 'Research Center', or 'Furnace Lv. 27'); the camera state
  persists between runs and every popup carries an Upgrade/Train button that
  the guard must keep off the tap list. Map tags ('26') are too small for OCR.
- **Effort:** M (CC ~1.5h). **Priority:** P2. **Depends on:** a survey of the
  camera reset gesture.

## Research per-node levels (wos-chief-state)
- **What:** Read every node level in the Growth/Economy/Battle trees.
- **Why:** `research.current` and per-tab totals cover coarse planning only.
- **Context:** The Research Center popup's Research button opens a pannable
  canvas (spend surface: every node has a Research button). Same navigation
  class as the city-map reader; do that first.
- **Effort:** M (CC ~1.5h). **Priority:** P3.

## Hindsight retain of the chief-sheet summary (wos-chief-state)
- **What:** After each snapshot, retain a 10-line summary (power, gems,
  furnace, troops totals, key deltas) in Hindsight from the skill's SKILL.md
  instructions (not the script).
- **Why:** Strategy conversations then start with the facts already in memory.
- **Effort:** S (CC ~10m). **Priority:** P3. **Depends on:** `report.py`.

## Automated tests for collect.py's press loop (wos-daily-collect)
- **What:** pytest coverage for `_press_loop`, `enter`, `go_home` over
  recorded frame sequences with a mocked driver.
- **Why:** The daily runner is verified live only; the goldens from the
  native/ extraction cover the moved helpers, not the loop.
- **Context:** Needs a frame-sequence recorder; the runner lives in
  `~/.claude/skills/wos-daily-collect/scripts/`, so the test adds it to
  sys.path. Regressions to encode first: the Loot Chest tab miss and the
  missions-last ordering (2026-09-08).
- **Effort:** M (CC ~1h). **Priority:** P3. **Depends on:** `native/` (done).

## Hero gear levels and charm levels (wos-chief-state)
- **What:** Per-hero gear slot levels (the card's Gear tab OCRs 'LV.3',
  'Lu.2', '+57' fuzzily) and per-slot charm levels (the profile shows charm
  tiers by colour only).
- **Why:** Hero gear and charms are SvS scoring inputs.
- **Effort:** M (CC ~1h). **Priority:** P3.

## Backpack ledger reader: make it safe enough for the default run (wos-chief-state)
- **What:** `native/readers/backpack.py` reads item names from tile tooltips.
  It is opt-in (`snapshot.py --readers backpack`) and NOT in the default order
  or the daily chain.
- **Why:** On 2026-09-08 the Backpack opened on its last-used tab (Gear), whose
  tiles open a Gear Details dialog instead of a tooltip; the tab-switch tap was
  not always honoured; and an earlier close gesture (re-tapping the tile under
  a bottom-row tooltip) hit the tooltip's Use button and consumed a Primal Vibe
  Avatar Frame (3 days, cosmetic, no gems). Fixed since: pre-tap exclusion the
  size of a button, close by a short drag away from the tooltip, per-tap page
  check, active-tab pixel check, `dialog_x_spot` for the Gear Details ×.
- **Where to start:** verify the tab switch on a live frame (why `tab_active`
  reported Resources while Gear content showed), then run
  `--no-write --readers hud,backpack` until every tab reads without a stop.
- **Effort:** M (CC ~1h). **Priority:** P2.

## Scheduled knowledge-freshness check (first CI in this repo)
- **What:** A weekly job that runs `scripts/refresh_knowledge.py` as a dry run
  and `uv run pytest tests/ -q`, reporting when an upstream game table changed
  or the suite broke.
- **Why:** the knowledge base is the first thing here that goes stale on its
  own. Today a game patch is invisible until someone reads a report's
  freshness line, and a broken suite is invisible until someone runs it.
- **Context:** `ls .github/workflows` returns nothing, so this is the repo's
  first CI. The freshness line from the knowledge-base plan (A9) covers the
  read path only. A private repo needs a decision about where the job runs.
- **Where to start:** `.github/workflows/knowledge.yml` on a weekly cron plus
  workflow_dispatch; the dry run already exits non-zero when a table fails.
- **Effort:** S (CC ~25min). **Priority:** P3. **Depends on:** M1 of the
  knowledge-base plan landing so there are tables to check.

## Completed

- **`core/fsm.py` deleted (2026-09-02).** A 167-line `GameFSM` navigation graph
  that nothing ever imported. Navigation stays deliberately hand-rolled as
  `recalibrate()` -> tap -> `ensure_screen()`. If a routing layer is ever wanted,
  start from `usecases/alliance.py`, which repeats that block five times, not
  from the deleted graph. Deleted in the Layer-1 capability-gate PR1.

- **Screen inset calibration (2026-08-31).** Removed the `cutout.emulation.tall`
  RRO — measured as doing nothing (0.03%) — and calibrated the in-game
  Non-standard Screen Adaptation distance to 70, where drift reads UPPER +0.01%,
  BOTTOM +0.15%. Ledger in `docs/port/INDEX.md`.
- **Chief Profile init blocking every task (2026-08-31).** The avatar tap missed
  because the top chrome sat ~118px above its recorded ROIs. Fixed by the
  calibration above, not by code: `printf 'anchor_drift' | ./run.sh` now completes,
  Chief Profile reads at 1.00, the profile parses, the pass exits 0.
