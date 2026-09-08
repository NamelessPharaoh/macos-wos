#!/usr/bin/env python3
"""Drive the native Whiteout Survival Mac app in the background.

Input goes through `silentmouse` (Tier 1 recipe: NSEvent carrying the target
windowNumber -> CGEvent, Cmd flag, mouse subtype 3, WindowUnderMousePointer
fields, CGEventSetWindowLocation, then CGEventPostToPid). Verified by bgtest.py:
clicks and drags land while the window is fully covered by another app, with the
cursor never moving and focus never changing.

Coordinates for tap/swipe are captured-image pixels (what you see in the PNG);
they are converted to window-local points here. `tapf` takes window FRACTIONS,
which survive a move between displays of different backing scale.

One process drives the game at a time: `acquire_run_lock()` takes an flock on
db/.wos-run.lock. It is re-entrant within a process (the daily runner calls the
snapshot in-process at its end), refuses another pid with the holder's
timestamp, and clears a lock whose holder pid is gone.

Moved here from ~/.claude/skills/wos-daily-collect/scripts/wos_drive.py on
2026-09-08 so both skills import one copy.
"""
import atexit
import fcntl
import json
import os
import subprocess
import sys
import time

try:
    import Quartz
except ImportError:  # non-mac test hosts import this module for the lock only
    Quartz = None

APP_SUBSTR = "whiteout"
SILENTMOUSE = os.path.expanduser("~/.cargo/bin/silentmouse")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK_PATH = os.path.join(REPO, "db", ".wos-run.lock")


class WindowNotFound(RuntimeError):
    """No Whiteout Survival window on the on-screen list, even after un-minimising."""


class RunLocked(RuntimeError):
    """Another process holds the run lock."""


# ----------------------------------------------------------------------------- run lock
_LOCK = {"fh": None, "pid": None}


