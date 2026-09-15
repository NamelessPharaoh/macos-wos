"""Vertical scroll scans, mixed into native.screen.Screen.

Split out of native/screen.py on 2026-09-15, when adding the scan would have
taken that file past its 600-line limit again. It needs only what a Screen
already has (frame, log, dry) plus the driver, so it rides along as a mixin and
callers keep writing `screen.scroll_scan(...)`.
"""
import time

from native import drive as drv


class ScrollMixin:
    def scroll_scan(self, tag, look, moved, ok=lambda img, items: True,
                    max_frames=12, fy_from=0.85, fy_to=0.35):
        """Swipe a vertical view step by step, calling look(img, items, path) on
        every frame until it returns something. Returns (hit, complete).

        complete=True: look found it, or the view reached its end -- two swipes
        in a row that moved(prev, img) says left it still. One still frame is
        not the end: the game drops a drag now and then (2026-09-14, Deals).
        complete=False: the frame budget ran out, ok(img, items) rejected a
        frame (a dialog opened), or this is a dry run. The caller must not read
        that as "nothing there". No swipe follows the last frame looked at."""
        prev, stills = None, 0
        for n in range(max_frames):
            img, items, path = self.frame(f"{tag}-{n}")
            if not ok(img, items):
                self.log(event="scroll-stopped", tag=tag, frame=path)
                return None, False
            hit = look(img, items, path)
            if hit:
                return hit, True
            stills = stills + 1 if prev is not None and not moved(prev, img) else 0
            if stills >= 2:
                return None, True
            if self.dry or n == max_frames - 1:
                break
            prev = img
            h, w = img.shape[:2]
            drv.swipe(int(0.5 * w), int(fy_from * h), int(0.5 * w), int(fy_to * h), 600)
            time.sleep(1.4)
        return None, False
