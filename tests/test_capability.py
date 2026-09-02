"""The capability gate: what it skips, and everything it must never skip.

The gate fails open on purpose, so its dangerous failure is SILENCE — a task
that quietly stops running produces no error and no output. Most of what is
asserted here is therefore the negative case: a missing knowledge base, an
unreadable condition, a corrupt file and an unknown task must all still RUN.
"""
import json

import pytest

from core import capability
from Main import task_menu


@pytest.fixture(autouse=True)
def clean_capability_state(monkeypatch):
    """Drop the module cache around every test.

    Without this the KB-missing, KB-corrupt and KB-valid cases all see whichever
    ran first. Same hazard, same fix, as clean_engine_state in
    tests/test_engine_dispatch.py.
    """
    capability._reset_cache()
    monkeypatch.delenv(task_menu.GATE_ENV, raising=False)
    yield
    capability._reset_cache()


def _kb(tmp_path, features):
    path = tmp_path / "feature-unlocks.json"
    path.write_text(json.dumps({"features": features}))
    return str(path)


F7 = {"furnace_level": 7}
SIMPLE = {"arena_of_glory": {
    "label": "Arena of Glory",
    "conditions": [{"state_key": "furnace_level", "op": ">=", "value": 8}],
}}


# --- the one case that skips ----------------------------------------------


def test_skips_only_when_a_readable_condition_is_definitively_unmet(tmp_path):
    table, _ = capability.load_table(_kb(tmp_path, SIMPLE))
    verdict = capability.evaluate("arena_of_glory", F7, table=table)
    assert verdict.decision == capability.SKIP
    assert "furnace_level=7 >= 8" in verdict.reason
    assert verdict.source == "table"


def test_runs_when_the_condition_is_met(tmp_path):
    table, _ = capability.load_table(_kb(tmp_path, SIMPLE))
    assert capability.evaluate("arena_of_glory", {"furnace_level": 8},
                               table=table).should_run


# --- fail open: every one of these must RUN --------------------------------


@pytest.mark.parametrize("profile", [
    {},                              # no furnace key at all
    {"furnace_level": None},         # present but never confirmed
    {"furnace_level": "banana"},     # unparseable
    {"furnace_level": 0},            # out of range, so not trusted
])
def test_unreadable_account_state_runs_the_task(tmp_path, profile):
    table, _ = capability.load_table(_kb(tmp_path, SIMPLE))
    assert capability.evaluate("arena_of_glory", profile, table=table).should_run


def test_missing_knowledge_base_runs_everything_and_warns(tmp_path):
    table, warnings = capability.load_table(str(tmp_path / "nope.json"))
    assert warnings and "not found" in warnings[0]
    assert capability.evaluate("arena_of_glory", F7, table=table).should_run


def test_corrupt_knowledge_base_runs_everything_and_warns(tmp_path):
    path = tmp_path / "feature-unlocks.json"
    path.write_text("{ this is not json")
    table, warnings = capability.load_table(str(path))
    assert warnings and "not valid JSON" in warnings[0]
    assert capability.evaluate("arena_of_glory", F7, table=table).should_run


def test_sentinels_never_skip(tmp_path):
    table, _ = capability.load_table(_kb(tmp_path, SIMPLE))
    for sentinel in (capability.GATE_ALWAYS, capability.GATE_UNKNOWN):
        assert capability.evaluate(sentinel, F7, table=table).should_run


def test_unknown_or_absent_gate_runs(tmp_path):
    table, _ = capability.load_table(_kb(tmp_path, SIMPLE))
    assert capability.evaluate(None, F7, table=table).should_run
    assert capability.evaluate("no_such_feature", F7, table=table).should_run


def test_feature_with_no_conditions_runs(tmp_path):
    table, _ = capability.load_table(_kb(tmp_path, {"x": {"label": "X", "conditions": []}}))
    assert capability.evaluate("x", F7, table=table).should_run


def test_unsupported_operator_runs_rather_than_guessing(tmp_path):
    table, _ = capability.load_table(_kb(tmp_path, {"x": {
        "label": "X",
        "conditions": [{"state_key": "furnace_level", "op": "~=", "value": 8}],
    }}))
    assert capability.evaluate("x", F7, table=table).should_run


# --- composite gates -------------------------------------------------------
# Pets needs furnace AND state age; Labyrinth needs furnace AND Command Center.
# Neither second half is readable today, so the gate must decide on the half it
# can read and never treat the unreadable half as satisfied.


COMPOSITE = {"beast_cage": {
    "label": "Pets (Beast Cage)",
    "conditions": [
        {"state_key": "furnace_level", "op": ">=", "value": 18},
        {"state_key": "state_age_days", "op": ">=", "value": 60},
    ],
}}


