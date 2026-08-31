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

## Top-band ROI offset: finish the slider calibration

- **What:** Raise the in-game Settings > Non-standard Screen Adaptation distance
  above 77, re-run the `anchor_drift` task, and either pin the value that zeroes
  the top band or re-anchor the top-band ROIs by the measured delta.
- **Why:** the upper band reads **-4.80% (-118px)** against its recorded boxes,
  reproducibly, with the cutout overlay on *or* off. Bottom nav is exactly in
  place, so it is a safe-area relayout: the recorded top boxes assume ~118px more
  top inset than the game applies. This is the same ~120px the 2026-08-29 port
  note says the shipped ROIs assume.
- **Context:** the `cutout.emulation.tall` RRO turned out to be doing **nothing** —
  removing a 126px cutout moved the layout by 0.03%. The game ignores the Android
  cutout and lays out from its own setting. The overlay is now off and stays off.
  Two slider values give pixels-per-unit and the target solves directly. If the
  slider moves the top band without disturbing the bottom nav this closes with no
  ROI edits; if it moves both, re-anchor the top-band boxes by -4.80% instead.
  Needs one manual in-game menu action, which is why it is not done.
- **Where to start:** `printf 'anchor_drift' | ./run.sh` (note: `Main.main` does
  player init first and currently fails on Chief Profile, so call
  `usecases.anchor_drift.report_anchor_drift()` directly against a running OCR
  server until that is fixed). Full ledger in `docs/port/INDEX.md`.
- **Effort:** S (CC ~20min once the slider is moved). **Priority:** P2.

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

## Chief Profile init blocks every task

- **What:** `Main.main`'s player initialization taps the avatar, fails to reach
  Chief Profile three times, and ends the whole pass — so no task runs at all.
- **Why:** observed live 2026-08-31: `Avatar tap did not open Chief Profile
  (attempt 3/3)` / `Player initialization failed, ending this pass.` The guard
  added in `7727e7f` is working as designed; what it is guarding against is not
  fixed. `ChiefProfile.Title` read back `'Wars'` at y 6.5-8.0%.
- **Context:** found while running the `anchor_drift` task through the menu. Worth
  checking against the -4.80% top-band offset above — the avatar is top-of-screen
  chrome, so a stale top-band ROI is a live candidate for the mis-tap.
- **Where to start:** `Main/main.py:237` `pick_best_text` / the avatar tap above it.
- **Effort:** M. **Priority:** **P1 — the bot cannot run any task until this is fixed.**

## Completed
(nothing yet — entries move here when a shipped diff completes them)
