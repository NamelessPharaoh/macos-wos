"""Read and navigate the native Whiteout Survival app from screenshots.

Shared by wos-daily-collect (press what is free) and wos-chief-state (read the
account). Nothing here decides what is worth pressing; callers pass labels and
this module finds them, taps them through the background driver, and refuses any
press that lands on a spend control.

    frame() ─▶ OCR items ─▶ find()/close_control()/read_hud()/parse_*()
                              │
        guarded press ◀────── spend_label(): NEVER_RE | DANGER_RE | PRICE_RE
        positional tap ◀───── pretap_check(): OCR box around the target

Helpers moved here from ~/.claude/skills/wos-daily-collect/scripts/collect.py on
2026-09-08; tests/fixtures/local/native_goldens.json pins their outputs.
"""
import json
import math
import os
import re
import time
from datetime import datetime

import cv2
import numpy as np

from native import drive as drv

# ----------------------------------------------------------------------------- guards
# A spend control is a short label that STARTS with a buy verb ("Buy", "Purchase",
# "Top up", "Recharge") or names a pack. Receipts ("Purchased", "You have
# purchased ...") and titles ("VIP 7 Daily Free Bundle") share the words and are
# not buttons, so matching is on the verb at the head of the label, and never on
# the past tense. "Buy to get:" is the section header on an owned monthly card.
DANGER_RE = re.compile(r"^(buy(?! to get)|purchase(?!d)|top ?up|recharge|special offer|first pack|gift pack|subscri)")
# A real-money price looks like "$4.99" / "US$ 9.99" / "€2,99". A bare "$" is OCR
# noise: hero-card star rows on Squad Settings came back as '88$85'.
PRICE_RE = re.compile(r"(^|\s)(us)?[$€£]\s?\d")
# Progression spend verbs. Anchored at the label start with a word boundary so
# "Warehouse" and "Lighthouse" rows pass while "Use x1" and "Upgrade" block.
NEVER_RE = re.compile(r"^(upgrade|ascend|use|train|enhance|level ?up|activate|spin|fight|challenge)\b")
CLOSE = ["back", "close", "cancel", "x", "×"]
CLOSE_GLYPHS = {"x", "×", "*", "✕", "<", "←", "‹"}
HOME_BAR = ["exploration", "heroes", "backpack", "shop", "alliance", "world"]
REVEAL_RE = re.compile(r"tap (anywhere|to)\b")


def norm(s):
    return re.sub(r"[^a-z0-9:/. $×&]", "", s.lower()).strip()


def slugify(text):
    """'Supreme Infantry' -> 'supreme_infantry': the key form the model stores."""
    t = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    return t or "unnamed"


def spend_label(t, extra=()):
    """Why a normalised label must never be pressed, or None."""
    if len(t) > 20:
        return None
    if DANGER_RE.match(t):
        return "danger"
    if NEVER_RE.match(t):
        return "never"
    if PRICE_RE.search(t):
        return "price"
    for n in extra:
        if t.startswith(n):
            return f"never:{n}"
    return None


# ----------------------------------------------------------------------------- parsing
_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_number(text):
    """'36.30M' -> (36300000, False); '1,423' -> (1423, True); garbage -> (None, True).

    The second value is `exact`: an abbreviated figure is stored with exact=0 so
    a later screen that shows the full number can override it. The decimal point
    is scaled, not stripped (usecases/chief_order.py read 77.3M as 773,000,000)."""
    if text is None:
        return None, True
    t = str(text).strip().replace(" ", "")
    # The game prints abbreviated figures with a locale decimal: "37,84M" is
    # 37.84M. A comma is a decimal only with 1-2 digits after it AND a suffix;
    # "1,423" and "35,020,652" keep their thousands commas.
    t = re.sub(r"^(\d+),(\d{1,2})([kmb])$", r"\1.\2\3", t, flags=re.IGNORECASE)
    t = t.replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb])?", t, re.IGNORECASE)
    if not m:
        return None, True
    num, suf = m.group(1), m.group(2)
    if suf:
        return int(round(float(num) * _SUFFIX[suf.lower()])), False
    if "." in num:
        return None, True
    return int(num), True


def parse_ratio(text):
    """'71/200' -> (71, 200); '36,940/143,010' -> (36940, 143010); else None."""
    if text is None:
        return None
    m = re.fullmatch(r"\s*([\d,.]+\s*[kmb]?)\s*/\s*([\d,.]+\s*[kmb]?)\s*", str(text), re.IGNORECASE)
    if not m:
        return None
    a, _ = parse_number(m.group(1))
    b, _ = parse_number(m.group(2))
    if a is None or b is None:
        return None
    return a, b


