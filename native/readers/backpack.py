"""Backpack ledger: every tab, every page, every tile.

Tiles carry no text but a count, so each tile is tapped once: the tooltip
shows the item name ('1 Gems'), its description and the owned count, with a
Use button that is never a target. The tooltip is closed by tapping the page
title. Names land in backpack.items.<tab>/<slug> (dynamic); speedups are also
folded into backpack.speedups.<type>.<duration> and fire crystals into their
static paths, so strategy queries do not depend on the ledger's naming.
"""
import os
import re
import time

from native.readers import ReaderResult, frac, parse_number, norm
from native.screen import signature, slugify
# SPEEDUP_RE, classify_kind and speedup_duration live in knowledge/util.py
# (fix round 1, following E1's precedent): knowledge/ depends on nothing but
# the standard library, and native/kb.py::record_item needed this module's
# classify_kind for one field, which meant calling record_item from
# anything but the backpack reader dragged this module's native.screen
# import (cv2, numpy, native.drive) in just to classify a string.
# Re-exported here unchanged so this module's own call sites don't move.
from knowledge.util import CLASSIFIER_VERSION, SPEEDUP_RE, classify_kind, speedup_duration

TABS = ("Resources", "Speedup", "Bonus", "Other")   # Gear tiles open a stats screen, not a tooltip: TODOS.md
TILE_COLS = (0.216, 0.404, 0.593, 0.783)
TITLE_TAP = (0.285, 0.067)
MAX_PAGES = 12
EXPECTED = []


def fold(res, tab, name, count, raw, frame, score, exact, directory=None):
    """Store the item under the ledger path and, when it is a known kind,
    under the static path a query can rely on.

    A11: the item catalogue (`native.kb.items()`, built from backpack
    tooltips) is consulted first by exact slug match -- the game's own
    tooltip text already classified this item on an earlier sighting.
    That stored `kind` is trusted only when its `classifier_version`
    matches `knowledge.util.CLASSIFIER_VERSION` (fix round 1): a classifier
    fix (a new keyword rule in `classify_kind`) ships with a version bump,
    which makes every already-catalogued row look stale at once and fall
    back to a fresh `classify_kind(name)` call here, immediately -- no live
    re-sighting of that exact tile required for the fix to take effect.
    `directory` is forwarded to `kb.items()` untouched; production callers
    leave it None (the real, committed `knowledge/items.json`), tests pass
    a tmp_path."""
    key = f"{slugify(tab)}/{slugify(name)}"
    res.put(f"backpack.items.{key}", count, raw=raw, frame=frame, score=score, exact=exact)
    n = norm(name)
    from native import kb
    entry = kb.items(directory=directory).get(slugify(name))
    if entry and entry.get("classifier_version") == CLASSIFIER_VERSION:
        kind = entry["kind"]
    else:
        kind = classify_kind(name)
    if kind == "speedup":
        su = speedup_duration(name)
        if su:
            spd, dur = su
            res.put(f"backpack.speedups.{spd}.{dur}", count, raw=raw, frame=frame, score=score, exact=exact)
    elif kind == "fire_crystal":
        if "refined fire crystal" in n:
            res.put("backpack.refined_fire_crystals", count, raw=raw, frame=frame, score=score, exact=exact)
        else:
            res.put("backpack.fire_crystals", count, raw=raw, frame=frame, score=score, exact=exact)


def tile_targets(items, h, w):
    """Tile centres from the count labels: a count sits at the tile's
    bottom-right, the tile centre is ~0.045 above it."""
    out = []
    for it in items:
        t = it["text"].strip()
        x, y = frac(it, h, w)
        if y < 0.16 or y > 0.985:
            continue
        v, _ = parse_number(t.replace(" ", ""))
        if v is None:
            continue
        col = min(TILE_COLS, key=lambda c: abs(c - (x - 0.04)))
        out.append((col, round(y - 0.045, 3), t))
    return sorted(set(out), key=lambda c: (c[1], c[0]))


# The quantity box under a tooltip's description starts around uy-0.090
# (measured on the live Chief Stamina frame, 2026-09-09). Candidates are
# taken down to this floor so a description of any number of lines is
# captured whole, without reaching into that box.
DESC_FLOOR = 0.10


def read_tooltip(items, h, w):
    """(name, None, description) from an open tile tooltip, else None. The
    tooltip pops up beside its tile, so it is located by its Use button: the
    name is the topmost text line 0.15-0.24 above it; the owned count is the
    tile's own label, not the tooltip's.

    Description: everything between the name and the quantity box, joined
    top-to-bottom by y into one space-separated string.

    The band is NOT split by a fixed name/description boundary, and the
    live sweep of 2026-09-09 is why. The earlier version took the name from
    a 0.15-0.24 band and the description from a 0.10-0.15 band below it,
    surveyed against the single-line `backpack_tile` fixture. On the real
    Chief Stamina tooltip the layout is:

        uy-0.1977  "Chief Stamina"                                  <- name
        uy-0.1646  "Restores 10 Chief Stamina. Used for daily ..."  <- desc line 1
        uy-0.1414  "troop deployment."                              <- desc line 2
        uy-0.0899  quantity box

    A two-line description puts its FIRST line inside the old name band, so
    the fixed split silently stored only "troop deployment." — the tail of a
    sentence, with no tell that anything was dropped. The fixture could not
    show this because it is single-line; only a real sweep could.

    So: the name is the topmost candidate, and the description is every
    candidate below it down to DESC_FLOOR, which sits above the quantity
    box (that box starts around uy-0.090). Wrapping to any number of lines
    is handled by the same rule as one line."""
    use = next((i for i in items if norm(i["text"]) == "use"), None)
    if use is None:
        return None
    uy = frac(use, h, w)[1]

    def band(lo, hi):
        return [i for i in items if 0.22 < frac(i, h, w)[0] < 0.78 and uy - hi <= frac(i, h, w)[1] <= uy - lo
                and len(norm(i["text"])) > 1 and not re.fullmatch(r"[\d,.+() k]+", i["text"].strip().lower())]

    cands = band(DESC_FLOOR, 0.24)
    if not cands:
        return None
    name = min(cands, key=lambda i: frac(i, h, w)[1])
    ny = frac(name, h, w)[1]
    desc_cands = sorted((i for i in cands if frac(i, h, w)[1] > ny), key=lambda i: frac(i, h, w)[1])
    description = " ".join(i["text"].strip() for i in desc_cands) if desc_cands else None
    return name["text"].strip(), None, description


