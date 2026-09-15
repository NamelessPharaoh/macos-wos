"""wos-daily-collect's checklist and custom flows, driven against a fake game.

collect.py lives in the skill directory, not this repo, so these tests skip
when the skill is not installed. Every fake below mirrors a real frame from
~/wos-daily/2026-09-15-1023."""
import os
import sys

import numpy as np
import pytest

from native import screen as s

SKILL = os.path.expanduser("~/.claude/skills/wos-daily-collect/scripts")
if not os.path.exists(os.path.join(SKILL, "collect.py")):
    pytest.skip("wos-daily-collect skill not installed", allow_module_level=True)
sys.path.insert(0, SKILL)
import collect  # noqa: E402
import collect_tech  # noqa: E402

H, W = 1902, 1284


def _item(text, x1, y1, x2, y2):
    return {"text": text, "score": 0.9, "box": [x1, y1, x2, y2]}


def _checklist(key):
    return next(i for i in collect.CHECKLIST if i["key"] == key)


def _quiet(monkeypatch, render=lambda: np.zeros((H, W, 3), dtype=np.uint8)):
    monkeypatch.setattr(s.drv, "shot", lambda path: None)
    monkeypatch.setattr(s.cv2, "imread", lambda path: render())
    monkeypatch.setattr(s.time, "sleep", lambda n: None)


def _log(tmp_path, event=None):
    import json
    rows = [json.loads(line) for line in open(os.path.join(str(tmp_path), "run.jsonl"))]
    return [r for r in rows if event is None or r.get("event") == event]


# ----------------------------------------------------------------------------- monthly card
# The cart strip as it scrolled on 2026-09-15. The SELECTED tab is drawn
# icon-only, so OCR reads no caption where it sits.
CART = ["Speedy Development Pack", "Daily Deals", "Weekly/Monthly Cards", "Regular Pack",
        "Dawn Fund", "Get Gems", "Tundra Supply Station"]


def _cart(monkeypatch, tmp_path, selected, title):
    tabs = [None if t == selected else t for t in CART]
    pos, swipes = {"i": 1}, []

    class Eng:
        def recognize(self, img):
            row = [_item(t, 100 + 300 * n, 230, 350 + 300 * n, 270)
                   for n, t in enumerate(tabs[pos["i"]:pos["i"] + 3]) if t]
            return row + [_item(title, 180, 380, 840, 440)]

    def fake_swipe(fx0, fy0, fx1, fy1, ms=450):
        swipes.append(fx1 < fx0)
        pos["i"] = max(0, min(pos["i"] + (1 if fx1 < fx0 else -1), len(tabs) - 3))

    sc = collect.Collector(str(tmp_path), dry_run=False, engine=Eng())
    _quiet(monkeypatch)
    monkeypatch.setattr(s.drv, "swipef", fake_swipe)
    monkeypatch.setattr(sc, "tap_item", lambda img, it: True)
    return sc, swipes


def test_monthly_card_hop_resolves_when_the_cart_opens_on_that_tab(tmp_path, monkeypatch):
    """2026-09-15: the cart opened with Weekly/Monthly Cards already selected, so
    its caption was not on screen and the page title "Ultra Value Monthly Card"
    matched neither wanted label. The walk crossed the strip twice, reported
    entry-not-found, and the daily claim sat there with its red dot lit."""
    sc, swipes = _cart(monkeypatch, tmp_path, selected="Weekly/Monthly Cards", title="Ultra Value Monthly Card")
    assert sc.enter(_checklist("cart_monthly_card_login")["sub"], ensure_home=False) is True
    assert swipes == [], "the page is already open: recognise it on the first frame"


def test_monthly_card_hop_still_taps_the_tab_when_another_is_selected(tmp_path, monkeypatch):
    """The ordinary case must keep working: the cart is on Daily Deals and the
    Weekly/Monthly Cards caption is on the strip, so the hop taps that tab."""
    sc, _ = _cart(monkeypatch, tmp_path, selected="Daily Deals", title="Daily Deals")
    assert sc.enter(_checklist("cart_monthly_card_login")["sub"], ensure_home=False) is True
    assert _log(tmp_path, "enter")[0]["label"] == "strip:Weekly/Monthly Cards"


# ----------------------------------------------------------------------------- alliance tech
TABS = [("Growth", 0.245, 0.248), ("Territory", 0.499, 0.25), ("Battle", 0.755, 0.242)]


