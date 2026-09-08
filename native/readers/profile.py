"""Chief Profile: identity, furnace level, kills, stamina, alliance tag.

Entry: the avatar at (0.148, 0.071). With a City Shield the same spot is the
City Bonus indicator (Main/main.py:202), so arrival is verified by the title.
"""
import re
import time

from native.readers import ReaderResult, after_prefix, frac, parse_number, parse_ratio, title_is, digits, leave_via_back, norm

EXPECTED = ["identity.id", "identity.name", "identity.state", "progress.furnace.level", "progress.kills",
            "economy.stamina.value"]
AVATAR = (0.148, 0.071)


def parse(res, img, items, path):
    h, w = img.shape[:2]
    it, rest = after_prefix(items, "ID:")
    if it is not None and digits(rest) is not None:
        res.put("identity.id", str(digits(rest)), raw=it["text"], frame=path, score=it["score"])
    it, rest = after_prefix(items, "State:")
    if it is not None and digits(rest) is not None:
        res.put("identity.state", digits(rest), raw=it["text"], frame=path, score=it["score"])
    it, rest = after_prefix(items, "Kills:")
    if it is not None and digits(rest) is not None:
        res.put("progress.kills", digits(rest), raw=it["text"], frame=path, score=it["score"])
    it, rest = after_prefix(items, "Alliance:")
    if it is not None and rest:
        res.put("alliance.tag", rest.split()[0].strip("[]"), raw=it["text"], frame=path, score=it["score"])
    for i in items:
        t = i["text"].strip()
        x, y = frac(i, h, w)
        m = re.search(r"lv\.?\s*(\d+)", norm(t))
        if m and 0.6 < x < 0.9 and 0.7 < y < 0.85:
            res.put("progress.furnace.level", int(m.group(1)), raw=t, frame=path, score=i["score"])
        elif 0.7 < y < 0.75 and 0.4 < x < 0.8 and len(t) > 2 and not t.startswith("ID"):
            m2 = re.match(r"\[([^\]]+)\]\s*(.+)", t)
            name = m2.group(2).strip() if m2 else t
            res.put("identity.name", name, raw=t, frame=path, score=i["score"])
            if m2 and "alliance" not in res.doc:
                res.put("alliance.tag", m2.group(1), raw=t, frame=path, score=i["score"])
        elif 0.85 < y < 0.9 and x < 0.4 and parse_ratio(t):
            a, b = parse_ratio(t)
            res.put("economy.stamina.value", a, raw=t, frame=path, score=i["score"])
            res.put("economy.stamina.cap", b, raw=t, frame=path, score=i["score"])
    return res


def read(sc):
    res = ReaderResult("profile")
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
    res.frames.append(path)
    if not title_is(items, h, w, "Chief Profile"):
        res.notes.append("title mismatch: Chief Profile not on frame")
        sc.go_home()
        return res
    parse(res, img, items, path)
    res.settle(EXPECTED)
    return res
