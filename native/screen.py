"""Read and navigate the native Whiteout Survival app from screenshots.

Shared by wos-daily-collect (press what is free) and wos-chief-state (read the
account). Nothing here decides what is worth pressing; callers pass labels and
this module finds them, taps them through the background driver, and refuses any
press that lands on a spend control.

    frame() ─▶ OCR items ─▶ find()/close_control()/read_hud()
                              │           parse_*/slugify live in knowledge/util.py
                              │           (re-exported here for the readers)
        guarded press ◀────── spend_label(): NEVER_RE | DANGER_RE | PRICE_RE
        positional tap ◀───── pretap_check(): OCR box around the target

Helpers moved here from ~/.claude/skills/wos-daily-collect/scripts/collect.py on
2026-09-08; tests/fixtures/local/native_goldens.json pins their outputs (not
the parsers, which moved on to knowledge/util.py the same day -- E1).
"""
import difflib
import json
import math
import os
import re
import time
from datetime import datetime

import cv2
import numpy as np

from native import drive as drv
# The knowledge base must not depend on native/ (E1), so parse_number,
# parse_ratio, parse_duration and slugify live in knowledge/util.py;
# re-exported here because native/readers/__init__.py:17 and several reader
# modules import them from native.screen and none of them change.
# native/kb.py::record_item imports slugify straight from knowledge.util,
# never from here, precisely so it doesn't drag this module's cv2/numpy/
# native.drive imports in for a two-line string function.
from knowledge.util import parse_number, parse_ratio, parse_duration, slugify

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


# ----------------------------------------------------------------------------- pixels
def centre(box):
    x1, y1, x2, y2 = box
    return (x1 + x2) // 2, (y1 + y2) // 2


# Pixel cues live in native/glyphs.py (see its docstring); re-exported here
# because the readers and both skills import them from native.screen.
from native.glyphs import (  # noqa: E402,F401
    DIALOG_X_SPOTS, _is_dialog_glyph, _is_icy, _rgb, dialog_x_spot,
    green_badges, has_back_arrow, has_dialog_x, has_modal_x)


def _is_tab_label(text):
    """Tab, or a scrap of the System News banner scrolling through the strip?

    The banner ("System News: We will be restarting the servers at 06:00 - 09:00
    UTC...") crosses the same band as the tabs and OCRs in chunks that differ
    every frame. Length alone does not separate them -- it also yields short
    scraps like "ng the period." -- and while any of them counted as a label the
    walk never saw a repeat, so it never concluded the strip had ended, never
    turned around, and spent its whole budget: 72 frames and two missed cart
    claims on 2026-09-11. Tabs are short title-ish names; banner prose carries
    sentence punctuation. The longest real tab is 23 chars.
    """
    t = text.strip()
    return bool(t) and len(t) <= 26 and ":" not in t and not t.endswith(".")


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


def items_near(items, fx, fy, h, w, radius=0.06, rx=None, ry=None):
    """OCR items whose box contains the point or sits within the radius of it.
    A button is much wider than its caption (the Backpack tooltip's Use is
    ~0.28 wide, 0.06 tall around a 0.04 label), so the default exclusion is
    0.14 in x and 0.05 in y: the tap that used an avatar frame on 2026-09-08
    landed 0.10 beside the caption."""
    rx = radius if rx is None else rx
    ry = radius if ry is None else ry
    px, py = fx * w, fy * h
    out = []
    for it in items:
        x1, y1, x2, y2 = it["box"]
        if x1 - rx * w <= px <= x2 + rx * w and y1 - ry * h <= py <= y2 + ry * h:
            out.append(it)
    return out