def _tech(monkeypatch, tmp_path, pages, start, badge=None, drop_tab_taps=(), dialog_on_swipe=None,
          dialog_ocr=True, banner=False):
    """A fake Tech page. pages/start: per-tab page count and the page each tab
    opens on. badge: (tab, page) with the recommended thumbs-up. drop_tab_taps:
    tab names whose FIRST tap the game ignores. dialog_on_swipe: the n-th swipe
    (1-based) lands as a tap on a hexagon and opens its contribute dialog, drawn
    with its x at (0.814, 0.182); dialog_ocr=False: OCR misses its words.
    banner: a research-in-progress banner covers y >= 0.877 and a drag that
    starts on it does not scroll the tree."""
    at = {"tab": "Growth", "dialog": False, **dict(start)}
    taps, swipes, dropped = [], [], set()

    def render():
        img = np.zeros((H, W, 3), dtype=np.uint8)
        for name, tx, ty in TABS:
            pale = name == at["tab"]
            img[int((ty - 0.03) * H):int((ty + 0.03) * H), int((tx - 0.12) * W):int((tx + 0.12) * W)] = \
                (232, 229, 219) if pale else (211, 158, 118)
        # The tree band's shade stands for the scroll position, so a swipe shows.
        img[int(0.30 * H):int(0.95 * H)] = 20 + (25 * at[at["tab"]] + 60 * [t[0] for t in TABS].index(at["tab"])) % 220
        if at["dialog"]:                     # the dialog x: a pale glyph on a dark header
            img[int(0.182 * H) - 6:int(0.182 * H) + 7, int(0.814 * W) - 6:int(0.814 * W) + 7] = (252, 219, 201)
        return img

    class Eng:
        def recognize(self, img):
            if at["dialog"] and dialog_ocr:
                return [_item("Contribute", 700, 1500, 1000, 1560), _item("Attempts: 25/25", 700, 1400, 1000, 1440)]
            return [_item("Tech", 80, 20, 200, 60), _item("Your Contribution: 10,200", 400, 200, 900, 240)]

    def fake_tapf(fx, fy):
        taps.append((round(fx, 3), round(fy, 3)))
        name = next((n for n, tx, ty in TABS if (round(fx, 3), round(fy, 3)) == (tx, ty)), None)
        if name and not at["dialog"]:
            if name in drop_tab_taps and name not in dropped:
                dropped.add(name)
                return
            at["tab"] = name

    def fake_swipe(x0, y0, x1, y1, ms=450):
        swipes.append((at["tab"], y1 < y0, y0 / H))
        if len(swipes) == dialog_on_swipe:
            at["dialog"] = True
            return
        if banner and y0 / H >= 0.877:
            return
        t = at["tab"]
        at[t] = max(0, min(at[t] + (1 if y1 < y0 else -1), pages[t] - 1))

    def fake_badges(img, y0, y1):
        if y0 < 0.25 or at["dialog"]:        # inside a dialog: end the contribute loop at once
            return []
        return [(0.40, 0.40, 1.0)] if (at["tab"], at[at["tab"]]) == badge else []

    sc = collect.Collector(str(tmp_path), dry_run=False, engine=Eng())
    _quiet(monkeypatch, render)
    monkeypatch.setattr(s.drv, "tapf", fake_tapf)
    monkeypatch.setattr(s.drv, "swipe", fake_swipe)
    monkeypatch.setattr(collect_tech, "thumbs_up_badges", fake_badges)
    return sc, taps, swipes


def _run_tech(sc):
    return sc.alliance_tech(_checklist("alliance_tech"), gems_before=3270)


def test_alliance_tech_finds_a_thumbs_up_above_where_the_tab_opens(tmp_path, monkeypatch):
    """2026-09-15: Growth opened at the BOTTOM of its tree. The one swipe the
    flow made dragged further down, the frame did not move, the upper tiers were
    never looked at, and the run reported no-recommended-tech."""
    sc, taps, _ = _tech(monkeypatch, tmp_path, {"Growth": 3, "Territory": 3, "Battle": 3},
                        {"Growth": 2, "Territory": 0, "Battle": 0}, badge=("Growth", 0))
    _run_tech(sc)
    assert [r["tab"] for r in _log(tmp_path, "recommended")] == ["Growth"]
    assert (0.45, 0.43) in taps, "the recommended hexagon must be tapped"


def test_alliance_tech_finds_a_thumbs_up_past_the_first_swipe(tmp_path, monkeypatch):
    """2026-09-15: Territory and Battle opened at their tops and one swipe showed
    two pages, but both trees go on below that."""
    sc, _, swipes = _tech(monkeypatch, tmp_path, {"Growth": 1, "Territory": 2, "Battle": 4},
                          {"Growth": 0, "Territory": 0, "Battle": 0}, badge=("Battle", 3))
    _run_tech(sc)
    assert [r["tab"] for r in _log(tmp_path, "recommended")] == ["Battle"]


def test_alliance_tech_with_every_tree_seen_and_no_badge_is_nothing_free(tmp_path, monkeypatch):
    sc, _, _ = _tech(monkeypatch, tmp_path, {"Growth": 3, "Territory": 2, "Battle": 2},
                     {"Growth": 1, "Territory": 0, "Battle": 1})
    assert _run_tech(sc) == (0, "nothing-free")
    assert _log(tmp_path, "no-recommended-tech")