def test_composite_skips_when_the_readable_half_is_unmet(tmp_path):
    table, _ = capability.load_table(_kb(tmp_path, COMPOSITE))
    verdict = capability.evaluate("beast_cage", F7, table=table)
    assert verdict.decision == capability.SKIP, "furnace 7 < 18 settles it alone"


def test_composite_runs_when_the_readable_half_passes_but_the_rest_is_unknown(tmp_path):
    table, _ = capability.load_table(_kb(tmp_path, COMPOSITE))
    verdict = capability.evaluate("beast_cage", {"furnace_level": 20}, table=table)
    assert verdict.should_run, "an unreadable condition must never count as met"
    assert "state_age_days" in verdict.reason, "say which half went unchecked"
    assert ("state_age_days", "unknown") in [(k, s) for k, s, _ in verdict.checked]


# --- the knowledge base and the task list must agree -----------------------


def test_every_task_declares_a_gate():
    """A task added without a gate would silently be ungated forever."""
    missing = [t.key for t in task_menu.TASKS if t.gate is None]
    assert missing == [], f"tasks with no declared gate: {missing}"


def test_every_declared_gate_resolves():
    table, warnings = capability.load_table()
    assert warnings == [], f"the shipped knowledge base must load cleanly: {warnings}"
    features = table["features"]
    unresolved = [
        (t.key, t.gate) for t in task_menu.TASKS
        if t.gate not in capability.SENTINELS and t.gate not in features
    ]
    assert unresolved == [], f"gates naming no known feature: {unresolved}"


def test_shipped_knowledge_base_entries_are_complete():
    table, _ = capability.load_table()
    required = {"label", "conditions", "confidence", "source", "last_verified"}
    for name, feature in table["features"].items():
        missing = required - set(feature)
        assert not missing, f"{name} is missing {sorted(missing)}"
        for condition in feature["conditions"]:
            assert set(condition) >= {"state_key", "op", "value"}, name
            assert condition["op"] in capability._OPS, name


# --- caching ---------------------------------------------------------------


def test_cache_is_reset_between_loads(tmp_path, monkeypatch):
    good = _kb(tmp_path, SIMPLE)
    monkeypatch.setattr(capability, "KB_PATH", good)
    first, _ = capability.load_table()
    assert first["features"]

    monkeypatch.setattr(capability, "KB_PATH", str(tmp_path / "gone.json"))
    cached, _ = capability.load_table()
    assert cached["features"], "still cached until explicitly reset"

    capability._reset_cache()
    after, warnings = capability.load_table()
    assert after["features"] == {} and warnings


# --- dispatch integration --------------------------------------------------
# The gate spans profile -> capability -> task_menu. Every other test here
# checks one piece; this checks the wiring, which is where a wrong skip would
# actually reach a user.


def _dispatch(monkeypatch, profile, was_explicit=False, gate_env=None):
    """Run the real dispatch loop with faked runners; returns the keys that ran."""
    ran = []
    monkeypatch.setattr(task_menu, "load_profile", lambda _id: profile)
    monkeypatch.setattr(task_menu.console, "print", lambda *a, **k: None)
    if gate_env is None:
        monkeypatch.delenv(task_menu.GATE_ENV, raising=False)
    else:
        monkeypatch.setenv(task_menu.GATE_ENV, gate_env)

    tasks = [
        task_menu.TaskSpec(t.key, t.title, t.description,
                           (lambda key: lambda _pid: ran.append(key))(t.key),
                           gate=t.gate)
        for t in task_menu.TASKS
    ]
    task_menu.run_selected_tasks("846646676", tasks, was_explicit=was_explicit)
    return ran


EXPECTED_SKIPS_AT_F7 = {
    "arena",            # Arena of Glory, furnace 8
    "heal",             # Infirmary, furnace 8
    "pet_treasure",     # Beast Cage, furnace 18
    "pet_exploration",  # Beast Cage, furnace 18
    "labyrinth",        # Labyrinth, furnace 19
    "life_essence",     # Daybreak Island, furnace 19
}


def test_furnace_7_skips_exactly_the_six_locked_tasks(monkeypatch):
    """Asserts the exact key set, not a count: the wrong six would also be six."""
    ran = _dispatch(monkeypatch, {"furnace_level": 7})
    skipped = {t.key for t in task_menu.TASKS} - set(ran)
    assert skipped == EXPECTED_SKIPS_AT_F7
    assert len(ran) == len(task_menu.TASKS) - len(EXPECTED_SKIPS_AT_F7)


def test_kill_switch_dispatches_everything(monkeypatch):
    ran = _dispatch(monkeypatch, {"furnace_level": 7}, gate_env="0")
    assert set(ran) == {t.key for t in task_menu.TASKS}


