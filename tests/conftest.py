"""Shared pytest setup for the wos-bot test suite.

Must run before any test module imports core.ocr: core/ocr.py:870 calls
take_preferred_screen_capture_tool() at module scope, which prompts
interactively unless OCR_CAPTURE_TOOL is already set in the environment.
pytest imports conftest.py before collecting test modules, so setting the
env var here (module top level) is sufficient to keep the whole suite
non-interactive.
"""
import os
import sys

# tests/ has no __init__.py, so pytest's default "prepend" import mode puts
# tests/ itself on sys.path, not the repo root. Add the repo root explicitly
# so `import core...` / `import cmd_program...` resolve the same way they do
# for the app's own entry points.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("OCR_CAPTURE_TOOL", "adb")

# Burn-in instrumentation writes logs/ocr_burnin.jsonl on every read; tests
# must not pollute (or depend on) the real burn-in ledger.
os.environ.setdefault("OCR_BURNIN", "0")

# Any test that misses a mock must fail FAST and against nothing real. Port 1 is
# never a live OCR server, and a zero replay window turns the 35s retry ladder in
# core/core.py:_post_json_with_replay into a single refused connection.
#
# Without this, one unmocked read costs 35s: adding capture_alliance_state() to
# player_initialization made the suite hang past 5 minutes, and run.sh:11 already
# documents that a FOREIGN dev server on the default port answers /docs while
# 404-ing /ocr -- so a missed mock could otherwise get plausible-looking answers
# from a stranger. A test that genuinely wants a live server sets these itself.
os.environ.setdefault("OCR_PORT", "1")
os.environ.setdefault("OCR_REPLAY_WAIT_SEC", "0")

# Importing Main.main pulls core.core, whose import-time init_database()
# (core/core.py:754) opens references/*.json via relative paths — the same
# repo-root-cwd requirement every real run has. Pin the cwd so collection
# works no matter where pytest is invoked from.
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