def parse_duration(text):
    """'9d 11:22:32' -> seconds; '04:50' -> 290; '1h 20m' -> 4800; else None."""
    if text is None:
        return None
    t = str(text).strip().lower()
    total, matched = 0, False
    m = re.search(r"(\d+)\s*d", t)
    if m:
        total += int(m.group(1)) * 86400
        matched = True
        t = t[m.end():]
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if m.group(3) is None:
            # mm:ss when short, hh:mm when a day prefix or 'h' context exists
            total += h * 60 + mi if not matched else h * 3600 + mi * 60
        else:
            total += h * 3600 + mi * 60 + s
        return total
    for unit, mult in (("h", 3600), ("m", 60), ("s", 1)):
        m = re.search(rf"(\d+)\s*{unit}\b", t)
        if m:
            total += int(m.group(1)) * mult
            matched = True
    return total if matched else None


# ----------------------------------------------------------------------------- pixels
def centre(box):
    x1, y1, x2, y2 = box
    return (x1 + x2) // 2, (y1 + y2) // 2


def _rgb(img, fx, fy):
    h, w = img.shape[:2]
    x, y = int(fx * w), int(fy * h)
    b, g, r = img[y - 3:y + 4, x - 3:x + 4].reshape(-1, 3).mean(0)
    return int(r), int(g), int(b)


def _is_icy(rgb):
    """The game's back arrow and modal × are the same icy white-cyan glyph."""
    r, g, b = rgb
    return r > 190 and g > 225 and b > 225


def has_back_arrow(img):
    return _is_icy(_rgb(img, 0.148, 0.063))


def has_modal_x(img):
    return _is_icy(_rgb(img, 0.868, 0.128))


def has_dialog_x(img):
    """Centred dialogs (Tech contribute, Tips, Daily Rewards) draw their × at
    ~(0.814, 0.182). It is a bluer white than the page-header glyph —
    measured rgb(201,219,252) — so the icy test is loosened here."""
    r, g, b = _rgb(img, 0.814, 0.182)
    return r > 180 and g > 200 and b > 235