def test_explicit_selection_runs_a_gated_task_anyway(monkeypatch):
    """A human naming the task outranks a guess made from community data."""
    ran = _dispatch(monkeypatch, {"furnace_level": 7}, was_explicit=True)
    assert set(ran) == {t.key for t in task_menu.TASKS}


def test_unreadable_furnace_dispatches_everything(monkeypatch):
    """The whole gate hangs on one OCR'd number. If it is missing, run it all."""
    ran = _dispatch(monkeypatch, {})
    assert set(ran) == {t.key for t in task_menu.TASKS}


def test_a_crashing_task_still_lets_the_gate_run_the_rest(monkeypatch):
    """Crash isolation and gating must compose, not fight."""
    ran = []

    def boom(_pid):
        raise RuntimeError("boom")

    monkeypatch.setattr(task_menu, "load_profile", lambda _id: {"furnace_level": 7})
    monkeypatch.setattr(task_menu.console, "print", lambda *a, **k: None)
    tasks = [
        task_menu.TaskSpec("arena", "Arena", "", boom, gate="arena_of_glory"),
        task_menu.TaskSpec("mail", "Mail", "", boom, gate="UNKNOWN"),
        task_menu.TaskSpec("gather", "Gather", "",
                           lambda _pid: ran.append("gather"), gate="ALWAYS"),
    ]
    task_menu.run_selected_tasks("846646676", tasks)
    assert ran == ["gather"], "the gated task is skipped, the crash is isolated"


# --- the report ------------------------------------------------------------
# The gate fails open, so a wrong knowledge-base entry produces a silent skip
# and this report is the only thing that catches it. It is therefore the last
# component that can afford to be untested.


def test_stale_flag_fires_past_the_threshold():
    from datetime import date, timedelta

    from scripts.capability_report import STALE_AFTER_DAYS, _staleness

    fresh = (date.today() - timedelta(days=STALE_AFTER_DAYS - 1)).isoformat()
    old = (date.today() - timedelta(days=STALE_AFTER_DAYS + 1)).isoformat()

    assert _staleness({"last_verified": fresh})[0] == "ok"
    assert _staleness({"last_verified": old})[0] == "STALE"
    assert _staleness({})[0] == "NO DATE"
    assert _staleness({"last_verified": "not-a-date"})[0].startswith("BAD DATE")


def test_stale_boundary_is_inclusive_of_the_threshold_day():
    from datetime import date, timedelta

    from scripts.capability_report import STALE_AFTER_DAYS, _staleness

    exactly = (date.today() - timedelta(days=STALE_AFTER_DAYS)).isoformat()
    label, days = _staleness({"last_verified": exactly})
    assert (label, days) == ("ok", STALE_AFTER_DAYS), "stale means OLDER than N days"


def test_report_renders_verdict_gate_and_confidence(tmp_path, monkeypatch, capsys):
    from scripts import capability_report

    monkeypatch.setattr(capability, "KB_PATH", _kb(tmp_path, SIMPLE))
    capability._reset_cache()
    table, warnings = capability.load_table()
    monkeypatch.setattr(capability_report, "load_profile",
                        lambda _id: {"furnace_level": 7})

    skipped = capability_report._report_player("846646676", table, warnings)
    out = capsys.readouterr().out

    assert "SKIP arena" in out
    assert "arena_of_glory" in out, "the gate name has to be visible to audit it"
    assert "furnace_level=7" in out, "and the state that drove the decision"
    assert "arena" in skipped


def test_report_names_the_state_it_cannot_read(tmp_path, monkeypatch, capsys):
    from scripts import capability_report

    monkeypatch.setattr(capability, "KB_PATH", _kb(tmp_path, COMPOSITE))
    capability._reset_cache()
    table, warnings = capability.load_table()
    monkeypatch.setattr(capability_report, "load_profile",
                        lambda _id: {"furnace_level": 7})

    capability_report._report_player("846646676", table, warnings)
    out = capsys.readouterr().out
    assert "state_age_days" in out
    assert "fail open" in out, "the reader must know which rules are not enforced"


def test_report_surfaces_a_broken_knowledge_base(tmp_path, monkeypatch, capsys):
    from scripts import capability_report

    capability._reset_cache()
    table, warnings = capability.load_table(str(tmp_path / "missing.json"))
    monkeypatch.setattr(capability_report, "load_profile",
                        lambda _id: {"furnace_level": 7})

    skipped = capability_report._report_player("846646676", table, warnings)
    out = capsys.readouterr().out
    assert "WARNING" in out and "not found" in out
    assert skipped == [], "a broken knowledge base gates nothing"
