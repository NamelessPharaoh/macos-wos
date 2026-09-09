"""scripts/refresh_knowledge.py: diff-before-write, provenance, per-table
failure isolation (A2) and the required/optional split (R3, F-b, E7)."""
import json
import os
import subprocess
import sys

import pytest

import scripts.refresh_knowledge as rk


class FakeResp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_sources_are_the_two_wosnerds_repos():
    repos = {s.repo for s in rk.SOURCES.values()}
    assert repos == {"wosnerdwarriors/website-index", "wosnerdwarriors/wos-data"}
    assert rk.SOURCES["buildings"].url.endswith("website-index/main/calculator/data/construction.json")
    assert rk.SOURCES["research"].url.endswith("wos-data/main/data/research-upgrades.json")


def test_required_sources_are_exactly_four():
    """R3: buildings, troops, troop_stats, research are required; calendar
    (and later the hero/gear/pet tables) is optional and left out of the
    default run."""
    assert set(rk.SOURCES) == {"buildings", "troops", "troop_stats", "research"}
    assert "calendar" in rk.OPTIONAL_SOURCES
    assert "calendar" not in rk.SOURCES


def test_registry_shape_holds_now_and_after_task2():
    """E7: the assertion ships in Task 1 (NORMALISERS is still empty, so it
    is a no-op here) and starts enforcing once Task 2 registers a normaliser
    for every required and optional table."""
    if rk.NORMALISERS:
        assert set(rk.SOURCES) | set(rk.OPTIONAL_SOURCES) == set(rk.NORMALISERS)


def test_meta_carries_provenance():
    m = rk.meta(rk.SOURCES["buildings"], "f1defd3d7d80", "2026-09-08T12:00:00Z")
    assert m["source_url"] == rk.SOURCES["buildings"].url
    assert m["source_commit"] == "f1defd3d7d80"
    assert m["fetched_at"] == "2026-09-08T12:00:00Z"
    assert "free to copy and use" in m["licence"] and "revocable" in m["licence"]
    assert m["normaliser_version"] == rk.NORMALISER_VERSION


def test_source_commit_returns_unknown_on_a_403(monkeypatch, capsys):
    import urllib.error
    from knowledge.fetch import FetchError

    def boom(url, opener=None):
        raise FetchError(f"{url}: HTTPError 403")

    monkeypatch.setattr(rk, "fetch_json", boom)
    assert rk.source_commit("wosnerdwarriors/wos-data") == "unknown"
    assert "recording 'unknown'" in capsys.readouterr().out


# ----------------------------------------------------------------------------- diff_rows (C1)
def test_diff_rows_first_fetch_adds_one_line_per_level():
    old = {"_meta": {"a": 1}}
    new = {"_meta": {"a": 2}, "buildings": {"furnace": {"27": {"meat": 1}, "28": {"meat": 2}}}}
    lines = rk.diff_rows(old, new)
    assert lines == ["buildings.furnace.27: added", "buildings.furnace.28: added"]


def test_diff_rows_reports_a_changed_cost():
    old = {"buildings": {"furnace": {"27": {"meat": 1}, "28": {"meat": 2}}}}
    new = {"buildings": {"furnace": {"27": {"meat": 1}, "28": {"meat": 3}, "29": {"meat": 4}}}}
    lines = rk.diff_rows(old, new)
    assert "buildings.furnace.28: meat 2 -> 3" in lines
    assert "buildings.furnace.29: added" in lines


def test_diff_rows_reports_a_nested_research_level_change():
    old = {"research": {"tooling_up_i": {"tree": "growth", "levels": {"2": {"seconds": 40}}}}}
    new = {"research": {"tooling_up_i": {"tree": "growth", "levels": {"2": {"seconds": 41}}}}}
    assert rk.diff_rows(old, new) == ["research.tooling_up_i.levels.2: seconds 40 -> 41"]


def test_diff_rows_reports_a_removed_table():
    old = {"gone": {"x": {"1": {"v": 1}}}}
    new = {}
    assert rk.diff_rows(old, new) == ["gone.x.1: removed"]


def test_diff_rows_ignores_meta_and_is_empty_for_equal_docs():
    old = {"_meta": {"fetched_at": "a"}, "buildings": {"furnace": {"27": {"meat": 1}}}}
    new = {"_meta": {"fetched_at": "b"}, "buildings": {"furnace": {"27": {"meat": 1}}}}
    assert rk.diff_rows(old, new) == []
    assert rk.diff_rows(new, new) == []


# ----------------------------------------------------------------------------- carry_marks (B4)
def test_carry_marks_keeps_the_mark_when_costs_are_unchanged():
    old = {"buildings": {"furnace": {"28": {"meat": 2, "verified_in_game": "s1"}}}}
    new = {"buildings": {"furnace": {"28": {"meat": 2}}}}
    doc = rk.carry_marks(old, new)
    assert doc["buildings"]["furnace"]["28"]["verified_in_game"] == "s1"


