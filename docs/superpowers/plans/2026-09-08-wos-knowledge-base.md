# WoS Knowledge Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor the game's building, troop, research and calendar tables from the wosnerds GitHub repos into `knowledge/*.json` with provenance, keep terms-restricted cross-checks local-only, and expose pure calculators in `native/kb.py` that the planner will call.

**Architecture:** `scripts/refresh_knowledge.py` fetches the source files (stdlib `urllib`), normalises them into one schema per table (slug keys, int costs, seconds), diffs against the committed files and writes only with `--write`. `native/kb.py` loads the JSON once and answers cost/time/prerequisite/path questions as pure functions. Cross-check parsers for whiteoutdata.com, whiteoutsurvival.wiki and wostools.net write under `knowledge/local/` (gitignored) and annotate committed rows with `disputed` when they disagree.

**Tech Stack:** Python 3.12, stdlib only (`urllib.request`, `json`, `re`, `html`, `difflib`), pytest via `uv run pytest tests/ -q` from the repo root (tests/conftest.py already chdirs there).

**Spec:** `docs/superpowers/specs/2026-09-08-wos-chief-of-staff-design.md`, section "Sub-project 1: knowledge base".

## Global Constraints

- No new dependencies; `pyproject.toml` stays unchanged.
- Files under 600 lines; docstrings explain WHY (repo style); no bare `except Exception`.
- Committed data comes only from `github.com/wosnerdwarriors/website-index` (`calculator/data/construction.json`, `calculator/data/troops.json`) and `github.com/wosnerdwarriors/wos-data` (`data/research-upgrades.json`, `data/troop-stats.json`, `data/calendar-data.json`), with `_meta.licence = "wosnerds.com: 'All data is free to copy and use'; no LICENSE file in the repo; treated as revocable"`.
- `knowledge/local/` is gitignored and never committed (spec D8). Fetchers for it make one HTTP request per page, no crawling, with a browser-like User-Agent.
- Slugs follow the repo: `furnace, embassy, command_center, research_center, infantry_camp, lancer_camp, marksman_camp, infirmary, coal_mine, iron_mine, sawmill, hunters_hut, shelter, barricade` (`native/schema.py` names). Research node ids are the wosnerds ids with `-` replaced by `_` (`tooling_up_i`).
- Furnace levels above 30 use the sheet's ordinal: `ordinal = level + 5*fc + sub`, so `30-1..30-4` are 31..34, `FC1` is 35, `FC1-4` is 39, `FC10` is 80 (`native/snapshot.derive_furnace`).
- Every knowledge file starts with a `_meta` object: `{"source_url", "source_commit", "fetched_at", "licence", "normaliser_version"}`.
- Nothing here touches the game or the database.

---

### Task 1: Fetch layer, provenance and the diff-before-write refresh script

**Files:**
- Create: `scripts/refresh_knowledge.py`
- Create: `knowledge/README.md`
- Create: `tests/test_refresh_knowledge.py`
- Modify: `.gitignore` (append `knowledge/local/`)

**Interfaces:**
- Produces: `refresh_knowledge.SOURCES: dict[str, Source]` where `Source = (repo, path, url)`; `fetch_json(url, opener=None) -> dict`; `source_commit(repo, opener=None) -> str`; `meta(source: Source, commit: str, fetched_at: str) -> dict`; `diff_rows(old: dict, new: dict) -> list[str]` (human lines, `_meta` ignored); `write_table(path, doc)`; `main(argv)` with `--write`, `--table NAME`, `--local`.
- Later tasks register normalisers in `NORMALISERS: dict[str, callable]` (Task 2) and local fetchers in `LOCAL: dict[str, callable]` (Task 4).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_refresh_knowledge.py
import json
import os

import pytest

import scripts.refresh_knowledge as rk


def test_sources_are_the_two_wosnerds_repos():
    repos = {s.repo for s in rk.SOURCES.values()}
    assert repos == {"wosnerdwarriors/website-index", "wosnerdwarriors/wos-data"}
    assert rk.SOURCES["buildings"].url.endswith("website-index/main/calculator/data/construction.json")
    assert rk.SOURCES["research"].url.endswith("wos-data/main/data/research-upgrades.json")


def test_fetch_json_uses_the_opener(tmp_path):
    class FakeResp:
        def __init__(self, body):
            self.body = body
        def read(self):
            return self.body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    calls = []
    def opener(url, headers=None):
        calls.append(url)
        return FakeResp(b'{"a": 1}')
    assert rk.fetch_json("https://x/y.json", opener=opener) == {"a": 1}
    assert calls == ["https://x/y.json"]


def test_meta_carries_provenance():
    m = rk.meta(rk.SOURCES["buildings"], "f1defd3d7d80", "2026-09-08T12:00:00Z")
    assert m["source_url"] == rk.SOURCES["buildings"].url
    assert m["source_commit"] == "f1defd3d7d80"
    assert m["fetched_at"] == "2026-09-08T12:00:00Z"
    assert "free to copy and use" in m["licence"] and "revocable" in m["licence"]
    assert m["normaliser_version"] == rk.NORMALISER_VERSION


def test_diff_rows_reports_changed_added_removed_and_ignores_meta():
    old = {"_meta": {"fetched_at": "a"}, "furnace": {"27": {"meat": 1}, "28": {"meat": 2}}, "gone": {"1": {"x": 1}}}
    new = {"_meta": {"fetched_at": "b"}, "furnace": {"27": {"meat": 1}, "28": {"meat": 3}, "29": {"meat": 4}}}
    lines = rk.diff_rows(old, new)
    assert "furnace.28: meat 2 -> 3" in lines
    assert "furnace.29: added" in lines
    assert "gone.1: removed" in lines
    assert not any("_meta" in l for l in lines)
    assert rk.diff_rows(new, new) == []


def test_write_table_is_atomic_and_pretty(tmp_path):
    p = tmp_path / "t.json"
    rk.write_table(str(p), {"_meta": {"x": 1}, "b": {"1": {"meat": 2}}})
    text = p.read_text()
    assert text.startswith('{\n  "_meta"') and text.endswith("}\n")
    assert not os.path.exists(str(p) + ".tmp")


def test_main_dry_run_prints_diff_and_does_not_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(rk, "NORMALISERS", {"demo": lambda raw: {"row": {"1": {"v": raw["v"]}}}})
    monkeypatch.setattr(rk, "SOURCES", {"demo": rk.Source("wosnerdwarriors/wos-data", "data/demo.json", "https://raw/demo.json")})
    monkeypatch.setattr(rk, "fetch_json", lambda url, opener=None: {"v": 7})
    monkeypatch.setattr(rk, "source_commit", lambda repo, opener=None: "abc123")
    rk.main(["--table", "demo"])
    out = capsys.readouterr().out
    assert "row.1: added" in out and "dry run" in out
    assert not (tmp_path / "demo.json").exists()
    rk.main(["--table", "demo", "--write"])
    doc = json.loads((tmp_path / "demo.json").read_text())
    assert doc["row"]["1"]["v"] == 7 and doc["_meta"]["source_commit"] == "abc123"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Developer/wos-bot && uv run pytest tests/test_refresh_knowledge.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.refresh_knowledge'` (add `scripts/__init__.py` if `scripts/` has none; check with `ls scripts/__init__.py`).

- [ ] **Step 3: Write the script**

```python
#!/usr/bin/env python3
"""Refresh the game knowledge base from its online sources, diff first.

    SOURCES ─fetch_json─▶ raw ─NORMALISERS[table]─▶ doc ─diff_rows─▶ printed
                                                        │ --write
                                                        ▼ knowledge/<table>.json (+ _meta)

A game patch shows up as changed rows on the terminal, never as a silent
overwrite: without --write nothing is written. Committed tables come from the
two wosnerds GitHub repos (community-published JSON, "free to copy and use",
no LICENSE file, so recorded as revocable). --local runs the cross-check
fetchers that write under knowledge/local/ (gitignored, spec D8).
"""
import argparse
import json
import os
import sys
import urllib.request
from collections import namedtuple
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_DIR = os.path.join(REPO, "knowledge")
LOCAL_DIR = os.path.join(KNOWLEDGE_DIR, "local")
NORMALISER_VERSION = 1
LICENCE = ("wosnerds.com: 'All data is free to copy and use'; no LICENSE file in the repo; "
           "treated as revocable, attribution kept in _meta")
USER_AGENT = "Mozilla/5.0 (Macintosh) wos-bot knowledge refresh (one request per table)"

Source = namedtuple("Source", "repo path url")


def _raw(repo, path):
    return Source(repo, path, f"https://raw.githubusercontent.com/{repo}/main/{path}")


SOURCES = {
    "buildings": _raw("wosnerdwarriors/website-index", "calculator/data/construction.json"),
    "troops": _raw("wosnerdwarriors/website-index", "calculator/data/troops.json"),
    "troop_stats": _raw("wosnerdwarriors/wos-data", "data/troop-stats.json"),
    "research": _raw("wosnerdwarriors/wos-data", "data/research-upgrades.json"),
    "calendar": _raw("wosnerdwarriors/wos-data", "data/calendar-data.json"),
}
NORMALISERS = {}   # table -> callable(raw) -> doc without _meta; filled by knowledge.normalise (Task 2)
LOCAL = {}         # name -> callable(opener) -> (relative path, doc); filled by knowledge.local (Task 4)


def _open(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=60)


def fetch_json(url, opener=None):
    with (opener or _open)(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url, opener=None):
    with (opener or _open)(url) as resp:
        return resp.read().decode("utf-8", errors="replace")


def source_commit(repo, opener=None):
    """Short SHA of the repo's main branch, so a table can be traced to the
    exact upstream revision it was normalised from."""
    data = fetch_json(f"https://api.github.com/repos/{repo}/commits/main", opener=opener)
    return str(data["sha"])[:12]


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


def diff_rows(old, new):
    """Human lines for every row that changed, was added or removed.
    Rows are the second level (table -> key -> row); _meta is ignored."""
    lines = []
    old_t = {k: v for k, v in old.items() if k != "_meta"}
    new_t = {k: v for k, v in new.items() if k != "_meta"}
    for table in sorted(set(old_t) | set(new_t)):
        o, n = old_t.get(table, {}), new_t.get(table, {})
        if not isinstance(o, dict):
            o = {"_": o}
        if not isinstance(n, dict):
            n = {"_": n}
        for key in sorted(set(o) | set(n), key=str):
            if key not in o:
                lines.append(f"{table}.{key}: added")
            elif key not in n:
                lines.append(f"{table}.{key}: removed")
            elif o[key] != n[key]:
                a, b = o[key], n[key]
                if isinstance(a, dict) and isinstance(b, dict):
                    for f in sorted(set(a) | set(b)):
                        if a.get(f) != b.get(f):
                            lines.append(f"{table}.{key}: {f} {a.get(f)} -> {b.get(f)}")
                else:
                    lines.append(f"{table}.{key}: {a} -> {b}")
    return lines


def write_table(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def refresh(table, write, opener=None):
    source = SOURCES[table]
    raw = fetch_json(source.url, opener=opener)
    doc = NORMALISERS[table](raw)
    doc = {"_meta": meta(source, source_commit(source.repo, opener=opener), utc_now()), **doc}
    path = os.path.join(KNOWLEDGE_DIR, f"{table}.json")
    lines = diff_rows(load_table(path), doc)
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
    rel, doc = LOCAL[name](opener)
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
    ap.add_argument("--table", action="append", help="one of " + ", ".join(SOURCES) + " (default: all)")
    ap.add_argument("--write", action="store_true", help="save the refreshed tables")
    ap.add_argument("--local", action="store_true", help="also run the gitignored cross-check fetchers")
    a = ap.parse_args(argv)
    tables = a.table or [t for t in SOURCES if t in NORMALISERS]
    missing = [t for t in tables if t not in NORMALISERS]
    if missing:
        sys.exit(f"no normaliser registered for: {', '.join(missing)}")
    for t in tables:
        refresh(t, a.write)
    if a.local:
        for name in LOCAL:
            refresh_local(name, a.write)


if __name__ == "__main__":
    main()
```

Then append to `.gitignore`:

```
knowledge/local/
```

And write `knowledge/README.md`:

```markdown
# Game knowledge base

Tables the planner computes with. Nothing here is read from the game; the
readers in `native/` verify rows against the screen (`verified_in_game`).

| file | source | refresh |
|---|---|---|
| buildings.json | wosnerdwarriors/website-index `calculator/data/construction.json` | `uv run python scripts/refresh_knowledge.py --table buildings` |
| troops.json | website-index `calculator/data/troops.json` + wos-data `data/troop-stats.json` | `--table troops --table troop_stats` |
| research.json | wos-data `data/research-upgrades.json` | `--table research` |
| calendar.json | wos-data `data/calendar-data.json` | `--table calendar` |

Licence: wosnerds.com states "All data is free to copy and use"; the repos
carry no LICENSE file, so every `_meta` records the source URL, commit and
fetch date and the data is treated as revocable.

`knowledge/local/` (gitignored, never committed): cross-check tables fetched
from whiteoutdata.com, whiteoutsurvival.wiki and wostools.net with
`--local`. Their terms restrict reproduction, so they stay on this machine
and only annotate committed rows as `disputed` when they disagree.

Refresh prints a diff and writes nothing without `--write`.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_refresh_knowledge.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/refresh_knowledge.py scripts/__init__.py knowledge/README.md tests/test_refresh_knowledge.py .gitignore
git commit -m "feat: knowledge refresh script with provenance and diff-before-write

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Normalisers for buildings, troops, research and calendar

**Files:**
- Create: `knowledge/normalise.py`
- Create: `tests/fixtures/knowledge/construction_excerpt.json`, `troops_excerpt.json`, `troop_stats_excerpt.json`, `research_excerpt.json`, `calendar_excerpt.json`
- Create: `tests/test_knowledge_normalise.py`
- Modify: `scripts/refresh_knowledge.py` (register `NORMALISERS`)

**Interfaces:**
- Consumes: raw dicts as fetched by Task 1.
- Produces: `normalise.buildings(raw) -> {"buildings": {slug: {"<level>": Row}}}` with `Row = {"meat","wood","coal","iron","fire_crystals","refined_fire_crystals","seconds","prerequisites": {slug: level}, "verified_in_game": None}`; `normalise.troops(raw) -> {"training": {type: {"<tier>": {"points","seconds","meat","wood","coal","iron"}}}}`; `normalise.troop_stats(raw) -> {"stats": {type: {"<tier>-fc<n>": {"name","power","attack","defense","lethality","health","load","speed"}}}}`; `normalise.research(raw) -> {"research": {node_id: {"tree","name","stat","troop_type","row","levels": {"<n>": {"stat_addition","power","cost": {...},"seconds","requires_research": {node_id: level},"requires_buildings": {slug: level}}}}}}`; `normalise.calendar(raw) -> {"events": {id: {"category","name","repeat_every_days","length_hours","anchor","available_after_age"}}}`; `normalise.slug(name) -> str`.

- [ ] **Step 1: Write the fixtures** (real rows copied from the sources on 2026-09-08)

`tests/fixtures/knowledge/construction_excerpt.json`:
```json
{"version": "3.1c-rev2", "lastUpdatedDate": "2026-05-01", "settings": {}, "buildingLevels": {
  "Furnace": [
    {"level": 0, "meat": 0, "wood": 0, "coal": 0, "iron": 0, "fireCrystals": 0, "refinedFireCrystals": 0, "seconds": 0, "prerequisites": {}},
    {"level": 27, "meat": 140000000, "wood": 140000000, "coal": 24000000, "iron": 7400000, "fireCrystals": 0, "refinedFireCrystals": 0, "seconds": 2187780,
     "prerequisites": {"Command Center": 1, "Coal Mine": 3, "Embassy": 26, "Hunter's Hut": 6, "Infantry Camp": 24, "Infirmary": 1, "Iron Mine": 5, "Lancer Camp": 26, "Marksman Camp": 25, "Research Center": 23, "Sawmill": 1, "Shelter": 3}},
    {"level": 30, "meat": 300000000, "wood": 300000000, "coal": 60000000, "iron": 15000000, "fireCrystals": 0, "refinedFireCrystals": 0, "seconds": 3472020,
     "prerequisites": {"Command Center": 1, "Coal Mine": 3, "Embassy": 29, "Hunter's Hut": 6, "Infantry Camp": 28, "Infirmary": 1, "Iron Mine": 5, "Lancer Camp": 26, "Marksman Camp": 29, "Research Center": 27, "Sawmill": 1, "Shelter": 3}}],
  "Hunter's Hut": [{"level": 0, "meat": 0, "wood": 0, "coal": 0, "iron": 0, "fireCrystals": 0, "refinedFireCrystals": 0, "seconds": 0, "prerequisites": {}}]
}}
```

`tests/fixtures/knowledge/troops_excerpt.json`:
```json
{"variables": [], "settings": {"trainingCapacity": 1581, "trainingSpeed": 1.8, "highestTier": 11, "baseTier": 10}, "troopCosts": {
  "infantry": [{"tier": 1, "points": 3, "baseSeconds": 12, "adjustedSeconds": 4.28, "meat": 36, "wood": 27, "coal": 7, "iron": 2},
               {"tier": 9, "points": 45, "baseSeconds": 131, "adjustedSeconds": 46.78, "meat": 1394, "wood": 1046, "coal": 244, "iron": 51}],
  "lancer": [{"tier": 9, "points": 45, "baseSeconds": 131, "adjustedSeconds": 46.78, "meat": 1394, "wood": 1046, "coal": 244, "iron": 51}],
  "marksman": [{"tier": 9, "points": 45, "baseSeconds": 131, "adjustedSeconds": 46.78, "meat": 1394, "wood": 1046, "coal": 244, "iron": 51}]},
 "upgradePlans": []}
