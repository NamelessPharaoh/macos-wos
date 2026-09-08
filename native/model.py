"""SQLite account model for wos-chief-state.

Standard-library only (sqlite3, json, re, math, datetime, os, shutil).
native/schema.py holds the static/dynamic path registry and table DDL; this
module owns everything touching a sqlite3.Connection plus the pure helpers
(flatten, slug, validate) that feed it.

    doc --flatten()--> flat {"progress.furnace.ordinal": 34, ...}
      --validate(path, new, prev, days_since_prev)--> (accepted, status)
      --write_snapshot()--> sqlite: players / snapshots (doc+envelope) / fields
      --> latest_static VIEW (OV2): newest non-unread row per (player, path)
      --> latest_dynamic(): newest snapshot whose reader section == 'ok'
      --> deltas(): current snapshot vs previous non-operator snapshot

status in {ok, "rejected: <reason>", carried, unread}. write_abort() is the
money-guard/identity-mismatch shortcut: a snapshot row with no `fields`.
"""
import json
import math
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

from . import schema
from .schema import (  # re-exported for callers of native.model
    DYNAMIC_PREFIXES,
    STATIC_SCHEMA,
    is_dynamic,
    is_known,
    path_class,
    static_paths,
)

SCHEMA_VERSION = 1

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(_HERE, os.pardir))
DB_PATH = os.environ.get("WOS_DB_PATH") or os.path.join(REPO, "db", "wos.sqlite")

# OV2: newest row per (player_id, path) whose status is not 'unread', across
# non-aborted snapshots, operator rows included. Ids are the UTC stamp
# "YYYYMMDDTHHMMSSZ" so MAX(id) as text orders the same as MAX(taken_at).
_LATEST_STATIC_VIEW = """
CREATE VIEW IF NOT EXISTS latest_static AS
SELECT f.* FROM fields f
JOIN snapshots s ON s.id = f.snapshot_id
WHERE f.kind = 'static'
  AND f.status != 'unread'
  AND s.status != 'aborted'
  AND f.snapshot_id = (
    SELECT MAX(f2.snapshot_id) FROM fields f2
    JOIN snapshots s2 ON s2.id = f2.snapshot_id
    WHERE f2.player_id = f.player_id AND f2.path = f.path
      AND f2.kind = 'static' AND f2.status != 'unread' AND s2.status != 'aborted'
  );
"""

# v1 has no predecessor; later versions append callables keyed by the target
# version, run in order inside ensure_schema.
MIGRATIONS = {}


