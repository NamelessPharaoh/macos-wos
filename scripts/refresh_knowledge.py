#!/usr/bin/env python3
"""Refresh the game knowledge base from its online sources, diff first.

    SOURCES/OPTIONAL_SOURCES ─fetch_json─▶ raw ─NORMALISERS[table]─▶ doc
        │ FetchError: FETCH FAILED, next table
        │ KeyError/TypeError/ValueError/AttributeError: NORMALISE FAILED, next table
        ▼
    doc ─carry_marks─▶ diff_rows(old, new) ─printed─▶ (--write) knowledge/<table>.json (+ _meta)
        any REQUIRED table failed ─▶ exit 1 after every table is attempted

A game patch shows up as changed rows on the terminal, never as a silent
overwrite: without --write nothing is written. A failure on one table never
stops the others (A2): each table's fetch and normalise are guarded and
printed, and the run only exits non-zero when a REQUIRED table (buildings,
troops, troop_stats, research -- R3) failed. An OPTIONAL table (calendar,
and later the hero/gear/pet tables) fails with the same printed line but
never changes the exit code. `verified_in_game` marks survive a refresh
whenever the row's costs did not change (carry_marks, B4).

Committed tables come from the two wosnerds GitHub repos (community-published
JSON, "free to copy and use", no LICENSE file, so recorded as revocable).
--local runs the cross-check fetchers that write under knowledge/local/
(gitignored, spec D8).
"""
import argparse
import json
import os
import sys
from collections import namedtuple
from datetime import datetime, timezone

# Run by path ("uv run python scripts/refresh_knowledge.py"), sys.path[0] is
# scripts/, not the repo root, so `knowledge` is not importable without this
# (A1). tests/conftest.py already puts the repo root on sys.path, which is
# why an in-process import of this module can hide the bug -- the regression
# test below invokes the script as a subprocess to catch it for real.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# fetch_text isn't called directly here, but R2 keeps it imported so
# `rk.fetch_text` stays a valid monkeypatch.setattr(rk, ...) target.
from knowledge.fetch import FetchError, fetch_json, fetch_text  # noqa: E402,F401
from knowledge.util import write_table  # noqa: E402
from knowledge.normalise import NORMALISERS  # noqa: E402  (Task 2)

KNOWLEDGE_DIR = os.path.join(REPO, "knowledge")
LOCAL_DIR = os.path.join(KNOWLEDGE_DIR, "local")
NORMALISER_VERSION = 1
LICENCE = ("wosnerds.com: 'All data is free to copy and use'; no LICENSE file in the repo; "
           "treated as revocable, attribution kept in _meta")

Source = namedtuple("Source", "repo path url")


def _raw(repo, path):
    return Source(repo, path, f"https://raw.githubusercontent.com/{repo}/main/{path}")


# M1's required tables (R3): the planner needs exactly these four. calendar,
# and (from Task 2 on) the hero/gear/pet tables, live in OPTIONAL_SOURCES --
# the default refresh skips them and their failure never changes the exit
# code (D-T3, E7).
SOURCES = {
    "buildings": _raw("wosnerdwarriors/website-index", "calculator/data/construction.json"),
    "troops": _raw("wosnerdwarriors/website-index", "calculator/data/troops.json"),
    "troop_stats": _raw("wosnerdwarriors/wos-data", "data/troop-stats.json"),
    "research": _raw("wosnerdwarriors/wos-data", "data/research-upgrades.json"),
}
OPTIONAL_SOURCES = {
    "calendar": _raw("wosnerdwarriors/wos-data", "data/calendar-data.json"),
}
from knowledge.local_sources import LOCAL, crosscheck  # noqa: E402  (Task 4)


def source_commit(repo, opener=None):
    """Short SHA of main; 'unknown' when GitHub's API is unreachable or rate
    limited (60/h unauthenticated), so a table still refreshes with its date."""
    try:
        data = fetch_json(f"https://api.github.com/repos/{repo}/commits/main", opener=opener)
        return str(data["sha"])[:12]
    except (FetchError, KeyError, TypeError) as exc:
        print(f"  commit lookup failed for {repo}: {exc}; recording 'unknown'")
        return "unknown"


