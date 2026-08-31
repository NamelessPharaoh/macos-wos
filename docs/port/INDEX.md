# Mac port — full decision record

Everything about porting this bot to Apple Silicon: the origin brief, the reviews that
challenged it, the decisions made, and the execution ledger. Consolidated here on
2026-08-28 so the record lives with the code instead of scattered across
`~/.claude`, `~/.gstack` and `~/Downloads`.

**Upstream pin:** `c7951f19e8cdb7fadcdf947a0bc0c6e74b6b951f` (2026-05-02, tagged 0.8.0).
Work branch: `mac-port`. Upstream `main` has not moved since May 2026.

## Read in this order

| File | What it is |
|---|---|
| `00-original-brief.md` | The handoff brief that started this. Research on the WOS bot ecosystem, two candidate codebases, rejected alternatives, risks. **Three of its technical claims are wrong** — see below. |
| `01-plan-reviewed.md` | The implementation plan, after an engineering review, a Codex outside voice, and a CEO review. Ends with the GSTACK REVIEW REPORT. |
| `02-ceo-review.md` | Scope and premise challenge. The uncomfortable finding about account risk is here. |
| `03-test-plan.md` | What to test and where, including the parts that need a live emulator. |
| `04-execution-ledger.md` | Task-by-task execution record with every ruling made and what each costs if wrong. |
| `05-final-review-findings.md` | The five findings from the final whole-branch review, with root causes. |
| `briefs/` | The exact requirements handed to each implementer. |
| `reports/` | What each implementer did, with verification output. |
| `decisions/` | gstack decisions + learnings as JSONL. |

## What the brief got wrong

Verified against source at the pinned SHA, not assumed:

1. **The v4l2loopback/sudo patch it prescribes is unnecessary.** `core/ocr.py:301-308`
   returns early on the adb path and never reaches the loopback bootstrap. Setting
   `OCR_CAPTURE_TOOL=adb` (`core/ocr.py:118-122`) bypasses it with zero code changes.
   Confirmed at runtime: the server prints `✅ Using Capture Tool from Env: ADB`.
2. **The repo is not hardcoded to pixels.** ROIs in `references/TextArea/*.json` are
   percentages, and `core/coord_utils.py` declares the canonical 1080x2460 base.
3. **The 4px bug pointed the other way.** Taps were always correct; the OCR path squashed
   frames 0.16% and carried a `y1 -= 5` fudge to compensate.

It also missed two live bugs: `input_text()` raised `TypeError` on every call (breaking
the gathering task), and the RAM guard was completely inert on macOS.

## What was decided, and why

- **Option A (Python port), not Frostguard.** Faster to a working daily loop.
- **Account choice is an explicit up-front decision, not deferred.** The CEO review found
  the plan's own mitigation ("run on an account you accept losing") cancels its purpose:
  the chores worth automating only matter on an alliance main. Read `02-ceo-review.md`.
- **RAM sensor repaired, actuator held dormant.** `OCR_RAM_CAP_GB` defaults to 16 so the
  guard reports honest memory without arming an engine-recycle path that has never run on
  macOS. Lower it to 3 once the daily loop is stable.
- **`STREAM_WIDTH`/`STREAM_HEIGHT` deliberately left at 2456.** They still feed a
  Linux-only scrcpy path that cannot be tested from a Mac. Not an oversight.
- **`WOS_ADB_SERIAL` fails loudly when set but absent.** An earlier decision to fall back
  silently was reversed — silent substitution would tap a different device with rescaled
  frames and no error.

## Screen inset calibration — 2026-08-31 (IN PROGRESS)

**ROLLBACK, if anything below goes wrong:**

```
adb -s 127.0.0.1:16384 shell cmd overlay enable com.android.internal.display.cutout.emulation.tall
```

…and set the game's Settings > Non-standard Screen Adaptation distance back to **77**.
Overlay state lives in `/data/system/overlays.xml`, so it survives a reboot but NOT a
MuMu instance reset — same durability as the in-game setting, which lives in the app's
own data.

**Why this is being changed.** Three layers push content down and only one has a UI:

| Layer | Mechanism | State at baseline |
|---|---|---|
| MuMu emulator | its own cutout option (maxes 72px) | off |
| Android framework | `cutout.emulation.tall` RRO | on, 126px, set by adb, no UI |
| The game | "Non-standard Screen Adaptation" | distance 77 |

The RRO is invisible manual emulator state that nothing in the repo sets, so a fresh
machine silently gets a different layout. Removing it and driving the inset from the
in-game slider leaves one knob, and that one has a UI.

**Baseline measured 2026-08-31, RRO still on, slider 77** (`anchor_drift` task,
reproduced identically twice):

```
Anchor drift: SAFE_AREA_RELAYOUT
  band means: UPPER -4.83%   BOTTOM +0.14%   (tolerance +/-0.30%)
  Home.Events   -4.86%     Home.World   +0.14%    Home.Shop      +0.16%
  Home.Deal     -4.80%     Home.Heroes  +0.10%    Home.Alliance  +0.14%
                           Home.Backpack +0.16%   Home.Exploration +0.10%
  not found: Home.VIPLevel
```

-4.83% of 2460px is **-118.8px** — the "~120px top safe-area inset" the 2026-08-29 note
below says the shipped ROIs assume. So even WITH the RRO enabled, the top-band content
sits a full inset higher than the recorded boxes expect, while the bottom nav is exactly
in place. The bottom nav being pinned is what makes this a safe-area relayout rather than
a whole-screen shift.