def test_alliance_tech_reports_a_tree_it_could_not_finish_as_incomplete(tmp_path, monkeypatch):
    """A tree longer than the frame budget was not fully seen, so the outcome
    must not claim there was nothing to contribute to."""
    sc, _, _ = _tech(monkeypatch, tmp_path, {"Growth": 2, "Territory": 2, "Battle": 40},
                     {"Growth": 0, "Territory": 0, "Battle": 0})
    assert _run_tech(sc) == (0, "scan-incomplete")


def test_alliance_tech_taps_a_tab_again_when_the_game_ignored_the_first_tap(tmp_path, monkeypatch):
    """A dropped tab tap would scan Growth a second time under Territory's name
    and call Territory empty. The pale tab says which tree is really showing."""
    sc, taps, _ = _tech(monkeypatch, tmp_path, {"Growth": 2, "Territory": 2, "Battle": 2},
                        {"Growth": 0, "Territory": 0, "Battle": 0}, badge=("Territory", 1),
                        drop_tab_taps=("Territory",))
    _run_tech(sc)
    assert [r["tab"] for r in _log(tmp_path, "recommended")] == ["Territory"]
    assert taps.count((0.499, 0.25)) == 2


def test_alliance_tech_stops_swiping_when_a_drag_opens_a_tech_dialog(tmp_path, monkeypatch):
    """The next drag after an accidental dialog would start between its two
    Contribute buttons, and the left one costs gems. Nothing more is swiped or
    tapped on the page, nothing is contributed, and the run says it did not
    finish looking."""
    sc, taps, swipes = _tech(monkeypatch, tmp_path, {"Growth": 3, "Territory": 3, "Battle": 3},
                             {"Growth": 1, "Territory": 0, "Battle": 0}, dialog_on_swipe=2)
    assert _run_tech(sc) == (0, "scan-incomplete")
    assert len(swipes) == 2, "no swipe after the dialog appeared"
    assert taps == [(0.245, 0.248)], "no tab tapped through the dialog"


def test_alliance_tech_sees_a_dialog_by_its_x_when_ocr_misses_its_words(tmp_path, monkeypatch):
    """Review, 2026-09-15: the dialog guard read OCR words only. A gem spend
    would take a dropped drag, a missed OCR and a second dropped drag in a row
    -- unlikely, but it is the one thing the run must never do, and the dialog x
    (0.814, 0.182) is found on both tech dialogs and on none of seven tree frames."""
    sc, taps, swipes = _tech(monkeypatch, tmp_path, {"Growth": 3, "Territory": 3, "Battle": 3},
                             {"Growth": 1, "Territory": 0, "Battle": 0}, dialog_on_swipe=2, dialog_ocr=False)
    assert _run_tech(sc) == (0, "scan-incomplete")
    assert len(swipes) == 2 and taps == [(0.245, 0.248)]


def test_alliance_tech_scans_a_tree_whose_bottom_is_covered_by_a_research_banner(tmp_path, monkeypatch):
    """Live run, 2026-09-15 11:23: an officer had started Food Gathering II, its
    progress banner covered the bottom of the tree from y 0.877, and every
    downward drag started at 0.90 -- on the banner. The tree never moved, two
    still frames read as its end, and Growth was called complete unseen."""
    sc, _, swipes = _tech(monkeypatch, tmp_path, {"Growth": 3, "Territory": 1, "Battle": 1},
                          {"Growth": 0, "Territory": 0, "Battle": 0}, badge=("Growth", 2), banner=True)
    _run_tech(sc)
    assert [r["tab"] for r in _log(tmp_path, "recommended")] == ["Growth"]
    assert {round(y0, 2) for _, up, y0 in swipes if up} == {collect_tech.SCAN_DOWN[0]}
    assert {round(y0, 2) for _, up, y0 in swipes if not up} == {collect_tech.REWIND[0]}


def test_tech_scan_drags_start_where_a_dropped_drag_presses_nothing():
    """A drag the game drops lands as a tap where it started. Both starting
    points must be plain dialog panel on the Contribute and the Research dialog
    (their buttons sit at y 0.77-0.84; the old 0.85 start touched the Research
    button's edge) and bare tree, not the research banner, on the tree page."""
    import cv2
    frames = os.path.join(os.path.dirname(__file__), "fixtures", "local", "frames")
    names = ("native-tech-dialog.png", "tech-research-dialog.png", "tech-growth-research-banner.png")
    if not all(os.path.exists(os.path.join(frames, n)) for n in names):
        pytest.skip("local frames not present")
    contribute, research, banner = (cv2.imread(os.path.join(frames, n)) for n in names)
    for fy in (collect_tech.SCAN_DOWN[0], collect_tech.REWIND[0]):
        for dialog in (contribute, research):
            rgb = s._rgb(dialog, 0.5, fy)
            assert min(rgb) > 150 and max(rgb) - min(rgb) < 60, (fy, rgb)   # pale panel, not a button
        assert max(s._rgb(banner, 0.5, fy)) < 140, fy                          # dark tree, not the banner
