"""Screen readers for wos-chief-state.

A reader is a function `read(screen) -> ReaderResult`. It navigates to one
screen from home with the guarded Screen helpers, parses the frame into a doc
fragment (nested dict, slug keys, None for unread) plus provenance per dotted
path, and returns the section status. Readers never press anything but entries,
tabs, tiles that open read-only detail and close controls.

    home ─enter─▶ screen ─frame─▶ items ─parse─▶ (doc, provenance) ─leave─▶ home
                    │ entry-not-found: status failed, doc {}
                    │ title mismatch:  status failed, frame kept as evidence
"""
import os
import re
import time

from native.screen import centre, norm, parse_number, parse_ratio, parse_duration

STATUS_OK, STATUS_PARTIAL, STATUS_FAILED, STATUS_SKIPPED = "ok", "partial", "failed", "skipped"


class ReaderResult:
    __slots__ = ("name", "status", "doc", "provenance", "frames", "notes")

    def __init__(self, name):
        self.name = name
        self.status = STATUS_FAILED
        self.doc = {}
        self.provenance = {}
        self.frames = []
        self.notes = []

    def put(self, path, value, *, raw=None, frame=None, score=None, method="ocr", exact=True):
        """Record a field value in the doc and its evidence in provenance."""
        node = self.doc
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
        self.provenance[path] = {"raw": raw, "frame": os.path.basename(frame) if frame else None,
                                 "score": score, "method": method, "exact": 1 if exact else 0}

    def settle(self, expected_paths):
        """ok when every expected path was read, partial when some were, failed when none.

        An EMPTY expectation list is refused rather than treated as satisfied.
        `got == len(expected_paths)` is 0 == 0 for an empty list, so settle([])
        would hand back an unconditional ok -- exactly the shape of the buildings
        bug on 2026-09-10, where a reader that read nothing reported success.
        backpack, events and heroes all declare EXPECTED = [] and set their own
        status from a count of what they read; this stops anyone wiring one of
        them, or a new reader, into settle() and getting a free green."""
        expected_paths = list(expected_paths)
        if not expected_paths:
            raise ValueError(
                f"{self.name}: settle() needs at least one expected path; a reader with "
                "no fixed expectation must set res.status from what it actually read")
        got = sum(1 for p in expected_paths if p in self.provenance)
        if got == len(expected_paths):
            self.status = STATUS_OK
        elif got:
            self.status = STATUS_PARTIAL
            self.notes.append("unread: " + ", ".join(p for p in expected_paths if p not in self.provenance))
        else:
            self.status = STATUS_FAILED
        return self


def frac(it, h, w):
    x, y = centre(it["box"])
    return x / w, y / h


def in_box(it, h, w, fx0, fy0, fx1, fy1):
    x, y = frac(it, h, w)
    return fx0 <= x <= fx1 and fy0 <= y <= fy1


def label_value(items, h, w, label, dx=(0.0, 0.6), dy=0.02):
    """The OCR item to the right of a 'Label:' item on the same line, or None."""
    lab = next((i for i in items if norm(i["text"]).rstrip(":") == norm(label).rstrip(":")), None)
    if lab is None:
        return None
    lx, ly = frac(lab, h, w)
    cands = [i for i in items if i is not lab and abs(frac(i, h, w)[1] - ly) <= dy
             and dx[0] <= frac(i, h, w)[0] - lx <= dx[1]]
    return min(cands, key=lambda i: frac(i, h, w)[0]) if cands else None


def after_prefix(items, prefix):
    """Item whose text starts with prefix (e.g. 'ID:'), and the remainder text."""
    p = norm(prefix)
    for it in items:
        t = norm(it["text"])
        if t.startswith(p):
            return it, it["text"].strip()[len(prefix):].strip()
    return None, None


def title_is(items, h, w, want, fy_max=0.12):
    return any(in_box(i, h, w, 0.0, 0.0, 1.0, fy_max) and norm(want) in norm(i["text"]) for i in items)


def digits(text):
    m = re.search(r"\d[\d,]*", text or "")
    return int(m.group(0).replace(",", "")) if m else None


def below(items, h, w, ref, dy=(0.01, 0.045), dx=0.08):
    """First item just below `ref` (same column), for name/number stacks."""
    rx, ry = frac(ref, h, w)
    cands = [i for i in items if i is not ref and dy[0] <= frac(i, h, w)[1] - ry <= dy[1]
             and abs(frac(i, h, w)[0] - rx) <= dx]
    return min(cands, key=lambda i: frac(i, h, w)[1]) if cands else None


def leave_via_back(sc, times=1):
    for _ in range(times):
        from native.screen import has_back_arrow
        img, items, _ = sc.frame("leave")
        if has_back_arrow(img):
            from native import drive as drv
            drv.tapf(0.148, 0.063) if not sc.dry else None
            time.sleep(1.8)
        else:
            break