def ensure_schema(conn):
    """Create tables/index/view (IF NOT EXISTS, safe every connect()), then
    bring user_version up to SCHEMA_VERSION."""
    conn.executescript(schema.DDL)
    conn.executescript(_LATEST_STATIC_VIEW)
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < SCHEMA_VERSION:
        for version in range(current + 1, SCHEMA_VERSION + 1):
            migration = MIGRATIONS.get(version)
            if migration is not None:
                migration(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def connect(path=None):
    """Open/create the DB and ensure its schema. `path` beats WOS_DB_PATH
    beats the repo-relative default."""
    db_path = path or os.environ.get("WOS_DB_PATH") or DB_PATH
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


# -- pure helpers: flatten, slug, tier/rarity ranks --------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text):
    """Lowercase, collapse non-alnum runs to '_', trim leading/trailing '_'."""
    return _SLUG_RE.sub("_", str(text).strip().lower()).strip("_")


def flatten(doc):
    """Nested dict/list -> {"dotted.path": leaf}. Dict keys used verbatim
    (already slugged); lists become ".0", ".1"; None leaves are kept
    (present-but-null means the reader looked and found nothing)."""
    out = {}

    def _walk(prefix, value):
        if isinstance(value, dict):
            for key, sub in value.items():
                _walk(f"{prefix}.{key}" if prefix else str(key), sub)
        elif isinstance(value, list):
            for index, sub in enumerate(value):
                _walk(f"{prefix}.{index}" if prefix else str(index), sub)
        else:
            out[prefix] = value

    _walk("", doc)
    return out


_TIER_BASE_RANKS = {"green": 0, "blue": 1, "purple": 2, "gold": 3}
_RED_TIER_RE = re.compile(r"^red_?t?(\d+)$")
_RARITY_RANKS = {"rare": 0, "epic": 1, "mythic": 2}


def rank_of_tier(text):
    """green 0 < blue 1 < purple 2 < gold 3 < red T1..T6 -> 4..9; bare 'red'
    is T1 (4). Unknown -> None."""
    if text is None:
        return None
    key = slug(text)
    if key in _TIER_BASE_RANKS:
        return _TIER_BASE_RANKS[key]
    if key == "red":
        return 4
    match = _RED_TIER_RE.match(key)
    if match:
        tier_number = int(match.group(1))
        if 1 <= tier_number <= 6:
            return 3 + tier_number
    return None


def rank_of_rarity(text):
    """rare 0 < epic 1 < mythic 2; unknown -> None."""
    if text is None:
        return None
    return _RARITY_RANKS.get(slug(text))


# -- validation (OV1) ---------------------------------------------------------


def validate(path, new_value, prev_value, days_since_prev=None):
    """(accepted_value, status) for one reading against the per-path latest
    accepted value (operator rows included; caller supplies `prev_value`).

    identity: not handled here; raises -- caller compares id/state directly
    and aborts on mismatch. monotonic_step (furnace ordinal, VIP level):
    never decreases, step <= max(1, ceil(days_since_prev)). monotonic (kills,
    building levels, gear/charm ranks, research done): never decreases, no
    step cap. bounded (power): within 0.5x-1.5x of prev unless prev is None.
    volatile: non-negative int, or non-empty text for text paths, no
    comparison against prev. new_value None always means 'unread'; a None
    prev_value always accepts the first valid reading outright.
    """
    field_class = schema.path_class(path)
    if field_class is None:
        raise ValueError(f"unknown static path: {path}")
    if field_class == "identity":
        raise ValueError(
            f"validate() does not handle identity paths ({path}); the caller "
            "must compare id/state directly and abort the run on mismatch"
        )
    if new_value is None:
        return None, "unread"

    if schema.value_type(path) == "text":
        if not isinstance(new_value, str) or not new_value.strip():
            return prev_value, "rejected: empty text"
        return new_value, "ok"

    try:
        number = float(new_value)
    except (TypeError, ValueError):
        return prev_value, "rejected: not a number"
    if number < 0:
        return prev_value, "rejected: negative value"
    accepted = int(round(number))

    if field_class == "volatile" or prev_value is None:
        return accepted, "ok"
    prev_number = float(prev_value)

    if field_class == "monotonic_step":
        if accepted < prev_number:
            return prev_value, f"rejected: decreased {prev_number:g} -> {accepted}"
        allowance = max(1, math.ceil(days_since_prev or 0))
        step = accepted - prev_number
        if step > allowance:
            return prev_value, f"rejected: step {step:g} exceeds allowance {allowance}"
        return accepted, "ok"

    if field_class == "monotonic":
        if accepted < prev_number:
            return prev_value, f"rejected: decreased {prev_number:g} -> {accepted}"
        return accepted, "ok"

    if field_class == "bounded":
        low, high = prev_number * 0.5, prev_number * 1.5
        if accepted < low or accepted > high:
            return prev_value, f"rejected: out of bounds ({accepted} not in [{low:g}, {high:g}])"
        return accepted, "ok"

    raise ValueError(f"unhandled field class: {field_class}")  # pragma: no cover


def _split_value(path, value):
    """(value_num, value_text) for a known static path's accepted value."""
    if value is None:
        return None, None
    if schema.value_type(path) == "text":
        return None, str(value)
    return float(value), None


_LAST_ID = {"value": None}


def new_snapshot_id(now=None):
    """UTC second stamp, strictly increasing within a process: three `--set`
    writes in one second collided on the primary key (2026-09-08), so a stamp
    equal to or below the last one issued is advanced by a second."""
    stamp = now or datetime.now(timezone.utc)
    sid = stamp.strftime("%Y%m%dT%H%M%SZ")
    last = _LAST_ID["value"]
    if last is not None and sid <= last:
        from datetime import timedelta
        nxt = datetime.strptime(last, "%Y%m%dT%H%M%SZ") + timedelta(seconds=1)
        sid = nxt.strftime("%Y%m%dT%H%M%SZ")
    _LAST_ID["value"] = sid
    return sid


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(timestamp):
    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"
    return datetime.fromisoformat(timestamp)


def days_since(prev_taken_at, now_taken_at):
    return (_parse_iso(now_taken_at) - _parse_iso(prev_taken_at)).total_seconds() / 86400.0


# -- writers ------------------------------------------------------------------


def _upsert_player(conn, player, taken_at):
    player_id = player["id"]
    name = player.get("name")
    state = player.get("state")
    exists = conn.execute("SELECT 1 FROM players WHERE id = ?", (player_id,)).fetchone()
    if exists is None:
        conn.execute(
            "INSERT INTO players (id, name, state, is_main, state_opened_on, first_seen, last_seen) "
            "VALUES (?, ?, ?, 0, NULL, ?, ?)",
            (player_id, name, state, taken_at, taken_at),
        )
    else:
        conn.execute(
            "UPDATE players SET name = COALESCE(?, name), state = COALESCE(?, state), last_seen = ? "
            "WHERE id = ?",
            (name, state, taken_at, player_id),
        )


def _prev_field(conn, player_id, path, exclude_snapshot_id):
    """(value, taken_at) of the per-path latest accepted row (OV1/OV2),
    excluding the snapshot being written."""
    row = conn.execute(
        """
        SELECT f.value_num, f.value_text, s.taken_at
        FROM fields f JOIN snapshots s ON s.id = f.snapshot_id
        WHERE f.player_id = ? AND f.path = ? AND f.kind = 'static'
          AND f.status != 'unread' AND s.status != 'aborted'
          AND f.snapshot_id != ?
        ORDER BY f.snapshot_id DESC LIMIT 1
        """,
        (player_id, path, exclude_snapshot_id),
    ).fetchone()
    if row is None:
        return None, None
    value = row["value_num"] if row["value_num"] is not None else row["value_text"]
    return value, row["taken_at"]


def _default_days_since_prev(conn, player_id, taken_at, exclude_snapshot_id):
    row = conn.execute(
        "SELECT MAX(taken_at) AS t FROM snapshots WHERE player_id = ? AND status != 'aborted' AND id != ?",
        (player_id, exclude_snapshot_id),
    ).fetchone()
    return days_since(row["t"], taken_at) if row and row["t"] else None


def _insert_field(conn, *, snapshot_id, player_id, path, kind, value, exact, raw, frame,
                   score, method, status):
    if kind == "static":
        value_num, value_text = _split_value(path, value)
    elif isinstance(value, bool):
        value_num, value_text = float(int(value)), None
    elif isinstance(value, (int, float)):
        value_num, value_text = float(value), None
    else:
        value_num, value_text = None, (None if value is None else str(value))
    conn.execute(
        "INSERT INTO fields (snapshot_id, player_id, path, kind, value_num, value_text, exact, "
        "raw, frame, score, method, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (snapshot_id, player_id, path, kind, value_num, value_text, int(exact), raw, frame,
         score, method, status),
    )


