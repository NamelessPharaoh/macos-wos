"""Entry taps from home that must be seen to take, mixed into native.screen.Screen.

Split out of native/screen.py on 2026-09-15 to keep that file under its
600-line limit. Codex review, same day: a ("hud", fx, fy) entry used to count as
entered the moment the tap was sent; when the game dropped it the city stayed
on screen and every later hop acted on home.
"""
import time

from native.glyphs import band_moved


class EntryMixin:
    def _entry_tap_took(self, alt, img, items, checked):
        """Tap a ("hud", fx, fy) spot. When `checked` (an entry from home) the
        screen must then have left home, else tap once more and give up. The
        retry's pre-tap spend check reads the frame on screen, not the home frame
        from before the first tap."""
        for attempt in range(2 if checked else 1):
            if not self.tapf(alt[1], alt[2], items, img):
                return False
            time.sleep(2.5)
            if not checked:
                return True
            left, img, items = self._left_home(img)
            if left:
                return True
            self.log(event="enter-retry" if attempt == 0 else "enter-no-effect", at=(alt[1], alt[2]))
        return False

    def _left_home(self, before):
        """(left, img, items): has the screen left the home frame `before`?

        Left = no longer at_home, or the whole frame moved (band_moved; city
        frames seconds apart 0.6-0.8, the side panel sliding in 11.6 with the HUD
        still up so at_home cannot tell, a page 38-60). Looked at twice, 1.5 s
        apart: a page still opening at the first look would otherwise take a
        second tap -- on the cart that spot is the gem counter (review, 2026-09-15)."""
        for wait in (0.0, 1.5):
            time.sleep(wait)
            img, items, _ = self.frame("enter-check")
            h, w = img.shape[:2]
            if band_moved(before, img, 0.0, 1.0) or not self.at_home(items, h, w, img):
                return True, img, items
        return False, img, items
