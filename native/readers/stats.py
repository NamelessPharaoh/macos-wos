"""Chief Profile -> Bonus Overview: the account's own construction, research
and training speed bonuses, as the game's own percentage (a displayed +128%
is stored as 128, not 1.28 -- native/kb.py::speed_bonus_from_sheet converts).

The vendored knowledge tables ship the calculator author's own buffs, not
this account's, so without this reader every building/research/training time
the knowledge base produces is an unbuffed base time -- roughly 2x too slow
on this account.

Entry: avatar (0.148, 0.071) -> Chief Profile -> the magnifier beside the
power figure (0.57, 0.79) -> "Bonus Overview". That dialog is one long
scrollable list; the three labels never share a frame (survey 2026-09-09):
Training Speed sits in the Military section, Construction Speed and Research
Speed further down in the Growth section, so the reader scrolls and keeps
parsing until all three are read or it runs out of new content.

Bonus Overview's close X does not sit on either of native/screen.py's two
known DIALOG_X_SPOTS; measured live at (0.845, 0.153) -- CLOSE below.
"""
import re
import time

from native.readers import ReaderResult, label_value, title_is

EXPECTED = ["progress.bonus.construction_speed", "progress.bonus.research_speed", "progress.bonus.training_speed"]
AVATAR = (0.148, 0.071)
POWER_MAGNIFIER = (0.57, 0.79)
CLOSE = (0.845, 0.153)
LABELS = (
    ("Construction Speed", "construction_speed"),
    ("Research Speed", "research_speed"),
    ("Training Speed", "training_speed"),
)
PCT_RE = re.compile(r"\+?(\d+(?:\.\d+)?)%")
MAX_SCROLLS = 6


def parse(res, img, items, path):
    """label_value returns the OCR item, so the regex runs on item["text"]
    (C7); a label already read on an earlier scroll is left alone."""
    h, w = img.shape[:2]
    for label, key in LABELS:
        path_key = f"progress.bonus.{key}"
        if path_key in res.provenance:
            continue
        v = label_value(items, h, w, label)
        if v is None:
            continue
        m = PCT_RE.search(v["text"])
        if m is None:
            continue
        res.put(path_key, int(round(float(m.group(1)))), raw=v["text"], frame=path, score=v["score"])
    return res


def _done(res):
    return all(f"progress.bonus.{key}" in res.provenance for _, key in LABELS)


def read(sc):
    res = ReaderResult("stats")
    img, items, _ = sc.frame("enter")
    h, w = img.shape[:2]
    if not sc.at_home(items, h, w, img):
        sc.go_home()
        img, items, _ = sc.frame("enter")
    if not sc.tapf(*AVATAR, items, img):
        res.notes.append("avatar tap refused")
        return res
    time.sleep(2.5)
    img, items, path = sc.frame("profile")
    h, w = img.shape[:2]
    if not title_is(items, h, w, "Chief Profile"):
        res.notes.append("title mismatch: Chief Profile not on frame")
        sc.go_home()
        return res
    if not sc.tapf(*POWER_MAGNIFIER, items, img):
        res.notes.append("power magnifier tap refused")
        sc.go_home()
        return res
    time.sleep(2.0)
    img, items, path = sc.frame("stats")
    h, w = img.shape[:2]
    res.frames.append(path)
    if not title_is(items, h, w, "Bonus Overview", fy_max=0.18):
        res.notes.append("title mismatch: Bonus Overview not on frame")
        sc.go_home()
        return res
    parse(res, img, items, path)
    for _ in range(MAX_SCROLLS):
        if _done(res):
            break
        if sc.dry:
            break
        from native import drive as drv
        drv.swipe(int(0.5 * w), int(0.85 * h), int(0.5 * w), int(0.35 * h), 600)
        time.sleep(1.2)
        img, items, path = sc.frame("stats-scroll")
        h, w = img.shape[:2]
        res.frames.append(path)
        parse(res, img, items, path)
    sc.tapf(*CLOSE, items, img)
    sc.go_home()
    return res.settle(EXPECTED)
