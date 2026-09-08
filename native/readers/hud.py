"""Home HUD: power, gems, coal, survivors, VIP, furnace upgrade timer. No taps."""
from native.readers import ReaderResult, frac, in_box, parse_duration, parse_number, parse_ratio, norm
from native.screen import read_hud
import re

EXPECTED = ["progress.power", "economy.gems", "economy.resources.coal", "city.survivors.value",
            "progress.vip.level"]


def parse(res, img, items, path):
    h, w = img.shape[:2]
    gems, power = read_hud(items, h, w)
    if power is not None:
        res.put("progress.power", power, raw=str(power), frame=path, method="hud-band")
    if gems is not None:
        res.put("economy.gems", gems, raw=str(gems), frame=path, method="hud-band")
    for it in items:
        t = it["text"].strip()
        x, y = frac(it, h, w)
        if y < 0.075 and 0.55 < x < 0.70:
            v, exact = parse_number(t)
            if v is not None:
                res.put("economy.resources.coal", v, raw=t, frame=path, score=it["score"], method="hud-band", exact=exact)
        elif y < 0.075 and 0.40 < x < 0.50 and parse_ratio(t):
            a, b = parse_ratio(t)
            res.put("city.survivors.value", a, raw=t, frame=path, score=it["score"], method="hud-band")
            res.put("city.survivors.cap", b, raw=t, frame=path, score=it["score"], method="hud-band")
        elif 0.07 < y < 0.105 and 0.58 < x < 0.72 and norm(t).startswith("vip"):
            m = re.search(r"vip\s*(\d+)", norm(t))
            if m:
                res.put("progress.vip.level", int(m.group(1)), raw=t, frame=path, score=it["score"], method="hud-band")
        elif 0.40 < y < 0.60 and 0.40 < x < 0.62 and re.search(r"\d+d\s*\d{1,2}:\d{2}", t.replace(" ", "")):
            secs = parse_duration(t)
            if secs is not None:
                res.put("progress.furnace.upgrading.remaining_s", secs, raw=t, frame=path, score=it["score"], method="hud-timer")
    return res


def read(sc):
    res = ReaderResult("hud")
    img, items, path = sc.frame("hud")
    h, w = img.shape[:2]
    if not sc.at_home(items, h, w, img):
        sc.go_home()
        img, items, path = sc.frame("hud")
    res.frames.append(path)
    parse(res, img, items, path)
    return res.settle(EXPECTED)