def test_carry_marks_clears_the_mark_when_a_cost_changed():
    old = {"buildings": {"furnace": {"28": {"meat": 2, "verified_in_game": "s1"}}}}
    new = {"buildings": {"furnace": {"28": {"meat": 3}}}}
    doc = rk.carry_marks(old, new)
    assert doc["buildings"]["furnace"]["28"]["verified_in_game"] is None
    assert "buildings.furnace.28: meat 2 -> 3" in rk.diff_rows(old, doc)


def test_carry_marks_recurses_into_research_levels():
    old = {"research": {"tooling_up_i": {"levels": {"2": {"seconds": 40, "verified_in_game": "s1"}}}}}
    new = {"research": {"tooling_up_i": {"levels": {"2": {"seconds": 40}}}}}
    doc = rk.carry_marks(old, new)
    assert doc["research"]["tooling_up_i"]["levels"]["2"]["verified_in_game"] == "s1"


def test_carry_marks_no_op_on_a_first_fetch():
    assert rk.carry_marks({}, {"buildings": {"furnace": {"27": {"meat": 1}}}}) == {
        "buildings": {"furnace": {"27": {"meat": 1}}}}


# ----------------------------------------------------------------------------- write_table (moved to knowledge.util, B9)
def test_write_table_is_imported_from_knowledge_util():
    from knowledge.util import write_table
    assert rk.write_table is write_table


def test_fetch_text_stays_a_patchable_module_level_name():
    """R2: the refresh script imports `fetch_json, fetch_text, FetchError`
    from knowledge.fetch so rk.fetch_json (and rk.fetch_text) stay valid
    monkeypatch.setattr(rk, ...) targets even though this module only calls
    fetch_json directly today."""
    from knowledge.fetch import fetch_text
    assert rk.fetch_text is fetch_text


# ----------------------------------------------------------------------------- main / refresh
def test_script_runs_as_a_command_from_the_repo_root():
    """tests/conftest.py puts the repo root on sys.path, so an in-process
    import cannot catch a broken script entry point. The documented command
    is `uv run python scripts/refresh_knowledge.py`, which resolves imports
    from scripts/, not the repo root -- a bare `from knowledge.fetch import
    ...` at module scope raises ModuleNotFoundError there without the
    sys.path.insert(0, REPO) A1 requires above those imports."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, os.path.join(repo, "scripts", "refresh_knowledge.py"), "--help"],
        capture_output=True, text=True, cwd=repo, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--write" in result.stdout


def test_script_runs_as_a_command_from_a_different_cwd(tmp_path):
    """A1's wording ('so the script also runs from another cwd') is about
    the invocation directory, not just the repo root -- run it from tmp_path
    to prove sys.path.insert(0, REPO) does not depend on cwd."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, os.path.join(repo, "scripts", "refresh_knowledge.py"), "--help"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--write" in result.stdout


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


def test_default_run_fetches_exactly_the_required_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(rk, "NORMALISERS", {t: (lambda raw: {}) for t in rk.SOURCES})
    monkeypatch.setattr(rk, "source_commit", lambda repo, opener=None: "abc123")
    urls = []

    def fake_fetch_json(url, opener=None):
        urls.append(url)
        return {}

    monkeypatch.setattr(rk, "fetch_json", fake_fetch_json)
    rk.main([])
    assert set(urls) == {s.url for s in rk.SOURCES.values()}
    assert rk.OPTIONAL_SOURCES["calendar"].url not in urls


def test_one_table_fetch_failure_does_not_stop_the_others(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(rk, "NORMALISERS", {t: (lambda raw: {}) for t in rk.SOURCES})
    monkeypatch.setattr(rk, "source_commit", lambda repo, opener=None: "abc123")
    from knowledge.fetch import FetchError

    def flaky(url, opener=None):
        if url == rk.SOURCES["buildings"].url:
            raise FetchError(f"{url}: boom")
        return {}

    monkeypatch.setattr(rk, "fetch_json", flaky)
    with pytest.raises(SystemExit):
        rk.main([])
    out = capsys.readouterr().out
    assert "buildings: FETCH FAILED" in out
    assert "== troops:" in out and "== troop_stats:" in out and "== research:" in out


def test_normalise_failure_prints_a_line_and_fails_that_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))

    def bad_normaliser(raw):
        raise KeyError("buildingLevels")

    monkeypatch.setattr(rk, "NORMALISERS", {"buildings": bad_normaliser})
    monkeypatch.setattr(rk, "fetch_json", lambda url, opener=None: {})
    monkeypatch.setattr(rk, "source_commit", lambda repo, opener=None: "abc123")
    with pytest.raises(SystemExit):
        rk.main(["--table", "buildings"])
    out = capsys.readouterr().out
    assert "buildings: NORMALISE FAILED KeyError" in out


