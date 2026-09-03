"""Glue between "the screen says this feature is locked" and the profile.

Deliberately a usecase-layer module, not part of core/capability.py: that one
is pure by contract (no OCR, no navigation, no I/O) so its verdicts can be
tested by asserting on a return value. This is the impure half.

The whole point is that only text actually READ off the screen may become a
recorded lock. usecases/arena.py find_arena() and usecases/labyrinth.py
go_to_labyrinth() both bail when a Daily Missions row is absent, and that row
also disappears when the daily is simply finished -- so recording from those
bails would permanently disable a working task the first day it succeeded.
Routing every record through read_lock_marker() makes that mistake impossible
to make by accident.
"""
from core.core import read_lock_marker
from core.player_profile import load_profile, record_lock


def note_if_locked(player_id, feature, context):
    """Record a lock for `feature` if the current screen proves one. Returns bool.

    `context` says where the observation happened, for the operator reading the
    profile later. `player_id` may be None (several routines are invoked with
    the id discarded), in which case there is nothing to write to and this is a
    no-op -- the static table still gates the feature.
    """
    if not player_id:
        return False

    marker = read_lock_marker()
    if not marker:
        return False

    print(f"{feature} reads as locked on screen ({marker!r}); "
          f"recording so later runs skip without navigating")
    return record_lock(load_profile(player_id), feature, context, marker)