def acquire_run_lock(path=LOCK_PATH, owner="wos"):
    """Take the single-runner lock, or raise RunLocked naming the holder.

    Re-entrant: a second acquire from the same pid returns the handle already
    held (collect.py chains the snapshot in-process). A lock file whose recorded
    pid no longer exists is stale by definition and is taken over; a live holder
    is reported with the timestamp it wrote, so the operator sees "since when".
    """
    if _LOCK["fh"] is not None and _LOCK["pid"] == os.getpid():
        return _LOCK["fh"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        holder = _read_holder(fh)
        fh.close()
        if holder and not _pid_alive(holder.get("pid")):
            # Holder is gone but its flock would have died with it; a second
            # failure here means a genuinely live process without a record.
            with open(path, "w"):
                pass
            return acquire_run_lock(path, owner)
        since = holder.get("since", "?") if holder else "?"
        who = holder.get("owner", "?") if holder else "?"
        pid = holder.get("pid", "?") if holder else "?"
        raise RunLocked(f"another run holds the lock since {since} (owner={who} pid={pid}); "
                        f"wait for it or remove {path} if that pid is dead")
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps({"pid": os.getpid(), "owner": owner,
                         "since": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    fh.flush()
    _LOCK["fh"], _LOCK["pid"] = fh, os.getpid()
    atexit.register(release_run_lock)
    return fh


def release_run_lock():
    fh = _LOCK["fh"]
    if fh is None or _LOCK["pid"] != os.getpid():
        return
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
    except OSError:
        pass
    _LOCK["fh"], _LOCK["pid"] = None, None


def _read_holder(fh):
    try:
        raw = fh.read().strip()
        return json.loads(raw) if raw else None
    except (ValueError, OSError):
        return None


def _pid_alive(pid):
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ----------------------------------------------------------------------------- window
def unminimize():
    """Restore a minimised window WITHOUT activating the app.

    Clearing AXMinimized through System Events puts the window back on the
    on-screen list and leaves focus where it was; `open -a` and `activate` both
    restore it too but steal the front position, which is the thing this driver
    exists to avoid.
    """
    subprocess.run(["osascript", "-e",
                    'tell application "System Events" to tell process "whiteoutsurvival" '
                    'to set value of attribute "AXMinimized" of window 1 to false'],
                   capture_output=True)
    time.sleep(1.2)


def find_window(_retry=True):
    """On-screen list only. A minimised window leaves this list and is unreachable:
    its captures freeze and silentmouse reports 'not on screen'."""
    if Quartz is None:
        raise WindowNotFound("Quartz unavailable: not on macOS")
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    best = None
    for w in infos:
        if APP_SUBSTR not in (w.get("kCGWindowOwnerName") or "").lower():
            continue
        b = w["kCGWindowBounds"]
        if b["Width"] < 400 or b["Height"] < 600:
            continue
        # The app briefly exposes smaller helper windows while moving between
        # displays; a tap sized against one of those lands in the wrong place.
        # The game board is portrait ~642x951 (0.675).
        if not 0.60 < b["Width"] / b["Height"] < 0.75:
            continue
        area = b["Width"] * b["Height"]
        if best is None or area > best[0]:
            best = (area, w)
    if best is None:
        if _retry:
            # Minimised windows leave the on-screen list: captures freeze and
            # silentmouse reports "not on screen". Restore it in place and retry.
            unminimize()
            return find_window(_retry=False)
        raise WindowNotFound("no Whiteout Survival window on the on-screen list, "
                             "and un-minimising it failed")
    return best[1]


def window_geom():
    w = find_window()
    b = w["kCGWindowBounds"]
    return {"wid": int(w["kCGWindowNumber"]), "pid": int(w["kCGWindowOwnerPID"]),
            "x": float(b["X"]), "y": float(b["Y"]),
            "w": float(b["Width"]), "h": float(b["Height"])}


_SCALE = {}


def image_scale(g):
    key = (round(g["x"]), round(g["y"]))
    if key not in _SCALE:
        pt = Quartz.CGPointMake(g["x"] + g["w"] / 2, g["y"] + g["h"] / 2)
        _err, ids, _n = Quartz.CGGetDisplaysWithPoint(pt, 1, None, None)
        did = ids[0] if ids else Quartz.CGMainDisplayID()
        mode = Quartz.CGDisplayCopyDisplayMode(did)
        _SCALE[key] = Quartz.CGDisplayModeGetPixelWidth(mode) / Quartz.CGDisplayModeGetWidth(mode)
    return _SCALE[key]


def shot(path):
    """Window capture works even while the window is occluded."""
    g = window_geom()
    subprocess.run(["screencapture", "-x", "-o", "-l", str(g["wid"]), path], check=True)
    return g


def _local(g, scale, ix, iy):
    """Captured-image pixel -> window-local point (origin = window top-left)."""
    return ix / scale, iy / scale


def _click(g, lx, ly, hold_ms):
    r = subprocess.run([SILENTMOUSE, "click", "-w", str(g["wid"]),
                        "-x", f"{lx:.0f}", "-y", f"{ly:.0f}", "-d", str(hold_ms)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"silentmouse click rc={r.returncode}: {r.stdout.strip()} {r.stderr.strip()}")
    return r.returncode


def tapf(fx, fy, hold_ms=45):
    """Tap at a FRACTION of the window (0..1 from top-left).

    Pixel coordinates read off a screenshot go stale the moment the window moves
    to a display with a different backing scale: the same button is at 440,900 on
    a 1x capture and 880,1800 on a 2x one. Fractions survive that move.
    """
    g = window_geom()
    lx, ly = fx * g["w"], fy * g["h"]
    rc = _click(g, lx, ly, hold_ms)
    print(f"tapf({fx:.3f},{fy:.3f}) -> window-local({lx:.0f},{ly:.0f}) wid={g['wid']}")
    return rc


def tap(ix, iy, hold_ms=45):
    g = window_geom()
    scale = image_scale(g)
    lx, ly = _local(g, scale, ix, iy)
    rc = _click(g, lx, ly, hold_ms)
    print(f"tap image({ix},{iy}) -> window-local({lx:.0f},{ly:.0f}) wid={g['wid']}")
    return rc


def swipe(x1, y1, x2, y2, ms=450):
    g = window_geom()
    scale = image_scale(g)
    ax, ay = _local(g, scale, x1, y1)
    bx, by = _local(g, scale, x2, y2)
    r = subprocess.run([SILENTMOUSE, "drag", "-w", str(g["wid"]),
                        "--from-x", f"{ax:.0f}", "--from-y", f"{ay:.0f}",
                        "--to-x", f"{bx:.0f}", "--to-y", f"{by:.0f}",
                        "-d", str(ms)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"silentmouse drag rc={r.returncode}: {r.stdout.strip()} {r.stderr.strip()}")
    print(f"swipe image({x1},{y1})->({x2},{y2}) wid={g['wid']}")
    return r.returncode


def swipef(fx1, fy1, fx2, fy2, ms=450):
    """Swipe between two window fractions (see tapf for why fractions)."""
    g = window_geom()
    r = subprocess.run([SILENTMOUSE, "drag", "-w", str(g["wid"]),
                        "--from-x", f"{fx1 * g['w']:.0f}", "--from-y", f"{fy1 * g['h']:.0f}",
                        "--to-x", f"{fx2 * g['w']:.0f}", "--to-y", f"{fy2 * g['h']:.0f}",
                        "-d", str(ms)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"silentmouse drag rc={r.returncode}: {r.stdout.strip()} {r.stderr.strip()}")
    print(f"swipef({fx1:.3f},{fy1:.3f})->({fx2:.3f},{fy2:.3f}) wid={g['wid']}")
    return r.returncode


def key(keycode, foreground=False):
    """Send a key to the game in the background.

    DANGER: keycode 53 (Escape) is the game's back gesture and TWO Escapes in a
    row QUIT Whiteout Survival. Never use it as a generic "get unstuck" move.

    Same Tier 1 recipe as the pointer path, keyboard flavour: an NSEvent carrying
    the target windowNumber, converted to a CGEvent and posted with
    CGEventPostToPid. Falls back to the focus-stealing session tap only when
    asked, since that is what the whole exercise was about avoiding.
    """
    g = window_geom()
    if not foreground:
        try:
            import AppKit
            for down in (True, False):
                ns = AppKit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
                    AppKit.NSEventTypeKeyDown if down else AppKit.NSEventTypeKeyUp,
                    (0, 0), 0, 0.0, g["wid"], None, "\x1b", "\x1b", False, keycode,
                )
                ev = ns.CGEvent() if ns is not None else None
                if ev is None:
                    raise RuntimeError("NSEvent keyboard bridge returned nothing")
                Quartz.CGEventSetIntegerValueField(
                    ev, Quartz.kCGKeyboardEventKeycode, keycode)
                Quartz.CGEventPostToPid(g["pid"], ev)
                time.sleep(0.05)
            print(f"key {keycode} (background)")
            return
        except (ImportError, RuntimeError, AttributeError) as e:
            print(f"background key failed ({e}); using foreground path")

    subprocess.run(["osascript", "-e", 'tell application "Whiteout Survival" to activate'], check=False)
    time.sleep(0.9)
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for down in (True, False):
        Quartz.CGEventPost(Quartz.kCGSessionEventTap,
                           Quartz.CGEventCreateKeyboardEvent(src, keycode, down))
        time.sleep(0.05)
    print(f"key {keycode} (foreground path)")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        if cmd == "shot":
            g = shot(argv[2])
            print(f"window pid={g['pid']} wid={g['wid']} at ({g['x']:.0f},{g['y']:.0f}) "
                  f"size {g['w']:.0f}x{g['h']:.0f}pt scale {image_scale(g):.1f}")
        elif cmd == "tapf":
            tapf(float(argv[2]), float(argv[3]))
        elif cmd == "stepf":
            tapf(float(argv[2]), float(argv[3]))
            time.sleep(float(argv[5]) if len(argv) > 5 else 2.0)
            shot(argv[4])
        elif cmd == "tap":
            tap(float(argv[2]), float(argv[3]))
        elif cmd == "swipe":
            swipe(*map(float, argv[2:6]))
        elif cmd == "swipef":
            swipef(*map(float, argv[2:6]))
        elif cmd == "step":
            tap(float(argv[2]), float(argv[3]))
            time.sleep(float(argv[5]) if len(argv) > 5 else 2.0)
            shot(argv[4])
        elif cmd == "key":
            key(int(argv[2]))
        elif cmd == "geom":
            print(window_geom())
        elif cmd == "lock":
            acquire_run_lock(owner="cli")
            print(f"lock held by pid {os.getpid()}; sleeping {argv[2] if len(argv) > 2 else 5}s")
            time.sleep(float(argv[2]) if len(argv) > 2 else 5)
        else:
            sys.exit(f"unknown command {cmd!r}; one of shot tapf stepf tap swipe swipef step key geom lock")
    except WindowNotFound as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv)
