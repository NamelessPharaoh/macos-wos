"""Which tasks this account can actually run.

The bot dispatches all 21 routines every cycle regardless of what the account
has unlocked, so a Furnace 7 profile walks into locked Arena, Infirmary, Pets,
Labyrinth and Daybreak Island screens on every pass and fails there. This
module answers "can this account do this at all?" from data already on disk,
before any navigation happens.

Two rules shape everything here:

FAIL OPEN. The gate removes work only when every condition it can read says the
feature is impossible. Missing state, an unreadable condition, an absent or
corrupt knowledge base, an unrecognised task — all of them RUN the task. A
wrongly-skipped task is invisible (skipping produces no error and no output),
whereas a wrongly-run task costs one wasted navigation and looks exactly like
today. Silence is the more expensive failure, so the gate never chooses it.

PURE. Nothing here does OCR, navigation, logging or environment reads. It maps
(profile, knowledge base) to a Verdict and stops. Warnings ride out on the
verdict for the caller to print, so the same evaluation is testable by
asserting on a return value.

    profile ──▶ account_state() ──┐
                                  ├──▶ evaluate() ──▶ Verdict
    feature-unlocks.json ─────────┘                   {decision, reason,
                                                       source, warnings}
"""
import json
import os

from core.player_profile import get_furnace_level

# Module-relative, NOT cwd-relative. core/player_profile.py uses bare relative
# paths and tests/conftest.py has to os.chdir() to compensate; copying that here
# would mean the gate silently finds no knowledge base whenever the bot is
# started from another directory — and under fail-open that looks like nothing
# happening at all, not like an error.
_HERE = os.path.dirname(os.path.abspath(__file__))
KB_PATH = os.path.normpath(
    os.path.join(_HERE, os.pardir, "docs", "knowledge", "feature-unlocks.json")
)

# A gate declared with either sentinel never skips. ALWAYS means the task has no
# game gate; UNKNOWN means one may exist but nobody has verified it. They are
# kept distinct so the report can tell "nothing to check" from "we do not know".
GATE_ALWAYS = "ALWAYS"
GATE_UNKNOWN = "UNKNOWN"
SENTINELS = (GATE_ALWAYS, GATE_UNKNOWN)

RUN = "RUN"
SKIP = "SKIP"

_OPS = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
}

_cache = None


def _reset_cache():
    """Drop the loaded knowledge base.

    Tests need it because the KB-missing, KB-corrupt and KB-valid cases would
    otherwise all see whichever ran first (same reason
    tests/test_engine_dispatch.py:220 has clean_engine_state). run_bot needs it
    because Main/main.py's account loop is `while True` — without a reload per
    pass, "load once per run" means "until you kill the process", and a
    corrected knowledge base would be ignored for as long as the bot runs.
    """
    global _cache
    _cache = None


def load_table(path=None):
    """Read the knowledge base. Returns (table, warnings); never raises.

    A missing or unparseable file yields an empty table and a warning. Empty
    means every gate resolves UNKNOWN, which fails open — so a broken knowledge
    base degrades to exactly today's behaviour rather than idling the bot.
    """
    global _cache
    if path is None and _cache is not None:
        return _cache

    target = path or KB_PATH
    warnings = []
    table = {"features": {}}

    try:
        with open(target, "r") as handle:
            raw = json.load(handle)
        table = {"features": raw.get("features") or {}}
    except FileNotFoundError:
        warnings.append(
            f"capability: knowledge base not found at {target} — every task "
            f"will run ungated"
        )
    except (json.JSONDecodeError, ValueError) as exc:
        warnings.append(
            f"capability: knowledge base at {target} is not valid JSON ({exc}) "
            f"— every task will run ungated"
        )

    result = (table, warnings)
    if path is None:
        _cache = result
    return result


def account_state(profile):
    """The facts the gate can read, and the ones it explicitly cannot.

    Keys that are always None are listed on purpose rather than omitted: a
    condition naming one then resolves to UNKNOWN and fails open, instead of
    raising KeyError or being silently treated as satisfied. When someone adds
    the read for state age or Command Center level, only this function changes.
    """
    alliance = profile.get("alliance") or {}
    name = alliance.get("name")
    return {
        "furnace_level": get_furnace_level(profile),
        # Not readable yet. profile["state"] is the state NUMBER (e.g. "4653"),
        # not its age in days, and nothing computes an age from it.
        "state_age_days": None,
        # Never read off screen.
        "command_center_level": None,
        # Captured by usecases.alliance.capture_alliance_state during init.
        # Absent until the first successful read, and "xxx" is example.json's
        # seed -- both mean unreadable, so conditions on them fail open.
        "alliance_member_count": alliance.get("member_count"),
        "alliance_name": None if name in (None, "", "xxx") else name,
    }


