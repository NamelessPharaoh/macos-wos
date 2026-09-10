"""native/screen.py: the helpers moved out of wos-daily-collect must reproduce
the goldens recorded before the move, and the new parsers/guards must behave
per the wos-chief-state plan."""
import json
import os

import pytest

from native import screen as s
from knowledge import util as ku

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GOLDENS = os.path.join(REPO, "tests", "fixtures", "local", "native_goldens.json")


def _goldens():
    if not os.path.exists(GOLDENS):
        pytest.skip("local goldens not present (gitignored, recorded on the dev Mac)")
    return json.load(open(GOLDENS))["frames"]


def test_parsers_are_reexported_from_knowledge_util():
    """E1/mandatory regression: parse_number, parse_ratio and parse_duration
    moved to knowledge/util.py; native/screen.py must re-export the exact
    same function objects, since native/readers/__init__.py:17 and ten
    reader modules import them from native.screen."""
    assert s.parse_number is ku.parse_number
    assert s.parse_ratio is ku.parse_ratio
    assert s.parse_duration is ku.parse_duration


def test_slugify_is_reexported_from_knowledge_util():
    """Fix round 1, item 1: slugify moved to knowledge/util.py alongside
    classify_kind, for the same reason the parsers moved (E1) -- so
    native/kb.py::record_item can import it without dragging this module's
    cv2/numpy/native.drive imports in. native/screen.py must still hand
    back the exact same function object, since several reader modules
    import slugify from here."""
    assert s.slugify is ku.slugify


@pytest.mark.parametrize("rel", list(_goldens().keys()) if os.path.exists(GOLDENS) else [])
def test_moved_helpers_reproduce_goldens(rel):
    import cv2
    g = _goldens()[rel]
    img = cv2.imread(os.path.join(REPO, rel))
    assert img is not None, rel
    h, w = img.shape[:2]
    assert [h, w] == g["size"]
    items = g["items"]
    assert [s.norm(i["text"]) for i in items] == g["norm"]
    assert list(s.read_hud(items, h, w)) == g["read_hud"]
    assert s.has_back_arrow(img) == g["has_back_arrow"]
    assert s.has_modal_x(img) == g["has_modal_x"]
    assert s.has_dialog_x(img) == g["has_dialog_x"]
    cc = s.close_control(items, h, w)
    assert (None if cc is None else cc["text"]) == g["close_control"]
    assert [s.is_enabled(img, i["box"]) for i in items] == g["is_enabled"]
    assert [[round(x, 4), round(y, 4), a] for x, y, a in s.green_badges(img, 0.27, 0.97)] == g["green_badges"]
    for lbl, want in g["find_bottom_bar"].items():
        it = s.find(items, lbl, "bottom", h)
        assert (None if it is None else it["text"]) == want, lbl
    hits = [t for t in (s.norm(i["text"]) for i in items)
            if len(t) <= 20 and (s.DANGER_RE.match(t) or s.PRICE_RE.search(t))]
    assert hits == g["danger_hits"]


@pytest.mark.parametrize("text,value,exact", [
    ("36.30M", 36_300_000, False), ("37,84M", 37_840_000, False), ("8,9M", 8_900_000, False), ("8.8M", 8_800_000, False), ("77.3M", 77_300_000, False),
    ("1,423", 1423, True), ("1423", 1423, True), ("35,020,652", 35_020_652, True),
    ("2.5K", 2500, False), ("1.2B", 1_200_000_000, False), ("12.5", None, True),
    ("x", None, True), ("", None, True), (None, None, True), ("7*440", None, True),
])
def test_parse_number(text, value, exact):
    assert s.parse_number(text) == (value, exact)


@pytest.mark.parametrize("text,want", [
    ("71/200", (71, 200)), ("36,940/143,010", (36940, 143010)), ("0/32", (0, 32)),
    ("1.5M/2M", (1_500_000, 2_000_000)), ("71", None), ("a/b", None), (None, None),
])
def test_parse_ratio(text, want):
    assert s.parse_ratio(text) == want


