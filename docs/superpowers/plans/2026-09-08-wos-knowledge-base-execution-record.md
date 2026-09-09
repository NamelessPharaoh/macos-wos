# Knowledge base: execution record

Companion to `2026-09-08-wos-knowledge-base.md`. That document is the plan;
this one records what was actually decided while executing it, and why.

All eight tasks complete. 784 tests passing, 1 skipped. Branch
`agent-autoplay`, commits `c48d780..38df7bb`.

The plan was executed by dispatching a fresh implementer per task, reviewing
each task, then reviewing the whole branch at each milestone. Twenty-three
decisions were made during execution without stopping to ask. Each is below
with what it costs if it turns out wrong.

## What the system does now

Answers cost, time and prerequisite questions about this account from
committed data, with no network call and no screen read:

```
furnace 27 -> 28
  cost     190M meat, 190M wood, 39M coal, 9.9M iron
  time     29.1 d base  ->  19.3 d at this account's +51.2% construction
  blocked  embassy 27 (have 26)
```

The speed bonuses come from the screen (`native/readers/stats.py`), read from
the Bonus Overview dialog: construction 51.20%, research 36.70%, training
156.30% as of 2026-09-09.

## Layering

```
knowledge/     stdlib only. Vendored tables, provenance, shared pure helpers.
native/kb.py   pure calculators. Imports knowledge/, never readers.
native/readers/ screen access. Imports both.
```

Five symbols are defined exactly once, in `knowledge/util.py`, and imported
everywhere else: `furnace_ordinal`, `next_level_label`, `write_table`,
`slugify`, `classify_kind`. Three separate reviews checked this held.

## The terms-restricted boundary

Data from whiteoutdata.com, whiteoutsurvival.wiki and wostools.net lives only
in gitignored `knowledge/local/`. It is never committed.

This holds structurally, not by convention: every writer of a committed file
re-reads that file from disk rather than serializing the in-memory table, which
`load()` merges the overlay into. So no ordering of calls can leak overlay data
into a commit. Two functions write to disk, `mark_verified` and `record_item`,
and both were checked against this.

**Still outstanding:** the invariant is documented, not enforced. A reviewer
recommended a tripwire asserting no row in any committed table carries a
`source` key. Note that `unlocks.json` has twelve `source` keys that are
citation strings, a different sense of the word, so a naive implementation will
false-positive.

## Rulings

| # | Decision | Cost if wrong |
|---|---|---|
| R1 | Every task brief is the task text plus all amendments verbatim, since the extractor drops them | A subagent implements superseded text; the review catches it a round later |
| R2 | Import style keeps the existing monkeypatch target valid | One test edit |
| R3 | Four required tables, not three; `troop_stats` stays because M1 calculators read it | One table vendored earlier than needed |
| R4 | `furnace_ordinal` and `next_level_label` defined only in `knowledge/util.py` | A duplicate definition the review would catch |
| R5 | `prerequisites` returns `(unmet, assumed)` with `have=None` for untracked buildings | A planner reads a tuple as a list |
| R6 | `diff_rows` is recursive and table-aware | A first fetch prints one line instead of one per row |
| R7 | Moving the parsers needs no golden re-record; goldens never pinned them | One golden re-record |
| R8 | Execute on `agent-autoplay` rather than a detached worktree | A messier revert if abandoned wholesale |
| R9 | All tasks sequential, never parallel implementers in one working tree | Wall-clock only |
| R10 | Task 2's reviewer got a code-only diff; the 570 KB of generated JSON was verified by the controller instead | A defect hiding in generated data rather than code |
| R11 | Did not launch the game unilaterally for Task 7 | Times stayed unbuffed until the user launched it |
| R12 | Added a third finding to a fix wave the reviewer scoped at two | A legitimate future row fails loudly instead of reading as zero |
| R13 | Kept the scratch workspace while the plan was unfinished | One stale gitignored directory |
| R14 | Did not merge or push without being asked | The branch waits |
| R15 | Cross-check output goes to the gitignored overlay, never the committed table | Fire Crystal data is local-only, which is the design |
| R16 | `local_sources.py` imports from `knowledge.util`, not `native.kb` | An import cycle the suite catches at collection |
| R17 | **Reversed R15's basis.** A user decision outranks the amendment I had cited; the implementer was right to push back | One fix round, already spent |
| R18 | Spent a fix round on an approved review, for a missing test on the only disk-write path | One dispatch on polish |
| R19 | Skipped a scoped re-review after mutation-testing the fix directly | Cost one Minor, which the next review found |
| R20 | Final M2 fix wave carried seven items, not the reviewer's three | Three known Minors survive, each recorded |
| R21 | Stopped and asked before Task 8, which touches the interaction that once consumed an item | One sentence of confirmation |
| R22 | Spent a fix round on the last task for a layering fix and a correctness trap | One dispatch |
| R23 | Final fix wave carried six items, not the reviewer's three | Three known items carried forward, each recorded |

## Two things I got wrong

**R15.** I wrote a task brief pointing at an amendment while missing a later
user decision that overrode it. The implementer built the wrong output shape,
flagged the conflict rather than complying, and was right. R17 reverses it.

**The classifier gate.** I claimed a mutation test proved that changing the
item classifier without bumping its version would fail the suite. It did not.
My mutation changed a mapping the tests pinned; adding a rule for a new family
touched none of them and the suite stayed green. The final reviewer caught it
and reproduced the real case. The gate now derives from a fingerprint of the
rule source, and the correct experiment fails as it should.

## Known and accepted

- The item catalogue ships empty. It fills on the first real backpack sweep.
- `resource_box` is a declared but unproduced classification, matching a
  pre-existing gap in the classifier.
- Speed bonuses store as whole percentages, losing two decimals, because an
  existing consumer's contract pins that unit. Raw screen text is preserved.
- A fresh clone runs fewer tests than this machine: several parser tests skip
  without their gitignored fixtures.
- `native/readers/backpack.py::read` and `native/readers/stats.py::read` have
  no tests; only their pure parse functions do.
- `items.json` is rewritten once per tile during a sweep, so a backpack run
  leaves the working tree dirty.
- `native/kb.py` is at 590 lines against a 600-line soft limit.

## For whoever picks this up

1. **Nothing has been run end to end against a live sheet.** The one
   integration test that goes database to calculator exists now and passes.
   Extend it before building a planner on top.
2. **If you touch `classify_kind`, the fingerprint test will fail until you
   bump the version.** That is deliberate. Bumping it makes every catalogued
   row reclassify.
3. **The account identifiers in this repo are public.** The repository is
   public and the player id, name and state appear in tracked files. Scrubbing
   them means rewriting pushed history.
