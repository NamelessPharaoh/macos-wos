"""Events: every page of the tab strip, by page title, with its timer.

The strip re-centres on each tap and its labels change weekly, so the page is
identified by the title band, not the tab label (the cart walk of 2026-09-08).
Per page: the title, the first countdown seen ('12:38:17', '2d 03:00:00') and
any 'Attempts/Attacks Left' counter. All of it is dynamic (events end).
"""
import re

from native.readers import ReaderResult, frac, parse_duration, norm
from native.screen import slugify

ENTRY = (0.844, 0.17)
EXPECTED = []
TIMER_RE = re.compile(r"(\d+d\s*)?\d{1,2}:\d{2}(:\d{2})?")


def parse_page(res, title, img, items, path):
    h, w = img.shape[:2]
    key = slugify(title)
    base = f"events.{key}"
    res.put(f"{base}.name", title, raw=title, frame=path)
    timers = [i for i in items if TIMER_RE.fullmatch(i["text"].strip().replace(" ", "")) and frac(i, h, w)[1] > 0.12]
    if timers:
        t = timers[-1]
        res.put(f"{base}.remaining_s", parse_duration(t["text"]), raw=t["text"], frame=path, score=t["score"])
    left = next((i for i in items if re.search(r"(attempts|attacks) left", norm(i["text"]))), None)
    if left is not None:
        m = re.search(r"(\d+)\s*$", left["text"].strip())
        if m:
            res.put(f"{base}.attempts_left", int(m.group(1)), raw=left["text"], frame=path, score=left["score"])
    return key


def read(sc):
    res = ReaderResult("events")
    if not sc.enter(("hud", ENTRY[0], ENTRY[1])):
        res.notes.append("Events entry tap refused")
        return res
    img, items, path = sc.frame("events")
    h, w = img.shape[:2]
    if not any(norm(i["text"]) == "events" and frac(i, h, w)[1] < 0.1 for i in items):
        res.notes.append("Events title not on frame")
        sc.go_home()
        return res
    pages = 0
    for title_norm, pimg, pitems, ppath in sc.walk_tabs("events-page"):
        ph, pw = pimg.shape[:2]
        title_it = next((i for i in pitems if 0.19 * ph < (i["box"][1] + i["box"][3]) // 2 < 0.27 * ph and len(i["text"]) > 3), None)
        title = title_it["text"].strip() if title_it else title_norm
        parse_page(res, title, pimg, pitems, ppath)
        res.frames.append(ppath)
        pages += 1
    sc.go_home()
    res.status = "ok" if pages else "failed"
    res.notes.append(f"{pages} event pages")
    return res