**Anchor caveats found on the first live run** — the UPPER band is currently not
trustworthy and needs replacing before this measurement carries weight:

- `Home.VIPLevel` ('VIP') does not OCR at all. Consistent with the known Vision
  behaviour on short isolated strings in this game's font.
- `Home.Events` and `Home.Deal` are buttons in a dynamic vertical stack, not fixed
  chrome. They move together when the number of active event banners changes, which
  produces exactly the signature above. Both moved by the same amount, so a lost slot
  in that column is a live alternative explanation to an inset change.

**Measured with the overlay OFF, slider still 77** (full quorum, 3/3 upper, 6/6 bottom,
nothing missing):

```
Anchor drift: SAFE_AREA_RELAYOUT
  band means: UPPER -4.80%   BOTTOM +0.14%   (tolerance +/-0.30%)
  Home.Events   -4.86%     Home.Heroes   +0.10%   Home.Alliance    +0.14%
  Home.Deal     -4.80%     Home.Exploration +0.10%  Home.Shop      +0.16%
  Home.VIPLevel -4.74%     Home.World    +0.14%   Home.Backpack    +0.16%
```

## The finding: the cutout overlay was doing nothing

| | UPPER band | BOTTOM nav |
|---|---|---|
| overlay ON, slider 77 | -4.83% | +0.14% |
| overlay OFF, slider 77 | **-4.80%** | **+0.14%** |

Removing a 126px display cutout moved the game's layout by **0.03%** — inside measurement
noise. The game never honoured the Android cutout inset at all; it lays itself out from
its own "Non-standard Screen Adaptation" setting and ignores the framework's. The RRO has
been invisible manual emulator state contributing nothing since the port.

**The overlay stays off.** It is not load-bearing, and nothing in the repo set it, so
leaving it enabled only guaranteed that the next machine would silently differ.

Also note `wm size` / framebuffer is unchanged at 1080x2460 with the overlay off, so
`run.sh`'s existing gate is unaffected.

## What is actually stale: the top-band ROIs

The -4.80% (-118px) upper-band offset is real, reproducible, and **predates the overlay
removal** — it shows identically with the RRO on. It is not the dynamic-event-column
artefact suspected at baseline: `Home.VIPLevel` sits at x 64.4-70.6% in the top resource
row while `Home.Events` / `Home.Deal` sit at x 88.9-96.0% in the right-hand column, two
independent regions, and all three moved by the same amount with their x unchanged
(dx +0.09%, -0.19%, +1.06%).

Bottom nav pinned + top band displaced = a safe-area relayout, not a whole-screen shift.
The recorded top-band boxes assume ~118px more top inset than the game currently applies.

**Open, needs one manual action:** raise the in-game Settings > Non-standard Screen
Adaptation distance above 77 and re-run `anchor_drift`. Two values give pixels-per-unit,
and from there the target solves directly. If the slider moves the top band without
disturbing the bottom nav, this closes with no ROI edits at all; if it moves both, the
top-band ROIs get re-anchored by the measured -4.80% instead.


## State as of 2026-08-29

Done and verified (see the 2026-08-29 ledger section for evidence):

- `uv run pytest tests/ -q` → **30 passed**, no emulator, no adb, no network.
- Emulator live: MuMuPlayer Pro instance 0 at 1080x2460 @ 420dpi (Pixel 8 profile),
  adb pinned to 127.0.0.1:16384, Homebrew adb 37.0.1. `run.sh`'s framebuffer gate passes.
- **Phase 2.5 done, with a finding:** the shipped ROIs assume a ~120px top safe-area
  inset. Fixed in the emulator, not in code:
  `adb shell cmd overlay enable com.android.internal.display.cutout.emulation.tall`
  (126px @ 420dpi, survives reboot). MuMu's own cutout setting maxes at 72px.
- **Phase 5 mail: done.** Collected for real (mailbox badge cleared), cooldown entry in
  `db/completion_log.txt` (the actual store — nothing writes `db/players/*.json
  last_visit`; that plan claim was stale), and an immediate re-run **skips**. That was
  the v1 acceptance criterion for mail.
- Burner account: `lord846646676` (846646676), guest login, in `db/account.json`
  (gitignored).
- **Single-account mode: done.** With one configured email, `run_bot` never calls
  `change_account()` — it runs one pass and exits cleanly with a marker line. The
  guest-account hazard (change_account taps into Sign-in-with-Google before checking
  the target email) is resolved for 1-email configs; the marker watchdog is retired.
  See the 2026-08-29 single-account ledger section.

- **Phase 5 gather: done.** Adaptive node level (8→7→6) with camera-jump search
  detection; march deployed live and the per-player profile persisted the level.
  **v1 acceptance (D12) is met** — see the gather ledger section.

Still open:

- **ocr_endpoint opaque-500** — parked fast-follow (predates this branch).
- Alliance tasks and the other 15 modules remain unproven on the burner.

## Operational gotcha

`which uv` resolves to `~/.local/bin/uv` **0.6.16**, which silently downgrades
`uv.lock` from revision 3 to 2 on any `uv add`. Use `/opt/homebrew/bin/uv` for
lockfile-mutating commands. `uv run` is unaffected.
