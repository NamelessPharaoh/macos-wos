"""Per-player profile persistence.

Reuses the schema that already ships in db/players/example.json: a profile is
that dict, seeded from the example on first sight of a player id and updated
as the account evolves (levels, unlocks, working gather node level). Nothing
here invents a parallel format.
"""
import json
import os
from datetime import datetime, timezone

PLAYERS_DIR = os.path.join("db", "players")
EXAMPLE_PATH = os.path.join(PLAYERS_DIR, "example.json")

DEFAULT_GATHER_NODE_LEVEL = 8


def _profile_path(player_id):
    return os.path.join(PLAYERS_DIR, f"{player_id}.json")


def load_profile(player_id):
    """Load db/players/<id>.json, seeding a new profile from example.json
    when the player has none yet or the file is corrupt (upstream even shipped
    a zero-byte one). A missing example.json still raises: that is repo
    damage, and failing loud beats running with an empty profile."""
    path = _profile_path(player_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            print(f"⚠️ Corrupt profile {path}, reseeding from example.json")

    with open(EXAMPLE_PATH, "r") as f:
        profile = json.load(f)
    profile["id"] = str(player_id)
    profile["name"] = None
    return profile


def save_profile(profile):
    """Write the profile back to db/players/<id>.json."""
    path = _profile_path(profile["id"])
    os.makedirs(PLAYERS_DIR, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(profile, f, indent=4)
    os.replace(tmp, path)


FURNACE_MIN = 1
# Base furnace caps at 30. Fire Crystal levels (FC1-FC10) come after it and are
# NOT representable as a plain int, so a read outside this range is rejected
# rather than guessed at. Revisit when there is an FC account to read from.
FURNACE_MAX = 30
FURNACE_MAX_STEP = 1


def get_furnace_level(profile):
    """The account's last trusted furnace level, or None if never confirmed."""
    level = profile.get("furnace_level")
    if level is None:
        return None
    try:
        level = int(level)
    except (TypeError, ValueError):
        return None
    return level if FURNACE_MIN <= level <= FURNACE_MAX else None


def validate_furnace_read(profile, raw):
    """Decide whether an OCR'd furnace level may be persisted.

    The capability gate reads this number, so a misread is a silent behaviour
    change rather than an error: 7 read as 17 marks Pets, Arena and Storehouse
    unlocked and sends the bot into locked screens; 7 read as 1 gates nearly
    everything off. `int()` on its own accepts both.

    Three rules, cheapest first — the value must parse and sit in range, it may
    never decrease (furnaces do not), and it may not climb more than one level
    between runs. Returns (level_or_None, reason); a rejected read leaves the
    stored value untouched.

    A profile with no stored level has no floor to compare against, so its first
    read is accepted at face value and reported as unconfirmed. WOS_FURNACE_RESET
    is the escape when that first read was wrong.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, "no-read"

    try:
        level = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, f"not-a-number: {raw!r}"

    if not (FURNACE_MIN <= level <= FURNACE_MAX):
        return None, f"out-of-range: {level} not in {FURNACE_MIN}..{FURNACE_MAX}"

    stored = get_furnace_level(profile)
    if stored is None:
        return level, "unconfirmed-first-read"
    if level < stored:
        return None, f"decreased: {stored} -> {level}, furnace levels never drop"
    if level - stored > FURNACE_MAX_STEP:
        return None, f"jumped: {stored} -> {level}, more than {FURNACE_MAX_STEP} level"
    return level, "ok"


def apply_furnace_reset(profile):
    """WOS_FURNACE_RESET=<n>: the operator escape when a bad value stuck.

    The monotonic rule above means a too-high level can never be corrected
    downward by another read, and a fresh profile's first read has no floor to
    reject it. This writes the value directly and clears every observed lock
    recorded at or above it, so a feature wrongly marked locked becomes
    testable again. Returns the applied level, or None when unset or invalid.
    """
    raw = os.environ.get("WOS_FURNACE_RESET")
    if raw is None or not str(raw).strip():
        return None

    try:
        level = int(str(raw).strip())
    except (TypeError, ValueError):
        print(f"⚠️ WOS_FURNACE_RESET={raw!r} is not a number, ignoring")
        return None

    if not (FURNACE_MIN <= level <= FURNACE_MAX):
        print(f"⚠️ WOS_FURNACE_RESET={level} outside "
              f"{FURNACE_MIN}..{FURNACE_MAX}, ignoring")
        return None

    profile["furnace_level"] = level
    locks = profile.get("observed_locks") or {}
    kept = {
        key: rec for key, rec in locks.items()
        if isinstance(rec, dict)
        and isinstance(rec.get("furnace_at_observation"), int)
        and rec["furnace_at_observation"] < level
    }
    cleared = len(locks) - len(kept)
    profile["observed_locks"] = kept
    save_profile(profile)
    print(f"WOS_FURNACE_RESET applied: furnace_level={level}, "
          f"cleared {cleared} observed lock(s)")
    return level


def record_lock(profile, feature, reason, evidence):
    """Persist that a FEATURE was observed locked, and what proved it.

    Keyed by feature rather than task: pet_treasure and pet_exploration both
    gate on beast_cage, so a lock either of them sees applies to both.

    `evidence` is POSITIONAL and required, deliberately. A keyword argument
    validated with a raise would be swallowed -- Main/task_menu.py:195 catches
    bare Exception, so a ValueError here is reported as "task crashed", and a
    future failure-streak suspend would then disable a healthy task because the
    lock recorder was miscalled. A missing positional fails at the call site
    where no catch-all sees it, and the test suite catches it at collection.

    What must never reach here is ABSENCE. usecases/arena.py find_arena() and
    usecases/labyrinth.py go_to_labyrinth() return False when a Daily Missions
    row is missing -- and that row also disappears once the daily is simply
    done. Recording from those bails would permanently disable a working task
    on the first day it succeeded. Pass core.core.read_lock_marker()'s return
    value: text actually read off the screen.

    Stamped with the furnace level that observed it, so a level-up invalidates
    it automatically (see core/capability.observed_lock) with no expiry timer
    and no cleanup job.
    """
    if not evidence:
        # Refusing to write is the safe direction: the static table still gates
        # the feature. Raising would be worse -- see the docstring.
        print(f"⚠️ record_lock({feature!r}) called with no evidence; not recorded")
        return False

    profile.setdefault("observed_locks", {})[feature] = {
        "reason": reason,
        "evidence": str(evidence)[:200],
        "furnace_at_observation": get_furnace_level(profile),
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    try:
        save_profile(profile)
    except OSError as exc:
        # A lock that cannot be saved must not kill the task that saw it.
        print(f"⚠️ could not persist the observed lock for {feature}: {exc}")
        return False
    return True


# Membership changes rarely: a name almost never, a member count across a gate
# threshold maybe weekly. Paying a recalibrate + tap + verify round-trip (~4-6s)
# on every startup would tax every run for data that moves monthly.
ALLIANCE_REFRESH_SECONDS = 24 * 60 * 60
ALLIANCE_SEED_NAME = "xxx"


def alliance_state_is_stale(profile, now=None):
    """Whether the alliance snapshot is worth re-reading."""
    alliance = profile.get("alliance") or {}
    name = alliance.get("name")
    if not name or name == ALLIANCE_SEED_NAME:
        return True

    seen = alliance.get("last_verified")
    if not seen:
        return True
    try:
        stamped = datetime.fromisoformat(str(seen))
    except (TypeError, ValueError):
        return True
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)

    now = now or datetime.now(timezone.utc)
    return (now - stamped).total_seconds() >= ALLIANCE_REFRESH_SECONDS


def set_alliance_state(profile, name, member_count):
    """Persist an observed alliance snapshot. Returns True when it wrote.

    Refuses a blank name: the capture path returns None when the screen could
    not be read, and overwriting a good value with nothing would make the gate
    forget an alliance the account is still in.
    """
    if not name:
        return False

    alliance = profile.setdefault("alliance", {})
    alliance["name"] = name
    if isinstance(member_count, int):
        alliance["member_count"] = member_count
    alliance["last_verified"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        save_profile(profile)
    except OSError as exc:
        print(f"⚠️ could not persist the alliance snapshot: {exc}")
        return False
    return True


DEFAULT_GATHER_REMOVE_HERO = False
DEFAULT_GATHER_EQUALIZE = True


def get_gather_flags(profile):
    """Per-account gather behaviour, as (remove_hero, equalize).

    This was a hardcoded `if current_player_id == "578380047"` in the task
    dispatcher. That account is upstream's, and its profile was deliberately
    removed from this repo as a privacy fix (see .gitignore) — it ran
    remove_hero=True, equalize=False, recorded here so the setting is not lost.
    Every other account takes the defaults above. Per-account facts belong in
    the profile, not in a dispatch conditional.
    """
    gather_cfg = profile.get("gather") or {}
    return (
        bool(gather_cfg.get("remove_hero", DEFAULT_GATHER_REMOVE_HERO)),
        bool(gather_cfg.get("equalize", DEFAULT_GATHER_EQUALIZE)),
    )


def get_gather_node_level(profile):
    """The gather node level this account is known to sustain."""
    level = profile.get("gather", {}).get("node_level", DEFAULT_GATHER_NODE_LEVEL)
    try:
        return max(1, min(8, int(level)))
    except (TypeError, ValueError):
        return DEFAULT_GATHER_NODE_LEVEL


def set_gather_node_level(profile, level):
    """Record the node level that actually worked, so the next run starts there."""
    profile.setdefault("gather", {})["node_level"] = max(1, min(8, int(level)))
    save_profile(profile)