```

`tests/fixtures/knowledge/troop_stats_excerpt.json`:
```json
{"troop-stats": {"infantry": [
  {"Troop Type": "infantry", "Troop Level": 1, "troop level name": "Rookie", "FC level": 0, "power": 3, "defense": 4, "lethality": 1, "load": 108, "attack": 1, "health": 6, "speed": 11},
  {"Troop Type": "infantry", "Troop Level": 9, "troop level name": "Supreme", "FC level": 0, "power": 50, "defense": 12, "lethality": 9, "load": 330, "attack": 9, "health": 14, "speed": 11}],
 "lancer": [], "marksman": []}}
```

`tests/fixtures/knowledge/research_excerpt.json`:
```json
{"Growth": {"tooling-up-i": {"row": 1, "name": "Tooling Up I", "stat": "construction-speed", "troop-type": null, "levels": {
  "1": {"stat-addition": 0.4, "power": 2000, "cost": {"meat": 2700, "wood": 2700, "coal": 540, "iron": 130, "steel": 160}, "research-time-seconds": 2, "requirements": {"research-items": {}, "buildings": {"research-center": 1}}},
  "2": {"stat-addition": 0.4, "power": 2000, "cost": {"meat": 3700, "wood": 3700, "coal": 750, "iron": 180, "steel": 220}, "research-time-seconds": 40, "requirements": {"research-items": {"tooling-up-i": 1}, "buildings": {"research-center": 2}}}}}},
 "Economy": {}, "Battle": {}}
```

`tests/fixtures/knowledge/calendar_excerpt.json`:
```json
{"events": {"svs-castle": {"category": "svs", "entry-name": "SVS Castle Battle", "repeat-every-days": 28, "same-time-in-every-state": true, "event-length-hours": 6, "global-start-time-anchor": "2024-10-12T12:00:00Z", "available-after-age": "unknown"}},
 "events-template": {}, "packs": {}}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_knowledge_normalise.py
import json
import os

from knowledge import normalise as n

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "knowledge")


def _load(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def test_slug():
    assert n.slug("Hunter's Hut") == "hunters_hut"
    assert n.slug("Research Center") == "research_center"
    assert n.slug("research-center") == "research_center"
    assert n.slug("tooling-up-i") == "tooling_up_i"


def test_buildings_furnace_rows_and_prerequisites():
    doc = n.buildings(_load("construction_excerpt.json"))
    f = doc["buildings"]["furnace"]
    assert set(f) == {"0", "27", "30"}
    r27 = f["27"]
    assert (r27["meat"], r27["wood"], r27["coal"], r27["iron"]) == (140_000_000, 140_000_000, 24_000_000, 7_400_000)
    assert r27["seconds"] == 2_187_780 and r27["fire_crystals"] == 0 and r27["refined_fire_crystals"] == 0
    assert r27["prerequisites"]["embassy"] == 26 and r27["prerequisites"]["hunters_hut"] == 6
    assert r27["verified_in_game"] is None
    assert "hunters_hut" in doc["buildings"]


def test_troops_training_rows():
    doc = n.troops(_load("troops_excerpt.json"))
    t9 = doc["training"]["infantry"]["9"]
    assert t9 == {"points": 45, "seconds": 131, "meat": 1394, "wood": 1046, "coal": 244, "iron": 51}
    assert set(doc["training"]) == {"infantry", "lancer", "marksman"}


def test_troop_stats_rows():
    doc = n.troop_stats(_load("troop_stats_excerpt.json"))
    s = doc["stats"]["infantry"]["9-fc0"]
    assert s["name"] == "Supreme" and s["power"] == 50 and s["lethality"] == 9 and s["load"] == 330


def test_research_nodes_levels_and_requirements():
    doc = n.research(_load("research_excerpt.json"))
    node = doc["research"]["tooling_up_i"]
    assert node["tree"] == "growth" and node["name"] == "Tooling Up I" and node["stat"] == "construction_speed"
    lv2 = node["levels"]["2"]
    assert lv2["cost"] == {"meat": 3700, "wood": 3700, "coal": 750, "iron": 180, "steel": 220}
    assert lv2["seconds"] == 40 and lv2["power"] == 2000
    assert lv2["requires_research"] == {"tooling_up_i": 1}
    assert lv2["requires_buildings"] == {"research_center": 2}


def test_calendar_events():
    doc = n.calendar(_load("calendar_excerpt.json"))
    e = doc["events"]["svs_castle"]
    assert e["name"] == "SVS Castle Battle" and e["repeat_every_days"] == 28 and e["length_hours"] == 6
    assert e["anchor"] == "2024-10-12T12:00:00Z" and e["available_after_age"] is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_knowledge_normalise.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge'`

- [ ] **Step 4: Write the normalisers**

`knowledge/__init__.py`: one line, `"""Game knowledge base: vendored tables and their normalisers."""`

`knowledge/normalise.py`:
```python
"""Turn the wosnerds source files into the knowledge-base schema.

Source keys differ per file ("Hunter's Hut", "research-center", "Troop Level");
every normaliser maps them onto the repo's slugs so the planner and the
readers speak one vocabulary. Values are ints (costs), seconds (times) and
None for unknowns; nothing is invented.
"""
import re

TROOP_TYPES = ("infantry", "lancer", "marksman")


def slug(name):
    """'Hunter's Hut' -> 'hunters_hut' (apostrophes dropped, not turned into
    separators), 'research-center' -> 'research_center'."""
    return re.sub(r"[^a-z0-9]+", "_", re.sub(r"['’]", "", str(name).lower())).strip("_")


def _int(v):
    if v in (None, "", "-", "–"):
        return 0
    return int(round(float(v)))


def buildings(raw):
    out = {}
    for name, levels in raw["buildingLevels"].items():
        rows = {}
        for lv in levels:
            rows[str(lv["level"])] = {
                "meat": _int(lv.get("meat")), "wood": _int(lv.get("wood")),
                "coal": _int(lv.get("coal")), "iron": _int(lv.get("iron")),
                "fire_crystals": _int(lv.get("fireCrystals")),
                "refined_fire_crystals": _int(lv.get("refinedFireCrystals")),
                "seconds": _int(lv.get("seconds")),
                "prerequisites": {slug(k): _int(v) for k, v in (lv.get("prerequisites") or {}).items()},
                "verified_in_game": None,
            }
        out[slug(name)] = rows
    return {"buildings": out}


def troops(raw):
    out = {}
    for ttype in TROOP_TYPES:
        rows = {}
        for r in raw["troopCosts"].get(ttype, []):
            rows[str(r["tier"])] = {"points": _int(r.get("points")), "seconds": _int(r.get("baseSeconds")),
                                    "meat": _int(r.get("meat")), "wood": _int(r.get("wood")),
                                    "coal": _int(r.get("coal")), "iron": _int(r.get("iron"))}
        out[ttype] = rows
    return {"training": out}


def troop_stats(raw):
    out = {}
    for ttype in TROOP_TYPES:
        rows = {}
        for r in raw["troop-stats"].get(ttype, []):
            key = f"{_int(r['Troop Level'])}-fc{_int(r.get('FC level'))}"
            rows[key] = {"name": r.get("troop level name"), "power": _int(r.get("power")),
                         "attack": _int(r.get("attack")), "defense": _int(r.get("defense")),
                         "lethality": _int(r.get("lethality")), "health": _int(r.get("health")),
                         "load": _int(r.get("load")), "speed": _int(r.get("speed"))}
        out[ttype] = rows
    return {"stats": out}


def research(raw):
    out = {}
    for tree, nodes in raw.items():
        for node_id, node in nodes.items():
            levels = {}
            for n, lv in (node.get("levels") or {}).items():
                req = lv.get("requirements") or {}
                levels[str(n)] = {
                    "stat_addition": lv.get("stat-addition"),
                    "power": _int(lv.get("power")),
                    "cost": {k: _int(v) for k, v in (lv.get("cost") or {}).items()},
                    "seconds": _int(lv.get("research-time-seconds")),
                    "requires_research": {slug(k): _int(v) for k, v in (req.get("research-items") or {}).items()},
                    "requires_buildings": {slug(k): _int(v) for k, v in (req.get("buildings") or {}).items()},
                }
            out[slug(node_id)] = {"tree": slug(tree), "name": node.get("name"), "stat": slug(node.get("stat") or ""),
                                  "troop_type": node.get("troop-type"), "row": node.get("row"), "levels": levels,
                                  "verified_in_game": None}
    return {"research": out}


def calendar(raw):
    out = {}
    for eid, e in (raw.get("events") or {}).items():
        age = e.get("available-after-age")
        out[slug(eid)] = {"category": e.get("category"), "name": e.get("entry-name"),
                          "repeat_every_days": _int(e.get("repeat-every-days")),
                          "length_hours": _int(e.get("event-length-hours")),
                          "anchor": e.get("global-start-time-anchor"),
                          "available_after_age": None if age in (None, "unknown") else _int(age)}
    return {"events": out}


NORMALISERS = {"buildings": buildings, "troops": troops, "troop_stats": troop_stats,
               "research": research, "calendar": calendar}
```

In `scripts/refresh_knowledge.py` replace `NORMALISERS = {}` with:
```python
from knowledge.normalise import NORMALISERS  # noqa: E402
```
(placed after the `REPO` constant, with `sys.path.insert(0, REPO)` just above it so the script also runs from another cwd).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_knowledge_normalise.py tests/test_refresh_knowledge.py -q`
Expected: `12 passed`

- [ ] **Step 6: Fetch and commit the real tables**

Run: `uv run python scripts/refresh_knowledge.py` (dry run; expect every row `added`), then `uv run python scripts/refresh_knowledge.py --write`.
Verify: `python3 -c "import json; d=json.load(open('knowledge/buildings.json')); print(d['_meta']['source_commit'], d['buildings']['furnace']['28'])"` prints a 12-char commit and `meat 190000000 ... seconds` for level 28.
Then:
```bash
git add knowledge/ scripts/refresh_knowledge.py tests/fixtures/knowledge tests/test_knowledge_normalise.py
git commit -m "feat: vendor building, troop, research and calendar tables from wosnerds

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Calculators in `native/kb.py`

**Files:**
- Create: `native/kb.py`
- Create: `tests/test_native_kb.py`

**Interfaces:**
- Consumes: `knowledge/*.json` from Task 2 (loaded lazily, cached per process; `kb.load(dir=None)` for tests).
- Produces:
  - `Cost` = `dict[str, int]` with keys `meat, wood, coal, iron, fire_crystals, refined_fire_crystals, steel` (missing keys mean 0); `add_cost(a, b) -> Cost`.
  - `building_cost(name, from_level, to_level, kb=None) -> Cost`
  - `building_time(name, from_level, to_level, speed_bonus=0.0, kb=None) -> int` seconds (`base / (1 + speed_bonus)`, rounded)
  - `building_row(name, level, kb=None) -> Row | None`
  - `prerequisites(name, level, sheet, kb=None) -> list[tuple[str, int, int]]` unmet `(building, needed, have)` where `sheet` maps building slug -> current level (None counts as 0, and is reported)
  - `research_node(node_id, kb=None) -> dict | None`
  - `research_path(node_id, level, sheet_research, kb=None) -> list[ResearchStep]` where `sheet_research` maps node_id -> current level; `ResearchStep = namedtuple("ResearchStep", "node level cost seconds")`, prerequisites expanded depth-first in dependency order, each (node, level) once.
  - `training_cost(troop_type, tier, count, kb=None) -> Cost`; `training_time(troop_type, tier, count, speed_bonus=0.0, kb=None) -> int`
  - `troop_power(troop_type, tier, fc=0, kb=None) -> int`
  - `power_gain(kind, **kw) -> int`: `kind="building"` (name, from_level, to_level: sum of whiteoutdata `power` if present in the row else 0), `kind="research"` (node, level), `kind="training"` (troop_type, tier, count)
  - `days_to(cost, income_per_day, stock=None) -> float | None` (max over resources of `(cost - stock) / income`; None when any needed income is 0 or missing; 0 when already covered)
  - `verify(kb_cost, screen_cost, tolerance=0.02) -> tuple[bool, str]` comparing the resources present in `screen_cost`
  - `furnace_ordinal(label) -> int` (`"27" -> 27`, `"30-3" -> 33`, `"FC1" -> 35`, `"FC 10" -> 80`, `"FC9-4" -> 79`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_native_kb.py
import json
import os

import pytest

from native import kb

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "knowledge", "kbdir")


@pytest.fixture(scope="module")
def k():
    return kb.load(FIX)


def test_load_reads_every_table(k):
    assert set(k) >= {"buildings", "training", "stats", "research", "events"}


def test_building_cost_and_time(k):
    c = kb.building_cost("furnace", 27, 28, kb=k)
    assert c["meat"] == 190_000_000 and c["iron"] == 9_900_000
    assert c.get("fire_crystals", 0) == 0
    assert kb.building_cost("furnace", 27, 27, kb=k) == {}
    assert kb.building_time("furnace", 27, 28, kb=k) == 2_515_920
    assert kb.building_time("furnace", 27, 28, speed_bonus=1.0, kb=k) == 1_257_960
    with pytest.raises(KeyError):
        kb.building_cost("furnace", 27, 99, kb=k)


def test_prerequisites_unmet_only(k):
    sheet = {"embassy": 26, "research_center": 27, "infantry_camp": 24, "lancer_camp": 26, "marksman_camp": 25,
             "command_center": 1, "coal_mine": 3, "hunters_hut": 6, "infirmary": 1, "iron_mine": 5, "sawmill": 1, "shelter": 3}
    unmet = kb.prerequisites("furnace", 28, sheet, kb=k)
    assert ("embassy", 27, 26) in unmet
    assert not any(b == "research_center" for b, _, _ in unmet)
    assert ("infirmary", 1, 0) in kb.prerequisites("furnace", 28, {**sheet, "infirmary": None}, kb=k)


def test_research_path_expands_prerequisites_once(k):
    steps = kb.research_path("tooling_up_i", 2, {}, kb=k)
    assert [(s.node, s.level) for s in steps] == [("tooling_up_i", 1), ("tooling_up_i", 2)]
    assert steps[1].cost["steel"] == 220 and steps[1].seconds == 40
    assert kb.research_path("tooling_up_i", 2, {"tooling_up_i": 2}, kb=k) == []


def test_training_cost_time_and_power(k):
    c = kb.training_cost("infantry", 9, 100, kb=k)
    assert c == {"meat": 139_400, "wood": 104_600, "coal": 24_400, "iron": 5_100}
    assert kb.training_time("infantry", 9, 100, kb=k) == 13_100
    assert kb.troop_power("infantry", 9, kb=k) == 50
    assert kb.power_gain("training", troop_type="infantry", tier=9, count=100, kb=k) == 5_000
    assert kb.power_gain("research", node="tooling_up_i", level=2, kb=k) == 2_000


def test_days_to_and_verify():
    assert kb.days_to({"meat": 100, "wood": 50}, {"meat": 10, "wood": 100}) == 10.0
    assert kb.days_to({"meat": 100}, {"meat": 10}, stock={"meat": 100}) == 0.0
    assert kb.days_to({"meat": 100}, {"meat": 0}) is None
    ok, why = kb.verify({"meat": 190_000_000, "wood": 190_000_000}, {"meat": 190_000_000})
    assert ok and why == "ok"
    ok, why = kb.verify({"meat": 190_000_000}, {"meat": 100_000_000})
    assert not ok and "meat" in why


def test_furnace_ordinal():
    assert [kb.furnace_ordinal(x) for x in ("27", "30-3", "FC1", "FC 10", "FC9-4")] == [27, 33, 35, 80, 79]
```

And the fixture directory `tests/fixtures/knowledge/kbdir/` with five files built from the excerpts of Task 2 plus furnace level 28 (`meat 190000000, wood 190000000, coal 39000000, iron 9900000, seconds 2515920, prerequisites {"embassy": 27, "research_center": 27, ...same others as 27}`): generate them once with
```bash
uv run python - <<'EOF'
import json, os
from knowledge import normalise as n
FIX = "tests/fixtures/knowledge"; OUT = os.path.join(FIX, "kbdir"); os.makedirs(OUT, exist_ok=True)
raw = json.load(open(f"{FIX}/construction_excerpt.json"))
r27 = next(l for l in raw["buildingLevels"]["Furnace"] if l["level"] == 27)
raw["buildingLevels"]["Furnace"].append({**r27, "level": 28, "meat": 190000000, "wood": 190000000, "coal": 39000000, "iron": 9900000, "seconds": 2515920,
    "prerequisites": {**r27["prerequisites"], "Embassy": 27, "Research Center": 27}})
meta = {"_meta": {"source_url": "fixture", "source_commit": "fixture", "fetched_at": "2026-09-08T00:00:00Z", "licence": "fixture", "normaliser_version": 1}}
for name, fn, src in (("buildings", n.buildings, raw), ("troops", n.troops, json.load(open(f"{FIX}/troops_excerpt.json"))),
                      ("troop_stats", n.troop_stats, json.load(open(f"{FIX}/troop_stats_excerpt.json"))),
                      ("research", n.research, json.load(open(f"{FIX}/research_excerpt.json"))),
                      ("calendar", n.calendar, json.load(open(f"{FIX}/calendar_excerpt.json")))):
    json.dump({**meta, **fn(src)}, open(f"{OUT}/{name}.json", "w"), indent=1)
print(os.listdir(OUT))
EOF
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_native_kb.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'native.kb'`

- [ ] **Step 3: Write `native/kb.py`**

```python
"""Pure calculators over the vendored knowledge tables.

    knowledge/*.json ─load─▶ kb dict ─▶ building_cost / research_path / training_cost / days_to / verify

No game access, no database: the planner passes in what the sheet knows
(current levels, income per day) and gets costs, times, unmet prerequisites
and expected power back. `verify` is what the executor runs against the cost
it reads off the screen before it presses anything.
"""
import json
import math
import os
import re
from collections import namedtuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_DIR = os.path.join(REPO, "knowledge")
TABLES = {"buildings": "buildings.json", "training": "troops.json", "stats": "troop_stats.json",
          "research": "research.json", "events": "calendar.json"}
RESOURCES = ("meat", "wood", "coal", "iron", "fire_crystals", "refined_fire_crystals", "steel")
ResearchStep = namedtuple("ResearchStep", "node level cost seconds")
_CACHE = {}


def load(directory=None):
    """The five tables keyed by their top-level name; cached per directory."""
    directory = directory or KNOWLEDGE_DIR
    if directory in _CACHE:
        return _CACHE[directory]
    kb = {}
    for key, fname in TABLES.items():
        path = os.path.join(directory, fname)
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} missing: run scripts/refresh_knowledge.py --write")
        with open(path) as f:
            doc = json.load(f)
        kb[key] = doc[key]
        kb[f"_meta_{key}"] = doc["_meta"]
    _CACHE[directory] = kb
    return kb


