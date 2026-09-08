"""Static/dynamic path registry and SQL DDL for the wos-chief-state model.

This module is pure data: every known "sheet" path (see native/model.py's
module docstring for what a sheet is), its validator class, its storage type,
and the SQL that creates the tables/index/view those paths live in. It has no
sqlite3.Connection-touching logic of its own -- native/model.py owns that --
so it can be imported (and its registry inspected) without opening a database.

A "static" path is one every snapshot carries a row for, even when unread
(identity, progress, economy, city, research summaries, troops totals, gear,
backpack summaries, alliance). A "dynamic" path is a collection whose members
come and go (a live event, a training queue, a backpack item, a hero) --
DYNAMIC_PREFIXES lists the doc prefixes that make a path dynamic; dynamic
paths are never in STATIC_SCHEMA and are never "carried" between snapshots.
"""

SCHEMA_VERSION = 1

DYNAMIC_PREFIXES = (
    "events.",
    "troops.by_name.",
    "city.queues.",
    "troops.training.",
    "backpack.items.",
    "heroes.",
)

# path -> (field_class, value_type)
# field_class: identity | monotonic_step | monotonic | bounded | volatile
# value_type: int | text
STATIC_SCHEMA = {}


def _add(path, field_class, value_type="int"):
    STATIC_SCHEMA[path] = (field_class, value_type)


_add("identity.id", "identity", "text")
_add("identity.name", "volatile", "text")
_add("identity.state", "identity", "int")
_add("identity.state_age_days", "volatile", "int")

_add("progress.furnace.level", "volatile", "int")
_add("progress.furnace.fc", "volatile", "int")
_add("progress.furnace.sub", "volatile", "int")
_add("progress.furnace.ordinal", "monotonic_step", "int")
_add("progress.furnace.upgrading.to", "volatile", "int")
_add("progress.furnace.upgrading.remaining_s", "volatile", "int")
_add("progress.vip.level", "monotonic_step", "int")
_add("progress.power", "bounded", "int")
_add("progress.kills", "monotonic", "int")

_add("economy.gems", "volatile", "int")
for _res in ("meat", "wood", "coal", "iron"):
    _add(f"economy.resources.{_res}", "volatile", "int")
_add("economy.stamina.value", "volatile", "int")
_add("economy.stamina.cap", "volatile", "int")

_add("city.survivors.value", "volatile", "int")
_add("city.survivors.cap", "volatile", "int")

BUILDINGS = (
    "furnace",
    "embassy",
    "command_center",
    "research_center",
    "infantry_camp",
    "lancer_camp",
    "marksman_camp",
    "infirmary",
    "storehouse",
    "warehouse",
    "war_academy",
)
for _b in BUILDINGS:
    _add(f"city.buildings.{_b}", "monotonic", "int")

_add("research.current.name", "volatile", "text")
_add("research.current.remaining_s", "volatile", "int")
RESEARCH_TABS = ("growth", "economy", "battle")
for _tab in RESEARCH_TABS:
    _add(f"research.tabs.{_tab}.done", "monotonic", "int")
    _add(f"research.tabs.{_tab}.total", "volatile", "int")

TROOP_TYPES = ("infantry", "lancer", "marksman")
for _t in TROOP_TYPES:
    for _tier in range(1, 12):
        _add(f"troops.by_tier.{_t}.t{_tier}", "volatile", "int")
    _add(f"troops.totals.{_t}", "volatile", "int")
_add("troops.wounded.value", "volatile", "int")
_add("troops.wounded.cap", "volatile", "int")
_add("troops.capacity.deploy", "volatile", "int")
_add("troops.capacity.rally", "volatile", "int")

GEAR_SLOTS = ("helmet", "watch", "jacket", "pants", "ring", "cane")
for _slot in GEAR_SLOTS:
    _add(f"gear.chief.{_slot}.tier", "volatile", "text")
    _add(f"gear.chief.{_slot}.rank", "monotonic", "int")
    _add(f"gear.chief.{_slot}.stars", "monotonic", "int")
    for _charm in range(3):
        _add(f"gear.charms.{_slot}.{_charm}", "monotonic", "int")

SPEEDUP_TYPES = ("general", "construction", "research", "training", "healing")
SPEEDUP_DURATIONS = ("1m", "5m", "10m", "15m", "30m", "1h", "3h", "8h", "24h")
for _st in SPEEDUP_TYPES:
    for _dur in SPEEDUP_DURATIONS:
        _add(f"backpack.speedups.{_st}.{_dur}", "volatile", "int")
_add("backpack.fire_crystals", "volatile", "int")
_add("backpack.refined_fire_crystals", "volatile", "int")