def pretap_check(items, fx, fy, h, w, extra=()):
    """Reason a positional tap must be refused, or None.

    Fixed HUD fractions and tile taps carry no label of their own, so the guard
    looks at what is drawn under and around the target: a spend label there
    means the layout is not the one the fraction was recorded on."""
    for it in items_near(items, fx, fy, h, w, rx=0.14, ry=0.05):
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

        A strategy that leaves the frame unchanged is retired for the rest of
        the call. Without that, one mis-measured glyph spot eats the whole
        budget: on 2026-09-10 the "Welcome back!" dialog matched a dialog-×
        spot 60px off its real ×, and go_home tapped the same dead pixel eight
        times and reported home-failed on a cold launch.
        """
        blocked, last_sig, last_via = set(), None, None
        for step in range(max_steps):
            img, items, _ = self.frame("home-check")
            h, w = img.shape[:2]
            if self.at_home(items, h, w, img):
                return True
            texts = [norm(i["text"]) for i in items]
            sig = " ".join(sorted(texts))
            if last_via is not None and sig == last_sig:
                self.log(event="home-exit-blocked", via=last_via)
                blocked.add(last_via)
            last_sig = sig
            if "back-arrow" not in blocked and has_back_arrow(img):
                last_via = "back-arrow"
                self.log(event="home-exit", via=last_via)
                drv.tapf(0.148, 0.063) if not self.dry else None
            elif "modal-x" not in blocked and has_modal_x(img):
                last_via = "modal-x"
                self.log(event="home-exit", via=last_via)
                drv.tapf(0.868, 0.128) if not self.dry else None
            elif (spot := dialog_x_spot(img, blocked)) is not None:
                last_via = f"dialog-x{spot}"
                self.log(event="home-exit", via="dialog-x", at=spot)
                drv.tapf(*spot) if not self.dry else None
            elif ((ctl := close_control(items, h, w)) is not None
                  and f"label:{ctl['text']}" not in blocked):
                last_via = f"label:{ctl['text']}"
                self.log(event="home-exit", via=last_via)
                self.tap_item(img, ctl)
            elif ("reveal" not in blocked
                  and (len(texts) <= 3 or any(REVEAL_RE.search(t) for t in texts))):
                # Reward reveals say so themselves ("Tap anywhere to exit") and
                # can carry a whole grid of item icons, so the hint text is the
                # tell AND the tap target: tapping an icon opens its tooltip
                # instead of closing the overlay. Fall back to the strip just
                # above the bottom edge where the hint always sits.
                last_via = "reveal"
                hint = next((i for i in items if REVEAL_RE.search(norm(i["text"]))), None)
                if hint is not None:
                    self.log(event="home-exit", via="reveal-hint")
                    self.tap_item(img, hint)
                else:
                    self.log(event="home-exit", via="reveal-tap")
                    drv.tapf(0.5, 0.93) if not self.dry else None
            else:
                last_via = None   # nothing was tapped; an unchanged frame blames no strategy
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

    @staticmethod
    def tab_matches(want, text):
        """Is `text` this tab, allowing for how badly strip labels OCR?

        Measured on one cart walk (2026-09-10): "Daily Deals" came back as
        "Dailý Deals" on one frame and clipped to "Daily Deal" on the next, and
        neither contains the wanted string, so an exact substring test walked
        straight past a tab that was on screen twice. Squeeze to letters and
        digits, then accept a containment, a prefix either way (clipping), or a
        close ratio (a swapped glyph). The 5-char floor keeps stubs like "Te"
        and "30" from matching everything."""
        a, b = re.sub(r"[^a-z0-9]", "", want), re.sub(r"[^a-z0-9]", "", norm(text))
        if len(a) < 5 or len(b) < 5:
            return bool(a) and a == b
        return a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85

    def _walk_strip(self, label, img, items, h, w, max_steps=24):
        """Tab strips (cart, Deals, Events): SCROLL the strip to find a named tab
        and tap only that tab. Stops on a tab label or the PAGE TITLE (y .19-.27),
        Over 26 chars is not a tab but the System News banner, which scrolls
        THROUGH this band: its text differs every frame, so unfiltered the walk
        never saw a repeat, never concluded the strip ended, and spent its whole
        budget (72 frames, two missed cart claims, 2026-09-11).

        Stops on a tab label or the PAGE TITLE (y .19-.27),
        reversing once at the end of the strip since the tab may sit behind the
        starting position. Until 2026-09-10 this tapped every visible label to
        recentre; harmless on four-tab Deals, but the cart now runs ~15 tabs of
        mostly paid packs, so it opened one €-pack page after another — it was
        three deep in "Tech Storm Pack, €5,99" when the app died mid-screenshot,
        and never reached "Weekly/Monthly Cards" in 24 steps."""
        want = norm(label)
        prev, direction, reversed_once = None, -1, False
        for _ in range(max_steps):
            labels = sorted((i for i in items if 0.10 * h < centre(i["box"])[1] < 0.23 * h
                             and _is_tab_label(i["text"])),
                            key=lambda i: centre(i["box"])[0])
            title = next((i for i in items if 0.19 * h < centre(i["box"])[1] < 0.27 * h and len(i["text"]) > 3), None)
            if title is not None and self.tab_matches(want, title["text"]):
                return True
            hit = next((i for i in labels if self.tab_matches(want, i["text"])), None)
            if hit is not None:
                self.log(event="enter", label=f"strip:{hit['text']}")
                self.tap_item(img, hit)
                time.sleep(2.5)
                return True
            # End of the strip is "the swipe did not move it", NOT "no new labels":
            # every label on the journey back has been seen already, so a seen-set
            # test turns around and then gives up one frame later (2026-09-10,
            # Weekly/Monthly Cards missed whenever a previous item left the strip
            # scrolled past it).
            shown = tuple(norm(i["text"]) for i in labels)
            if shown == prev:
                if reversed_once:
                    return False
                reversed_once, direction = True, 1
            prev = shown
            if not self.dry:
                x0, x1 = (0.85, 0.30) if direction < 0 else (0.30, 0.85)
                drv.swipef(x0, 0.16, x1, 0.16, 500)
                time.sleep(1.8)
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
