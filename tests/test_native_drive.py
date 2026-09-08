"""native/drive.py run lock: one driver per game window, re-entrant per process."""
import json
import os
import subprocess
import sys
import time

import pytest

from native import drive as d


@pytest.fixture
def lock_path(tmp_path):
    return str(tmp_path / ".wos-run.lock")


@pytest.fixture(autouse=True)
def _release():
    yield
    d.release_run_lock()


def test_same_pid_reacquire_returns_same_handle(lock_path):
    a = d.acquire_run_lock(lock_path, owner="t")
    b = d.acquire_run_lock(lock_path, owner="t")
    assert a is b
    holder = json.load(open(lock_path))
    assert holder["pid"] == os.getpid() and holder["owner"] == "t"


def test_other_process_is_refused_with_holder(lock_path):
    code = ("import sys, time; sys.path.insert(0, %r); from native import drive as d; "
            "d.acquire_run_lock(%r, owner='holder'); print('held', flush=True); time.sleep(4)"
            % (d.REPO, lock_path))
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        with pytest.raises(d.RunLocked) as ei:
            d.acquire_run_lock(lock_path, owner="t")
        assert "owner=holder" in str(ei.value) and "since" in str(ei.value)
    finally:
        p.kill()
        p.wait()


def test_stale_lock_from_dead_pid_is_taken_over(lock_path):
    # A record whose pid is gone and no live flock: the lock is free to take.
    with open(lock_path, "w") as f:
        f.write(json.dumps({"pid": 999999, "owner": "ghost", "since": "2026-01-01T00:00:00"}))
    fh = d.acquire_run_lock(lock_path, owner="t")
    assert fh is not None
    assert json.load(open(lock_path))["pid"] == os.getpid()


def test_release_then_reacquire(lock_path):
    d.acquire_run_lock(lock_path, owner="t")
    d.release_run_lock()
    assert d._LOCK["fh"] is None
    d.acquire_run_lock(lock_path, owner="t2")
    assert json.load(open(lock_path))["owner"] == "t2"