def _provenance_fields(provenance, path):
    prov = provenance.get(path)
    if not prov:
        return None, None, None, None, 1, None
    return (prov.get("raw"), prov.get("frame"), prov.get("score"), prov.get("method"),
            prov.get("exact", 1), prov.get("status"))


def write_snapshot(conn, *, player, snapshot_id, taken_at, source, run_dir, status,
                    sections, duration_s, gems_before, gems_after, power_before,
                    power_after, power_rose, doc, provenance, days_since_prev=None):
    """Upsert the player, insert the snapshot envelope, write one `fields` row
    for every known static path plus one for every dynamic path present in
    `doc`. Returns {written, ok, rejected, carried, unread, dynamic} counts.

    `provenance` is {path: {raw, frame, score, method, exact?, status?}} for
    paths a reader touched (present even when the parsed value is None --
    that is how a field is told apart from one never looked at). `sections`
    is {reader: ok|partial|failed|skipped}; decides unread vs carried for
    static paths not touched this run.
    """
    player_id = player["id"]
    provenance = provenance or {}
    _upsert_player(conn, player, taken_at)

    if days_since_prev is None:
        days_since_prev = _default_days_since_prev(conn, player_id, taken_at, snapshot_id)

    conn.execute(
        "INSERT INTO snapshots (id, player_id, taken_at, source, run_dir, status, abort_reason, "
        "sections, duration_s, gems_before, gems_after, power_before, power_after, power_rose, "
        "schema_version, doc) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (snapshot_id, player_id, taken_at, source, run_dir, status, json.dumps(sections),
         duration_s, gems_before, gems_after, power_before, power_after, int(bool(power_rose)),
         SCHEMA_VERSION, json.dumps(doc)),
    )

    flat = flatten(doc)
    counts = {"written": 0, "ok": 0, "rejected": 0, "carried": 0, "unread": 0, "dynamic": 0}

    for path in schema.static_paths():
        raw, frame, score, method, exact, _ = _provenance_fields(provenance, path)
        was_attempted = path in provenance or (path in flat and flat[path] is not None)
        prev_value, _ = _prev_field(conn, player_id, path, snapshot_id)
        if was_attempted:
            new_value = flat.get(path)
            if schema.path_class(path) == "identity":
                # Identity mismatches abort the run before write_snapshot is
                # ever called (see validate()'s docstring), so a read that
                # gets here is trusted as-is; only "attempted but empty"
                # still needs the universal None -> 'unread' rule.
                accepted, field_status = (None, "unread") if new_value is None else (new_value, "ok")
            else:
                accepted, field_status = validate(path, new_value, prev_value, days_since_prev)
        else:
            section_status = sections.get(schema.SECTION_OF_PATH(path))
            if section_status == "ok":
                accepted, field_status = None, "unread"
            elif prev_value is not None:
                accepted, field_status = prev_value, "carried"
            else:
                accepted, field_status = None, "unread"
        _insert_field(conn, snapshot_id=snapshot_id, player_id=player_id, path=path,
                      kind="static", value=accepted, exact=exact, raw=raw, frame=frame,
                      score=score, method=method, status=field_status)
        counts["written"] += 1
        if field_status == "ok":
            counts["ok"] += 1
        elif field_status.startswith("rejected"):
            counts["rejected"] += 1
        elif field_status == "carried":
            counts["carried"] += 1
        else:
            counts["unread"] += 1

    for path, value in flat.items():
        if not schema.is_dynamic(path) or value is None:
            continue
        raw, frame, score, method, exact, prov_status = _provenance_fields(provenance, path)
        _insert_field(conn, snapshot_id=snapshot_id, player_id=player_id, path=path,
                      kind="dynamic", value=value, exact=exact, raw=raw, frame=frame,
                      score=score, method=method, status=prov_status or "ok")
        counts["written"] += 1
        counts["dynamic"] += 1

    conn.commit()
    return counts


