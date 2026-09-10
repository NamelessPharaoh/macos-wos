"""Side panel, City tab: building queue, training queues, current research.

Entry: the panel handle at (0.11, 0.45), then the City tab at (0.185, 0.239).
The panel prints each queue as a name line followed by a state line
('Furnace Upgrading' / '9d 09:23:44', 'Infantry' / 'Completed', 'Center
Research' / 'Idle'). Everything here is dynamic except research.current.
"""
import re
import time

from native.readers import ReaderResult, below, frac, parse_duration, norm
from native.screen import slugify

HANDLE = (0.11, 0.45)
CITY_TAB = (0.185, 0.239)
EXPECTED = ["research.current.name"]
TYPES = ("infantry", "lancer", "marksman")


def parse(res, img, items, path):
    h, w = img.shape[:2]
    rows = [i for i in items if 0.28 < frac(i, h, w)[0] < 0.42 and 0.28 < frac(i, h, w)[1] < 0.72]
    rows.sort(key=lambda i: frac(i, h, w)[1])
    builder = 0
    # "Tech Research" is a SECTION HEADER, not the research row: the row under it
    # carries the tech's own name ("Weapons Prep IV" / "08:52:49"). Measured
    # 2026-09-10, when the reader failed for want of research.current.name even
    # though the whole panel had OCR'd cleanly.
    tech_y = next((frac(i, h, w)[1] for i in items if "tech research" in norm(i["text"])), None)
    for it in rows:
        t = it["text"].strip()
        nt = norm(t)
        m = re.match(r"(.+?)\s+upgrading$", nt)
        if m:
            builder += 1
            state = below(items, h, w, it, dy=(0.01, 0.03), dx=0.1)
            secs = parse_duration(state["text"]) if state else None
            name = slugify(m.group(1))
            res.put(f"city.queues.{builder}.building", name, raw=t, frame=path, score=it["score"])
            res.put(f"city.queues.{builder}.remaining_s", secs, raw=state["text"] if state else None, frame=path,
                    score=state["score"] if state else None)
            continue
        if nt in TYPES:
            state = below(items, h, w, it, dy=(0.01, 0.03), dx=0.1)
            st = norm(state["text"]) if state else None
            secs = parse_duration(state["text"]) if state else None
            res.put(f"troops.training.{nt}.state", "completed" if st == "completed" else ("training" if secs else (st or "unknown")),
                    raw=state["text"] if state else None, frame=path, score=it["score"])
            res.put(f"troops.training.{nt}.remaining_s", secs if secs else 0, raw=state["text"] if state else None,
                    frame=path, score=it["score"])
            continue
        # Either shape: a row that names itself ("Center Research" / "Idle"), or
        # any row sitting under the Tech Research header.
        under_tech = tech_y is not None and frac(it, h, w)[1] > tech_y
        if ("research" in nt and "tech" not in nt) or under_tech:
            state = below(items, h, w, it, dy=(0.01, 0.03), dx=0.1)
            st = state["text"].strip() if state else None
            if st and norm(st) == "idle":
                res.put("research.current.name", "idle", raw=st, frame=path, score=it["score"])
                res.put("research.current.remaining_s", 0, raw=st, frame=path, score=it["score"])
            elif st:
                secs = parse_duration(st)
                res.put("research.current.name", slugify(t) if secs else slugify(st), raw=t + " / " + st, frame=path, score=it["score"])
                res.put("research.current.remaining_s", secs if secs else 0, raw=st, frame=path, score=it["score"])
    return res


def read(sc):
    res = ReaderResult("queues")
    img, items, _ = sc.frame("enter")
    h, w = img.shape[:2]
    if not sc.at_home(items, h, w, img):
        sc.go_home()
        img, items, _ = sc.frame("enter")
    if not sc.tapf(*HANDLE, items, img):
        return res
    time.sleep(1.8)
    img, items, _ = sc.frame("sidepanel")
    h, w = img.shape[:2]
    tab = sc.find(items, "City", None, h)
    if tab is not None and 0.2 < frac(tab, h, w)[1] < 0.27:
        sc.tap_item(img, tab)
    else:
        sc.tapf(*CITY_TAB, items, img)
    time.sleep(1.5)
    img, items, path = sc.frame("queues")
    h, w = img.shape[:2]
    res.frames.append(path)
    if not any("building queue" in norm(i["text"]) for i in items):
        res.notes.append("City tab did not show the Building Queue")
        sc.go_home()
        return res
    parse(res, img, items, path)
    # close the panel: tap the handle again (it slides back) then verify home
    sc.tapf(*HANDLE, items, img)
    time.sleep(1.2)
    sc.go_home()
    return res.settle(EXPECTED)