def test_normalise_attribute_error_is_caught_too(tmp_path, monkeypatch, capsys):
    """An upstream dict-to-list or dict-to-null reshape (e.g. a level's
    `cost` becoming None) raises AttributeError from `.items()`/`.get()`
    calls in knowledge/normalise.py, not KeyError/TypeError/ValueError --
    it must be rescued by the same per-table guard, not end the run."""
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))

    def bad_normaliser(raw):
        raise AttributeError("'NoneType' object has no attribute 'items'")

    monkeypatch.setattr(rk, "NORMALISERS", {"buildings": bad_normaliser})
    monkeypatch.setattr(rk, "fetch_json", lambda url, opener=None: {})
    monkeypatch.setattr(rk, "source_commit", lambda repo, opener=None: "abc123")
    with pytest.raises(SystemExit):
        rk.main(["--table", "buildings"])
    out = capsys.readouterr().out
    assert "buildings: NORMALISE FAILED AttributeError" in out


def test_required_table_failure_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(rk, "NORMALISERS", {"buildings": lambda raw: {}})
    from knowledge.fetch import FetchError

    monkeypatch.setattr(rk, "fetch_json", lambda url, opener=None: (_ for _ in ()).throw(FetchError("boom")))
    with pytest.raises(SystemExit) as exc:
        rk.main(["--table", "buildings"])
    assert exc.value.code != 0


def test_optional_table_failure_leaves_the_exit_code_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(rk, "NORMALISERS", {"calendar": lambda raw: {}})
    from knowledge.fetch import FetchError

    monkeypatch.setattr(rk, "fetch_json", lambda url, opener=None: (_ for _ in ()).throw(FetchError("boom")))
    rk.main(["--table", "calendar"])  # must not raise SystemExit
    assert "calendar: FETCH FAILED" in capsys.readouterr().out


def test_unknown_table_name_exits_before_fetching(tmp_path, monkeypatch):
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        rk.main(["--table", "not_a_real_table"])


def test_crosscheck_without_local_dir(tmp_path, monkeypatch, capsys):
    """B10: --crosscheck with no knowledge/local/ prints 0 finding(s) and
    writes nothing -- this is what a fresh clone (or a run before --local)
    sees."""
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(rk, "LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setattr(rk, "NORMALISERS", {t: (lambda raw: {}) for t in rk.SOURCES})
    monkeypatch.setattr(rk, "source_commit", lambda repo, opener=None: "abc123")
    monkeypatch.setattr(rk, "fetch_json", lambda url, opener=None: {})
    (tmp_path / "buildings.json").write_text(json.dumps({"buildings": {"furnace": {}}}))
    rk.main(["--crosscheck"])
    out = capsys.readouterr().out
    assert "crosscheck: 0 finding(s)" in out
    assert not (tmp_path / "local").exists()


def test_crosscheck_write_never_touches_buildings_json(tmp_path, monkeypatch):
    """A5, the constraint that must not regress: --crosscheck --write writes
    ONLY knowledge/local/overlay.json; knowledge/buildings.json is left
    byte-for-byte unmodified. SOURCES is emptied so the ordinary per-table
    refresh loop (which --crosscheck runs alongside, unrelated to this test)
    has nothing to fetch or write, isolating the guarantee under test."""
    monkeypatch.setattr(rk, "KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr(rk, "LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setattr(rk, "SOURCES", {})
    committed = {"buildings": {"furnace": {"28": {"meat": 1, "wood": 1, "coal": 1, "iron": 1, "seconds": 1,
                                                   "fire_crystals": 0, "refined_fire_crystals": 0,
                                                   "prerequisites": {}, "verified_in_game": None}}}}
    (tmp_path / "buildings.json").write_text(json.dumps(committed))
    before = (tmp_path / "buildings.json").read_text()
    os.makedirs(tmp_path / "local")
    (tmp_path / "local" / "whiteoutdata-furnace.json").write_text(json.dumps(
        {"furnace": {"28": {"label": "28", "meat": 1, "wood": 1, "coal": 2, "iron": 1, "seconds": 1,
                             "fire_crystals": 0, "refined_fire_crystals": 0, "power": 999, "prerequisites": {}}}}))
    rk.main(["--crosscheck", "--write"])
    assert (tmp_path / "buildings.json").read_text() == before
    overlay = json.loads((tmp_path / "local" / "overlay.json").read_text())
    assert overlay["buildings"]["furnace"]["28"]["power"] == 999
    assert overlay["buildings"]["furnace"]["28"]["disputed"] == {"whiteoutdata": {"coal": 2}}