def write_abort(conn, *, player_id, snapshot_id, taken_at, source, run_dir, reason,
                 sections, gems_before, power_before):
    """Snapshot row with no `fields` at all: the money-guard/identity gate."""
    _upsert_player(conn, {"id": player_id}, taken_at)
    conn.execute(
        "INSERT INTO snapshots (id, player_id, taken_at, source, run_dir, status, abort_reason, "
        "sections, duration_s, gems_before, gems_after, power_before, power_after, power_rose, "
        "schema_version, doc) VALUES (?, ?, ?, ?, ?, 'aborted', ?, ?, NULL, ?, NULL, ?, NULL, 0, ?, '{}')",
        (snapshot_id, player_id, taken_at, source, run_dir, reason, json.dumps(sections),
         gems_before, power_before, SCHEMA_VERSION),
    )
    conn.commit()


def _unflatten_one(path, value):
    parts = path.split(".")
    doc = current = {}
    for index, part in enumerate(parts):
        if index == len(parts) - 1:
            current[part] = value
        else:
            current[part] = {}
            current = current[part]
    return doc


def write_operator(conn, *, player_id, path, value, taken_at, snapshot_id):
    """`--set path=value`: one field row with method='operator', a dedicated
    snapshot with every reader section 'skipped', value validated by its
    class -- except identity.state, whose operator writes exist to override
    it (a state-transfer)."""
    field_class = schema.path_class(path)
    if field_class is None or schema.is_dynamic(path):
        raise ValueError(f"write_operator: not a known static path: {path}")

    prev_value, _ = _prev_field(conn, player_id, path, exclude_snapshot_id=snapshot_id)

    if field_class == "identity":
        if schema.value_type(path) == "int":
            try:
                accepted = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"write_operator: invalid int for {path}: {value!r}")
        else:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"write_operator: invalid text for {path}: {value!r}")
            accepted = value
    else:
        accepted, field_status = validate(path, value, prev_value, days_since_prev=None)
        if field_status != "ok":
            raise ValueError(f"write_operator: value rejected for {path}: {field_status}")

    _upsert_player(conn, {"id": player_id}, taken_at)
    doc = _unflatten_one(path, accepted)
    sections = {reader: "skipped" for reader in schema.ALL_READERS}
    conn.execute(
        "INSERT INTO snapshots (id, player_id, taken_at, source, run_dir, status, abort_reason, "
        "sections, duration_s, gems_before, gems_after, power_before, power_after, power_rose, "
        "schema_version, doc) VALUES (?, ?, ?, 'operator', NULL, 'ok', NULL, ?, NULL, NULL, NULL, "
        "NULL, NULL, 0, ?, ?)",
        (snapshot_id, player_id, taken_at, json.dumps(sections), SCHEMA_VERSION, json.dumps(doc)),
    )
    _insert_field(conn, snapshot_id=snapshot_id, player_id=player_id, path=path, kind="static",
                  value=accepted, exact=1, raw=None, frame=None, score=None, method="operator",
                  status="ok")
    conn.commit()


# -- readers ------------------------------------------------------------------