def meta(source, commit, fetched_at):
    return {"source_url": source.url, "source_commit": commit, "fetched_at": fetched_at,
            "licence": LICENCE, "normaliser_version": NORMALISER_VERSION}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_table(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _is_table(v):
    """A container of rows: a non-empty dict whose every value is a dict."""
    return isinstance(v, dict) and bool(v) and all(isinstance(x, dict) for x in v.values())


def diff_rows(old, new, _path=""):
    """Row-level diff (C1). Recurse while both sides are TABLES; at a row,
    compare fields, recursing into a field that is itself a table (e.g. a
    research node's `levels`). An added or removed table recurses against {}
    so every row gets its own line instead of one line for the whole table.
    `_meta` is ignored, but only at the top level."""
    lines, keys = [], sorted((set(old) | set(new)) - ({"_meta"} if not _path else set()), key=str)
    for key in keys:
        path = f"{_path}.{key}" if _path else str(key)
        a, b = old.get(key), new.get(key)
        if key not in old and _is_table(b):
            lines.extend(diff_rows({}, b, path))
            continue
        if key not in new and _is_table(a):
            lines.extend(diff_rows(a, {}, path))
            continue
        if key not in old:
            lines.append(f"{path}: added")
            continue
        if key not in new:
            lines.append(f"{path}: removed")
            continue
        if a == b:
            continue
        if _is_table(a) and _is_table(b):
            lines.extend(diff_rows(a, b, path))
        elif isinstance(a, dict) and isinstance(b, dict):
            for f in sorted(set(a) | set(b)):
                if a.get(f) == b.get(f):
                    continue
                if _is_table(a.get(f)) and _is_table(b.get(f)):
                    lines.extend(diff_rows(a[f], b[f], f"{path}.{f}"))
                else:
                    lines.append(f"{path}: {f} {a.get(f)} -> {b.get(f)}")
        else:
            lines.append(f"{path}: {a} -> {b}")
    return lines


_MARK_FIELDS = ("meat", "wood", "coal", "iron", "seconds", "cost")


def _carry_marks_walk(old_node, new_node):
    """Recurse through nested tables exactly like `diff_rows` (C1); at a
    leaf ROW, carry `verified_in_game` from the matching old row when the
    row's own cost fields are unchanged (B4). A row's own table-shaped field
    (a research node's `levels`) is walked too, so a per-level mark (C4)
    survives independently of its parent node's fields."""
    if not isinstance(new_node, dict):
        return
    old_node = old_node if isinstance(old_node, dict) else {}
    if _is_table(new_node):
        for key, child in new_node.items():
            _carry_marks_walk(old_node.get(key), child)
        return
    if old_node.get("verified_in_game"):
        same = all(new_node.get(f) == old_node.get(f) for f in _MARK_FIELDS)
        new_node["verified_in_game"] = old_node["verified_in_game"] if same else None
    for key, child in new_node.items():
        if _is_table(child):
            _carry_marks_walk(old_node.get(key), child)


def carry_marks(old, new):
    """`verified_in_game` is evidence from the screen, not upstream data; a
    refresh must not erase it (B4). Carried only when the row's own cost
    fields are unchanged from the previous fetch; recurses like `diff_rows`
    so a research node's per-level marks (C4) are carried independently."""
    for table, rows in new.items():
        if table == "_meta" or not isinstance(rows, dict):
            continue
        _carry_marks_walk(old.get(table), rows)
    return new


def refresh(table, write, opener=None):
    """Fetch, normalise, carry marks, diff and (optionally) write one table.
    Returns the diff lines, or None when the table failed -- printed, named,
    and the caller moves on to the next table (A2)."""
    source = SOURCES[table] if table in SOURCES else OPTIONAL_SOURCES[table]
    try:
        raw = fetch_json(source.url, opener=opener)
    except FetchError as exc:
        print(f"== {table}: FETCH FAILED {exc}")
        return None
    try:
        doc = NORMALISERS[table](raw)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        print(f"== {table}: NORMALISE FAILED {exc.__class__.__name__}: {exc} (upstream shape changed?)")
        return None
    doc = {"_meta": meta(source, source_commit(source.repo, opener=opener), utc_now()), **doc}
    path = os.path.join(KNOWLEDGE_DIR, f"{table}.json")
    old = load_table(path)
    doc = carry_marks(old, doc)
    lines = diff_rows(old, doc)
    print(f"== {table}: {len(lines)} changed row(s)" + ("" if write else " (dry run, pass --write to save)"))
    for line in lines[:200]:
        print("  " + line)
    if len(lines) > 200:
        print(f"  ... {len(lines) - 200} more")
    if write:
        write_table(path, doc)
        print(f"  wrote {path}")
    return lines


def refresh_local(name, write, opener=None):
    """Same guarded shape as `refresh`, for the gitignored cross-check
    fetchers (LOCAL, Task 4): a broken local page never stops the others."""
    try:
        rel, doc = LOCAL[name](opener)
    except FetchError as exc:
        print(f"== local {name}: FETCH FAILED {exc}")
        return None
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        print(f"== local {name}: NORMALISE FAILED {exc.__class__.__name__}: {exc}")
        return None
    path = os.path.join(LOCAL_DIR, rel)
    lines = diff_rows(load_table(path), doc)
    print(f"== local {name}: {len(lines)} changed row(s)" + ("" if write else " (dry run)"))
    for line in lines[:50]:
        print("  " + line)
    if write:
        write_table(path, doc)
        print(f"  wrote {path} (gitignored)")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", action="append",
                     help="one of " + ", ".join(list(SOURCES) + list(OPTIONAL_SOURCES)) + " (default: the required sources)")
    ap.add_argument("--write", action="store_true", help="save the refreshed tables")
    ap.add_argument("--local", action="store_true", help="also run the gitignored cross-check fetchers")
    ap.add_argument("--crosscheck", action="store_true",
                     help="report disagreements and FC rows from knowledge/local/*.json into "
                          "knowledge/local/overlay.json (needs --write to save; never touches knowledge/buildings.json)")
    a = ap.parse_args(argv)
    # A standalone --local or --crosscheck run must not also refetch (and, with
    # --write, re-timestamp) the required tables: `--crosscheck --write` writes
    # ONLY knowledge/local/overlay.json (A5). `--table` still opts back in.
    if a.table:
        tables = a.table
    elif a.local or a.crosscheck:
        tables = []
    else:
        tables = list(SOURCES)
    unknown = [t for t in tables if t not in SOURCES and t not in OPTIONAL_SOURCES]
    if unknown:
        sys.exit(f"unknown table(s): {', '.join(unknown)}")
    failed_required = 0
    for t in tables:
        lines = refresh(t, a.write)
        if lines is None and t in SOURCES:
            failed_required += 1
    if a.local:
        for name in LOCAL:
            refresh_local(name, a.write)
    if a.crosscheck:
        bpath = os.path.join(KNOWLEDGE_DIR, "buildings.json")
        committed = load_table(bpath)
        local_docs = {}
        for name, rel in (("whiteoutdata", "whiteoutdata-furnace.json"), ("wiki", "wiki-furnace.json"),
                           ("wostools", "wostools-buildings.json")):
            doc = load_table(os.path.join(LOCAL_DIR, rel))
            if doc:
                local_docs[name] = doc
        overlay, lines = crosscheck(committed.get("buildings", {}), local_docs)
        print(f"== crosscheck: {len(lines)} finding(s)" + ("" if a.write else " (dry run)"))
        for line in lines[:100]:
            print("  " + line)
        if a.write:
            opath = os.path.join(LOCAL_DIR, "overlay.json")
            write_table(opath, overlay)
            print(f"  wrote {opath} (gitignored)")
    if failed_required:
        sys.exit(f"{failed_required} required table(s) failed")


if __name__ == "__main__":
    main()
