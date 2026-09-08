"""Chief gear from the Chief Profile: six slots, tier by border colour, stars
by yellow star count. Charm levels need the per-slot screen (TODOS.md)."""
import time

from native.readers import ReaderResult, title_is
from native.readers.imgcues import gear_slot
from native.readers.profile import AVATAR
from native.model import rank_of_tier

SLOTS = {"helmet": (0.23, 0.21), "watch": (0.77, 0.21), "jacket": (0.18, 0.34),
         "pants": (0.82, 0.34), "ring": (0.23, 0.47), "cane": (0.77, 0.47)}
EXPECTED = [f"gear.chief.{s}.stars" for s in SLOTS]


def parse(res, img, items, path):
    for slot, (cx, cy) in SLOTS.items():
        tier, stars, charms = gear_slot(img, cx, cy)
        res.put(f"gear.chief.{slot}.stars", stars, raw=f"{stars} yellow stars", frame=path, method="pixels")
        if tier:
            res.put(f"gear.chief.{slot}.tier", tier, raw=tier, frame=path, method="pixels")
            res.put(f"gear.chief.{slot}.rank", rank_of_tier(tier), raw=tier, frame=path, method="pixels")
        for i, ct in enumerate(charms):
            if ct:
                res.put(f"gear.charms.{slot}.{i}", rank_of_tier(ct), raw=ct, frame=path, method="pixels")
    return res


def read(sc):
    res = ReaderResult("gear")
    img, items, _ = sc.frame("enter")
    h, w = img.shape[:2]
    if not sc.at_home(items, h, w, img):
        sc.go_home()
        img, items, _ = sc.frame("enter")
    if not sc.tapf(*AVATAR, items, img):
        return res
    time.sleep(2.5)
    img, items, path = sc.frame("gear")
    h, w = img.shape[:2]
    res.frames.append(path)
    if not title_is(items, h, w, "Chief Profile"):
        res.notes.append("title mismatch: Chief Profile not on frame")
        sc.go_home()
        return res
    parse(res, img, items, path)
    sc.go_home()
    return res.settle(EXPECTED)
