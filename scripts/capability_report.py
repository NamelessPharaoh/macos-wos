"""What the capability gate believes about an account, and why.

The gate fails open by design, so a wrong knowledge-base value produces a
SILENT skip: the task simply does not appear in the run log, which looks
identical to it never having been selected. This report is the detector. It
reads the profile and the knowledge base, prints the verdict for all 21 tasks
with the source and confidence behind each one, and flags entries that have not
been re-checked in a long time.

Run it BEFORE trusting the gate to skip anything, and re-run it after a game
patch. Community data is not ground truth.

It touches no device: profile and knowledge base are both files on disk, so
unlike `./run.sh` this needs no emulator and no adb. Same shape as
scripts/burnin_report.py.

Usage:
  uv run python scripts/capability_report.py [player_id]
  uv run python scripts/capability_report.py --check-table

With no player id it reports on every profile in db/players/ except the
example template.

`--check-table` skips the per-player report and instead checks the task
gates in Main/task_menu.py against knowledge/unlocks.json for drift: gates a
task declares that the table has no entry for (List A), and table entries no
task's gate names (List B). It exits non-zero when either list is non-empty,
so it can gate CI the way the per-table registry tests do for the other
knowledge tables.
"""
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import capability                                    # noqa: E402
from core.player_profile import PLAYERS_DIR, load_profile      # noqa: E402
from Main.task_menu import TASKS                               # noqa: E402

# A game patch can move an unlock threshold, and a stale-but-well-formed entry
# reads exactly like a fresh one. Six months is long enough that the value
# deserves re-checking before it is trusted to skip work.
STALE_AFTER_DAYS = 180


def _discover_player_ids():
    directory = Path(PLAYERS_DIR)
    if not directory.is_dir():
        return []
    return sorted(
        path.stem for path in directory.glob("*.json")
        if path.stem != "example"
    )


def _staleness(feature):
    """(label, days) for a feature's last_verified date."""
    raw = (feature or {}).get("last_verified")
    if not raw:
        return "NO DATE", None
    try:
        seen = datetime.strptime(str(raw), "%Y-%m-%d").date()
    except ValueError:
        return f"BAD DATE ({raw})", None
    days = (date.today() - seen).days
    return ("STALE" if days > STALE_AFTER_DAYS else "ok"), days


def _report_player(player_id, table, warnings):
    profile = load_profile(player_id)
    state = capability.account_state(profile)
    features = table.get("features") or {}

    print(f"\n=== {player_id} ===")
    readable = {k: v for k, v in state.items() if v is not None}
    unreadable = sorted(k for k, v in state.items() if v is None)
    print("account state : " + (
        ", ".join(f"{k}={v}" for k, v in sorted(readable.items())) or "nothing readable"))
    if unreadable:
        # Named explicitly: a gate condition on any of these fails open, so the
        # reader can see which requirements are not actually being enforced.
        print(f"not readable  : {', '.join(unreadable)} "
              f"(conditions on these fail open)")

    for warning in warnings:
        print(f"WARNING       : {warning}")

    skipped, ran = [], []
    rows = []
    for task in TASKS:
        verdict = capability.evaluate(task.gate, profile, table=table)
        feature = features.get(task.gate) if task.gate else None
        confidence = (feature or {}).get("confidence", "-")
        stale, days = _staleness(feature) if feature else ("-", None)
        age = f"{days}d" if days is not None else "-"
        rows.append((
            verdict.decision, task.key, task.gate or "-", confidence,
            age, stale, verdict.reason,
        ))
        (skipped if not verdict.should_run else ran).append(task.key)

    width = max(len(r[1]) for r in rows)
    gate_width = max(len(r[2]) for r in rows)
    print(f"\n{'':4} {'task'.ljust(width)}  {'gate'.ljust(gate_width)}  "
          f"{'conf':<7} {'age':<6} {'why'}")
    for decision, key, gate, confidence, age, stale, reason in rows:
        mark = "SKIP" if decision == capability.SKIP else "run "
        flag = "  <-- STALE" if stale == "STALE" else (
            "  <-- no date" if stale in ("NO DATE",) else "")
        print(f"{mark} {key.ljust(width)}  {gate.ljust(gate_width)}  "
              f"{confidence:<7} {age:<6} {reason}{flag}")

    print(f"\n{len(skipped)} of {len(TASKS)} tasks gated off: "
          f"{', '.join(skipped) if skipped else 'none'}")
    if not capability_gate_on():
        print(f"NOTE: {os.environ.get('WOS_CAPABILITY_GATE')!r} in "
              f"WOS_CAPABILITY_GATE means the live run ignores all of this.")
    return skipped


def capability_gate_on():
    return os.environ.get("WOS_CAPABILITY_GATE", "1").strip() != "0"


def check_table(table, tasks):
    """Drift between the task gate declarations and the unlock table.

    Returns (missing_from_table, unreferenced): List A is gates a task
    declares that have no entry in the table (the dangerous direction — a
    task would run ungated); List B is table entries no task's gate names
    (stale data, not a bug in the gate). ALWAYS/UNKNOWN are sentinels, not
    features, and are excluded from List A.
    """
    features = table.get("features") or {}
    gates = {
        task.gate for task in tasks
        if task.gate and task.gate not in capability.SENTINELS
    }
    missing_from_table = sorted(gates - set(features))
    unreferenced = sorted(set(features) - gates)
    return missing_from_table, unreferenced


def _report_check_table():
    capability._reset_cache()
    table, warnings = capability.load_table()
    for warning in warnings:
        print(f"WARNING: {warning}")

    missing_from_table, unreferenced = check_table(table, TASKS)
    print("List A (gate referenced, not in table): " +
          (", ".join(missing_from_table) if missing_from_table else "empty"))
    print("List B (in table, no task references it): " +
          (", ".join(unreferenced) if unreferenced else "empty"))
    return 1 if (missing_from_table or unreferenced) else 0


def main():
    if "--check-table" in sys.argv[1:]:
        return _report_check_table()

    capability._reset_cache()
    table, warnings = capability.load_table()

    features = table.get("features") or {}
    stale = [name for name, feature in features.items()
             if _staleness(feature)[0] in ("STALE", "NO DATE", )]

    player_ids = [sys.argv[1]] if len(sys.argv) > 1 else _discover_player_ids()
    if not player_ids:
        print(f"No profiles found in {PLAYERS_DIR}/. "
              f"Pass a player id to report on one directly.")
        return 1

    print(f"knowledge base: {capability.KB_PATH}")
    print(f"features      : {len(features)}")
    if stale:
        print(f"NEEDS RECHECK : {', '.join(sorted(stale))}")

    for player_id in player_ids:
        _report_player(player_id, table, warnings)

    print("\nCommunity-sourced values are not ground truth. Check a SKIP "
          "against the game before trusting it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