def close_tooltip(sc, img, items, cx=None, cy=None):
    """Close an open tooltip with a short vertical drag in the grid, away from
    the tooltip (survey 2026-09-08: a 0.05h drag closes it; the tab label and
    the page title do not; tapping any tile only moves the tooltip, and the
    tile under the tooltip is its Use button, which consumed an avatar frame).
    The drag may scroll the grid a little, so callers re-frame afterwards."""
    from native import drive as drv
    h, w = img.shape[:2]
    use = next((i for i in items if norm(i["text"]) == "use"), None)
    uy = frac(use, h, w)[1] if use else 0.5
    y = 0.20 if uy > 0.5 else 0.85
    if not sc.dry:
        drv.swipe(int(0.5 * w), int(y * h), int(0.5 * w), int((y - 0.03) * h), 300)
    time.sleep(0.9)
    return True


def tab_active(img, tab_item):
    """The selected tab is drawn on a near-white pill (S~14, V~250); the
    others on blue (S~111, V~196). Sampled just above the caption."""
    import cv2
    import numpy as np
    x1, y1, x2, y2 = tab_item["box"]
    cx = (x1 + x2) // 2
    px = img[max(0, y1 - 14), cx]
    hsv = cv2.cvtColor(np.uint8([[px]]), cv2.COLOR_BGR2HSV)[0][0]
    return int(hsv[1]) < 60 and int(hsv[2]) > 220


def on_backpack(items, h, w):
    return any(norm(i["text"]) == "backpack" and frac(i, h, w)[1] < 0.1 for i in items)


def tooltip_open(items):
    return any(norm(i["text"]) == "use" for i in items)


def read(sc, tabs=TABS):
    res = ReaderResult("backpack")
    if not sc.enter(("bar", "Backpack")):
        res.notes.append("Backpack bar label not found")
        return res
    tiles_read = 0
    for tab in tabs:
        img, items, path = sc.frame("backpack-tab")
        h, w = img.shape[:2]
        it = sc.find(items, tab, None, h)
        if it is None or frac(it, h, w)[1] > 0.16:
            res.notes.append(f"tab {tab} not found")
            continue
        active = False
        for attempt in range(3):
            sc.tap_item(img, it)
            time.sleep(1.5)
            img, items, path = sc.frame("backpack-tab")
            h, w = img.shape[:2]
            it = sc.find(items, tab, None, h) or it
            if tab_active(img, it):
                active = True
                break
        if not active:
            res.notes.append(f"tab {tab} did not activate")
            continue
        last_sig = None
        for page in range(MAX_PAGES):
            img, items, path = sc.frame(f"backpack-{slugify(tab)}-p{page}")
            h, w = img.shape[:2]
            sig = signature(items) + "|" + ",".join(t[2] for t in tile_targets(items, h, w))
            if sig == last_sig:
                break
            last_sig = sig
            visited = set()
            while True:
                nxt = next((t for t in tile_targets(items, h, w) if t[1] <= 0.84
                            and (t[0], round(t[1], 1), t[2]) not in visited), None)
                if nxt is None:
                    break
                cx, cy, count_text = nxt
                visited.add((cx, round(cy, 1), count_text))
                if not on_backpack(items, h, w):
                    res.notes.append("left the Backpack page; stopping")
                    sc.go_home()
                    res.status = "partial" if tiles_read else "failed"
                    return res
                if tooltip_open(items):
                    close_tooltip(sc, img, items)
                    img, items, path = sc.frame("tile-close")
                    h, w = img.shape[:2]
                    if tooltip_open(items):
                        res.notes.append("a tooltip would not close; stopping")
                        sc.go_home()
                        res.status = "partial" if tiles_read else "failed"
                        return res
                    continue  # positions may have shifted: pick the next tile from the fresh frame
                if not sc.tapf(cx, cy, items, img):
                    continue
                time.sleep(1.0)
                timg, titems, tpath = sc.frame("tile")
                th, tw = timg.shape[:2]
                if not on_backpack(titems, th, tw):
                    res.notes.append(f"tile at ({cx},{cy}) opened another screen; stopping")
                    sc.go_home()
                    res.status = "partial" if tiles_read else "failed"
                    return res
                got = read_tooltip(titems, th, tw)
                if got:
                    v, exact = parse_number(count_text.replace(" ", "").replace(".", ""))
                    if v is not None:
                        fold(res, tab, got[0], v, count_text, tpath, 1.0, exact)
                        tiles_read += 1
                    from native import kb
                    kb.record_item(got[0], tab, got[2], os.path.basename(sc.dir))
                close_tooltip(sc, timg, titems)
                img, items, path = sc.frame("tile-closed")
                h, w = img.shape[:2]
            if sc.dry:
                break
            from native import drive as drv
            drv.swipe(int(0.5 * w), int(0.90 * h), int(0.5 * w), int(0.25 * h), 600)
            time.sleep(1.4)
    sc.go_home()
    res.status = "ok" if tiles_read else "failed"
    res.notes.append(f"{tiles_read} tiles read")
    return res