def _kb(kb):
    return kb if kb is not None else load()


def add_cost(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out


def _row_cost(row):
    return {k: row[k] for k in RESOURCES if row.get(k)}


def building_row(name, level, kb=None):
    return _kb(kb)["buildings"].get(name, {}).get(str(level))


def building_cost(name, from_level, to_level, kb=None):
    """Sum of the rows from_level+1 .. to_level (each row is the cost OF that level)."""
    total = {}
    for lv in range(from_level + 1, to_level + 1):
        row = building_row(name, lv, kb)
        if row is None:
            raise KeyError(f"{name} level {lv} not in the knowledge base")
        total = add_cost(total, _row_cost(row))
    return total


def building_time(name, from_level, to_level, speed_bonus=0.0, kb=None):
    secs = 0
    for lv in range(from_level + 1, to_level + 1):
        row = building_row(name, lv, kb)
        if row is None:
            raise KeyError(f"{name} level {lv} not in the knowledge base")
        secs += row["seconds"]
    return int(round(secs / (1.0 + speed_bonus)))


def prerequisites(name, level, sheet, kb=None):
    """Unmet (building, needed, have) for reaching `level`; an unknown sheet
    value counts as 0 so it is reported rather than assumed satisfied."""
    row = building_row(name, level, kb)
    if row is None:
        raise KeyError(f"{name} level {level} not in the knowledge base")
    unmet = []
    for b, needed in sorted(row["prerequisites"].items()):
        have = sheet.get(b)
        have = 0 if have is None else int(have)
        if have < needed:
            unmet.append((b, needed, have))
    return unmet


def research_node(node_id, kb=None):
    return _kb(kb)["research"].get(node_id)


def research_path(node_id, level, sheet_research, kb=None):
    """Every (node, level) still to research to reach node_id@level, in an
    order where prerequisites come first; each pair appears once."""
    kb = _kb(kb)
    done = set()
    steps = []

    def visit(nid, lv):
        have = int(sheet_research.get(nid) or 0)
        for l in range(have + 1, lv + 1):
            if (nid, l) in done:
                continue
            node = kb["research"].get(nid)
            if node is None:
                raise KeyError(f"research node {nid} unknown")
            row = node["levels"].get(str(l))
            if row is None:
                raise KeyError(f"{nid} level {l} unknown")
            for req, req_lv in row["requires_research"].items():
                visit(req, req_lv)
            done.add((nid, l))
            steps.append(ResearchStep(nid, l, dict(row["cost"]), row["seconds"]))

    visit(node_id, level)
    return steps


def training_cost(troop_type, tier, count, kb=None):
    row = _kb(kb)["training"][troop_type][str(tier)]
    return {k: row[k] * count for k in ("meat", "wood", "coal", "iron") if row.get(k)}


def training_time(troop_type, tier, count, speed_bonus=0.0, kb=None):
    row = _kb(kb)["training"][troop_type][str(tier)]
    return int(round(row["seconds"] * count / (1.0 + speed_bonus)))


def troop_power(troop_type, tier, fc=0, kb=None):
    return _kb(kb)["stats"][troop_type][f"{tier}-fc{fc}"]["power"]


def power_gain(kind, kb=None, **kw):
    kb = _kb(kb)
    if kind == "building":
        total = 0
        for lv in range(kw["from_level"] + 1, kw["to_level"] + 1):
            total += int((building_row(kw["name"], lv, kb) or {}).get("power") or 0)
        return total
    if kind == "research":
        return int(kb["research"][kw["node"]]["levels"][str(kw["level"])]["power"])
    if kind == "training":
        return troop_power(kw["troop_type"], kw["tier"], kw.get("fc", 0), kb) * kw["count"]
    raise ValueError(f"unknown power_gain kind {kind!r}")


def days_to(cost, income_per_day, stock=None):
    """Days until every resource in `cost` is covered, at the sheet's per-day
    income; None when some needed resource has no income."""
    stock = stock or {}
    worst = 0.0
    for res, need in cost.items():
        missing = need - int(stock.get(res, 0))
        if missing <= 0:
            continue
        inc = income_per_day.get(res) or 0
        if inc <= 0:
            return None
        worst = max(worst, missing / inc)
    return worst


def verify(kb_cost, screen_cost, tolerance=0.02):
    """Do the resources read off the screen match the table within tolerance?"""
    for res, seen in screen_cost.items():
        expect = kb_cost.get(res, 0)
        if expect == 0 and seen == 0:
            continue
        if expect == 0 or abs(seen - expect) / expect > tolerance:
            return False, f"{res}: table {expect:,} vs screen {seen:,}"
    return True, "ok"


def furnace_ordinal(label):
    """'27' -> 27, '30-3' -> 33, 'FC1' -> 35, 'FC 10' -> 80, 'FC9-4' -> 79."""
    t = str(label).strip().upper().replace(" ", "")
    m = re.fullmatch(r"FC(\d+)(?:-(\d))?", t)
    if m:
        return 30 + 5 * int(m.group(1)) + int(m.group(2) or 0)
    m = re.fullmatch(r"(\d+)(?:-(\d))?", t)
    if m:
        return int(m.group(1)) + int(m.group(2) or 0)
    raise ValueError(f"not a furnace level: {label!r}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_native_kb.py -q`
Expected: `7 passed`

- [ ] **Step 5: Smoke the real tables against the game**

Run: `uv run python -c "from native import kb; print(kb.building_cost('furnace', 27, 28), kb.building_time('furnace', 27, 28)//86400, 'days')"`
Expected: `{'meat': 190000000, 'wood': 190000000, 'coal': 39000000, 'iron': 9900000} 29 days` (the furnace popup on 2026-09-08 showed the 28 upgrade running with 9d 09h left after speedups; the base 29d 2h matches whiteoutdata's table).

- [ ] **Step 6: Commit**

```bash
git add native/kb.py tests/test_native_kb.py tests/fixtures/knowledge/kbdir
git commit -m "feat: knowledge calculators (costs, times, prerequisites, research paths, verify)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Local cross-checks (whiteoutdata, wiki, wostools) and `disputed` annotation

**Files:**
- Create: `knowledge/local_sources.py`
- Create: `tests/fixtures/knowledge/whiteoutdata_furnace_excerpt.html`, `wiki_furnace_excerpt.html`, `wostools_chunk_excerpt.js`
- Create: `tests/test_knowledge_local.py`
- Modify: `scripts/refresh_knowledge.py` (register `LOCAL`, add `--crosscheck`)

**Interfaces:**
- Consumes: `fetch_text` from Task 1, `normalise.slug`, `kb.furnace_ordinal`.
- Produces: `local_sources.parse_html_tables(html) -> list[list[list[str]]]`; `local_sources.whiteoutdata_furnace(html) -> {"furnace": {"<ordinal>": {"label","meat","wood","coal","iron","fire_crystals","refined_fire_crystals","seconds","power","prerequisites": {slug: level}}}}`; `local_sources.wiki_furnace(html)` same shape; `local_sources.wostools_buildings(js) -> {"buildings": {slug: {"<level>": {meat,wood,coal,iron,fire_crystals,refined_fire_crystals,seconds}}}} | {}` (empty when the bundle shape is not recognised, with a printed reason); `local_sources.parse_amount("140M") -> 140_000_000`, `parse_time("29d 2h 52m") -> seconds`; `local_sources.LOCAL = {"whiteoutdata_furnace": fetcher, "wiki_furnace": fetcher, "wostools_buildings": fetcher}` where a fetcher is `callable(opener) -> (relative_path, doc)`; `local_sources.crosscheck(committed_buildings, local_docs) -> (annotated_buildings, report_lines)` adding `disputed: {source: {field: value}}` to committed furnace rows whose meat/wood/coal/iron/seconds differ by more than 2% and appending FC rows (ordinal > 30) from whiteoutdata as `{"source": "whiteoutdata", ...}` under `buildings.furnace` with `verified_in_game: None`.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/knowledge/whiteoutdata_furnace_excerpt.html` (two tables, header rows as on the site):
```html
<table><tr><th>Level</th><th>Requirements</th><th>Wood</th><th>Meat</th><th>Coal</th><th>Iron</th><th>Upgrade Time</th><th>Power</th><th>Max Hero Level</th><th>Unlocks</th></tr>
<tr><td>27</td><td>Embassy Lv. 26,Lancer Camp Lv. 26</td><td>140M</td><td>140M</td><td>24M</td><td>7.4M</td><td>25d 7h 43m</td><td>1,086,600</td><td>,</td><td>–</td></tr>
<tr><td>28</td><td>Embassy Lv. 27,Research Center Lv. 27</td><td>190M</td><td>190M</td><td>39M</td><td>9.9M</td><td>29d 2h 52m</td><td>1,213,100</td><td>,</td><td>–</td></tr></table>
<table><tr><th>Level</th><th>Requirements</th><th>Wood</th><th>Meat</th><th>Coal</th><th>Iron</th><th>Fire Crystal</th><th>Refined FC</th><th>Upgrade Time</th><th>Power</th></tr>
<tr><td>30-1</td><td>Embassy Lv. 30, Research Center Lv. 30</td><td>67M</td><td>67M</td><td>13M</td><td>3.3M</td><td>132</td><td>–</td><td>7d</td><td>1,580,900</td></tr>
<tr><td>FC 10</td><td>Embassy FC 9, Marksman Camp FC 9</td><td>160M</td><td>160M</td><td>33M</td><td>8.4M</td><td>175</td><td>140</td><td>20d</td><td>4,754,500</td></tr></table>
```

`tests/fixtures/knowledge/wiki_furnace_excerpt.html`:
```html
<table><tr><th>Level</th><th>Prerequisites</th><th>Build Cost</th><th>Time</th><th>Power</th></tr>
<tr><td>28</td><td>Embassy Lv. 27, Research Center Lv. 27</td><td>Meat 190M, Wood 190M, Coal 39M, Iron 9.9M</td><td>29d 2h 52m</td><td>1,213,100</td></tr></table>
```

`tests/fixtures/knowledge/wostools_chunk_excerpt.js` (the shape of the Next.js data literal, minified):
```js
var a=1;let b={name:"Furnace",levels:[{level:27,meat:14e7,wood:14e7,coal:24e6,iron:74e5,fireCrystal:0,refined:0,time:2187780},{level:28,meat:19e7,wood:19e7,coal:39e6,iron:99e5,fireCrystal:0,refined:0,time:2515920}]},c={name:"Embassy",levels:[{level:1,meat:0,wood:0,coal:0,iron:0,fireCrystal:0,refined:0,time:0}]};export{b,c};
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_knowledge_local.py
import os

from knowledge import local_sources as ls

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "knowledge")


def _read(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


def test_parse_amount_and_time():
    assert ls.parse_amount("140M") == 140_000_000 and ls.parse_amount("7.4M") == 7_400_000
    assert ls.parse_amount("1,213,100") == 1_213_100 and ls.parse_amount("–") == 0 and ls.parse_amount("132") == 132
    assert ls.parse_time("29d 2h 52m") == 29 * 86400 + 2 * 3600 + 52 * 60
    assert ls.parse_time("7d") == 7 * 86400 and ls.parse_time("") == 0


def test_whiteoutdata_furnace_rows_by_ordinal():
    doc = ls.whiteoutdata_furnace(_read("whiteoutdata_furnace_excerpt.html"))
    f = doc["furnace"]
    assert f["28"]["meat"] == 190_000_000 and f["28"]["seconds"] == 29 * 86400 + 2 * 3600 + 52 * 60 and f["28"]["power"] == 1_213_100
    assert f["28"]["prerequisites"] == {"embassy": 27, "research_center": 27}
    assert f["31"]["label"] == "30-1" and f["31"]["fire_crystals"] == 132 and f["31"]["refined_fire_crystals"] == 0
    assert f["80"]["label"] == "FC 10" and f["80"]["refined_fire_crystals"] == 140 and f["80"]["prerequisites"] == {"embassy": 75, "marksman_camp": 75}


def test_wiki_furnace_row():
    doc = ls.wiki_furnace(_read("wiki_furnace_excerpt.html"))
    r = doc["furnace"]["28"]
    assert (r["meat"], r["wood"], r["coal"], r["iron"]) == (190_000_000, 190_000_000, 39_000_000, 9_900_000)
    assert r["prerequisites"]["research_center"] == 27


def test_wostools_buildings_from_chunk():
    doc = ls.wostools_buildings(_read("wostools_chunk_excerpt.js"))
    assert doc["buildings"]["furnace"]["28"]["meat"] == 190_000_000 and doc["buildings"]["furnace"]["28"]["seconds"] == 2_515_920
    assert "embassy" in doc["buildings"]
    assert ls.wostools_buildings("var x = 1;") == {}


def test_crosscheck_marks_disputed_and_appends_fc_rows():
    committed = {"furnace": {"28": {"meat": 190_000_000, "wood": 190_000_000, "coal": 39_000_000, "iron": 9_900_000,
                                    "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_515_920,
                                    "prerequisites": {}, "verified_in_game": None}}}
    wd = {"furnace": {"28": {"label": "28", "meat": 190_000_000, "wood": 190_000_000, "coal": 40_000_000, "iron": 9_900_000,
                             "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_515_920, "power": 1_213_100, "prerequisites": {}},
                      "31": {"label": "30-1", "meat": 67_000_000, "wood": 67_000_000, "coal": 13_000_000, "iron": 3_300_000,
                             "fire_crystals": 132, "refined_fire_crystals": 0, "seconds": 604_800, "power": 1_580_900, "prerequisites": {}}}}
    out, lines = ls.crosscheck(committed, {"whiteoutdata": wd})
    assert out["furnace"]["28"]["disputed"] == {"whiteoutdata": {"coal": 40_000_000}}
    assert out["furnace"]["28"]["power"] == 1_213_100
    assert out["furnace"]["31"]["source"] == "whiteoutdata" and out["furnace"]["31"]["fire_crystals"] == 132
    assert any("furnace.28 coal" in l for l in lines) and any("furnace.31 added from whiteoutdata" in l for l in lines)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_knowledge_local.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge.local_sources'`

- [ ] **Step 4: Write `knowledge/local_sources.py`**

```python
"""Cross-check tables from sites whose terms restrict reproduction.

Everything here writes under knowledge/local/ (gitignored) and is never
committed (spec D8). One page fetch per table, no crawling. The parsers are
plain HTML-table and JS-literal readers; when a page's shape is not
recognised they return {} and say so rather than guess.

    page ─fetch_text─▶ html/js ─parse─▶ {"furnace": {ordinal: row}} ─crosscheck─▶ disputed annotations
"""
import html as htmllib
import json
import re

from knowledge.normalise import slug
from knowledge.util import furnace_ordinal  # (B9)

WHITEOUTDATA_FURNACE = "https://whiteoutdata.com/buildings/furnace/"
WIKI_FURNACE = "https://www.whiteoutsurvival.wiki/buildings/furnace/"
WOSTOOLS_BUILDINGS = "https://wostools.net/building-calculator"
_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_amount(text):
    t = str(text).strip().replace(",", "").replace(" ", "").lower()
    if t in ("", "-", "–", "—", "none"):
        return 0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb])?", t)
    if not m:
        return 0
    return int(round(float(m.group(1)) * _SUFFIX.get(m.group(2) or "", 1)))


def parse_time(text):
    total = 0
    for num, unit in re.findall(r"(\d+)\s*([dhms])", str(text).lower()):
        total += int(num) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def parse_html_tables(page):
    tables = []
    for t in re.findall(r"<table.*?</table>", page, re.S):
        rows = []
        for r in re.findall(r"<tr.*?</tr>", t, re.S):
            cells = [htmllib.unescape(re.sub(r"<.*?>", "", c)).strip() for c in re.findall(r"<t[hd][^>]*>.*?</t[hd]>", r, re.S)]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _prereqs(text):
    """'Embassy Lv. 27, Research Center Lv. 27' / 'Embassy FC 9' -> {slug: ordinal}."""
    out = {}
    for name, lvl in re.findall(r"([A-Za-z' ]+?)\s+(?:Lv\.?\s*(\d+)|(FC\s*\d+(?:-\d)?))", text):
        pass
    for m in re.finditer(r"([A-Za-z' ]+?)\s+(Lv\.?\s*\d+|FC\s*\d+(?:-\d)?)", text):
        label = m.group(2).replace("Lv.", "").replace("Lv", "").strip()
        out[slug(m.group(1))] = furnace_ordinal(label)
    return out


def _table_rows(rows, columns):
    """Map header names to indexes and yield dicts per data row."""
    header = [h.lower() for h in rows[0]]
    idx = {}
    for key, names in columns.items():
        for n in names:
            if n in header:
                idx[key] = header.index(n)
                break
    for r in rows[1:]:
        if len(r) < len(header) - 1:
            continue
        yield {k: (r[i] if i < len(r) else "") for k, i in idx.items()}


def whiteoutdata_furnace(page):
    columns = {"level": ["level"], "req": ["requirements"], "wood": ["wood"], "meat": ["meat"], "coal": ["coal"],
               "iron": ["iron"], "fc": ["fire crystal"], "rfc": ["refined fc"], "time": ["upgrade time"], "power": ["power"]}
    out = {}
    for rows in parse_html_tables(page):
        if "level" not in [h.lower() for h in rows[0]]:
            continue
        for d in _table_rows(rows, columns):
            label = d.get("level", "").strip()
            if not label or not re.match(r"(\d|FC)", label, re.I):
                continue
            try:
                ordinal = furnace_ordinal(label)
            except ValueError:
                continue
            out[str(ordinal)] = {"label": label, "meat": parse_amount(d.get("meat")), "wood": parse_amount(d.get("wood")),
                                 "coal": parse_amount(d.get("coal")), "iron": parse_amount(d.get("iron")),
                                 "fire_crystals": parse_amount(d.get("fc")), "refined_fire_crystals": parse_amount(d.get("rfc")),
                                 "seconds": parse_time(d.get("time")), "power": parse_amount(d.get("power")),
                                 "prerequisites": _prereqs(d.get("req", ""))}
    return {"furnace": out}


def wiki_furnace(page):
    columns = {"level": ["level"], "req": ["prerequisites", "requirements"], "cost": ["build cost", "cost"],
               "time": ["time", "upgrade time"], "power": ["power"]}
    out = {}
    for rows in parse_html_tables(page):
        if "level" not in [h.lower() for h in rows[0]]:
            continue
        for d in _table_rows(rows, columns):
            label = d.get("level", "").strip()
            try:
                ordinal = furnace_ordinal(label)
            except ValueError:
                continue
            cost = {}
            for res, amt in re.findall(r"(Meat|Wood|Coal|Iron|Fire Crystal|Refined FC)\s*([\d.,]+[KMB]?)", d.get("cost", ""), re.I):
                cost[slug(res)] = parse_amount(amt)
            out[str(ordinal)] = {"label": label, "meat": cost.get("meat", 0), "wood": cost.get("wood", 0),
                                 "coal": cost.get("coal", 0), "iron": cost.get("iron", 0),
                                 "fire_crystals": cost.get("fire_crystal", 0), "refined_fire_crystals": cost.get("refined_fc", 0),
                                 "seconds": parse_time(d.get("time")), "power": parse_amount(d.get("power")),
                                 "prerequisites": _prereqs(d.get("req", ""))}
    return {"furnace": out}


_JS_NUM = r"-?\d+(?:\.\d+)?(?:e\d+)?"


def _js_number(text):
    return int(round(float(text)))


def wostools_buildings(js):
    """Building tables from the calculator page's JS bundle: objects shaped
    {name:"Furnace",levels:[{level:27,meat:14e7,...,time:2187780},...]}.
    Returns {} when no such object is found (bundle shape changed)."""
    out = {}
    for m in re.finditer(r'name:"([A-Za-z\' ]+)",levels:\[(.*?)\]\}', js):
        name, body = m.group(1), m.group(2)
        rows = {}
        for lv in re.finditer(r"\{level:(\d+),([^{}]*)\}", body):
            fields = dict(re.findall(rf"(\w+):({_JS_NUM})", lv.group(2)))
            rows[lv.group(1)] = {"meat": _js_number(fields.get("meat", 0)), "wood": _js_number(fields.get("wood", 0)),
                                 "coal": _js_number(fields.get("coal", 0)), "iron": _js_number(fields.get("iron", 0)),
                                 "fire_crystals": _js_number(fields.get("fireCrystal", 0)),
                                 "refined_fire_crystals": _js_number(fields.get("refined", 0)),
                                 "seconds": _js_number(fields.get("time", 0))}
        if rows:
            out[slug(name)] = rows
    if not out:
        print("wostools: no building objects recognised in the bundle; nothing written")
        return {}
    return {"buildings": out}


def _fetch_whiteoutdata(opener):
    from knowledge.fetch import fetch_text  # (A1)
    return "whiteoutdata-furnace.json", whiteoutdata_furnace(fetch_text(WHITEOUTDATA_FURNACE, opener))


def _fetch_wiki(opener):
    from knowledge.fetch import fetch_text  # (A1)
    return "wiki-furnace.json", wiki_furnace(fetch_text(WIKI_FURNACE, opener))


def _fetch_wostools(opener):
    from knowledge.fetch import fetch_text  # (A1)
    page = fetch_text(WOSTOOLS_BUILDINGS, opener)
    m = re.search(r'src="([^"]*_next/static/chunks/app/building-calculator/page-[^"]+\.js)"', page)
    if not m:
        print("wostools: building-calculator chunk not found in the page; nothing written")
        return "wostools-buildings.json", {}
    url = m.group(1) if m.group(1).startswith("http") else "https://wostools.net" + m.group(1)
    return "wostools-buildings.json", wostools_buildings(fetch_text(url, opener))


LOCAL = {"whiteoutdata_furnace": _fetch_whiteoutdata, "wiki_furnace": _fetch_wiki, "wostools_buildings": _fetch_wostools}
CHECKED = ("meat", "wood", "coal", "iron", "seconds")


def crosscheck(committed_buildings, local_docs, tolerance=0.02):
    """Annotate the committed furnace rows with `disputed` per source and add
    the post-30 rows only whiteoutdata has. Returns (buildings, lines)."""
    out = json.loads(json.dumps(committed_buildings))
    lines = []
    furnace = out.setdefault("furnace", {})
    for source, doc in local_docs.items():
        rows = doc.get("furnace") or (doc.get("buildings") or {}).get("furnace") or {}
        for ordinal, row in rows.items():
            if ordinal in furnace and int(ordinal) <= 30:
                mine = furnace[ordinal]
                for f in CHECKED:
                    a, b = int(mine.get(f) or 0), int(row.get(f) or 0)
                    if a == b or (a and abs(a - b) / a <= tolerance):
                        continue
                    mine.setdefault("disputed", {}).setdefault(source, {})[f] = b
                    lines.append(f"furnace.{ordinal} {f}: committed {a:,} vs {source} {b:,}")
                if row.get("power") and not mine.get("power"):
                    mine["power"] = row["power"]
            elif int(ordinal) > 30 and ordinal not in furnace and source == "whiteoutdata":
                furnace[ordinal] = {"source": source, "label": row.get("label"), "meat": row["meat"], "wood": row["wood"],
                                    "coal": row["coal"], "iron": row["iron"], "fire_crystals": row["fire_crystals"],
                                    "refined_fire_crystals": row["refined_fire_crystals"], "seconds": row["seconds"],
                                    "power": row.get("power", 0), "prerequisites": row.get("prerequisites", {}),
                                    "verified_in_game": None}
                lines.append(f"furnace.{ordinal} added from {source} ({row.get('label')})")
    return out, lines
```

Remove the dead first `for ... pass` loop in `_prereqs` before committing (it is there only to show the shape being matched; the `finditer` loop is the implementation).

In `scripts/refresh_knowledge.py`, replace `LOCAL = {}` with `from knowledge.local_sources import LOCAL, crosscheck  # noqa: E402` and add a `--crosscheck` flag to `main`:
```python
    ap.add_argument("--crosscheck", action="store_true",
                    help="annotate knowledge/buildings.json with disputed rows from knowledge/local/*.json (needs --write to save)")
```
and, after the `--local` loop:
```python
    if a.crosscheck:
        bpath = os.path.join(KNOWLEDGE_DIR, "buildings.json")
        committed = load_table(bpath)
        local_docs = {}
        for name, rel in (("whiteoutdata", "whiteoutdata-furnace.json"), ("wiki", "wiki-furnace.json"), ("wostools", "wostools-buildings.json")):
            doc = load_table(os.path.join(LOCAL_DIR, rel))
            if doc:
                local_docs[name] = doc
        annotated, lines = crosscheck(committed.get("buildings", {}), local_docs)
        print(f"== crosscheck: {len(lines)} finding(s)" + ("" if a.write else " (dry run)"))
        for line in lines[:100]:
            print("  " + line)
        if a.write:
            write_table(bpath, {**committed, "buildings": annotated})
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_knowledge_local.py tests/test_refresh_knowledge.py -q`
Expected: `11 passed`

- [ ] **Step 6: Fetch the local tables, cross-check, commit only the annotation**

Run: `uv run python scripts/refresh_knowledge.py --local --write` then `uv run python scripts/refresh_knowledge.py --crosscheck` (read the findings: expect 0 disputes for levels 1-30 and 50 furnace rows added from whiteoutdata) then `--crosscheck --write`.
Verify: `git status --short` shows `knowledge/buildings.json` modified and nothing under `knowledge/local/` (gitignored); `python3 -c "import json; d=json.load(open('knowledge/buildings.json')); print(len(d['buildings']['furnace']), d['buildings']['furnace']['80']['label'])"` prints `81 FC 10`.
```bash
git add knowledge/local_sources.py knowledge/buildings.json scripts/refresh_knowledge.py tests/test_knowledge_local.py tests/fixtures/knowledge
git commit -m "feat: local cross-check sources and disputed annotations for the furnace chain

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: In-game verification hook and the FC rows in the calculators

**Files:**
- Modify: `native/kb.py` (`mark_verified`, `building_row` accepting furnace labels)
- Modify: `tests/test_native_kb.py`
- Modify: `knowledge/README.md` (verification section)

**Interfaces:**
- Produces: `kb.mark_verified(table, key, level, snapshot_id, directory=None) -> bool` writes `verified_in_game` into the JSON file (atomic, via `scripts.refresh_knowledge.write_table`) and invalidates the cache; `kb.building_row("furnace", "FC1")` resolves labels through `furnace_ordinal`; `kb.next_level_label(ordinal) -> str` (`27 -> "28"`, `30 -> "30-1"`, `34 -> "FC1"`, `35 -> "FC1-1"`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_native_kb.py`)

```python
def test_building_row_accepts_furnace_labels_and_next_label(k):
    assert kb.building_row("furnace", "28", kb=k)["meat"] == 190_000_000
    assert kb.building_row("furnace", 99, kb=k) is None
    assert [kb.next_level_label(x) for x in (27, 30, 34, 35, 79)] == ["28", "30-1", "FC1", "FC1-1", "FC10"]


def test_mark_verified_writes_and_invalidates_cache(tmp_path):
    import shutil
    d = tmp_path / "kbdir"
    shutil.copytree(FIX, d)
    kb.load(str(d))
    assert kb.mark_verified("buildings", "furnace", 28, "20260908T122223Z", directory=str(d))
    assert kb.load(str(d))["buildings"]["furnace"]["28"]["verified_in_game"] == "20260908T122223Z"
    assert not kb.mark_verified("buildings", "furnace", 999, "x", directory=str(d))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_native_kb.py -q`
Expected: 2 FAIL (`next_level_label` undefined; `mark_verified` undefined)

- [ ] **Step 3: Implement** (add to `native/kb.py`)

```python
def building_row(name, level, kb=None):
    key = str(level)
    if name == "furnace" and not key.isdigit():
        try:
            key = str(furnace_ordinal(key))
        except ValueError:
            return None
    return _kb(kb)["buildings"].get(name, {}).get(key)


def next_level_label(ordinal):
    """The label the game shows for the level after `ordinal` (furnace chain)."""
    nxt = int(ordinal) + 1
    if nxt <= 30:
        return str(nxt)
    if nxt <= 34:
        return f"30-{nxt - 30}"
    fc, sub = divmod(nxt - 30, 5)
    return f"FC{fc}" if sub == 0 else f"FC{fc}-{sub}"


def mark_verified(table, key, level, snapshot_id, directory=None):
    """Record that a screen read agreed with a row; the planner trusts
    verified rows first and the briefing lists unverified ones it relies on."""
    from knowledge.util import write_table  # (B9)
    directory = directory or KNOWLEDGE_DIR
    fname = {"buildings": "buildings.json", "research": "research.json"}[table]
    path = os.path.join(directory, fname)
    with open(path) as f:
        doc = json.load(f)
    row = doc[table].get(key, {}).get(str(level)) if table == "buildings" else doc[table].get(key, {}).get("levels", {}).get(str(level))
    if row is None:
        return False
    row["verified_in_game"] = snapshot_id
    write_table(path, doc)
    _CACHE.pop(directory, None)
    return True
```
(replace the earlier `building_row` definition with this one). Append to `knowledge/README.md`:

```markdown
## In-game verification

Rows carry `verified_in_game: null | "<snapshot_id>"`. The executor calls
`native.kb.mark_verified("buildings", "furnace", 28, snapshot_id)` when the
cost it read on the Upgrade popup matched the table within 2%
(`native.kb.verify`). A `disputed` row is planned from the committed value and
listed in the briefing until a screen read settles it.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/ -q`
Expected: all pass (previous suite plus the new files).

- [ ] **Step 5: Commit**

```bash
git add native/kb.py tests/test_native_kb.py knowledge/README.md
git commit -m "feat: furnace labels, next-level labels and in-game verification marks in the knowledge base

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

---

## CEO review amendments (2026-09-08, all approved; binding over the tasks above)

### A1. `knowledge/fetch.py` replaces the fetch helpers in Task 1 (decision 1A)

Create `knowledge/fetch.py`; `scripts/refresh_knowledge.py` and `knowledge/local_sources.py` import from it. Delete `_open`, `fetch_json`, `fetch_text` from the script (keep `source_commit` there, calling `fetch.fetch_json`). Remove the function-scope imports in `local_sources._fetch_*`.

```python
# knowledge/fetch.py
"""One HTTP path for every knowledge source: a browser-like User-Agent, a
60 s timeout, one request per call, no retries (the refresh prints the
failure and moves on; the user re-runs)."""
import json
import urllib.request

USER_AGENT = "Mozilla/5.0 (Macintosh) wos-bot knowledge refresh (one request per table)"


class FetchError(RuntimeError):
    """Named wrapper so the refresh can print `FetchError: <url>: <cause>`."""


def open_url(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=60)


def fetch_text(url, opener=None):
    try:
        with (opener or open_url)(url) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"{url}: {exc.__class__.__name__}: {exc}") from exc


def fetch_json(url, opener=None):
    text = fetch_text(url, opener)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url}: not JSON ({exc.msg} at char {exc.pos}); first bytes: {text[:80]!r}") from exc