# Phase-1 survey additions (2026-09-08): Troops Preview header, Resource
# Overview columns, alliance hub details.
_add("troops.total.value", "volatile", "int")
_add("troops.total.cap", "volatile", "int")
_add("troops.march_queue.used", "volatile", "int")
_add("troops.march_queue.cap", "volatile", "int")
for _r in ("meat", "wood", "coal", "iron"):
    _add(f"economy.output.{_r}", "volatile", "int")
    _add(f"economy.protected.{_r}", "volatile", "int")
_add("alliance.tag", "volatile", "text")
_add("alliance.leader", "volatile", "text")
_add("alliance.power", "volatile", "int")
_add("alliance.state_rank", "volatile", "int")
_add("alliance.level", "monotonic", "int")
_add("alliance.name", "volatile", "text")
_add("alliance.members", "volatile", "int")
_add("alliance.cap", "volatile", "int")
_add("alliance.rank", "volatile", "text")


def path_class(path):
    """Validator class for a known static path, else None. Never true for a
    dynamic path -- callers check is_dynamic() separately."""
    entry = STATIC_SCHEMA.get(path)
    return entry[0] if entry else None


def value_type(path):
    """'int' or 'text' for a known static path, else None."""
    entry = STATIC_SCHEMA.get(path)
    return entry[1] if entry else None


def is_dynamic(path):
    return path.startswith(DYNAMIC_PREFIXES)


def is_known(path):
    return path in STATIC_SCHEMA or is_dynamic(path)


def static_paths():
    return tuple(STATIC_SCHEMA.keys())


# Longest/most-specific prefix first: a path is routed to the first prefix it
# starts with, so "progress.power" and "progress.vip" must precede the
# catch-all "progress." (which routes the rest of progress.* to "profile").
SECTION_OF_PATH_TABLE = (
    ("identity.", "profile"),
    ("progress.power", "hud"),
    ("progress.vip", "hud"),
    ("progress.", "profile"),
    ("economy.gems", "hud"),
    ("economy.resources.coal", "hud"),
    ("economy.resources.", "resources"),
    ("economy.output.", "resources"),
    ("economy.protected.", "resources"),
    ("economy.stamina", "profile"),
    ("city.survivors", "hud"),
    ("city.queues.", "queues"),
    ("troops.training.", "queues"),
    ("research.current.", "queues"),
    ("city.", "buildings"),
    ("research.", "research"),
    ("troops.", "troops"),
    ("heroes.", "heroes"),
    ("gear.", "gear"),
    ("backpack.", "backpack"),
    ("events.", "events"),
    ("alliance.", "alliance"),
)

ALL_READERS = tuple(sorted({reader for _, reader in SECTION_OF_PATH_TABLE}))


def SECTION_OF_PATH(path):
    """Which reader is responsible for a path, by longest-matching prefix.
    None only for a path that fits none of the known sections (unreachable
    for anything in STATIC_SCHEMA today, but callers should not assume it)."""
    for prefix, reader in SECTION_OF_PATH_TABLE:
        if path.startswith(prefix):
            return reader
    return None


# Table DDL only. The `latest_static` view (OV2) is assembled in model.py
# next to SCHEMA_VERSION, since its correlated subquery encodes a rule
# (newest non-unread row per path, operator rows included) rather than a
# fixed shape -- keeping it here would split one rule across two files.
DDL = """
CREATE TABLE IF NOT EXISTS players (
  id              TEXT PRIMARY KEY,
  name            TEXT,
  state           INTEGER,
  is_main         INTEGER NOT NULL DEFAULT 0,
  state_opened_on TEXT,
  first_seen      TEXT NOT NULL,
  last_seen       TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_main ON players(is_main) WHERE is_main = 1;

CREATE TABLE IF NOT EXISTS snapshots (
  id             TEXT PRIMARY KEY,
  player_id      TEXT NOT NULL REFERENCES players(id),
  taken_at       TEXT NOT NULL,
  source         TEXT NOT NULL,
  run_dir        TEXT,
  status         TEXT NOT NULL,
  abort_reason   TEXT,
  sections       TEXT NOT NULL,
  duration_s     INTEGER,
  gems_before    INTEGER,
  gems_after     INTEGER,
  power_before   INTEGER,
  power_after    INTEGER,
  power_rose     INTEGER NOT NULL DEFAULT 0,
  schema_version INTEGER NOT NULL,
  doc            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fields (
  snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
  player_id   TEXT NOT NULL,
  path        TEXT NOT NULL,
  kind        TEXT NOT NULL,
  value_num   REAL,
  value_text  TEXT,
  exact       INTEGER NOT NULL DEFAULT 1,
  raw         TEXT,
  frame       TEXT,
  score       REAL,
  method      TEXT,
  status      TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, path)
);

CREATE INDEX IF NOT EXISTS fields_series ON fields(player_id, path, snapshot_id);
"""
