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

TABS = ("Resources", "Speedup", "Bonus", "Other")   # Gear tiles open a stats screen, not a tooltip: TODOS.md
TILE_COLS = (0.216, 0.404, 0.593, 0.783)
TITLE_TAP = (0.285, 0.067)
MAX_PAGES = 12
SPEEDUP_RE = re.compile(r"(?:(general|construction|research|training|healing)\s+)?(?:(\d+)\s*(m|min|h|hr|d)\s+)?speed-?up[s]?(?:\s*\(?(\d+)\s*(m|min|h|hr|d)\)?)?", re.I)
DUR = {"m": "m", "min": "m", "h": "h", "hr": "h", "d": "d"}
EXPECTED = []


def classify_kind(name):
    """"speedup" | "fire_crystal" | "other" from the exact SPEEDUP_RE match
    and "fire crystal" keyword `fold` uses to route ledger writes (A11):
    one function, so the item catalogue's `kind` (native/kb.py::record_item)
    and the ledger router can never classify the same name two different
    ways. "resource_box" is part of the kind enum `record_item` promises,
    but like `fold` before this change, nothing here recognises it yet --
    the day a keyword for it is added, it is added HERE, and both the
    catalogue and the ledger pick it up from this one place."""
    n = norm(name)
    m = SPEEDUP_RE.search(name)
    if m and (m.group(2) or m.group(4)):
        return "speedup"
    if "fire crystal" in n:
        return "fire_crystal"
    return "other"


def fold(res, tab, name, count, raw, frame, score, exact, directory=None):
    """Store the item under the ledger path and, when it is a known kind,
    under the static path a query can rely on.

    A11: the item catalogue (`native.kb.items()`, built from backpack
    tooltips) is consulted first by exact slug match -- the game's own
    tooltip text already classified this item on an earlier sighting --
    and only when the slug isn't catalogued yet does this fall back to
    `classify_kind`'s SPEEDUP_RE/keyword rules. `directory` is forwarded to
    `kb.items()` untouched; production callers leave it None (the real,
    committed `knowledge/items.json`), tests pass a tmp_path."""
    key = f"{slugify(tab)}/{slugify(name)}"
    res.put(f"backpack.items.{key}", count, raw=raw, frame=frame, score=score, exact=exact)
    n = norm(name)
    from native import kb
    entry = kb.items(directory=directory).get(slugify(name))
    kind = entry["kind"] if entry else classify_kind(name)
    if kind == "speedup":
        m = SPEEDUP_RE.search(name)
        if m and (m.group(2) or m.group(4)):
            spd = (m.group(1) or "general").lower()
            num, unit = (m.group(2), m.group(3)) if m.group(2) else (m.group(4), m.group(5))
            dur = f"{num}{DUR[unit.lower()]}"
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


def read_tooltip(items, h, w):
    """(name, None, description) from an open tile tooltip, else None. The
    tooltip pops up beside its tile, so it is located by its Use button: the
    name is the topmost text line 0.15-0.24 above it; the owned count is the
    tile's own label, not the tooltip's.

    Description band (C9, surveyed against the `backpack_tile` fixture in
    tests/fixtures/local/reader_frames.json, not guessed): with the Use
    button at uy, that frame's name sits at uy-0.175 (inside the 0.15-0.24
    band above) and its description line ("Grants 1 Gems.") sits right
    below the name at uy-0.144; the quantity box ("+220") starts at
    uy-0.090. The 0.10-0.15 band below the name therefore holds the
    description with margin on both sides, without reaching into the
    quantity box."""
    use = next((i for i in items if norm(i["text"]) == "use"), None)
    if use is None:
        return None
    uy = frac(use, h, w)[1]

    def band(lo, hi):
        return [i for i in items if 0.22 < frac(i, h, w)[0] < 0.78 and uy - hi <= frac(i, h, w)[1] <= uy - lo
                and len(norm(i["text"])) > 1 and not re.fullmatch(r"[\d,.+() k]+", i["text"].strip().lower())]

    cands = band(0.15, 0.24)
    if not cands:
        return None
    name = min(cands, key=lambda i: frac(i, h, w)[1])
    desc_cands = band(0.10, 0.15)
    description = min(desc_cands, key=lambda i: frac(i, h, w)[1])["text"].strip() if desc_cands else None
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
