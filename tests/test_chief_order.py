"""chief_order currency parse + task isolation regressions.

The first live exercise of chief_order crashed the whole run: a plain
non-M currency ("1,423") stayed a str through the parse and the
`currency > value` comparison raised TypeError, which task_menu did not
contain. Both regressions are pinned here.
"""
import pytest

import usecases.chief_order as co
import Main.task_menu as tm


def _run_with_currency(monkeypatch, text):
    taps = []
    monkeypatch.setattr(co, "recalibrate", lambda: None)
    monkeypatch.setattr(co.time, "sleep", lambda s: None)
    monkeypatch.setattr(co, "tap_on_template", lambda *a, **k: True)
    monkeypatch.setattr(co, "tap_on_text", lambda t, **k: taps.append(t) or False)
    monkeypatch.setattr(co, "req_text", lambda *a, **k: [[text, [0, 0, 1, 1]]])
    assert co.activate_chief_order() is True
    return taps


class TestCurrencyParse:
    def test_plain_value_does_not_crash_and_skips_unaffordable(self, monkeypatch):
        # The live crash: "1,423" stayed a str -> TypeError at `currency > value`.
        taps = _run_with_currency(monkeypatch, "1,423")
        assert taps == []  # 1423 < every order cost

    def test_m_suffix_enables_orders(self, monkeypatch):
        taps = _run_with_currency(monkeypatch, "12M")
        assert any("UrgentMobilization" in t for t in taps)

    def test_k_suffix_parses(self, monkeypatch):
        taps = _run_with_currency(monkeypatch, "60K")
        assert any("UrgentMobilization" in t for t in taps)   # 60000 > 50000
        assert not any("RushJob" in t for t in taps)          # 60000 < 150000

    def test_garbage_read_treated_as_zero(self, monkeypatch):
        assert _run_with_currency(monkeypatch, "??~") == []

    def test_empty_read_treated_as_zero(self, monkeypatch):
        taps = []
        monkeypatch.setattr(co, "recalibrate", lambda: None)
        monkeypatch.setattr(co.time, "sleep", lambda s: None)
        monkeypatch.setattr(co, "tap_on_template", lambda *a, **k: True)
        monkeypatch.setattr(co, "tap_on_text", lambda t, **k: taps.append(t) or False)
        monkeypatch.setattr(co, "req_text", lambda *a, **k: [])
        assert co.activate_chief_order() is True
        assert taps == []


class TestTaskIsolation:
    def test_crashing_task_does_not_kill_the_run(self, monkeypatch):
        ran = []
        bad = tm.TaskSpec("bad", "Bad", "boom", lambda pid: (_ for _ in ()).throw(RuntimeError("boom")))
        good = tm.TaskSpec("good", "Good", "fine", lambda pid: ran.append(pid))
        tm.run_selected_tasks("p1", [bad, good])
        assert ran == ["p1"]  # the run survived the crash and reached the next task