def green_badges(img, ymin=0.0, ymax=1.0):
    """Bright green blobs of badge size. On Alliance → Tech the alliance's
    recommended tech carries a green thumbs-up at the hexagon's top-left."""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (40, 120, 120), (80, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _, stats, cent = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        x, y, bw, bh, a = stats[i]
        fy = cent[i][1] / h
        if 400 <= a <= 6000 and 0.6 <= bw / max(bh, 1) <= 1.6 and ymin <= fy <= ymax:
            out.append((cent[i][0] / w, fy, int(a)))
    return out


def close_glyph(items, h, w):
    """A lone close glyph ('*' or 'x' for ×, '<' for a back arrow) in the top
    strip, hard left or hard right; None otherwise. A lone glyph anywhere else
    is punctuation, not a button."""
    for it in items:
        t = it["text"].strip().lower()
        x, y = centre(it["box"])
        if t in CLOSE_GLYPHS and y < 0.32 * h and (x > 0.75 * w or x < 0.22 * w):
            return it
    return None


def close_control(items, h, w):
    """The panel close control: a lone glyph in the top strip, else an OCR'd
    Back/Close/Cancel label."""
    it = close_glyph(items, h, w)
    if it is not None:
        return it
    for it in items:
        if norm(it["text"]) in ("back", "close", "cancel"):
            return it
    return None


def is_enabled(img, box):
    """Grey buttons are desaturated; live ones (green Claim, blue Go) are not.
    Advisory only: a white caption on a pale icon reads as disabled."""
    x1, y1, x2, y2 = box
    crop = img[max(0, y1 - 6):y2 + 6, max(0, x1 - 6):x2 + 6]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return float(hsv[:, :, 1].mean()) > 60


def read_hud(items, h, w):
    """Gems (top-right) and power (row 2, left). Raw digit runs only: the gem
    icon's sparkle OCRs as a digit glued to the count ('7*440' for 440)."""
    gems = power = None
    for it in items:
        x, y = centre(it["box"])
        raw = it["text"].strip()
        if not re.fullmatch(r"[\d,]+", raw):
            continue
        val = int(raw.replace(",", ""))
        if y < 0.08 * h and x > 0.72 * w:
            gems = val
        elif 0.06 * h < y < 0.11 * h and x < 0.45 * w:
            power = val
    return gems, power


def find(items, label, region=None, h=None):
    want = norm(label)
    for it in items:
        t = norm(it["text"])
        if t == want or t.startswith(want + " ") or (len(want) > 3 and want in t and len(t) <= len(want) + 6):
            if region == "bottom" and centre(it["box"])[1] < 0.85 * h:
                continue
            return it
    return None


def items_near(items, fx, fy, h, w, radius=0.06):
    """OCR items whose box contains the point or sits within `radius` of it."""
    px, py = fx * w, fy * h
    out = []
    for it in items:
        x1, y1, x2, y2 = it["box"]
        if x1 - radius * w <= px <= x2 + radius * w and y1 - radius * h <= py <= y2 + radius * h:
            out.append(it)
    return out


def pretap_check(items, fx, fy, h, w, extra=()):
    """Reason a positional tap must be refused, or None.

    Fixed HUD fractions and tile taps carry no label of their own, so the guard
    looks at what is drawn under and around the target: a spend label there
    means the layout is not the one the fraction was recorded on."""
    for it in items_near(items, fx, fy, h, w):
        why = spend_label(norm(it["text"]), extra)
        if why:
            return f"{why}:{it['text']}"
    return None


def signature(items):
    """Text signature of a frame with digits removed: equal signatures mean the
    screen did not change (a no-op press, or a scroll that reached the end)."""
    return " ".join(sorted(norm(i["text"]) for i in items if not re.search(r"\d", norm(i["text"]))))


# ----------------------------------------------------------------------------- screen
class Screen:
    """Frames, logging, guarded taps and navigation. Runners subclass it."""

    def __init__(self, report_dir, dry_run=False, engine=None, never=()):
        if engine is None:
            from core.vision_engine import VisionEngine
            engine = VisionEngine()
            engine.warmup()
        self.eng = engine
        self.dry = dry_run
        self.dir = report_dir
        os.makedirs(report_dir, exist_ok=True)
        self.log_path = os.path.join(report_dir, "run.jsonl")
        self.n = 0
        self.never = tuple(never)
        self.last = None  # (img, items, path) of the most recent frame

    # -- perception -----------------------------------------------------------
    def frame(self, tag):
        self.n += 1
        path = os.path.join(self.dir, f"{self.n:03d}-{tag}.png")
        drv.shot(path)
        img = cv2.imread(path)
        items = self.eng.recognize(img)
        self.last = (img, items, path)
        return img, items, path

    def log(self, **rec):
        rec["ts"] = datetime.now().isoformat(timespec="seconds")
        with open(self.log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(json.dumps(rec))

    # -- taps -------------------------------------------------------------------
    def tap_item(self, img, it, extra=()):
        """Tap an OCR item, unless its label is a spend control."""
        ih, iw = img.shape[:2]
        x, y = centre(it["box"])
        why = spend_label(norm(it["text"]), tuple(extra) + self.never)
        if why:
            self.log(event="refused-press", label=it["text"], why=why)
            return False
        if self.dry:
            print(f"  [dry] would tap '{it['text']}' at ({x},{y})")
            return True
        drv.tapf(x / iw, y / ih)
        return True

    def tapf(self, fx, fy, items=None, img=None, extra=()):
        """Positional tap with the pre-tap OCR check (OV6). With no items given the
        last frame is consulted; a fresh frame is taken when there is none."""
        if items is None:
            if self.last is None:
                img, items, _ = self.frame("pretap")
            else:
                img, items, _ = self.last
        h, w = img.shape[:2]
        why = pretap_check(items, fx, fy, h, w, tuple(extra) + self.never)
        if why:
            self.log(event="refused-tap", at=(round(fx, 3), round(fy, 3)), why=why)
            return False
        if self.dry:
            print(f"  [dry] would tap ({fx:.3f},{fy:.3f})")
            return True
        drv.tapf(fx, fy)
        return True

    def find(self, items, label, region=None, h=None):
        return find(items, label, region, h)

    # -- navigation -------------------------------------------------------------
    def at_home(self, items, h, w=None, img=None):
        """Home = the live HUD reads (power and gems only appear together on the
        city view), or the bottom bar is readable AND there is no back arrow.
        The arrow test matters: "My Island" (Tree of Life) carries the same
        bottom bar as home, and calling it home sent the next item's entry tap
        into the island instead of the side panel."""
        if w is not None:
            gems, power = read_hud(items, h, w)
            if gems is not None and power is not None:
                # The Resource Overview and similar sheets slide over the city
                # with the HUD still visible; their lone × in the top strip is
                # the tell (survey 2026-09-08: "Overview" modal, × at 0.867,0.305).
                return close_glyph(items, h, w) is None
        found = sum(1 for lbl in HOME_BAR if self.find(items, lbl, "bottom", h))
        if found >= 3:
            return img is None or not has_back_arrow(img)
        return False

    def go_home(self, max_steps=9):
        """Leave whatever is open until home is visible.

        Exits are chosen by LOOKING for them, never by blind taps: a blind tap
        on the world selects a player and opens their profile (seen), and on
        Events it hits the gem "+" (store). Order per frame:
          1. icy back arrow at top-left (full pages)      -> tap it
          2. icy × at top-right of a modal (Daily Missions) -> tap it
          3. centred dialog ×                              -> tap it
          4. OCR-found close label                        -> tap it
          5. a reward reveal (almost no text)             -> tap its hint
        """
        for step in range(max_steps):
            img, items, _ = self.frame("home-check")
            h, w = img.shape[:2]
            if self.at_home(items, h, w, img):
                return True
            texts = [norm(i["text"]) for i in items]
            if has_back_arrow(img):
                self.log(event="home-exit", via="back-arrow")
                drv.tapf(0.148, 0.063) if not self.dry else None
            elif has_modal_x(img):
                self.log(event="home-exit", via="modal-x")
                drv.tapf(0.868, 0.128) if not self.dry else None
            elif has_dialog_x(img):
                self.log(event="home-exit", via="dialog-x")
                drv.tapf(0.814, 0.182) if not self.dry else None
            elif (ctl := close_control(items, h, w)) is not None:
                self.log(event="home-exit", via=f"label:{ctl['text']}")
                self.tap_item(img, ctl)
            elif len(texts) <= 3 or any(REVEAL_RE.search(t) for t in texts):
                # Reward reveals say so themselves ("Tap anywhere to exit") and
                # can carry a whole grid of item icons, so the hint text is the
                # tell AND the tap target: tapping an icon opens its tooltip
                # instead of closing the overlay. Fall back to the strip just
                # above the bottom edge where the hint always sits.
                hint = next((i for i in items if REVEAL_RE.search(norm(i["text"]))), None)
                if hint is not None:
                    self.log(event="home-exit", via="reveal-hint")
                    self.tap_item(img, hint)
                else:
                    self.log(event="home-exit", via="reveal-tap")
                    drv.tapf(0.5, 0.93) if not self.dry else None
            else:
                self.log(event="home-exit", via="none-found", texts=texts[:6])
                time.sleep(1.5)
            time.sleep(1.8)
        img, items, path = self.frame("home-failed")
        self.log(event="home-failed", frame=path)
        return False

    def dismiss_reveal(self, img, items):
        """Close a reward overlay if one is up; returns True when it tapped."""
        hint = next((i for i in items if REVEAL_RE.search(norm(i["text"]))), None)
        if hint is None:
            return False
        self.tap_item(img, hint)
        time.sleep(1.5)
        return True

    def enter(self, entry, ensure_home=True):
        """entry is one ("kind", ...) tuple or a list of alternatives; the first
        that resolves on the current frame is used. OCR sometimes misses small
        HUD captions (the Events caption on the main account), so text entries
        carry a fixed-fraction fallback.

          ("bar", "Heroes")     bottom-bar label found by OCR
          ("hud", fx, fy)       fixed window fraction (guarded by pretap_check)
          ("text", "VIP")       first OCR match anywhere
          ("row", "Pet Adventure", fx)  side-panel row, tap at fx on its line
          ("strip", "Daily Deals")      walk a tab strip until the tab or title matches
          ("home", None)        stay on home
        """
        alts = entry if isinstance(entry, list) else [entry]
        img, items, _ = self.frame("enter")
        h, w = img.shape[:2]
        if ensure_home and not self.at_home(items, h, w, img) and not (alts and alts[0][0] == "home"):
            self.go_home()
            img, items, _ = self.frame("enter")
            h, w = img.shape[:2]
        for alt in alts:
            kind = alt[0]
            if kind == "home":
                return True
            if kind == "hud":
                if not self.tapf(alt[1], alt[2], items, img):
                    continue
                time.sleep(2.5)
                return True
            if kind == "strip":
                if self._walk_strip(alt[1], img, items, h, w):
                    return True
                continue
            if kind == "row":
                # The side panel prints each name twice: a section header at
                # x~0.24 and the row label at x~0.35. Only the row has the
                # action button, so prefer the match in the label column and
                # otherwise the lowest one.
                want = norm(alt[1])
                cands = [i for i in items if norm(i["text"]) == want]
                if not cands:
                    continue
                col = [i for i in cands if 0.28 * w <= centre(i["box"])[0] <= 0.45 * w]
                it = max(col or cands, key=lambda i: centre(i["box"])[1])
                cy = centre(it["box"])[1]
                self.log(event="enter", label=f"row:{it['text']}", at=(alt[2], round(cy / h, 3)))
                if not self.tapf(alt[2], cy / h, items, img):
                    continue
                time.sleep(2.5)
                return True
            if kind in ("bar", "text"):
                it = self.find(items, alt[1], "bottom" if kind == "bar" else None, h)
                if it is None:
                    continue
                cx, cy = centre(it["box"])
                self.log(event="enter", label=it["text"], at=(round(cx / w, 3), round(cy / h, 3)))
                if not self.tap_item(img, it):
                    continue
                time.sleep(2.5)
                return True
        return False

    def _walk_strip(self, label, img, items, h, w, max_steps=24):
        """Tab strips (cart, Deals, Events). This is the walk that reached all 12
        cart pages on 2026-09-08: tap every visible label left to right (each tap
        recentres the strip and reveals neighbours), re-read after each, and stop
        when either a tab label or the PAGE TITLE (y 0.19-0.27) matches. When
        nothing new is visible, nudge with the clipped right slot and a flick."""
        want = norm(label)
        done = set()
        for _ in range(max_steps):
            labels = sorted((i for i in items if 0.10 * h < centre(i["box"])[1] < 0.23 * h and i["text"].strip()),
                            key=lambda i: centre(i["box"])[0])
            title = next((i for i in items if 0.19 * h < centre(i["box"])[1] < 0.27 * h and len(i["text"]) > 3), None)
            if title is not None and want in norm(title["text"]):
                return True
            hit = next((i for i in labels if want in norm(i["text"])), None)
            if hit is not None:
                self.log(event="enter", label=f"strip:{hit['text']}")
                self.tap_item(img, hit)
                time.sleep(2.5)
                return True
            todo = [i for i in labels if norm(i["text"]) not in done]
            if todo:
                done.add(norm(todo[0]["text"]))
                self.tap_item(img, todo[0])
                time.sleep(2.0)
            else:
                if not self.dry:
                    drv.tapf(0.90, 0.16)
                    time.sleep(1.8)
                    drv.swipe(int(0.85 * w), int(0.16 * h), int(0.30 * w), int(0.16 * h), 450)
                    time.sleep(1.5)
                done.clear()
            img, items, _ = self.frame("strip")
            h, w = img.shape[:2]
        return False

    def walk_tabs(self, tag, fy_lo=0.10, fy_hi=0.23, max_steps=24):
        """Visit every page of a tab strip, yielding (title, img, items, path)
        once per distinct page title. Same walk as _walk_strip, de-duplicated by
        the page title because tab labels OCR clipped ('30', 'We', 'Cl')."""
        seen_titles, done = set(), set()
        img, items, path = self.frame(tag)
        h, w = img.shape[:2]
        for _ in range(max_steps):
            title_it = next((i for i in items if 0.19 * h < centre(i["box"])[1] < 0.27 * h and len(i["text"]) > 3), None)
            title = norm(title_it["text"]) if title_it else f"untitled-{len(seen_titles)}"
            if title not in seen_titles:
                seen_titles.add(title)
                yield title, img, items, path
            labels = sorted((i for i in items if fy_lo * h < centre(i["box"])[1] < fy_hi * h and i["text"].strip()),
                            key=lambda i: centre(i["box"])[0])
            todo = [i for i in labels if norm(i["text"]) not in done]
            if todo:
                done.add(norm(todo[0]["text"]))
                if not self.tap_item(img, todo[0]):
                    continue
                time.sleep(2.0)
            else:
                if not self.dry:
                    drv.tapf(0.90, 0.16)
                    time.sleep(1.8)
                    drv.swipe(int(0.85 * w), int(0.16 * h), int(0.30 * w), int(0.16 * h), 450)
                    time.sleep(1.5)
                if done:
                    done.clear()
                else:
                    return
            img, items, path = self.frame(tag)
            h, w = img.shape[:2]

    def scroll_pages(self, tag, max_pages=40, fy_from=0.85, fy_to=0.35):
        """Yield (img, items, path) per page of a vertical list, swiping up
        between pages and stopping when the frame signature repeats (the end)."""
        last_sig = None
        for page in range(max_pages):
            img, items, path = self.frame(f"{tag}-p{page}")
            sig = signature(items)
            if sig == last_sig:
                return
            last_sig = sig
            yield img, items, path
            if self.dry:
                return
            h, w = img.shape[:2]
            drv.swipe(int(0.5 * w), int(fy_from * h), int(0.5 * w), int(fy_to * h), 600)
            time.sleep(1.4)
