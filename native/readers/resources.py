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


def parse(res, img, items, path):
    h, w = img.shape[:2]
    owned = sorted((i for i in items if 0.60 < frac(i, h, w)[0] < 0.72 and 0.38 < frac(i, h, w)[1] < 0.72
                    and not i["text"].strip().startswith("•") and parse_number(i["text"])[0] is not None),
                   key=lambda i: frac(i, h, w)[1])
    protected = sorted((i for i in items if 0.60 < frac(i, h, w)[0] < 0.72 and 0.38 < frac(i, h, w)[1] < 0.72
                        and i["text"].strip().startswith("•")), key=lambda i: frac(i, h, w)[1])
    output = sorted((i for i in items if 0.36 < frac(i, h, w)[0] < 0.52 and 0.40 < frac(i, h, w)[1] < 0.72
                     and "/" in i["text"]), key=lambda i: frac(i, h, w)[1])
    for name, it in zip(ORDER, owned):
        v, exact = parse_number(it["text"])
        res.put(f"economy.resources.{name}", v, raw=it["text"], frame=path, score=it["score"], exact=exact)
    for name, it in zip(ORDER, protected):
        v, exact = parse_number(it["text"].strip("• ").strip())
        if v is not None:
            res.put(f"economy.protected.{name}", v, raw=it["text"], frame=path, score=it["score"], exact=exact)
    for name, it in zip(ORDER, output):
        num = it["text"].split("/")[0]
        v, exact = parse_number(num)
        if v is not None:
            res.put(f"economy.output.{name}", v, raw=it["text"], frame=path, score=it["score"], exact=exact)
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
