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


# Anchored on the tapped tile's centre (cy), not the Use button: the live
# sweep of 2026-09-09 found at least three tooltip layouts (see below), and
# a fixed offset from Use cannot fit all of them -- Speedup tooltips have no
# Use button at all. What all three share is the tap position, which the
# caller already knows (read() has the tile's cx, cy to hand close_tooltip).
#
# Measured offsets from cy, real frames in
# ~/wos-chief/20260909T120815Z/ (2026-09-09):
#
#   Shape A -- Resources, 010-tile.png (tile at cx=0.216, cy=0.218):
#       cy+0.117  "Chief Stamina"                                  <- name
#       cy+0.151  "Restores 10 Chief Stamina. Used for daily ..."  <- desc line 1
#       cy+0.174  "troop deployment."                              <- desc line 2
#       cy+0.226  quantity slider ("00", "+", count)
#       cy+0.315  Source / Use
#
#   Shape B -- Speedup, 086-tile.png (tile at cx=0.404, cy=0.218):
#       cy+0.120  "1m Construction Speedup"                        <- name
#       cy+0.150  "Speeds up your [Construction] queue by 1 minute." <- desc
#       (no Source/Use button at all -- speedups can't be re-sourced or
#       manually used, so the old Use-anchored read_tooltip returned None
#       for every one of these, which is why the 2026-09-09 sweep captured
#       zero speedups)
#
#   Shape C -- Other, 110-tile.png (tile at cx=0.593, cy=0.257):
#       cy+0.121  "Mystery Badge"                                  <- name
#       cy+0.151  "Mystery Badge can be used for trading in the ..." <- desc line 1
#       cy+0.174  "Shop."                                          <- desc line 2
#       cy+0.242  Source / Use  (no quantity slider, so Source/Use sits
#       closer to the description than in shape A)
#
# Name lands at cy+0.117..0.121 and description lines at cy+0.15/+0.174
# across all three shapes; the only thing that varies is what comes after
# (a slider, Source/Use, both, or nothing), which is why a fixed offset
# from a button could never work but a fixed band below the tile does.
# NAME_LO sits below the tooltip's own border/pointer glyphs (stray single
# characters at cy+0.00..0.06, already excluded by the len(text) > 1
# filter below); DESC_FLOOR sits above the nearest thing that can follow a
# two-line description in any shape (the slider at cy+0.226 in shape A).
NAME_LO = 0.08
DESC_FLOOR = 0.19


def read_tooltip(items, h, w, cx, cy):
    """(name, None, description) from an open tile tooltip, else None.

    `cx, cy` is the tapped tile's centre, the same fraction the caller
    already passes to `close_tooltip` -- the tooltip always opens directly
    below the tapped tile, in every shape seen so far, so this is the one
    thing all shapes can be anchored on (see the offsets measured above).
    `cx` is accepted for symmetry with the caller's tile-target tuple but
    unused: the tooltip is horizontally centred (fx ~= 0.5) regardless of
    which grid column was tapped.

    The name is the topmost text line in the cy+NAME_LO .. cy+DESC_FLOOR
    band; the description is every remaining candidate below the name,
    joined top-to-bottom by y into one space-separated string. Button
    labels ("Source", "Use") are excluded outright rather than relying on
    the band alone, since shape A's Use sits close enough to the floor
    that a slightly generous band could otherwise catch it."""
    def band(lo, hi):
        return [i for i in items if 0.22 < frac(i, h, w)[0] < 0.78 and cy + lo <= frac(i, h, w)[1] <= cy + hi
                and len(norm(i["text"])) > 1 and norm(i["text"]) not in ("source", "use")
                and not re.fullmatch(r"[\d,.+() k]+", i["text"].strip().lower())]

    cands = band(NAME_LO, DESC_FLOOR)
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
                got = read_tooltip(titems, th, tw, cx, cy)
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
