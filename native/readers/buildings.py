"""Building levels via the side panel's City tab.

Tapping a queue or training row there selects that building on the city map
and opens its info popup: '27' beside 'Research Center' with Details /
Research / Upgrade buttons (survey 2026-09-08). Only the popup is read; the
Upgrade button is never a target. Buildings without a panel row (Embassy,
Command Center, Infirmary) stay unread until the city-map spike (TODOS.md).

The Training rows (Infantry/Lancer/Marksman) are deliberately NOT tapped: a
row with 'Completed' centres the camera on the camp and a tap on that camp
COLLECTS the trained troops (Power +32,700 floated, state went Idle) on
2026-09-08. Nothing was spent, but a reader must not change state. Camp
levels wait for the city-map reader.

    handle ─▶ City tab ─▶ row N ─▶ popup 'NN Name' ─▶ neutral scene tap ─▶ handle ...
"""
import re
import time

from native import drive as drv
from native.readers import ReaderResult, frac, norm
from native.readers.queues import HANDLE, CITY_TAB
from native.screen import slugify

ROWS = {  # normalised row label -> schema building key
    "furnace": "furnace", "storehouse": "storehouse", "warehouse": "warehouse",
    "center research": "research_center", "research center": "research_center",
    "embassy": "embassy", "command center": "command_center", "infirmary": "infirmary",
    "war academy": "war_academy",
}
NEUTRAL = (0.5, 0.22)
EXPECTED = ["city.buildings.research_center"]


def popup_level(items, h, w):
    """('research_center', 27, level_item, name_item) from a building popup
    ('27' beside 'Research Center'), or from a building panel header
    ('Furnace Lv. 27'), or None."""
    for it in items:
        m = re.match(r"(.+?)\s+lv\.?\s*(\d+)$", norm(it["text"]))
        if m and 0.3 < frac(it, h, w)[1] < 0.6:
            key = ROWS.get(m.group(1).strip(), slugify(m.group(1)))
            return key, int(m.group(2)), it, it
    for it in items:
        t = it["text"].strip()
        x, y = frac(it, h, w)
        if 0.3 < y < 0.6 and t.isdigit():
            name = next((i for i in items if abs(frac(i, h, w)[1] - y) < 0.02 and frac(i, h, w)[0] > x + 0.05), None)
            if name is not None:
                key = ROWS.get(norm(name["text"]), slugify(name["text"]))
                return key, int(t), it, name
    return None


def _open_city_tab(sc):
    img, items, _ = sc.frame("enter")
    h, w = img.shape[:2]
    if not sc.at_home(items, h, w, img):
        sc.go_home()
        img, items, _ = sc.frame("enter")
    if not sc.tapf(*HANDLE, items, img):
        return None
    time.sleep(1.6)
    img, items, _ = sc.frame("sidepanel")
    h, w = img.shape[:2]
    tab = sc.find(items, "City", None, h)
    if tab is not None and 0.2 < frac(tab, h, w)[1] < 0.27:
        sc.tap_item(img, tab)
    else:
        sc.tapf(*CITY_TAB, items, img)
    time.sleep(1.3)
    return sc.frame("city-tab")


def read(sc):
    res = ReaderResult("buildings")
    first = _open_city_tab(sc)
    if first is None:
        return res
    img, items, path = first
    h, w = img.shape[:2]
    rows = []
    for it in items:
        t = norm(it["text"])
        x, y = frac(it, h, w)
        if not (0.28 < x < 0.45 and 0.28 < y < 0.72):
            continue
        base = re.sub(r"\s+upgrading$", "", t)
        if base in ROWS:
            rows.append((base, it["text"], x, y))
    seen = set()
    for base, text, x, y in rows:
        key = ROWS[base]
        if key in seen:
            continue
        seen.add(key)
        if not sc.tapf(x, y, items, img):
            continue
        time.sleep(1.8)
        img, items, path = sc.frame(f"building-{key}")
        h, w = img.shape[:2]
        got = popup_level(items, h, w)
        if got and got[0] in (key, slugify(text)) or (got and key == "research_center" and "research" in got[0]):
            res.put(f"city.buildings.{key}", got[1], raw=f"{got[2]['text']} {got[3]['text']}", frame=path, score=got[2]["score"])
        elif got:
            res.put(f"city.buildings.{got[0]}", got[1], raw=f"{got[2]['text']} {got[3]['text']}", frame=path, score=got[2]["score"])
            res.notes.append(f"row {text!r} opened {got[0]} instead of {key}")
        else:
            res.notes.append(f"row {text!r}: no building popup read")
        res.frames.append(path)
        # dismiss the popup and reopen the panel for the next row
        sc.tapf(*NEUTRAL, items, img)
        time.sleep(1.0)
        nxt = _open_city_tab(sc)
        if nxt is None:
            break
        img, items, path = nxt
        h, w = img.shape[:2]
    sc.tapf(*HANDLE, items, img)
    time.sleep(1.0)
    sc.go_home()
    return res.settle(EXPECTED)
