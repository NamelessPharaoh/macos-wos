"""Heroes: roster grid (level per card, rarity by card colour) plus each card's
detail (name, SSR/SR/R, power, level, escorts, troops capacity, EXP, stars).

The detail view carries an Upgrade button and an ascend arrow: the only
presses here are the card tile (pre-tap checked) and the back arrow; the card
tell is that level and stars read the same before and after (OV3).

    roster page ─▶ for each card with 'Lv.': tap ─▶ detail ─▶ back
                └─ swipe up ─▶ next page until the frame signature repeats
"""
import re
import time

from native import drive as drv
from native.readers import ReaderResult, frac, parse_number, parse_ratio, title_is, digits, norm
from native.readers.imgcues import hero_card_stars, rarity_from_hue
from native.screen import has_back_arrow, signature, slugify

RARITY = {"ssr": "mythic", "sr": "epic", "r": "rare"}
RANK = {"rare": 0, "epic": 1, "mythic": 2}
MAX_PAGES = 6
EXPECTED = []


def roster_cards(items, h, w):
    """[(cx, cy, level)] for every card whose level label is on the frame."""
    out = []
    for it in items:
        m = re.match(r"lv\.?\s*(\d+)", norm(it["text"]))
        if not m:
            continue
        x, y = frac(it, h, w)
        if y > 0.93:
            continue
        out.append((round(x, 3), round(y - 0.07, 3), int(m.group(1))))
    return sorted(out, key=lambda c: (c[1], c[0]))


def parse_card(res, img, items, path, rarity_hint=None):
    """Read one hero detail frame into res.doc['heroes'][slug]."""
    h, w = img.shape[:2]
    title = next((i for i in items if frac(i, h, w)[1] < 0.09 and 0.3 < frac(i, h, w)[0] < 0.7 and len(i["text"].strip()) > 1), None)
    if title is None:
        return None
    name = title["text"].strip()
    key = slugify(name)
    base = f"heroes.{key}"
    res.put(f"{base}.name", name, raw=name, frame=path, score=title["score"])
    rar = next((RARITY[norm(i["text"])] for i in items if norm(i["text"]) in RARITY and frac(i, h, w)[1] < 0.2), rarity_hint)
    if rar:
        res.put(f"{base}.rarity", rar, raw=rar, frame=path)
        res.put(f"{base}.rank", RANK[rar], raw=rar, frame=path)
    for it in items:
        t = it["text"].strip()
        x, y = frac(it, h, w)
        if 0.6 < y < 0.66 and 0.35 < x < 0.65 and digits(t) is not None and "/" not in t:
            res.put(f"{base}.power", digits(t), raw=t, frame=path, score=it["score"])
        elif 0.76 < y < 0.81 and 0.42 < x < 0.58 and t.isdigit():
            res.put(f"{base}.level", int(t), raw=t, frame=path, score=it["score"])
        elif 0.76 < y < 0.81 and x > 0.6 and digits(t) is not None:
            res.put(f"{base}.troops_capacity", digits(t), raw=t, frame=path, score=it["score"])
        elif 0.76 < y < 0.81 and x < 0.4 and digits(t) is not None:
            res.put(f"{base}.escorts", digits(t), raw=t, frame=path, score=it["score"])
        elif 0.87 < y < 0.91 and parse_ratio(t):
            a, b = parse_ratio(t)
            res.put(f"{base}.exp", a, raw=t, frame=path, score=it["score"])
            res.put(f"{base}.exp_next", b, raw=t, frame=path, score=it["score"])
    full, partial = hero_card_stars(img)
    res.put(f"{base}.stars", full, raw=f"{full} full{' + partial' if partial else ''}", frame=path, method="pixels")
    return key


def read(sc, budget_s=None):
    res = ReaderResult("heroes")
    if not sc.enter(("bar", "Heroes")):
        res.notes.append("Heroes bar label not found")
        return res
    seen, last_sig, read_count = set(), None, 0
    for page in range(MAX_PAGES):
        img, items, path = sc.frame(f"roster-p{page}")
        h, w = img.shape[:2]
        if not title_is(items, h, w, "Heroes"):
            res.notes.append("roster title missing")
            break
        sig = signature(items) + "|" + ",".join(str(c) for c in roster_cards(items, h, w))
        if sig == last_sig:
            break
        last_sig = sig
        for cx, cy, level in roster_cards(items, h, w):
            rarity = rarity_from_hue(img, cx - 0.05, cy - 0.07)
            if not sc.tapf(cx, cy, items, img):
                continue
            time.sleep(1.8)
            cimg, citems, cpath = sc.frame("card")
            ch, cw = cimg.shape[:2]
            if title_is(citems, ch, cw, "Heroes"):
                # the tap did not open a card (empty slot / partial card at the bottom)
                continue
            key = parse_card(res, cimg, citems, cpath, rarity)
            if key is None:
                res.notes.append(f"card at ({cx},{cy}) had no title")
            elif key in seen:
                pass
            else:
                seen.add(key)
                read_count += 1
                res.put(f"heroes.{key}.roster_level", level, raw=f"Lv. {level}", frame=path)
            res.frames.append(cpath)
            if has_back_arrow(cimg):
                drv.tapf(0.148, 0.063) if not sc.dry else None
                time.sleep(1.5)
            else:
                sc.go_home()
                if not sc.enter(("bar", "Heroes")):
                    res.notes.append("could not return to the roster")
                    res.status = "partial"
                    return res
            img, items, path = sc.frame(f"roster-p{page}")
            h, w = img.shape[:2]
        if sc.dry:
            break
        drv.swipe(int(0.5 * w), int(0.85 * h), int(0.5 * w), int(0.30 * h), 600)
        time.sleep(1.6)
    sc.go_home()
    res.status = "ok" if read_count else "failed"
    res.notes.append(f"{read_count} heroes read")
    return res