```
(`import urllib.error` alongside `urllib.request`.) Tests in `tests/test_refresh_knowledge.py` that exercised `rk.fetch_json` move to `tests/test_knowledge_fetch.py` and add: a fake opener raising `urllib.error.HTTPError(url, 429, "rate", {}, None)` yields `FetchError` whose message contains `429`; a body of `<html>` yields `FetchError` containing `not JSON`.

### A2. Per-table failures in the refresh (decision 2A)

In `scripts/refresh_knowledge.py`:

```python
def source_commit(repo, opener=None):
    """Short SHA of main; 'unknown' when GitHub's API is unreachable or rate
    limited (60/h unauthenticated), so a table still refreshes with its date."""
    try:
        data = fetch_json(f"https://api.github.com/repos/{repo}/commits/main", opener=opener)
        return str(data["sha"])[:12]
    except (FetchError, KeyError, TypeError) as exc:
        print(f"  commit lookup failed for {repo}: {exc}; recording 'unknown'")
        return "unknown"


def refresh(table, write, opener=None):
    """Returns the diff lines, or None when the table failed (printed, named)."""
    source = SOURCES[table]
    try:
        raw = fetch_json(source.url, opener=opener)
    except FetchError as exc:
        print(f"== {table}: FETCH FAILED {exc}")
        return None
    try:
        doc = NORMALISERS[table](raw)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"== {table}: NORMALISE FAILED {exc.__class__.__name__}: {exc} (upstream shape changed?)")
        return None
    ...  # unchanged from here: _meta, diff, print, write
