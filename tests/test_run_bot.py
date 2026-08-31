"""run_bot loop termination: single-account mode vs multi-email progression.

Main.main is import-safe (start_game/init_database run only under
__main__), so these tests drive run_bot directly with the module globals
that init_database would normally build, and fake everything that touches
the emulator (player_initialization, run_task, change_account).
"""
import time

import pytest

import Main.main as mm


def _setup(monkeypatch, tmp_path, accounts):
    """Install fake account state and recorders; return the recorders."""
    monkeypatch.setattr(mm, "COMPLETION_LOG_PATH", str(tmp_path / "completion_log.txt"))

    email_list = [email for email, _ in accounts]
    monkeypatch.setattr(mm, "email_list", email_list, raising=False)
    monkeypatch.setattr(mm, "player_data", accounts, raising=False)

    # One Player per email, keyed so the fake player_initialization can
    # follow change_account: it always "logs in" as the first player of
    # whichever email change_account last targeted (initially the first).
    state = {"email": email_list[0]}
    calls = {"run_task": [], "change_account": []}

    def fake_player_initialization():
        email = state["email"]
        info = dict(accounts)[email]
        player = info["player"][0]
        mm.current_player = mm.Player(player["name"], player["id"], "789", email)
        # Mirrors the real contract: the player on success, None on failure.
        # run_bot branches on this, so a double that returns nothing reads as
        # a failed init and ends the pass.
        return mm.current_player

    def fake_run_task(player_id, selected_tasks):
        calls["run_task"].append((player_id, tuple(selected_tasks)))

    def fake_change_account(next_email):
        calls["change_account"].append(next_email)
        return None  # run_bot must treat this as a loud failure

    monkeypatch.setattr(mm, "player_initialization", fake_player_initialization)
    monkeypatch.setattr(mm, "run_task", fake_run_task)
    monkeypatch.setattr(mm, "change_account", fake_change_account)
    return calls


SINGLE = [("a@x.com", {"player": [{"id": "111", "name": "One"}]})]
DOUBLE = SINGLE + [("b@x.com", {"player": [{"id": "222", "name": "Two"}]})]


def test_single_email_runs_tasks_and_exits_without_change_account(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, SINGLE)

    mm.run_bot(["mail"])  # returns instead of looping forever

    assert calls["run_task"] == [("111", ("mail",))]
    assert calls["change_account"] == []
    # mark_player_completed still writes through to the (redirected) log.
    assert "111" in (tmp_path / "completion_log.txt").read_text()


def test_single_email_all_on_cooldown_still_exits_without_change_account(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, SINGLE)
    (tmp_path / "completion_log.txt").write_text(f"111|{time.time()}\n")

    mm.run_bot(["mail"])

    # The ledger's exact hazard path: every player skipped, and the run
    # still must never reach the account-switch flow.
    assert calls["run_task"] == []
    assert calls["change_account"] == []


def test_two_emails_still_progress_to_next_email(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, tmp_path, DOUBLE)

    with pytest.raises(RuntimeError, match="Account changing error"):
        mm.run_bot(["mail"])

    assert calls["change_account"] == ["b@x.com"]


def test_failed_player_initialization_ends_pass_without_nameerror(monkeypatch):
    """A failed profile read must not take the whole run down.

    run_bot used to call player_initialization() and then dereference the
    module-level current_player regardless. When initialization bailed early
    (observed live: the avatar tap opened City Bonus, so the Chief Profile title
    never read back) that line raised NameError and killed the run.
    """
    monkeypatch.setattr(mm, "player_initialization", lambda: None)
    monkeypatch.setattr(mm, "load_completion_log", lambda: {})
    called = []
    monkeypatch.setattr(mm, "run_task", lambda *a: called.append(a))

    mm.run_bot(("mail",))          # must return, not raise

    assert called == [], "no task should run when initialization failed"