@pytest.mark.parametrize("text,want", [
    ("9d 11:22:32", 9 * 86400 + 11 * 3600 + 22 * 60 + 32), ("11:22:32", 40952),
    ("04:50", 290), ("1h 20m", 4800), ("45s", 45), ("2d", 172800), ("soon", None), (None, None),
])
def test_parse_duration(text, want):
    assert s.parse_duration(text) == want


@pytest.mark.parametrize("label,why", [
    ("upgrade", "never"), ("ascend", "never"), ("use x1", "never"), ("train", "never"),
    ("level up", "never"), ("levelup", "never"), ("spin", "never"), ("challenge", "never"),
    ("warehouse", None), ("lighthouse", None), ("training", None), ("used", None),
    ("buy", "danger"), ("purchase", "danger"), ("purchased", None), ("buy to get:", None),
    ("$4.99", "price"), ("us$ 9.99", "price"), ("88$85", None), ("claim", None), ("free", None),
    ("a very long label that is not a button at all", None),
])
def test_spend_label(label, why):
    assert s.spend_label(label) == why


def test_spend_label_extra_words():
    assert s.spend_label("gems", ("gems",)) == "never:gems"
    assert s.spend_label("claim", ("gems",)) is None


def _item(text, x1, y1, x2, y2):
    return {"text": text, "score": 0.9, "box": [x1, y1, x2, y2]}


def test_pretap_check_refuses_spend_under_target():
    h, w = 1000, 600
    items = [_item("Upgrade", 250, 480, 350, 520), _item("Heroes", 100, 900, 200, 950)]
    assert s.pretap_check(items, 0.5, 0.5, h, w) == "never:Upgrade"
    assert s.pretap_check(items, 0.25, 0.93, h, w) is None
    assert s.pretap_check([_item("Warehouse", 250, 480, 350, 520)], 0.5, 0.5, h, w) is None


def test_signature_ignores_numbers_and_order():
    a = [_item("Claim", 0, 0, 1, 1), _item("1,423", 0, 0, 1, 1), _item("Mail", 0, 0, 1, 1)]
    b = [_item("Mail", 0, 0, 1, 1), _item("Claim", 0, 0, 1, 1), _item("9,999", 0, 0, 1, 1)]
    assert s.signature(a) == s.signature(b)


def test_screen_tap_item_refuses_spend(tmp_path):
    class Eng:
        def recognize(self, img):
            return []
    import numpy as np
    sc = s.Screen(str(tmp_path), dry_run=True, engine=Eng())
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    assert sc.tap_item(img, _item("Upgrade", 10, 10, 30, 30)) is False
    assert sc.tap_item(img, _item("Claim", 10, 10, 30, 30)) is True
    log = open(os.path.join(str(tmp_path), "run.jsonl")).read()
    assert "refused-press" in log


def test_screen_tapf_pretap_uses_given_items(tmp_path):
    class Eng:
        def recognize(self, img):
            return []
    import numpy as np
    sc = s.Screen(str(tmp_path), dry_run=True, engine=Eng())
    img = np.zeros((1000, 600, 3), dtype=np.uint8)
    assert sc.tapf(0.5, 0.5, [_item("Use x1", 250, 480, 350, 520)], img) is False
    assert sc.tapf(0.5, 0.5, [_item("Claim", 250, 480, 350, 520)], img) is True


def test_at_home_refuses_hud_with_overlay_close_glyph(tmp_path):
    class Eng:
        def recognize(self, img):
            return []
    sc = s.Screen(str(tmp_path), dry_run=True, engine=Eng())
    h, w = 1902, 1284
    hud = [_item("1,423", 1000, 90, 1050, 110), _item("35,020,652", 350, 160, 470, 180)]
    assert sc.at_home(hud, h, w) is True
    overlay = hud + [_item("X", 1100, 570, 1125, 600)]
    assert sc.at_home(overlay, h, w) is False


