"""native/screen.py: the helpers moved out of wos-daily-collect must reproduce
the goldens recorded before the move, and the new parsers/guards must behave
per the wos-chief-state plan."""
import json
import os

import pytest

from native import screen as s

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GOLDENS = os.path.join(REPO, "tests", "fixtures", "local", "native_goldens.json")


def _goldens():
    if not os.path.exists(GOLDENS):
        pytest.skip("local goldens not present (gitignored, recorded on the dev Mac)")
    return json.load(open(GOLDENS))["frames"]


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
