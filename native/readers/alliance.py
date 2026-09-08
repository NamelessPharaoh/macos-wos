"""Alliance hub: name and tag, leader, power, state rank, members, level."""
import re
import time

from native.readers import ReaderResult, frac, label_value, parse_ratio, digits, title_is, norm

EXPECTED = ["alliance.name", "alliance.members", "alliance.cap"]


def parse(res, img, items, path):
    h, w = img.shape[:2]
    for it in items:
        t = it["text"].strip()
        x, y = frac(it, h, w)
        m = re.match(r"\[([^\]]+)\]\s*(.+)", t)
        if m and 0.10 < y < 0.15:
            res.put("alliance.tag", m.group(1), raw=t, frame=path, score=it["score"])
            res.put("alliance.name", m.group(2).strip(), raw=t, frame=path, score=it["score"])
        elif 0.30 < y < 0.34 and x < 0.3 and t.isdigit():
            res.put("alliance.level", int(t), raw=t, frame=path, score=it["score"])
    v = label_value(items, h, w, "Power:")
    if v is not None and digits(v["text"]) is not None:
        res.put("alliance.power", digits(v["text"]), raw=v["text"], frame=path, score=v["score"])
    v = label_value(items, h, w, "Rank:")
    if v is not None and digits(v["text"]) is not None:
        res.put("alliance.state_rank", digits(v["text"]), raw=v["text"], frame=path, score=v["score"])
    v = label_value(items, h, w, "Members:")
    if v is not None and parse_ratio(v["text"]):
        a, b = parse_ratio(v["text"])
        res.put("alliance.members", a, raw=v["text"], frame=path, score=v["score"])
        res.put("alliance.cap", b, raw=v["text"], frame=path, score=v["score"])
    v = label_value(items, h, w, "Alliance Leader:")
    if v is not None:
        res.put("alliance.leader", v["text"].strip(), raw=v["text"], frame=path, score=v["score"])
    return res


def read(sc):
    res = ReaderResult("alliance")
    if not sc.enter(("bar", "Alliance")):
        res.notes.append("Alliance bar label not found")
        return res
    img, items, path = sc.frame("alliance")
    h, w = img.shape[:2]
    res.frames.append(path)
    if not title_is(items, h, w, "Alliance"):
        res.notes.append("title mismatch: Alliance not on frame")
        sc.go_home()
        return res
    parse(res, img, items, path)
    sc.go_home()
    return res.settle(EXPECTED)
