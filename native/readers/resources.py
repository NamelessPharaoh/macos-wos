"""Resource Overview sheet (tap the coal figure on the HUD): owned meat, wood,
coal, iron with output rates and protected amounts. Abbreviated figures, so
every value is exact=0. The sheet keeps the HUD visible; its × is a lone 'X'
at (0.867, 0.305)."""
import time

from native.readers import ReaderResult, frac, parse_number, norm
from native.screen import close_glyph

COAL_FIGURE = (0.612, 0.052)
ORDER = ("meat", "wood", "coal", "iron")
EXPECTED = [f"economy.resources.{r}" for r in ORDER]


ROW_Y = (0.42, 0.51, 0.59, 0.68)     # meat, wood, coal, iron
PROTECTED_DY = 0.025
TOL = 0.012


def _row_of(y):
    """(index, kind) for a figure at fraction y: owned sits on the row line,
    protected 0.025 below it. Assigning by position, not by order, is what
    survives a dropped bullet: on 2026-09-08 '• 7.0M' OCR'd as '97.0M' and an
    order-based reader filed it as iron owned (97M instead of 1.9M)."""
    for i, ry in enumerate(ROW_Y):
        if abs(y - ry) <= TOL:
            return i, "owned"
        if abs(y - (ry + PROTECTED_DY)) <= TOL:
            return i, "protected"
    return None, None


def parse(res, img, items, path):
    h, w = img.shape[:2]
    for it in items:
        x, y = frac(it, h, w)
        if not (0.60 < x < 0.72 and 0.38 < y < 0.72):
            continue
        idx, kind = _row_of(y)
        if idx is None:
            continue
        name = ORDER[idx]
        text = it["text"].strip()
        bullet = text.startswith("•")
        if kind == "owned" and bullet:
            continue
        v, exact = parse_number(text.lstrip("• ").strip())
        if v is None:
            continue
        if kind == "owned":
            res.put(f"economy.resources.{name}", v, raw=text, frame=path, score=it["score"], exact=exact)
        else:
            if not bullet and len(text) > 4 and text[0] == "9":
                # a dropped bullet reads as a leading 9 ('• 7.0M' -> '97.0M'); the
                # figure without it is the protected amount
                v, exact = parse_number(text[1:])
                if v is None:
                    continue
            res.put(f"economy.protected.{name}", v, raw=text, frame=path, score=it["score"], exact=exact)
    for it in items:
        x, y = frac(it, h, w)
        if 0.36 < x < 0.52 and 0.40 < y < 0.72 and "/" in it["text"]:
            idx, kind = _row_of(y - 0.013)
            if idx is None:
                continue
            v, exact = parse_number(it["text"].split("/")[0])
            if v is not None:
                res.put(f"economy.output.{ORDER[idx]}", v, raw=it["text"], frame=path, score=it["score"], exact=exact)
    return res


def read(sc):
    res = ReaderResult("resources")
    img, items, _ = sc.frame("enter")
    h, w = img.shape[:2]
    if not sc.at_home(items, h, w, img):
        sc.go_home()
        img, items, _ = sc.frame("enter")
    if not sc.tapf(*COAL_FIGURE, items, img):
        return res
    time.sleep(2.0)
    img, items, path = sc.frame("resources")
    h, w = img.shape[:2]
    res.frames.append(path)
    if not any(norm(i["text"]) == "overview" for i in items):
        res.notes.append("Overview sheet did not open")
        sc.go_home()
        return res
    parse(res, img, items, path)
    x = close_glyph(items, h, w)
    if x is not None:
        sc.tap_item(img, x)
    else:
        sc.tapf(0.867, 0.305, items, img)
    time.sleep(1.5)
    return res.settle(EXPECTED)