```
`main` counts `None` results and ends with `sys.exit(1)` after printing `N table(s) failed` when any did; `refresh_local` gets the same two `except` blocks. Tests: a table whose fetch raises `FetchError` prints `FETCH FAILED`, the next table still refreshes, `main` raises `SystemExit(1)`; a normaliser raising `KeyError` prints `NORMALISE FAILED`; `source_commit` returns `"unknown"` on a 403.

### A3. Duplicate research node ids are an error (decision 3A)

In `knowledge/normalise.py::research`, before `out[slug(node_id)] = ...`:
```python
            key = slug(node_id)
            if key in out:
                raise ValueError(f"research node id {key!r} appears in both {out[key]['tree']} and {slug(tree)}")
```
Test: a fixture with `tooling-up-i` under both Growth and Economy raises `ValueError` naming both trees.

### A4. Reuse the screen parsers (decision 4A)

`knowledge/local_sources.py` drops `parse_amount`/`parse_time` bodies for:
```python
from native.screen import parse_number, parse_duration


def parse_amount(text):
    """Web tables print '140M', '1,213,100', '–'; the screen parser already
    handles every one of those shapes."""
    v, _exact = parse_number(str(text).replace("–", "").replace("—", "").strip())
    return v or 0


def parse_time(text):
    return parse_duration(text) or 0
```
The Task 4 `test_parse_amount_and_time` cases stay as they are (they pass through the shared parsers; `parse_duration("7d")` returns 604800 and `parse_duration("")` returns None, mapped to 0).

### A5. Local overlay, never a merged commit (decision D4, spec D8)

Task 4 step 4's `crosscheck()` no longer edits the committed table. It writes `knowledge/local/overlay.json`:
```json
{"_meta": {"built_at": "...", "sources": ["whiteoutdata", "wiki", "wostools"]},
 "buildings": {"furnace": {"28": {"power": 1213100, "disputed": {"whiteoutdata": {"coal": 40000000}}},
                           "31": {"source": "whiteoutdata", "label": "30-1", "meat": 67000000, "...": "full row"}}}}
```
Signature becomes `crosscheck(committed_buildings, local_docs, tolerance=0.02) -> (overlay, lines)` where `overlay["buildings"][name][level]` holds only the fields to add (`power`, `disputed`) for committed levels and full rows (with `"source"`) for levels the committed table lacks. `--crosscheck --write` writes `knowledge/local/overlay.json`; `knowledge/buildings.json` is never modified by cross-checks. The Task 4 test `test_crosscheck_marks_disputed_and_appends_fc_rows` asserts on the overlay instead: `overlay["buildings"]["furnace"]["28"] == {"power": 1_213_100, "disputed": {"whiteoutdata": {"coal": 40_000_000}}}` and `overlay["buildings"]["furnace"]["31"]["source"] == "whiteoutdata"`.

`native/kb.py::load` merges it when present:
```python
OVERLAY = os.path.join(KNOWLEDGE_DIR, "local", "overlay.json")


def _apply_overlay(kb, path):
    """Terms-restricted cross-check data stays on this machine (spec D8):
    committed tables are wosnerds-only and the overlay adds FC rows, power and
    disputes at load time. Without it, power_gain('building') is None."""
    if not os.path.exists(path):
        kb["_overlay"] = None
        return
    with open(path) as f:
        ov = json.load(f)
    for name, levels in (ov.get("buildings") or {}).items():
        table = kb["buildings"].setdefault(name, {})
        for level, patch in levels.items():
            if level in table:
                table[level].update(patch)
            else:
                table[level] = {"verified_in_game": None, **patch}
    kb["_overlay"] = ov.get("_meta")
```
called at the end of `load()` with `os.path.join(directory, "local", "overlay.json")`. `power_gain("building", ...)` returns `None` when any level in the range has no `power` (planner treats None as unknown, never 0). Tests: `kb.load` on the fixture dir without an overlay gives `power_gain("building", name="furnace", from_level=27, to_level=28) is None` and no level `"31"`; with a fixture overlay it gives `1_213_100` and `building_row("furnace", "30-1")["meat"] == 67_000_000`.

### A6. New Task 6: absorb `docs/knowledge/feature-unlocks.json` (decision D1, approach C)

**Files:** move `docs/knowledge/feature-unlocks.json` -> `knowledge/unlocks.json` (`git mv`), modify `core/capability.py:41` (path), `knowledge/README.md`, `tests/test_capability.py`, add `knowledge/unlocks_check.py`.

- [ ] Step 1: failing test in `tests/test_capability.py`: `load_table()` default path ends with `knowledge/unlocks.json`; the file's top level has `_meta` with `source_url`, `fetched_at`, `licence` (values: the design doc's community sources, `2026-09-01`, `"community guides; per-entry source and confidence kept"`) and the existing `_schema`, `features`, `_unverified_gates` keys unchanged.
- [ ] Step 2: run, expect FAIL on the path assertion.
- [ ] Step 3: `git mv docs/knowledge/feature-unlocks.json knowledge/unlocks.json`; in `core/capability.py` replace the `docs/knowledge` path with `os.path.join(_HERE, os.pardir, "knowledge", "unlocks.json")`; prepend the `_meta` object to the file (keep every other key byte-identical; `write_table` from Task 1 re-indents, which is fine); update the ASCII diagram at `core/capability.py:25` to name `knowledge/unlocks.json`; README row: `unlocks.json | community guides, seeded 2026-09-01 (see docs/designs/adaptive-automation.md) | hand-maintained; observation overrides`.
- [ ] Step 4: `uv run pytest tests/ -q` green.
- [ ] Step 5: commit `refactor: unlock gates live in the knowledge base with the same provenance format`.

### A7. Task 2 gains five more tables (decision D3.1)

Add to `SOURCES`: `heroes`, `chief_gear` (`calculator/data/chief-gear-charms.json`), `hero_gear` (`hero-gear.json`), `pets` (`pets.json`), `dawn_academy` (`dawn-academy.json`), all from `wosnerdwarriors/website-index`. Before writing normalisers, fetch each once (`uv run python -c "from knowledge.fetch import fetch_json; import json; print(json.dumps(fetch_json('<url>'))[:1500])"`) and record the observed top-level shape in a comment above the normaliser; each normaliser follows `troops()`: iterate the source's list or dict, `slug` the names, `_int` the numbers, keep unknown fields under `"extra"` rather than dropping them. Fixtures: one real record per table saved to `tests/fixtures/knowledge/<table>_excerpt.json`; tests assert one known value per table (choose the first record's cost/stat and assert it verbatim). Output files: `knowledge/heroes.json` (`{"heroes": {slug: {...}}}`), `knowledge/chief_gear.json` (`{"chief_gear": {...}, "charms": {...}}`), `knowledge/hero_gear.json`, `knowledge/pets.json`, `knowledge/dawn_academy.json`; `native/kb.py::TABLES` gains them under the same keys, `load()` tolerates their absence with a printed line (they are not required by the phase-1 calculators).

### A8. `kb.next_occurrences` (decision D3.2)

Add to `native/kb.py`:
```python
def next_occurrences(event_id, now, count=3, kb=None):
    """Upcoming (start, end) datetimes of a recurring event from its anchor and
    period. Verified against this state: Castle Battle (svs_castle, anchor
    2024-10-12T12:00Z, every 28 days) was announced for 2026-09-14 ~11:30 UTC."""
    from datetime import datetime, timedelta, timezone
    e = _kb(kb)["events"][event_id]
    anchor = datetime.fromisoformat(e["anchor"].replace("Z", "+00:00"))
    period = timedelta(days=e["repeat_every_days"])
    if period.total_seconds() <= 0:
        return []
    k = max(0, math.ceil((now - anchor) / period))
    start = anchor + k * period
    out = []
    for _ in range(count):
        out.append((start, start + timedelta(hours=e["length_hours"])))
        start += period
    return out
