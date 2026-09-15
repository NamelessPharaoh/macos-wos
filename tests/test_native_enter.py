"""Screen.enter must not take a dropped entry tap for an entry.

Codex review, 2026-09-15: a ("hud", fx, fy) entry returned success as soon as
the tap was sent. When the game drops that tap the city is still on screen, and
every hop after it acts on home. Frames are real, from ~/wos-daily/2026-09-15-1023:
two city frames a few seconds apart differ by 0.77, the side panel sliding in by
11.6 (the HUD stays up, so at_home cannot tell), the Deals page by 50.5."""
import os

import cv2
import pytest

from native import screen as s

FRAMES = os.path.join(os.path.dirname(__file__), "fixtures", "local", "frames")


def _frames(*names):
    if not all(os.path.exists(os.path.join(FRAMES, n)) for n in names):
        pytest.skip("local frames not present")
    return [cv2.imread(os.path.join(FRAMES, n)) for n in names]


def _screen(tmp_path, monkeypatch, before, after_tap):
    """before: the frame until a tap lands. after_tap: list of frames, one per
    tap the game honours (None = the game dropped that tap)."""
    state = {"img": before, "taps": 0}

    def fake_tapf(fx, fy):
        n = state["taps"]
        state["taps"] += 1
        if n < len(after_tap) and after_tap[n] is not None:
            state["img"] = after_tap[n]

    sc = s.Screen(str(tmp_path), dry_run=False, engine=type("E", (), {"recognize": lambda self, img: []})())
    monkeypatch.setattr(s.drv, "shot", lambda path: None)
    monkeypatch.setattr(s.drv, "tapf", fake_tapf)
    monkeypatch.setattr(s.cv2, "imread", lambda path: state["img"])
    monkeypatch.setattr(s.time, "sleep", lambda n: None)
    monkeypatch.setattr(sc, "at_home", lambda *a, **k: True)
    return sc, state


def _log(tmp_path):
    return open(os.path.join(str(tmp_path), "run.jsonl")).read() if os.path.exists(
        os.path.join(str(tmp_path), "run.jsonl")) else ""


def test_an_entry_tap_the_game_dropped_is_tapped_again(tmp_path, monkeypatch):
    home, _, deals = _frames("home-city-a.png", "home-city-b.png", "deals-landing.png")
    sc, state = _screen(tmp_path, monkeypatch, home, [None, deals])
    assert sc.enter([("hud", 0.844, 0.245)]) is True
    assert state["taps"] == 2
    assert "enter-retry" in _log(tmp_path)


def test_an_entry_tap_that_never_takes_is_not_an_entry(tmp_path, monkeypatch):
    """City noise alone (another city frame, 0.77 apart) is not a change."""
    home, home_later = _frames("home-city-a.png", "home-city-b.png")
    sc, state = _screen(tmp_path, monkeypatch, home, [home_later, home_later])
    assert sc.enter([("hud", 0.844, 0.245)]) is False
    assert state["taps"] == 2
    assert "enter-no-effect" in _log(tmp_path)


def test_the_side_panel_counts_as_an_entry_although_the_hud_stays(tmp_path, monkeypatch):
    before, panel = _frames("home-before-side-panel.png", "side-panel-open.png")
    sc, state = _screen(tmp_path, monkeypatch, before, [panel])
    assert sc.enter([("hud", 0.11, 0.45)]) is True
    assert state["taps"] == 1


def test_a_hop_inside_a_page_may_change_nothing(tmp_path, monkeypatch):
    """Selecting the tab a panel already shows is a legitimate no-op (the quest
    panel reopens on its last tab), so hops with ensure_home=False are not checked."""
    page, = _frames("deals-landing.png")
    sc, state = _screen(tmp_path, monkeypatch, page, [])
    assert sc.enter(("hud", 0.631, 0.89), ensure_home=False) is True
    assert state["taps"] == 1
