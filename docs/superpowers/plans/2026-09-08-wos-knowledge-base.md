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
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


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
    assert c["meat"] == 190_000_000 and c["iron"] == 9_900_000 and c["fire_crystals"] == 0
    assert kb.building_cost("furnace", 27, 27, kb=k) == {}
    assert kb.building_time("furnace", 27, 28, kb=k) == 2_522_820
    assert kb.building_time("furnace", 27, 28, speed_bonus=1.0, kb=k) == 1_261_410
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

And the fixture directory `tests/fixtures/knowledge/kbdir/` with five files built from the excerpts of Task 2 plus furnace level 28 (`meat 190000000, wood 190000000, coal 39000000, iron 9900000, seconds 2522820, prerequisites {"embassy": 27, "research_center": 27, ...same others as 27}`): generate them once with
```bash
uv run python - <<'EOF'
import json, os
from knowledge import normalise as n
FIX = "tests/fixtures/knowledge"; OUT = os.path.join(FIX, "kbdir"); os.makedirs(OUT, exist_ok=True)
raw = json.load(open(f"{FIX}/construction_excerpt.json"))
r27 = next(l for l in raw["buildingLevels"]["Furnace"] if l["level"] == 27)
raw["buildingLevels"]["Furnace"].append({**r27, "level": 28, "meat": 190000000, "wood": 190000000, "coal": 39000000, "iron": 9900000, "seconds": 2522820,
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
var a=1;let b={name:"Furnace",levels:[{level:27,meat:14e7,wood:14e7,coal:24e6,iron:74e5,fireCrystal:0,refined:0,time:2187780},{level:28,meat:19e7,wood:19e7,coal:39e6,iron:99e5,fireCrystal:0,refined:0,time:2522820}]},c={name:"Embassy",levels:[{level:1,meat:0,wood:0,coal:0,iron:0,fireCrystal:0,refined:0,time:0}]};export{b,c};
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
    assert doc["buildings"]["furnace"]["28"]["meat"] == 190_000_000 and doc["buildings"]["furnace"]["28"]["seconds"] == 2_522_820
    assert "embassy" in doc["buildings"]
    assert ls.wostools_buildings("var x = 1;") == {}


def test_crosscheck_marks_disputed_and_appends_fc_rows():
    committed = {"furnace": {"28": {"meat": 190_000_000, "wood": 190_000_000, "coal": 39_000_000, "iron": 9_900_000,
                                    "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_522_820,
                                    "prerequisites": {}, "verified_in_game": None}}}
    wd = {"furnace": {"28": {"label": "28", "meat": 190_000_000, "wood": 190_000_000, "coal": 40_000_000, "iron": 9_900_000,
                             "fire_crystals": 0, "refined_fire_crystals": 0, "seconds": 2_522_820, "power": 1_213_100, "prerequisites": {}},
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
from native.kb import furnace_ordinal

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
    from scripts.refresh_knowledge import fetch_text
    return "whiteoutdata-furnace.json", whiteoutdata_furnace(fetch_text(WHITEOUTDATA_FURNACE, opener))


def _fetch_wiki(opener):
    from scripts.refresh_knowledge import fetch_text
    return "wiki-furnace.json", wiki_furnace(fetch_text(WIKI_FURNACE, opener))


def _fetch_wostools(opener):
    from scripts.refresh_knowledge import fetch_text
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
    from scripts.refresh_knowledge import write_table
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

## Self-review

- **Spec coverage.** Sources and licence note: Task 1-2. Layout and `_meta`: Task 1-2. Local-only cross-checks with `disputed` and FC rows: Task 4. `refresh_knowledge.py` diff-before-write and `--local`: Task 1, 4. Calculators listed in the spec (`building_cost`, `building_time`, `prerequisites`, `research_path`, `training_cost`, `training_time`, `power_gain`, `days_to`, `verify`): Task 3. `verified_in_game` marking: Task 5. Tests per spec (schema, known values, prerequisite resolution, refresh diff, missing `knowledge/local/`): Tasks 1-4 (`load_table` returns `{}` for a missing local file, exercised by `--crosscheck` with no local docs).
- **Placeholders.** None; the `_prereqs` scaffold loop is explicitly removed in Task 4 step 4.
- **Type consistency.** `Row` keys (`meat, wood, coal, iron, fire_crystals, refined_fire_crystals, seconds, prerequisites, verified_in_game`) are the same in Task 2 normaliser, Task 3 calculators and Task 4 cross-check; `furnace_ordinal` defined in Task 3 is imported by Task 4; `write_table` from Task 1 is reused by Task 5; `ResearchStep` fields are `node level cost seconds` throughout.