```
Test: with `now = 2026-09-08T12:00Z` the first `svs_castle` occurrence starts on `2026-09-14` (date only; the in-game timer showed 11:32 UTC against the anchor's 12:00, so assert `abs(start - datetime(2026, 9, 14, 12, tzinfo=utc)) <= timedelta(hours=1)`), the second 28 days later; `count=1` returns one pair; an event with `repeat_every_days=0` returns `[]`.

### A9. Knowledge freshness in the report (decision D3.3)

`native/kb.py`:
```python
def freshness(kb=None, now=None, stale_days=30):
    """[(table, age_days, stale)] from each table's _meta.fetched_at."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    out = []
    for key in TABLES:
        m = _kb(kb).get(f"_meta_{key}")
        if not m or not m.get("fetched_at"):
            continue
        age = (now - datetime.fromisoformat(m["fetched_at"].replace("Z", "+00:00"))).days
        out.append((key, age, age > stale_days))
    return out
```
`native/report.py::render_text` appends under Warnings: `knowledge: buildings 12 d, research 12 d` and, for any stale table, `  knowledge table <name> is <n> days old: uv run python scripts/refresh_knowledge.py --table <name>`; `doctor()` adds the same hint. Tests: fixture `_meta.fetched_at` 40 days before an injected `now` flags stale; 5 days does not; the report line appears.

### B. Spec-review fixes (2026-09-08, verified against the real source files; binding)

B1. **Slug and apostrophes.** `slug()` above now drops `'` and `’` before splitting (`hunters_hut`). `_prereqs` in Task 4 uses the name class `[A-Za-z'’ ]+?`. Add `assert n.slug("Hunter’s Hut") == "hunters_hut"` to `test_slug`.

B2. **Furnace 28 time.** The real value in `construction.json` and whiteoutdata ("29d 2h 52m") is `2_515_920` s; every occurrence in this plan is corrected. The Task 3 smoke check prints `29 days`.

B3. **`diff_rows` is recursive.** Replace the Task 1 implementation with:
```python
def diff_rows(old, new, _path=""):
    """Leaf-level diff of two nested dict documents; `_meta` ignored at the
    top. A first fetch prints one `added` line per ROW (buildings.furnace.27),
    a changed cost prints `buildings.furnace.28: meat 1 -> 2`."""
    lines = []
    keys = sorted((set(old) | set(new)) - ({"_meta"} if not _path else set()), key=str)
    for key in keys:
        path = f"{_path}.{key}" if _path else str(key)
        if key not in old:
            lines.append(f"{path}: added")
        elif key not in new:
            lines.append(f"{path}: removed")
        else:
            a, b = old[key], new[key]
            if isinstance(a, dict) and isinstance(b, dict) and (
                    any(isinstance(v, dict) for v in a.values()) or any(isinstance(v, dict) for v in b.values())):
                lines.extend(diff_rows(a, b, path))
            elif isinstance(a, dict) and isinstance(b, dict):
                for f in sorted(set(a) | set(b)):
                    if a.get(f) != b.get(f):
                        lines.append(f"{path}: {f} {a.get(f)} -> {b.get(f)}")
            elif a != b:
                lines.append(f"{path}: {a} -> {b}")
    return lines
```
The Task 1 test becomes three-level: `old = {"_meta": {...}, "buildings": {"furnace": {"27": {"meat": 1}, "28": {"meat": 2}}}, "gone": {"x": {"1": {"v": 1}}}}`, `new = {"_meta": {...}, "buildings": {"furnace": {"27": {"meat": 1}, "28": {"meat": 3}, "29": {"meat": 4}}}}`; expect `buildings.furnace.28: meat 2 -> 3`, `buildings.furnace.29: added`, `gone: removed`. Add a research-shaped case: a change in `research.tooling_up_i.levels.2.seconds` prints exactly `research.tooling_up_i.levels.2: seconds 40 -> 41`.

B4. **Refresh keeps in-game marks.** In `refresh()`, after normalising and before diffing:
```python
    doc = carry_marks(load_table(path), doc)
```
```python
def carry_marks(old, new):
    """`verified_in_game` is evidence from the screen, not upstream data; a
    refresh must not erase it. Carried only when the row's costs are unchanged."""
    for table, rows in new.items():
        if table == "_meta" or not isinstance(rows, dict):
            continue
        for key, levels in rows.items():
            old_levels = (old.get(table) or {}).get(key) or {}
            if not isinstance(levels, dict):
                continue
            for lv, row in levels.items():
                orow = old_levels.get(lv) if isinstance(old_levels, dict) else None
                if isinstance(row, dict) and isinstance(orow, dict) and orow.get("verified_in_game"):
                    same = all(row.get(f) == orow.get(f) for f in ("meat", "wood", "coal", "iron", "seconds", "cost"))
                    row["verified_in_game"] = orow["verified_in_game"] if same else None
    return new
```
Test: an old table with `verified_in_game: "s1"` on furnace 28 and identical costs keeps it; with a changed `meat` it becomes `None` (and the diff prints the change).

B5. **Restricted fixtures stay local.** `whiteoutdata_furnace_excerpt.html`, `wiki_furnace_excerpt.html` and `wostools_chunk_excerpt.js` live in `tests/fixtures/local/knowledge/` (gitignored); their tests `pytest.skip` when absent. The committed `tests/fixtures/knowledge/` holds only wosnerds excerpts and the synthetic `kbdir`.

B6. **Ordinal convention.** Every building level in the knowledge base is an ordinal (`level + 5*fc + sub`, same rule for Embassy, camps and the furnace: `"Embassy FC 9"` is 75). The sheet stores plain ints today (all buildings are below 30); `prerequisites()` compares ints to ordinals directly, which is correct while the account is pre-FC, and `native/readers/buildings.py` must start storing ordinals when it first reads an `FC` label (a note goes into TODOS.md "City-map reader" entry). Global constraint line updated: slugs are `slug()` of the wosnerds names; `native/schema.py` `BUILDINGS` is a subset plus `storehouse`, `warehouse`, `war_academy`, which wosnerds lacks (they stay unread in the knowledge base).

B7. **`furnace_ordinal` is strict.** The `N-sub` form is accepted only with `N == 30` and `sub` in 1..4; `FCn-sub` only with `sub` in 1..4 and `n` in 1..10; anything else raises `ValueError`. Tests: `"27-3"` and `"FC1-5"` raise.

B8. **`research_path` cycle guard.** `visit` keeps a `visiting` set; re-entering a `(node, level)` already in it raises `ValueError(f"research prerequisite cycle at {nid}@{lv}")`. Test with a two-node fixture that requires each other.

B9. **Layering.** `knowledge/util.py` holds `furnace_ordinal`, `next_level_label`, `write_table`; `native/kb.py`, `knowledge/local_sources.py` and `scripts/refresh_knowledge.py` import them from there (no `native.kb` import inside `knowledge/`, no lazy `scripts` import inside `native/`).

B10. **Overlay rules (with A5).** `crosscheck` copies `power` only from `whiteoutdata` (the wiki and wostools `power` fields are ignored); `disputed` in the overlay carries the values; the overlay is rebuilt from scratch on every `--crosscheck --write`, so a changed local row is always re-diffed (the refresh prints the overlay diff like any table). Add `test_crosscheck_without_local_dir`: `--crosscheck` with no `knowledge/local/` prints `0 finding(s)` and writes nothing.

B11. **Wording.** `_fetch_wostools` makes two requests (page, then its chunk); the constraint reads "at most two requests per source, no crawling". `wostools_buildings` compiles its regexes with `re.S`.

B12. **`power_gain("building")`** returns `None` unless every level in the range carries `power` from the overlay (A5); the docstring says so.

### A10. Speed bonuses read from the game (user: build now)

**Files:** `native/readers/stats.py`, `native/schema.py` (paths `progress.bonus.construction_speed`, `progress.bonus.research_speed`, `progress.bonus.training_speed`, volatile int, section `stats`), `native/snapshot.py` (register after `profile`), `references/screen-map.md`, `tests/test_native_readers.py`.

- [ ] Step 1 (survey, no writes): from home, `uv run python ~/.claude/skills/wos-chief-state/scripts/survey.py tap 0.148 0.071 profile`, then `survey.py tap 0.57 0.79 power-details` (the magnifier beside the power figure). Record every label containing "Speed" with its fraction. If none, try `survey.py label Settings` and then the first row containing "Stats"/"Bonus"; record the path that shows `Construction Speed +NN%`, `Research Speed +NN%`, `Training Speed +NN%`. Write the path into `references/screen-map.md` (entry, arrival tell, labels, exit). Stop and report if no screen shows all three.
- [ ] Step 2: failing test on the recorded frame (added to `tests/fixtures/local/reader_frames.json` as `stats`): `stats.parse(...)` yields the three percentages as ints (e.g. `{"construction_speed": 128, ...}` for "+128%").
- [ ] Step 3: `native/readers/stats.py` with `EXPECTED` = the three paths, `parse` using `label_value(items, h, w, "Construction Speed")` and `re.search(r"\+?(\d+(?:\.\d+)?)%", value)`, `read(sc)` following the surveyed path and leaving via `sc.go_home()`.
- [ ] Step 4: `native/kb.py` gains `speed_bonus_from_sheet(sheet, kind) -> float` returning `sheet.get(f"progress.bonus.{kind}_speed") / 100.0` or `0.0`; `building_time`/`training_time` callers in the planner pass it. Test: 128 -> 1.28.
- [ ] Step 5: live `snapshot.py --readers hud,profile,stats --no-write` shows the three values; commit `feat: read construction, research and training speed bonuses`.

### A11. Item catalogue from the game (user: build now; source changed from the wiki)

The wiki items index has no tables, only 421 item pages, and fetching them all is the crawling the constraints forbid. The tooltips the backpack reader already opens carry the item name and its effect text, an in-game source with no terms problem, so the catalogue is built from there.

**Files:** `knowledge/items.json` (committed: in-game source), `native/readers/backpack.py` (`read_tooltip` also returns the description line), `native/kb.py` (`items()`, `record_item(name, tab, description, snapshot_id)`), tests.

- [ ] Step 1: failing test: `read_tooltip` on the `backpack_tile` fixture returns `("1 Gems", None, <description text>)` where the description is the text line between the name and the Use button (the fixture's "Use to obtain 1 Gem" style line; assert it is a non-empty string not equal to the name).
- [ ] Step 2: `record_item` upserts `{"name", "slug", "tab", "description", "kind": "speedup"|"resource_box"|"fire_crystal"|"other" (from the same SPEEDUP_RE/keyword rules as `fold`), "first_seen": snapshot_id, "last_seen": snapshot_id}` into `knowledge/items.json` (`_meta.source = "in-game backpack tooltips"`), never overwriting `first_seen`; the backpack reader calls it for every tooltip it reads.
- [ ] Step 3: `kb.items()` returns the catalogue; `fold` consults `kb.items()` first (exact slug match) before regex parsing.
- [ ] Step 4: tests for upsert semantics and the `fold` lookup; commit `feat: item catalogue grown from backpack tooltips`.

### Task list after amendments

T1 (fetch.py, util.py, refresh with per-table failures, recursive diff, carry_marks) -> T2 (nine normalisers, real tables committed) -> T3 (calculators incl. next_occurrences, strict ordinal, cycle guard) -> T4 (local sources, overlay) -> T5 (verification marks, freshness, overlay merge) -> T6 (unlocks absorbed) -> A10 (speed bonuses reader) -> A11 (item catalogue). Verification stays: `uv run pytest tests/ -q` green after each task; `git status` never shows `knowledge/local/`.

## Self-review

- **Spec coverage.** Sources and licence note: Task 1-2. Layout and `_meta`: Task 1-2. Local-only cross-checks with `disputed` and FC rows: Task 4. `refresh_knowledge.py` diff-before-write and `--local`: Task 1, 4. Calculators listed in the spec (`building_cost`, `building_time`, `prerequisites`, `research_path`, `training_cost`, `training_time`, `power_gain`, `days_to`, `verify`): Task 3. `verified_in_game` marking: Task 5. Tests per spec (schema, known values, prerequisite resolution, refresh diff, missing `knowledge/local/`): Tasks 1-4 (`load_table` returns `{}` for a missing local file, exercised by `--crosscheck` with no local docs).
- **Placeholders.** None; the `_prereqs` scaffold loop is explicitly removed in Task 4 step 4.
- **Type consistency.** `Row` keys (`meat, wood, coal, iron, fire_crystals, refined_fire_crystals, seconds, prerequisites, verified_in_game`) are the same in Task 2 normaliser, Task 3 calculators and Task 4 cross-check; `furnace_ordinal` defined in Task 3 is imported by Task 4; `write_table` from Task 1 is reused by Task 5; `ResearchStep` fields are `node level cost seconds` throughout.

## C. Second-pass fixes (verified against the real sources; binding, override A/B where they conflict)

C1. **`diff_rows` recursion rule.** B3's function recurses one level too far (a research level row contains `cost`, so a change prints `research.node.levels.2.seconds`, and a first fetch of the real `construction.json` prints one line: `buildings: added`). Define the rule explicitly:

```python
def _is_table(v):
    """A container of rows: a non-empty dict whose every value is a dict."""
    return isinstance(v, dict) and bool(v) and all(isinstance(x, dict) for x in v.values())