class Verdict:
    """Why a task will or will not run. Carries its own warnings; prints nothing."""

    __slots__ = ("decision", "reason", "source", "warnings", "feature", "checked")

    def __init__(self, decision, reason, source, feature=None,
                 warnings=None, checked=None):
        self.decision = decision
        self.reason = reason
        self.source = source   # "table" | "observed" | "sentinel" | "no-gate"
        self.feature = feature
        self.warnings = warnings or []
        # Per-condition trace: [(state_key, "met" | "unmet" | "unknown"), ...].
        # The report uses it to show which half of a composite gate was checked.
        self.checked = checked or []

    @property
    def should_run(self):
        return self.decision == RUN

    def __repr__(self):
        return f"<Verdict {self.decision} {self.feature or '-'}: {self.reason}>"


def observed_lock(feature, profile):
    """A still-valid observed lock for `feature`, or None.

    A lock stamped at a furnace level BELOW the account's current one is stale
    by construction: the account has progressed past the state that produced
    the evidence, so the feature may well be open now. That is what makes the
    stamp self-invalidating -- no expiry timer, no cleanup job, no operator
    action. Without it, a lock recorded at Furnace 7 would keep Pets skipped
    forever on an account that reached Furnace 18, which is a worse failure
    than the one the gate exists to fix.
    """
    record = (profile.get("observed_locks") or {}).get(feature)
    if not isinstance(record, dict):
        return None

    stamped = record.get("furnace_at_observation")
    current = get_furnace_level(profile)
    if isinstance(stamped, int) and isinstance(current, int) and current > stamped:
        return None
    return record


def _resolve_condition(condition, state):
    """(status, description) for one condition. status: met | unmet | unknown."""
    key = condition.get("state_key")
    op = condition.get("op")
    want = condition.get("value")

    if key not in state:
        return "unknown", f"{key} is not a known account fact"
    if op not in _OPS:
        return "unknown", f"unsupported operator {op!r} for {key}"

    have = state.get(key)
    if have is None:
        return "unknown", f"{key} is not readable yet"

    try:
        ok = _OPS[op](have, want)
    except TypeError:
        return "unknown", f"cannot compare {key}={have!r} to {want!r}"

    return ("met" if ok else "unmet"), f"{key}={have} {op} {want}"


def evaluate(gate, profile, table=None):
    """Decide whether a task declaring `gate` can run for this profile.

    `gate` is the feature key the task declares (TaskSpec.gate), or a sentinel.
    The task-to-feature mapping lives in code, next to the task list: it is a
    fact about this bot's routines. The requirement itself lives in the
    knowledge base, because it is a fact about the game and changes with
    patches, not with our code.

    Only a feature whose conditions are ALL readable, with at least one
    definitively unmet, produces SKIP. Everything else runs.
    """
    warnings = []
    if table is None:
        table, warnings = load_table()

    features = table.get("features") or {}
    state = account_state(profile)

    if gate is None:
        return Verdict(RUN, "task declares no gate", "no-gate",
                       warnings=warnings)

    if gate in SENTINELS:
        reason = ("no game gate" if gate == GATE_ALWAYS
                  else "gate not verified — running rather than guessing")
        return Verdict(RUN, reason, "sentinel", feature=gate, warnings=warnings)

    feature = features.get(gate)
    if not feature:
        return Verdict(RUN, f"feature {gate!r} missing from the knowledge base",
                       "no-gate", feature=gate, warnings=warnings)

    conditions = feature.get("conditions") or []
    if not conditions:
        return Verdict(RUN, f"{gate!r} declares no conditions", "no-gate",
                       feature=gate, warnings=warnings)

    checked = []
    unmet = []
    for condition in conditions:
        status, description = _resolve_condition(condition, state)
        checked.append((condition.get("state_key"), status, description))
        if status == "unmet":
            unmet.append(description)

    if unmet:
        return Verdict(
            SKIP,
            f"{feature.get('label', gate)} needs {'; '.join(unmet)}",
            "table", feature=gate, warnings=warnings, checked=checked,
        )

    # The table says nothing definitive stops this task. Before running it,
    # check what the bot actually SAW: in-game evidence outranks a
    # community-sourced table, which is the reason this design beat a static
    # one. A stale stamp is dropped by observed_lock() itself.
    seen = observed_lock(gate, profile)
    if seen:
        return Verdict(
            SKIP,
            f"{feature.get('label', gate)} was seen locked "
            f"({seen.get('evidence', 'no evidence')!r})",
            "observed", feature=gate, warnings=warnings, checked=checked,
        )

    unknown = [c[0] for c in checked if c[1] == "unknown"]
    if unknown:
        return Verdict(
            RUN,
            f"{feature.get('label', gate)}: cannot read {', '.join(unknown)} — "
            f"running rather than guessing",
            "table", feature=gate, warnings=warnings, checked=checked,
        )

    return Verdict(RUN, f"{feature.get('label', gate)} is unlocked", "table",
                   feature=gate, warnings=warnings, checked=checked)