def test_pretap_check_excludes_a_whole_button_around_a_spend_caption():
    h, w = 1902, 1284
    items = [_item("Use", 620, 1240, 660, 1270)]           # caption centre (0.50, 0.66)
    assert s.pretap_check(items, 0.404, 0.68, h, w) is not None   # 0.10 beside the caption: still the button
    assert s.pretap_check(items, 0.404, 0.80, h, w) is None       # 0.14 below: clear


def test_dialog_x_spot_finds_the_gear_details_close():
    import cv2
    p = os.path.join(REPO, "tests", "fixtures", "local", "frames", "native-gear-details-dialog.png")
    if not os.path.exists(p):
        pytest.skip("local frame not present")
    img = cv2.imread(p)
    assert s.dialog_x_spot(img) == (0.815, 0.257)
    assert s.has_dialog_x(img) is False


def _light(img, fx, fy):
    """Paint the 7x7 patch _rgb() averages, in BGR, to the dialog-glyph colour."""
    h, w = img.shape[:2]
    x, y = int(fx * w), int(fy * h)
    img[y - 3:y + 4, x - 3:x + 4] = (250, 220, 200)   # BGR -> rgb(200, 220, 250)


def test_dialog_x_spot_skips_a_blocked_spot():
    """go_home retires a x spot that changed nothing, so the next candidate on
    the same frame gets a turn instead of the first being retried forever."""
    import numpy as np
    img = np.zeros((1902, 1284, 3), dtype=np.uint8)
    for spot in s.DIALOG_X_SPOTS:
        _light(img, *spot)
    first, second = s.DIALOG_X_SPOTS[0], s.DIALOG_X_SPOTS[1]
    assert s.dialog_x_spot(img) == first
    assert s.dialog_x_spot(img, blocked={f"dialog-x{first}"}) == second
    assert s.dialog_x_spot(img, blocked={f"dialog-x{sp}" for sp in s.DIALOG_X_SPOTS}) is None


def test_dialog_x_spots_cover_the_welcome_back_dialog():
    """The startup offline-income dialog draws its x at (0.836, 0.224); before
    2026-09-10 the nearest known spot was (0.815, 0.257), 60px away and dead."""
    import numpy as np
    img = np.zeros((1902, 1284, 3), dtype=np.uint8)
    _light(img, 0.836, 0.224)
    assert s.dialog_x_spot(img) == (0.836, 0.224)


def test_go_home_retires_an_exit_that_never_changes_the_frame(tmp_path, monkeypatch):
    """Regression, 2026-09-10: the "Welcome back!" dialog matched a x spot 60px
    off its real x. go_home tapped that dead pixel on all nine steps and gave up.
    An exit whose tap leaves the frame identical must not be tried twice."""
    import numpy as np
    frame = np.zeros((1902, 1284, 3), dtype=np.uint8)
    for spot in s.DIALOG_X_SPOTS:
        _light(frame, *spot)

    class Eng:
        def recognize(self, img):
            # Enough text that the <=3-item reveal branch does not swallow the
            # frame, and no close label, so the dialog-x branch is the one tried.
            return [_item("Welcome back", 400, 400, 800, 450),
                    _item("Time Offline", 400, 500, 800, 550),
                    _item("Offline Income", 400, 600, 800, 650),
                    _item("Confirm", 500, 1500, 700, 1560)]

    sc = s.Screen(str(tmp_path), dry_run=True, engine=Eng())
    monkeypatch.setattr(s.drv, "shot", lambda path: None)
    monkeypatch.setattr(s.cv2, "imread", lambda path: frame)
    monkeypatch.setattr(s.time, "sleep", lambda n: None)

    assert sc.go_home(max_steps=6) is False
    log = open(os.path.join(str(tmp_path), "run.jsonl")).read()
    assert "home-exit-blocked" in log
    # Every dead spot is tried once and retired, never once per step.
    for spot in s.DIALOG_X_SPOTS:
        assert log.count('"at": [%s, %s]' % spot) == 1
