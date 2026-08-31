"""Pet task reads: the digit guard, and the NameError that shipped inside it.

`_read_remaining_attempts` exists because the Pet task once handed int() a mail
subject -- a mis-tapped icon opened Mail, the task never checked it had arrived,
and int('Gathering Income Report') took the run down. The guard that fixed that
called re.sub() in a module that never imported re, so the fix crashed on its own
first line and no test covered it.
"""
import pytest

import usecases.pet as pet


class TestReadRemainingAttempts:
    def _arm(self, monkeypatch, value):
        monkeypatch.setattr(pet, "req_text", lambda *a, **k: value)

    def test_a_plain_count_reads_back(self, monkeypatch):
        self._arm(monkeypatch, [["3", [0, 0, 1, 1]]])
        assert pet._read_remaining_attempts() == 3

    def test_a_fraction_yields_the_first_number_not_both(self, monkeypatch):
        # Stripping every non-digit turned '3/3' into 33 attempts out of thin air.
        self._arm(monkeypatch, [["3/3", [0, 0, 1, 1]]])
        assert pet._read_remaining_attempts() == 3

    def test_a_labelled_count_reads_the_number(self, monkeypatch):
        self._arm(monkeypatch, [["Remaining attempts today: 4", [0, 0, 1, 1]]])
        assert pet._read_remaining_attempts() == 4

    def test_surrounding_punctuation_is_stripped(self, monkeypatch):
        self._arm(monkeypatch, [[" 2 ", [0, 0, 1, 1]]])
        assert pet._read_remaining_attempts() == 2

    def test_a_mail_subject_is_refused(self, monkeypatch, capsys):
        # The original crash: int('Gathering Income Report').
        self._arm(monkeypatch, [["Gathering Income Report", [0, 0, 1, 1]]])
        assert pet._read_remaining_attempts() is None
        assert "not a number" in capsys.readouterr().out

    def test_an_empty_read_is_none(self, monkeypatch):
        self._arm(monkeypatch, [])
        assert pet._read_remaining_attempts() is None
        self._arm(monkeypatch, None)
        assert pet._read_remaining_attempts() is None

    def test_the_guard_does_not_raise_nameerror(self, monkeypatch):
        # REGRESSION: re.sub() was called in a module that never imported re, so
        # the guard raised NameError on its first non-empty read. It shipped to
        # origin/main because no test exercised this function at all.
        self._arm(monkeypatch, [["Gathering Income Report", [0, 0, 1, 1]]])
        try:
            pet._read_remaining_attempts()
        except NameError as e:
            pytest.fail(f"digit guard is not importable: {e}")

    def test_re_is_actually_bound_in_the_module(self):
        assert getattr(pet, "re", None) is not None, "usecases.pet must import re"


class TestCountAdventuring:
    def test_counts_only_hh_mm_ss_timers(self, monkeypatch):
        monkeypatch.setattr(pet, "req_text", lambda *a, **k: [
            ["01:23:45", [0, 0, 1, 1]],
            ["00:05:00", [0, 0, 1, 1]],
            ["Adventure", [0, 0, 1, 1]],
            ["12:30", [0, 0, 1, 1]],
        ])
        assert pet._count_adventuring() == 2

    def test_no_timers_is_zero(self, monkeypatch):
        monkeypatch.setattr(pet, "req_text", lambda *a, **k: [])
        assert pet._count_adventuring() == 0

    def test_a_failed_read_is_zero_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(pet, "req_text", lambda *a, **k: None)
        assert pet._count_adventuring() == 0
