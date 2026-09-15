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


def _screen(tmp_path, monkeypatch, before, after_tap, homes=None, go_home_ok=True):
    """before: the frame until a tap lands. after_tap[n]: what the n-th tap does
    -- None (the game dropped it) or a list of frames shown on the captures
    that follow, the last one staying. homes: frames at_home accepts (default:
    `before` only; the side panel keeps the HUD, so pass it in when needed)."""
    state = {"queue": [before], "taps": 0, "tap_imgs": []}
    homes = [before] if homes is None else homes

    def capture(path):
        img = state["queue"][0]
        if len(state["queue"]) > 1:
            state["queue"].pop(0)
        return img

    sc = s.Screen(str(tmp_path), dry_run=False, engine=type("E", (), {"recognize": lambda self, img: []})())

    def fake_tapf(fx, fy, items=None, img=None):
        state["tap_imgs"].append(img)
        n = state["taps"]
        state["taps"] += 1
        if n < len(after_tap) and after_tap[n] is not None:
            state["queue"] = list(after_tap[n])
        return True

    monkeypatch.setattr(s.drv, "shot", lambda path: None)
    monkeypatch.setattr(s.cv2, "imread", capture)
    monkeypatch.setattr(s.time, "sleep", lambda n: None)
    monkeypatch.setattr(sc, "tapf", fake_tapf)
    monkeypatch.setattr(sc, "at_home", lambda items, h, w=None, img=None: any(img is f for f in homes))
    monkeypatch.setattr(sc, "go_home", lambda max_steps=9: go_home_ok)
    return sc, state


def _log(tmp_path):
    p = os.path.join(str(tmp_path), "run.jsonl")
    return open(p).read() if os.path.exists(p) else ""


def test_an_entry_tap_the_game_dropped_is_tapped_again(tmp_path, monkeypatch):
    home, home_later, deals = _frames("home-city-a.png", "home-city-b.png", "deals-landing.png")
    sc, state = _screen(tmp_path, monkeypatch, home, [None, [deals]], homes=[home, home_later])
    assert sc.enter([("hud", 0.844, 0.245)]) is True
    assert state["taps"] == 2
    assert "enter-retry" in _log(tmp_path)


def test_an_entry_tap_that_never_takes_is_not_an_entry(tmp_path, monkeypatch):
    """City noise alone (another city frame, 0.77 apart) is not a change."""
    home, home_later = _frames("home-city-a.png", "home-city-b.png")
    sc, state = _screen(tmp_path, monkeypatch, home, [[home_later], [home_later]], homes=[home, home_later])
    assert sc.enter([("hud", 0.844, 0.245)]) is False
    assert state["taps"] == 2
    assert "enter-no-effect" in _log(tmp_path)


def test_the_side_panel_counts_as_an_entry_although_the_hud_stays(tmp_path, monkeypatch):
    before, panel = _frames("home-before-side-panel.png", "side-panel-open.png")
    sc, state = _screen(tmp_path, monkeypatch, before, [[panel]], homes=[before, panel])
    assert sc.enter([("hud", 0.11, 0.45)]) is True
    assert state["taps"] == 1


def test_a_page_that_opens_slowly_is_not_tapped_a_second_time(tmp_path, monkeypatch):
    """Review, 2026-09-15: the check ran once, 2.5 s after the tap. A page still
    opening then looked like home, and the retry landed on the page -- on the
    cart that spot is the gem counter, the gem store's shortcut. Look again
    before tapping again."""
    home, home_later, deals = _frames("home-city-a.png", "home-city-b.png", "deals-landing.png")
    sc, state = _screen(tmp_path, monkeypatch, home, [[home_later, deals]], homes=[home, home_later])
    assert sc.enter([("hud", 0.886, 0.086)]) is True
    assert state["taps"] == 1


def test_the_retry_is_guarded_by_the_frame_on_screen_not_the_one_before_the_tap(tmp_path, monkeypatch):
    """The retry's pre-tap spend check must read what is on screen now."""
    home, home_later, deals = _frames("home-city-a.png", "home-city-b.png", "deals-landing.png")
    sc, state = _screen(tmp_path, monkeypatch, home, [[home_later], [deals]], homes=[home, home_later])
    assert sc.enter([("hud", 0.844, 0.245)]) is True
    assert state["tap_imgs"][0] is home and state["tap_imgs"][1] is home_later


def test_no_entry_tap_is_made_when_going_home_failed(tmp_path, monkeypatch):
    """Review, 2026-09-15: enter() ignored go_home's result, so after home-failed
    the entry taps landed on whatever screen was left open."""
    _, _, deals = _frames("home-city-a.png", "home-city-b.png", "deals-landing.png")
    sc, state = _screen(tmp_path, monkeypatch, deals, [], homes=[], go_home_ok=False)
    assert sc.enter([("hud", 0.844, 0.245)]) is False
    assert state["taps"] == 0


def test_a_hop_inside_a_page_may_change_nothing(tmp_path, monkeypatch):
    """Selecting the tab a panel already shows is a legitimate no-op (the quest
    panel reopens on its last tab), so hops with ensure_home=False are not checked."""
    page, = _frames("deals-landing.png")
    sc, state = _screen(tmp_path, monkeypatch, page, [], homes=[])
    assert sc.enter(("hud", 0.631, 0.89), ensure_home=False) is True
    assert state["taps"] == 1
