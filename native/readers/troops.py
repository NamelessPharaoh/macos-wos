"""Troops Preview (Chief Profile -> Troops): totals, march queue, injured and
counts per troop type at the top tier. The tier badge (IX) does not OCR; the
name does, and TIER_NAMES maps the names the game uses. Unknown names land in
troops.by_name.<type>.<slug> so nothing is lost while the map grows."""
import time

from native.readers import ReaderResult, below, frac, parse_number, parse_ratio, title_is, norm, leave_via_back
from native.screen import slugify

TIER_NAMES = {"supreme": 9}
TYPES = ("infantry", "lancer", "marksman")
EXPECTED = ["troops.total.value", "troops.total.cap", "troops.march_queue.used", "troops.wounded.value",
            "troops.totals.infantry", "troops.totals.lancer", "troops.totals.marksman"]


def parse(res, img, items, path):
    h, w = img.shape[:2]
    for it in items:
        t = norm(it["text"])
        x, y = frac(it, h, w)
        if 0.17 < y < 0.21:
            val = below(items, h, w, it, dy=(0.01, 0.04), dx=0.08)
            if val is None:
                continue
            r = parse_ratio(val["text"])
            if r is None:
                continue
            if t.startswith("total troops"):
                res.put("troops.total.value", r[0], raw=val["text"], frame=path, score=val["score"], exact="k" not in val["text"].lower() and "m" not in val["text"].lower())
                res.put("troops.total.cap", r[1], raw=val["text"], frame=path, score=val["score"], exact="k" not in val["text"].lower() and "m" not in val["text"].lower())
            elif t.startswith("march queue"):
                res.put("troops.march_queue.used", r[0], raw=val["text"], frame=path, score=val["score"])
                res.put("troops.march_queue.cap", r[1], raw=val["text"], frame=path, score=val["score"])
            elif t.startswith("injured"):
                res.put("troops.wounded.value", r[0], raw=val["text"], frame=path, score=val["score"])
                res.put("troops.wounded.cap", r[1], raw=val["text"], frame=path, score=val["score"])
    # The preview lists every tier the account owns; a tier absent from the
    # list is zero, not unread, so the by_tier grid is zero-filled first.
    totals = {k: 0 for k in TYPES}
    for ttype in TYPES:
        for tier in range(1, 12):
            res.put(f"troops.by_tier.{ttype}.t{tier}", 0, raw="absent", frame=path, method="absent")
    for it in items:
        t = norm(it["text"])
        parts = t.split()
        if len(parts) >= 2 and parts[-1] in TYPES and 0.25 < frac(it, h, w)[1] < 0.95:
            ttype = parts[-1]
            tier_name = " ".join(parts[:-1])
            cnt = below(items, h, w, it, dy=(0.01, 0.04), dx=0.1)
            if cnt is None:
                continue
            v, exact = parse_number(cnt["text"])
            if v is None:
                continue
            tier = TIER_NAMES.get(tier_name)
            if tier:
                res.put(f"troops.by_tier.{ttype}.t{tier}", v, raw=cnt["text"], frame=path, score=cnt["score"], exact=exact)
            else:
                res.put(f"troops.by_name.{ttype}.{slugify(tier_name)}", v, raw=cnt["text"], frame=path, score=cnt["score"], exact=exact)
                res.notes.append(f"unknown tier name {tier_name!r}")
            totals[ttype] += v
    for k, v in totals.items():
        if v:
            res.put(f"troops.totals.{k}", v, raw=str(v), frame=path, method="sum")
    return res


def read(sc, from_profile=False):
    res = ReaderResult("troops")
    if not from_profile:
        from native.readers import profile as prof
        img, items, _ = sc.frame("enter")
        h, w = img.shape[:2]
        if not sc.at_home(items, h, w, img):
            sc.go_home()
            img, items, _ = sc.frame("enter")
        if not sc.tapf(*prof.AVATAR, items, img):
            return res
        time.sleep(2.5)
    img, items, path = sc.frame("profile")
    h, w = img.shape[:2]
    it = sc.find(items, "Troops", None, h)
    if it is None or not sc.tap_item(img, it):
        res.notes.append("Troops button not found")
        sc.go_home()
        return res
    time.sleep(2.5)
    img, items, path = sc.frame("troops")
    h, w = img.shape[:2]
    res.frames.append(path)
    if not title_is(items, h, w, "Troops Preview"):
        res.notes.append("title mismatch: Troops Preview not on frame")
        sc.go_home()
        return res
    parse(res, img, items, path)
    sc.go_home()
    return res.settle(EXPECTED)