def diff_rows(old, new, _path=""):
    """Row-level diff. Recurse while both sides are TABLES; at a row, compare
    fields, recursing into a field that is itself a table (`levels`). An added
    or removed table recurses against {} so every row is named."""
    lines, keys = [], sorted((set(old) | set(new)) - ({"_meta"} if not _path else set()), key=str)
    for key in keys:
        path = f"{_path}.{key}" if _path else str(key)
        a, b = old.get(key), new.get(key)
        if key not in old and _is_table(b):
            lines.extend(diff_rows({}, b, path)); continue
        if key not in new and _is_table(a):
            lines.extend(diff_rows(a, {}, path)); continue
        if key not in old:
            lines.append(f"{path}: added"); continue
        if key not in new:
            lines.append(f"{path}: removed"); continue
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
```

Tests (replacing B3's): a first fetch of the buildings fixture prints one `added` line per LEVEL (`buildings.furnace.27: added`), a cost change prints `buildings.furnace.28: meat 2 -> 3`, a research change prints `research.tooling_up_i.levels.2: seconds 40 -> 41`, a removed table prints `gone.x.1: removed`.

C2. **Stale halved value.** `building_time("furnace", 27, 28, speed_bonus=1.0)` is `1_257_960` (2,515,920 / 2); corrected throughout this plan.

C3. **A8 test asserts the table's own contract, not the game.** `next_occurrences("svs_castle", 2026-09-08T12:00Z)` returns `2026-09-12T12:00Z` from the shipped anchor; one test asserts exactly that, a second asserts the observed-anchor path of D-T4 returns `2026-09-14T11:32Z`. `knowledge/README.md` records the two-day disagreement.

C4. **Research verification marks live on level rows.** `normalise.research` puts `verified_in_game: None` inside each `levels[n]` row, not on the node; `mark_verified("research", node, level, sid)` already writes there; `carry_marks` walks recursively and carries the mark wherever a dict holds that key, comparing that row's own cost fields.

C5. **`research-center-lv` alias.** The real source uses that key in two rows (`coal_mining_iii` L3, `marksman_armor_iii` L4). `normalise.research` maps building keys through `BUILDING_ALIASES = {"research_center_lv": "research_center"}` before storing; test both rows.

C6. **Expected disputes.** Levels 11 and 17 disagree between whiteoutdata and wosnerds (coal 260,000 vs 20,000; iron 460,000 vs 480,000). Both sit below the D-T2 scope floor of level 26, so Task 4's expectation is "0 findings in scope, 2 known disagreements below the floor recorded in the README".

C7. **A10 wiring.** Insert `("progress.bonus.", "stats")` ABOVE `("progress.", "profile")` in `native/schema.py::SECTION_OF_PATH_TABLE`; `label_value` returns the OCR item, so the reader runs its percentage regex on `item["text"]`.

C8. **Unknown vs unmet.** `prerequisites()` returns `have=None` when the sheet has no key for that building (see D-T1); the `("infirmary", 1, 0)` case in the Task 3 test becomes `("infirmary", 1, None)`.

C9. **A11 description band.** Before writing `read_tooltip`'s third return value, survey the `backpack_tile` frame already in `tests/fixtures/local/reader_frames.json` and record the description's y-band; the current name band (`uy-0.24 .. uy-0.15`) sits above it. The three-tuple return is safe: the only caller uses `got[0]`.

C10. **Superseded text.** Task 4 step 6 (`len(furnace)==81`, `git add knowledge/buildings.json`) and Task 5's `local_sources`/`scripts` imports are superseded by A5, B9 and D-T2; each carries a `SUPERSEDED by <id>` line so an executor reading one task alone is not misled.

C11. **`chief_gear.json` splits.** It carries two top-level tables and `kb.load`'s `doc[key]` would drop one; normalise into `knowledge/chief_gear.json` and `knowledge/charms.json`.

C12. **`mark_verified` and overlay rows.** It opens the committed file only, so overlay levels above 30 always return `False`. Documented; irrelevant until the furnace passes 30.

## D. Cross-model decisions (user, after the outside voice)

**D-T1 (buildings vocabulary).** The sheet reads 11 buildings, wosnerds ships 14, 8 overlap.

- `prerequisites()` returns `have=None` for a building the sheet does not track. `native/kb.py` gains `UNTRACKED_ASSUMED_MET = ("coal_mine", "iron_mine", "sawmill", "hunters_hut", "shelter", "barricade")`, and the signature becomes `prerequisites(name, level, sheet, kb=None, assume_untracked_met=True) -> (unmet, assumed)`: those six leave the unmet list and appear in `assumed`, so the planner prints one line (`assumed met (not tracked): coal_mine 3, hunters_hut 6, iron_mine 5, sawmill 1, shelter 3`) instead of five permanent unmet rows.
- `storehouse`, `warehouse` and `war_academy` are absent upstream: `building_cost` raises `KeyError` naming the building and saying "read from the popup", and the planner's candidates for those three carry `cost=None, source="popup"` (the executor reads the cost off the Upgrade popup before pressing anyway). Test: `building_cost("storehouse", 26, 27)` raises with that message.

**D-T2 (cross-checks: report only, from level 26 up).** Task 4 shrinks:

- Scope floor: only furnace levels with ordinal >= 26 are parsed and compared (the account is upgrading 26 -> 27), which is also why the two known sub-26 disagreements (C6) are out of scope.
- Output: `knowledge/local/crosscheck.json` (gitignored) with, per level >= 26, each source's costs and times and a `disagrees` list; the refresh prints the disagreements. No `disputed` writes into the committed table, no `power` copy, and no prerequisite parsing at all (the requirement text is the part that breaks on the live page: "Command Centre", "Lvl.", and only 2 of 12 prerequisites listed per row).
- FC rows (ordinal 31-80) are kept in `knowledge/local/overlay.json` exactly as A5 defines, with `prerequisites: {}` and a `"source": "whiteoutdata"` marker. `kb.building_row` returns them so long-range cost questions work, and `prerequisites()` on an overlay row returns `([], ["unknown: overlay row"])` so the planner never reads an empty prerequisite list as satisfied.
- `local_sources` keeps `whiteoutdata_furnace`, `wiki_furnace` and `wostools_buildings` as parsers; `_prereqs` is deleted.

**D-T3 (extra tables optional).** `heroes`, `chief_gear`, `charms`, `hero_gear`, `pets` and `dawn_academy` move to `OPTIONAL_SOURCES`: the default refresh does not fetch them, `--table <name>` does, and a failure among them never changes the exit code. `native/kb.py::TABLES` marks them optional and `load()` skips a missing one with a single printed line.

**D-T4 (calendar anchored on observation).** `next_occurrences(event_id, now, count=3, observed=None, kb=None)`: with `observed` (a datetime) the occurrences are `observed + k * period`; otherwise the shipped anchor is used. Each returned tuple gains a third element, `"observed"` or `"table"`, so the briefing can say which it used. `report.py` passes `manual.castle_battle_at` as `observed` for `svs_castle`. `knowledge/README.md` records that the shipped SvS anchor is two days off for state #4562 because SvS is staggered per state group.

## NOT in scope

- Building `power` values for anything but the furnace: no open source carries them (verified against `construction.json`), so `power_gain("building", ...)` returns `None` outside the overlay's furnace rows.
- Prerequisite text from the cross-check sources (broken on the live page, incomplete by construction).
- Readers for the six untracked buildings (assumed met; the city-map spike in TODOS.md covers them).
- The wiki item index (421 item pages; crawling them is out, and A11 grows the catalogue from in-game tooltips instead).
- Any game access or database write from `knowledge/` or `native/kb.py`.

## What already exists (reused, not rebuilt)

| need | exists | reused how |
|---|---|---|
| number and duration parsing | `native/screen.py` `parse_number`, `parse_duration` | A4 wraps them |
| atomic JSON write | `native/model.py::write_table` shape | B9: one copy in `knowledge/util.py` |
| furnace ordinal maths | `native/snapshot.py::derive_furnace` | `furnace_ordinal` mirrors it; B7 makes it strict |
| unlock gates with confidence and provenance | `docs/knowledge/feature-unlocks.json` + `core/capability.py` | A6 moves it into `knowledge/`, same `_meta` format |
| state-age milestones | `native/timeline.py` (observed days override the table) | the calendar complements it; D-T4 uses the same observation-wins rule |
| the sheet the calculators answer against | `db/wos.sqlite` `latest_static`, `deltas` | planner input; the knowledge base never reads it |
| verification pattern | `native/model.py` field provenance | same idea, one `verified_in_game` field per row |

## Dream state delta

After this plan: one knowledge store with provenance, four required tables and six optional, calculators for cost, time, prerequisites, research paths, training, power, event dates and freshness, all pure and tested, plus in-game verification marks and a local-only disagreement report. Remaining to the twelve-month ideal: the planner and executor that consume it, speed bonuses beyond the three A10 reads, per-node research levels, and building power values no public source carries.

## Error and rescue registry

| codepath | failure | exception | rescued | action / user sees |
|---|---|---|---|---|
| `fetch.fetch_text` | DNS, timeout, 404, 429 | `FetchError` (wraps `URLError`, `HTTPError`, `OSError`, `TimeoutError`) | Y | `== <table>: FETCH FAILED <url>: HTTPError 429`; other tables continue |
| `fetch.fetch_json` | HTML error page instead of JSON | `FetchError` (wraps `JSONDecodeError`) | Y | `not JSON ... first bytes: '<!DOCTYPE h'` |
| `source_commit` | GitHub API rate limit (60/h) | `FetchError`, `KeyError` | Y | `commit lookup failed ...; recording 'unknown'`; the table still writes |
| `NORMALISERS[table]` | upstream renamed a key | `KeyError`, `TypeError`, `ValueError` | Y | `NORMALISE FAILED KeyError: 'buildingLevels' (upstream shape changed?)`; exit 1 at the end |
| `normalise.research` | duplicate node id across trees | `ValueError` | N (deliberate) | that table stops, both trees named |
| `write_table` | disk full, read-only directory | `OSError` | N | traceback with the path; nothing half-written (atomic replace) |
| `kb.load` | required table missing | `FileNotFoundError` | N | names the file and the refresh command |
| `kb.load` | optional table missing | none | Y | one printed line; that table's calculators unavailable |
| `kb._apply_overlay` | overlay absent | none | Y | `power_gain("building")` returns `None`, FC rows absent |
| `kb.building_cost` | building or level not in the table | `KeyError` | N (deliberate) | names it; for the three the sheet tracks, says "read from the popup" |
| `kb.research_path` | prerequisite cycle | `ValueError` | N | names the node and level |
| `kb.next_occurrences` | `repeat_every_days` 0 or missing | none | Y | returns `[]` |
| `kb.freshness` | `_meta.fetched_at` missing | none | Y | that table omitted from the freshness line |
| `mark_verified` | row not in the committed file | none | Y | returns `False`; the caller logs it |
| `local_sources.*` | page shape changed | none | Y | prints "nothing recognised", writes `{}` |

## Failure modes registry

| codepath | failure mode | rescued | test | user sees | logged |
|---|---|---|---|---|---|
| refresh, one table | upstream 404 or rename | Y | Y | FETCH/NORMALISE FAILED line, exit 1 | stdout |
| refresh, all | game patch changes costs | Y | Y | per-row diff, nothing written without `--write` | stdout |
| refresh, marks | verified marks erased | Y (`carry_marks`) | Y | mark kept when costs unchanged, cleared when not | diff line |
| calculators | building the sheet tracks, table lacks | Y (`KeyError` naming it) | Y | planner line reads `cost from popup` | plan line |
| calculators | prerequisite the sheet never reads | Y (`assumed`) | Y | one `assumed met` line, not five unmet rows | plan line |
| calculators | stale table after a patch | Y (`freshness`) | Y | `knowledge table buildings is 41 days old: <command>` | report |
| overlay | absent on a fresh clone | Y | Y | `power_gain` None, FC rows absent, no crash | none |
| cross-check | live page reshaped | Y | Y | "nothing recognised", `{}` written | stdout |
| calendar | anchor wrong for this state | Y (D-T4) | Y | occurrences marked `table` or `observed` | report |

No row is unrescued, untested and silent.

## Diagrams

Data flow with shadow paths:

```
 upstream JSON ─fetch─▶ raw ─normalise─▶ doc ─carry_marks─▶ diff ─(--write)─▶ knowledge/<t>.json
   │ FetchError: line, next table  │ KeyError: line, exit 1     │ no --write: printed only
   │                               │ duplicate id: ValueError   │
 local pages ─parse─▶ crosscheck.json + overlay.json (gitignored) ─┐
                                                                   ▼
 sheet (db/wos.sqlite) ─────────────────────────────▶ native/kb.py ─▶ costs, times, (unmet, assumed),
                                                      load+overlay     research paths, event dates,
                                                                       freshness, verify
```

Knowledge row lifecycle:

```
 absent ─refresh─▶ ok(unverified) ─screen agrees─▶ verified(snapshot_id)
                        │                                │ costs change upstream
                        │ a local source disagrees       ▼
                        └───────────────────────▶ ok(unverified) + crosscheck.json entry