def latest(conn, player_id):
    """{path: row dict} from latest_static -- the current sheet."""
    rows = conn.execute("SELECT * FROM latest_static WHERE player_id = ?", (player_id,)).fetchall()
    return {row["path"]: dict(row) for row in rows}


def latest_dynamic(conn, player_id, reader):
    """{path: row dict} of one reader's dynamic fields from the newest
    non-aborted snapshot whose sections[reader] == 'ok' (dynamic paths are
    never carried, so an older run's extra paths have vanished)."""
    row = conn.execute(
        "SELECT id FROM snapshots WHERE player_id = ? AND status != 'aborted' "
        "AND json_extract(sections, '$.' || ?) = 'ok' ORDER BY id DESC LIMIT 1",
        (player_id, reader),
    ).fetchone()
    if row is None:
        return {}
    rows = conn.execute(
        "SELECT * FROM fields WHERE snapshot_id = ? AND player_id = ? AND kind = 'dynamic'",
        (row["id"], player_id),
    ).fetchall()
    return {r["path"]: dict(r) for r in rows if schema.SECTION_OF_PATH(r["path"]) == reader}


def previous_snapshot(conn, player_id, before_id, exclude_operator=True):
    query = "SELECT * FROM snapshots WHERE player_id = ? AND id < ? AND status != 'aborted'"
    params = [player_id, before_id]
    if exclude_operator:
        query += " AND source != 'operator'"
    query += " ORDER BY id DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def series(conn, player_id, path, limit=None):
    """[(snapshot_id, taken_at, value), ...] ascending, every non-aborted
    snapshot regardless of status -- a run of 'carried' reads differently
    from a gap."""
    rows = conn.execute(
        "SELECT f.snapshot_id, s.taken_at, f.value_num, f.value_text "
        "FROM fields f JOIN snapshots s ON s.id = f.snapshot_id "
        "WHERE f.player_id = ? AND f.path = ? AND s.status != 'aborted' "
        "ORDER BY f.snapshot_id ASC",
        (player_id, path),
    ).fetchall()
    if limit:
        rows = rows[-limit:]
    return [
        (r["snapshot_id"], r["taken_at"], r["value_num"] if r["value_num"] is not None else r["value_text"])
        for r in rows
    ]


def _static_values(conn, player_id, snapshot_id):
    rows = conn.execute(
        "SELECT path, value_num, value_text FROM fields "
        "WHERE snapshot_id = ? AND player_id = ? AND kind = 'static'",
        (snapshot_id, player_id),
    ).fetchall()
    return {r["path"]: (r["value_num"] if r["value_num"] is not None else r["value_text"]) for r in rows}


def deltas(conn, player_id, snapshot_id):
    """{path: (prev, cur, delta)} for one snapshot's static fields against
    the previous non-operator snapshot's static fields."""
    current_values = _static_values(conn, player_id, snapshot_id)
    prev_snapshot = previous_snapshot(conn, player_id, snapshot_id, exclude_operator=True)
    prev_values = _static_values(conn, player_id, prev_snapshot["id"]) if prev_snapshot else {}

    result = {}
    for path, current_value in current_values.items():
        prev_value = prev_values.get(path)
        delta = (
            current_value - prev_value
            if isinstance(current_value, (int, float)) and isinstance(prev_value, (int, float))
            else None
        )
        result[path] = (prev_value, current_value, delta)
    return result


# -- run-directory pruning (pure filesystem, no DB) --------------------------

_STAMP_RE_NEW = re.compile(r"^\d{8}T\d{6}Z$")
_STAMP_RE_OLD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{4})$")


def _parse_run_stamp(name):
    if _STAMP_RE_NEW.match(name):
        return datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    match = _STAMP_RE_OLD.match(name)
    if match:
        year, month, day, hhmm = match.groups()
        return datetime.strptime(f"{year}-{month}-{day} {hhmm}", "%Y-%m-%d %H%M").replace(
            tzinfo=timezone.utc
        )
    return None


def prune_runs(root, keep_days=2, current=None):
    """Delete run dirs under `root` older than keep_days, by name
    ('YYYYMMDDTHHMMSSZ' or '2026-09-08-1251'); never touches `current` or a
    non-stamp name. Returns the removed names."""
    removed = []
    if not os.path.isdir(root):
        return removed
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    for name in sorted(os.listdir(root)):
        if name == current:
            continue
        full_path = os.path.join(root, name)
        if not os.path.isdir(full_path):
            continue
        stamp = _parse_run_stamp(name)
        if stamp is None or stamp >= cutoff:
            continue
        shutil.rmtree(full_path)
        removed.append(name)
    return removed
