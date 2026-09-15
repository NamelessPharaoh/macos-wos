# Backpack tooltip parsing fix — report

## Status
Done. All three tooltip shapes verified against real recorded frames; full
test suite green.

## Commit
`bdb6e1b` on branch `agent-autoplay`:
"fix: anchor backpack tooltip parsing on the tapped tile, not the Use button"
(ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`)

## Test summary
`uv run pytest tests/ -q` → **786 passed, 1 skipped** (baseline was 785
passed, 1 skipped; net +1 from replacing 2 old synthetic tests with 3 new
shape-specific ones plus updating 1 existing test's call signature).

All 4 new/changed tests (`test_events_page_and_backpack_tooltip_parsers`,
`test_read_tooltip_shape_a_resources_with_slider`,
`test_read_tooltip_shape_b_speedup_has_no_buttons_at_all`,
`test_read_tooltip_shape_c_other_no_slider_buttons_sit_closer`) were
confirmed to FAIL against the pre-fix `read_tooltip` (verified via
`git stash` on just `backpack.py`, rerun, `git stash pop`).

## Parsed output for the three named real frames
(`~/wos-chief/20260909T120815Z/`, using `core.vision_engine.VisionEngine`,
the project's real OCR)

- Shape A, `010-tile.png` (Resources, tile cx=0.216 cy=0.218):
  `('Chief Stamina', None, 'Restores 10 Chief Stamina. Used for daily events like troop deployment.')`
- Shape B, `086-tile.png` (Speedup, tile cx=0.404 cy=0.218):
  `('1m Construction Speedup', None, 'Speeds up your [Construction] queue by 1 minute.')`
- Shape C, `110-tile.png` (Other, tile cx=0.593 cy=0.257):
  `('Mystery Badge', None, 'Mystery Badge can be used for trading in the Mystery Shop.')`

Cross-checked against ~15 more real tiles across Resources (052-078,
multiple resource/secured-resource variants) and Speedup (further
Construction/Training Speedup tiles) — all parsed full names and complete,
correctly-joined multi-line descriptions.

## What changed
`read_tooltip(items, h, w)` → `read_tooltip(items, h, w, cx, cy)`. It no
longer looks for a Use button; it reads a name+description band anchored
at the tapped tile's own centre (`cy+0.08` to `cy+0.19`), which measured
consistently (~cy+0.12 name, ~cy+0.15/+0.174 description lines) across all
three shapes regardless of what follows (slider, buttons, both, or
nothing). `read()`'s only change is passing the `cx, cy` it already has
into that call — confirmed by `git show bdb6e1b -- native/readers/backpack.py`
grepped for `tapf|swipe|drv\.`: no diff line touches any of them.

## Concerns / out-of-scope observations
- The Bonus tab produced zero tiles in the 2026-09-09 sweep because its
  tiles carry duration labels ("2hr(s)") rather than numeric counts, so
  `tile_targets()` never matches them — a separate, pre-existing issue in
  tile targeting, not tooltip parsing. Not touched.
- Frames 116-149 (nominally under the "Other" tab's grid-frame markers)
  actually show Speedup-tab content (Construction/Training Speedup items),
  suggesting the Other tab's activation may not have taken effect partway
  through that sweep. This looks like a separate tab-activation bug, out
  of scope here and not touched — flagging it since it may explain gaps in
  "Other" tab coverage beyond the tooltip-offset bug.