```

## Implementation tasks

Synthesized from this review. Each derives from a specific finding above.

- [x] **T1 (P1, human ~1d / CC ~45m)** — knowledge/fetch.py, util.py, refresh — named per-table failures, recursive `diff_rows`, `carry_marks`, provenance
  - Surfaced by: A1, A2, B3, B4, B9, C1
  - Files: `knowledge/fetch.py`, `knowledge/util.py`, `scripts/refresh_knowledge.py`, `knowledge/README.md`, `tests/test_knowledge_fetch.py`, `tests/test_refresh_knowledge.py`, `.gitignore`
  - Verify: `uv run pytest tests/test_knowledge_fetch.py tests/test_refresh_knowledge.py -q`
- [x] **T2 (P1, human ~1d / CC ~1h)** — normalisers for the four required tables plus the real vendored files
  - Surfaced by: Task 2, A3, A7 (now optional per D-T3), B1, C5, C11
  - Files: `knowledge/normalise.py`, `knowledge/*.json`, `tests/fixtures/knowledge/*`, `tests/test_knowledge_normalise.py`
  - Verify: `uv run python scripts/refresh_knowledge.py` dry run, then `--write`; `uv run pytest tests/test_knowledge_normalise.py -q`
- [x] **T3 (P1, human ~1d / CC ~1h)** — `native/kb.py` calculators, strict ordinal, cycle guard, `next_occurrences`, assumed-met prerequisites
  - Surfaced by: Task 3, A8, B7, B8, C2, C3, C8, D-T1, D-T4
  - Files: `native/kb.py`, `tests/test_native_kb.py`, `tests/fixtures/knowledge/kbdir/*`
  - Verify: `uv run pytest tests/test_native_kb.py -q`
- [x] **T4 (P2, human ~0.5d / CC ~40m)** — cross-check report from level 26 up, FC rows to the local overlay
  - Surfaced by: Task 4, A5, B5, B10, C6, D-T2
  - Files: `knowledge/local_sources.py`, `scripts/refresh_knowledge.py`, `tests/fixtures/local/knowledge/*`, `tests/test_knowledge_local.py`
  - Verify: `--local --write`, `--crosscheck`; `git status` shows nothing under `knowledge/local/`
- [x] **T5 (P2, human ~0.5d / CC ~30m)** — verification marks, overlay merge, freshness in the report
  - Surfaced by: Task 5, A5, A9, C12
  - Files: `native/kb.py`, `native/report.py`, `knowledge/README.md`, `tests/test_native_kb.py`
  - Verify: `uv run pytest tests/ -q`; `report.py` prints the knowledge line
- [x] **T6 (P2, human ~2h / CC ~20m)** — absorb the unlock table and update every reference
  - Surfaced by: A6, outside voice 8
  - Files: `knowledge/unlocks.json` (git mv), `core/capability.py`, `Main/task_menu.py`, `usecases/alliance.py`, `tests/test_alliance_capture.py`, `DIRECTORY_MAP.md`, `docs/designs/adaptive-automation.md`
  - Verify: `uv run pytest tests/ -q`; `grep -r "docs/knowledge" --include=*.py --include=*.md .` returns nothing outside `docs/superpowers/`
- [x] **T7 (P2, human ~4h / CC ~30m)** — speed-bonus reader
  - Surfaced by: A10, C7
  - Files: `native/readers/stats.py`, `native/schema.py`, `native/snapshot.py`, `references/screen-map.md`, `tests/test_native_readers.py`
  - Verify: `snapshot.py --readers hud,profile,stats --no-write` shows three percentages
- [x] **T8 (P3, human ~4h / CC ~30m)** — item catalogue from tooltips
  - Surfaced by: A11, C9
  - Files: `native/readers/backpack.py`, `native/kb.py`, `knowledge/items.json`, tests
  - Verify: a backpack run writes catalogue entries; `fold` prefers the catalogue
  - Done 2026-09-09: fixture-driven only (no live run -- optional per the
    controller brief and unnecessary once the fixtures covered the
    behaviour). `read_tooltip` widened to a 3-tuple `(name, None,
    description)`, the description band surveyed (not guessed) against the
    `backpack_tile` fixture. `classify_kind` extracted from `fold` so the
    catalogue's `kind` and the ledger's routing share one implementation.
    `native.kb.items()`/`record_item()` added; `record_item` is the second
    function in the module that writes to disk, and both "only place that
    writes to disk" claims (native/kb.py:36, :390) were corrected, along
    with a matching claim in knowledge/README.md.

Order: T1 -> T2 -> T3 (T4 and T6 parallel after T2) -> T5 -> T7 -> T8.

## E. Engineering review decisions (2026-09-08; binding, override A/B/C/D where they conflict)

**Milestones.** The plan ships as two gated units. **M1 = Tasks 1-3** (fetch, util, normalise, refresh, four required tables, calculators): the planner is unblocked here and the suite must be green before M2 starts. **M2 = Tasks 4-8** (cross-check report, unlock absorption, speed-bonus reader, item catalogue, optional tables). Each milestone gets its own eng-review pass.

**E1 (finding 1, layering).** `parse_number`, `parse_ratio` and `parse_duration` move OUT of `native/screen.py` into `knowledge/util.py`. `native/screen.py` imports and re-exports them (`from knowledge.util import parse_number, parse_ratio, parse_duration  # re-exported: native/readers/__init__.py:17 and ten readers import them from here`), so no reader changes. Amendment A4 is superseded: `knowledge/local_sources.py` imports from `knowledge.util`, never from `native.screen`, and `knowledge/` depends on nothing outside the standard library.

- **REGRESSION TEST (mandatory, iron rule).** `native/readers/__init__.py:17` reads `from native.screen import centre, norm, parse_number, parse_ratio, parse_duration` and ten reader modules use those names. After the move: `tests/test_native_screen.py` keeps its three parser table tests unchanged and gains `assert native.screen.parse_number is knowledge.util.parse_number` (same for the other two); `uv run pytest tests/ -q` must report at least 628 passing. `~/.claude/skills/wos-daily-collect/scripts/collect.py:40-41` imports no parsers, so the daily runner is untouched.

**E2 (finding 5, diagram).** In the same task, `native/screen.py`'s module docstring diagram changes from `frame() ─▶ OCR items ─▶ find()/close_control()/read_hud()/parse_*()` to name the parsers' new home:

```
    frame() ─▶ OCR items ─▶ find()/close_control()/read_hud()
                              │           parse_* live in knowledge/util.py
                              │           (re-exported here for the readers)
        guarded press ◀────── spend_label(): NEVER_RE | DANGER_RE | PRICE_RE
        positional tap ◀───── pretap_check(): OCR box around the target
```

**E3 (finding 4, absent is not zero).** `knowledge/normalise.py` gains `_int(v, required=False)`: with `required=True` an ABSENT key raises `ValueError(f"{context}: missing {key}")`; a present `0` is legal (furnace level 0 rows are genuinely zero). `buildings()` requires `meat, wood, coal, iron, seconds` on every row; `troops()` requires `meat, wood, coal, iron, seconds, points`; `research()` requires `cost` and `research-time-seconds` on every level. A dropped upstream field becomes `NORMALISE FAILED ValueError: furnace L28: missing meat`, never a cheaper plan. Tests: a fixture row with `meat` deleted raises and names the building and level; a level-0 row of zeros normalises fine.

**E4 (finding 2, the gate must not fail silently).** Task 6 gains: `test_load_table_finds_the_shipped_file` — `core.capability.load_table()` with NO argument returns a features table with at least one entry and an empty warnings list. `core/capability.py:79-100` treats a missing file as an empty table that fails every gate open, so the tmp_path tests cannot catch a half-finished move.

**E5 (finding 3, the placeholder specified).** `knowledge/unlocks_check.py` is a post-move consistency check, not a second reporter: `check_gates(table=None, tasks=None) -> list[str]` returns one line per task gate whose feature key is absent from `knowledge/unlocks.json`, reading the gate keys from the same place `Main/task_menu.py:246` does. Task 6 runs it once after the move and asserts an empty result; `scripts/capability_report.py` keeps its own job (reporting gate verdicts for a profile) and is not touched.

**E6 (finding 6, integration test).** `tests/test_knowledge_integration.py`: a fake opener serves the five real source files from `tests/fixtures/local/knowledge/sources/` (gitignored; copied from the cached fetch of 2026-09-08), `refresh.main(["--write"])` runs into a `tmp_path` knowledge dir, then `kb.load(tmp)` answers `building_cost("furnace", 27, 28)`, `research_path("tooling_up_i", 2, {})` and `training_cost("infantry", 9, 100)` with the values the unit tests pin. Skips when the fixtures are absent. This is the only test that proves `SOURCES`, `NORMALISERS` and `TABLES` agree.

**E7 (finding 7, the optional contract).** `tests/test_refresh_knowledge.py` gains three assertions: the default `main([])` fetches exactly the four required tables (assert on the fake opener's URL list); an optional table whose fetch raises prints its FAILED line and leaves the exit code 0 while a required one exits 1; `kb.load` with an optional file absent returns the required tables and prints one line naming the missing file. Plus a registry test: `set(SOURCES) | set(OPTIONAL_SOURCES) == set(NORMALISERS)` and every `TABLES` key resolves to a file one of them writes.

**E8 (finding 8, the cache is read-only).** `native/kb.py`'s module docstring states that every dict returned by `load`, `building_row`, `research_node` and `latest`-style lookups is owned by the cache and must not be mutated; consumers copy what they annotate. Test: mutate a row returned by `building_row`, then assert a fresh `load` of the same directory (after `_CACHE.clear()`) returns the original value, so a future caller that caches a mutated table fails loudly here.

### Failure modes added by these decisions

| codepath | failure mode | rescued | test | user sees | logged |
|---|---|---|---|---|---|
| parser move | a reader imports a name that moved | N/A (import error at collection) | Y (E1 regression) | pytest collection error, immediately | pytest |
| normaliser required keys | upstream drops a cost field | Y (raises) | Y (E3) | `NORMALISE FAILED ... missing meat`, exit 1 | stdout |
| unlock move | file moved, path not updated | Y (E4 test) | Y | test failure naming the default path | pytest |
| optional table | an optional source 404s | Y | Y (E7) | its FAILED line, exit code still 0 | stdout |
| registry drift | a table key exists in one registry only | Y (E6, E7) | Y | test failure before the first live refresh | pytest |
| kb cache | a consumer mutates a returned row | N (contract) | Y (E8) | test failure when the contract breaks | pytest |

No row is unrescued, untested and silent.

### Worktree parallelization

| Step | Modules touched | Depends on |
|---|---|---|
| T1 fetch, util, refresh, parser move | `knowledge/`, `scripts/`, `native/screen.py`, `tests/` | — |
| T2 normalisers, vendored tables | `knowledge/`, `tests/` | T1 |
| T3 calculators | `native/kb.py`, `tests/` | T1 (util), T2 (tables) |
| T4 cross-check, overlay | `knowledge/`, `tests/` | T2 |
| T6 unlock absorption | `core/`, `Main/`, `usecases/`, `knowledge/`, docs | T1 (util for write_table) |
| T5 marks, freshness, report | `native/kb.py`, `native/report.py` | T3 |
| T7 speed-bonus reader | `native/readers/`, `native/schema.py`, `native/snapshot.py` | — (independent of the knowledge base) |
| T8 item catalogue | `native/readers/backpack.py`, `native/kb.py` | T3 |

- **Lane A (M1):** T1 → T2 → T3, sequential; they share `knowledge/`.
- **Lane B (M2):** T6, independent of Lane A after T1 lands (it needs only `write_table`). Touches `core/`, `Main/`, `usecases/`, which no other lane touches.
- **Lane C (M2):** T7, fully independent: it touches only the readers and the schema, no knowledge module at all. It can run in parallel with M1 from the start.
- **Lane D (M2):** T4 after T2; T5 and T8 after T3.
- **Conflict flags:** Lanes A and D both write `knowledge/` and `tests/` — run D after A merges. T5 and T8 both edit `native/kb.py`; sequence them. T7 edits `native/schema.py`, which no other lane touches, so it is the safest parallel worktree.
- **Execution:** launch Lane A and Lane C in parallel worktrees now; merge A, then Lane B and Lane D.

## F. Outside-voice decisions (2026-09-08; binding, override A-E where they conflict)

Every finding below was verified against the repo or the cached source files before it was raised.

**F-a (M1 contract: real times, not base times).** The source files ship the calculator author's own buffs (`construction.json settings.constructionSpeed: 0.918`, `troops.json settings.trainingSpeed: 1.8`), which are NOT this account's, so nothing in the vendored data can supply a speed bonus. Task 7 (the stats reader) moves into **M1**, making it four tasks: T1, T2, T3, T7. `native/kb.py` gains

```python
def speed_bonus_from_sheet(sheet, kind):
    """kind is construction | research | training. The sheet stores the game's
    own percentage (128 means +128%); the calculators want a multiplier delta.
    Returns 0.0 when the stats reader has not run, and the caller is expected
    to say so rather than present a base time as an ETA."""
    pct = sheet.get(f"progress.bonus.{kind}_speed")
    return (float(pct) / 100.0) if pct is not None else 0.0
```

and `building_time`/`training_time` keep their `speed_bonus` parameter. Every consumer that prints an ETA states whether a bonus was applied. Test: `128 -> 1.28`, absent -> `0.0`. M1's contract now reads: costs, unmet prerequisites, research paths, training figures, and times that reflect the account's real buffs. Power for buildings stays `None` outside the M2 overlay (no open source carries it; verified across all 14 buildings in `construction.json`).

**F-b (calendar leaves M1).** `calendar-data.json` holds five events, every one with `available-after-age: "unknown"`, and D-T4 already makes your observed dates win for the only one that matters. The calendar source entry, its normaliser, its fixture, `next_occurrences`, the `"observed" | "table"` tuple element and C3's tests all move to **M2**, next to the events reader that would consume them. M1's required tables are **buildings, troops, research** (three, not four). `TABLES` in `native/kb.py` lists `events` as optional from the start.

**F-c (the unlock consistency check already exists).** `scripts/capability_report.py:32` imports `from Main.task_menu import TASKS` and walks every gate through `capability.evaluate`; `core/capability.py:255` already emits `feature ... missing from the knowledge base`. A new module in `knowledge/` would duplicate that AND import the task tree, which pulls `usecases` and `core.core`'s import-time `init_database()` into a package this plan declares dependency-free. Decision 3B is superseded: **no `knowledge/unlocks_check.py`**. Instead `scripts/capability_report.py` gains `--check-table`, which prints two lists and exits non-zero when either is non-empty:

- gates referenced by a task but absent from `knowledge/unlocks.json` (zero today, verified),
- features in the table that no task references (the drift that actually exists today: `alliance_mobilization`, `embassy`).

Task 6 runs `--check-table` after the move and asserts a clean first list.

**F-d (integration fixtures stay gitignored; regeneration documented).** Decision: the five raw source files live in `tests/fixtures/local/knowledge/sources/` and are NOT committed, so `tests/test_knowledge_integration.py` skips on a fresh clone the same way the screen goldens do. To keep that recoverable, `knowledge/README.md` carries the exact regeneration command and the test's skip message names it:

```bash
mkdir -p tests/fixtures/local/knowledge/sources && cd $_ && \
for u in \
  https://raw.githubusercontent.com/wosnerdwarriors/website-index/main/calculator/data/construction.json \
  https://raw.githubusercontent.com/wosnerdwarriors/website-index/main/calculator/data/troops.json \
  https://raw.githubusercontent.com/wosnerdwarriors/wos-data/main/data/research-upgrades.json \
  https://raw.githubusercontent.com/wosnerdwarriors/wos-data/main/data/troop-stats.json \
  https://raw.githubusercontent.com/wosnerdwarriors/wos-data/main/data/calendar-data.json ; \
do curl -sSLO "$u"; done
```

Accepted limitation: the only test that proves `SOURCES`, `NORMALISERS` and `TABLES` agree does not run on a clean checkout.

**F-e (two corrections to binding text).**

1. **The gate test could not fail.** `core/capability.py:62` holds a module-level `_cache` that `load_table()` short-circuits when called with no argument, and line 97 keeps only `features`. Decision 2A's test becomes:

```python
def test_load_table_finds_the_shipped_unlock_file():
    """A move that lands without the path change fails open silently
    (core/capability.py:79-112), so the tmp_path tests cannot catch it."""
    capability._reset_cache()
    table, warnings = capability.load_table()
    assert warnings == []
    assert table["features"], "the shipped knowledge base is empty or missing"
```

and A6 Step 1's `_meta` / `_schema` / `_unverified_gates` assertions read the file with a plain `json.load`, since `load_table` discards those keys.

2. **A4 is superseded in its own body.** The code block in A4 that reads `from native.screen import parse_number, parse_duration` gets the line `SUPERSEDED by E1: import from knowledge.util; knowledge/ imports nothing from native/` directly above it, so an implementer reading A4 alone does not undo the layering fix.

### Task list after the outside voice

**M1 (gated, suite green before M2):** T1 fetch/util/refresh/parser move -> T2 normalisers for buildings, troops, research -> T3 calculators incl. `speed_bonus_from_sheet` -> T7 stats reader (parallel lane from the start).

**M2:** T4 cross-check report and overlay -> T5 marks, freshness, report line -> T6 unlock absorption plus `capability_report --check-table` -> calendar table and `next_occurrences` -> optional tables -> T8 item catalogue.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 3 | CLEAR (2026-09-08) | 5 proposals, 5 accepted, 0 deferred; 2 adversarial passes, 4 cross-model tensions |
| Codex Review | `/codex review` | Independent 2nd opinion | 5 | ISSUES FOUND (Claude subagent; Codex pinned to an unusable model) | 6 problems this pass, every checkable one verified true; 5 became decisions |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAR (2026-09-08) | 14 issues: 3 architecture, 2 quality, 3 test gaps + 1 mandatory regression, 1 performance, 5 outside-voice tensions; 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | not run (no UI in this plan) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | not run |

- **CROSS-MODEL:** the outside voice contradicted this review on five points and was right on all five, each verified before it was raised: `native/screen.py` imports cv2 and the driver, so amendment A4 would have made the data layer depend on the vision layer; the vendored speed settings belong to the calculator's author, so every M1 time was a base time; `scripts/capability_report.py:32` already walks every gate, so the new check module was duplication that would have imported the task tree into a dependency-free package; the calendar table carries five rows whose only useful anchor is already overridden by observed dates; and the gate test could not fail because `core/capability.py:62` caches the no-argument call. All five resolved: stats reader pulled into M1, calendar deferred to M2, `--check-table` flag instead of a module, corrected gate test, A4 marked superseded in its own body.
- **VERDICT:** CEO + ENG CLEARED — ready to implement M1 (tasks 1, 2, 3, 7).

NO UNRESOLVED DECISIONS
