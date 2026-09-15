#!/usr/bin/env python3
"""
bgtest.py  -  background input delivery test for a Designed-for-iPad app on macOS.

Target default: /Applications/Whiteout Survival.app  (override with --target).

WHY THIS EXISTS
  A bare CGEventPostToPid is not what working background-input tools send. This
  script replicates the recipe used by ph0ryn/silentmouse (Tier 1) and the
  SkyLight tricks used by hyprcat/mac-cua (Tiers 2 and 3), and measures whether
  anything landed with occluded-window screenshots, cursor position, and
  frontmost-app checks. Read background-input-test-brief.md first.

SETUP  (run from Terminal.app or iTerm2. THAT app is what needs the permissions.)
  python3 -m venv ~/bgtest-venv
  ~/bgtest-venv/bin/pip install pyobjc pillow
  # System Settings > Privacy & Security > Accessibility      : add the terminal app
  # System Settings > Privacy & Security > Screen Recording   : add the terminal app
  # QUIT and relaunch the terminal app. Then:
  ~/bgtest-venv/bin/python bgtest.py probe

COMMANDS
  probe                      permissions, private symbols, app pid/window/bounds, scale
  idle   [--reps 3]          screenshot noise with NO events (write the number down)
  t0     --x X --y Y         calibration: activates the app, posts a real HID click.
                             MOVES THE REAL CURSOR. Must pass or nothing else counts.
  t1     --x X --y Y [--move] [--no-cmd] [--builder nsevent|cg]
                             silentmouse recipe via CGEventPostToPid
  t2     --x X --y Y [--move] [--no-cmd]
                             micro-activation (window-server frontmost flip) around t1
  t3     --x X --y Y         CGSPostMouseEventToProcess (private SPI)
  drag   --x X --y Y --x2 X2 --y2 Y2 [--tier 1|3] [--ms 500]
COMMON OPTIONS
  --state A|B|C   A = app visible, other app active. B = app fully covered. C = other Space
  --reps N        repetitions (default 3)
  --countdown S   seconds to wait before posting so you can arrange the window state
  --hold-ms 35    ms between down and up
  --settle-ms 500 ms to wait before the "after" screenshot
  --log FILE      results file (default results.jsonl, appended)
X and Y are WINDOW-LOCAL POINTS from the window's top-left corner. Not pixels.
If you measured on a screenshot, divide by the backing scale factor (probe prints it).
"""

import argparse
import ctypes
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import objc  # noqa: F401  (pyobjc)
from Quartz import (
    CGEventCreate,
    CGEventCreateMouseEvent,
    CGEventGetLocation,
    CGEventPost,
    CGEventPostToPid,
    CGEventSetDoubleValueField,
    CGEventSetFlags,
    CGEventSetIntegerValueField,
    CGEventSetLocation,
    CGEventSetTimestamp,
    CGWarpMouseCursorPosition,
    CGWindowListCopyWindowInfo,
    kCGEventFlagMaskCommand,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseDragged,
    kCGEventLeftMouseUp,
    kCGEventMouseMoved,
    kCGHIDEventTap,
    kCGMouseButtonLeft,
    kCGMouseEventButtonNumber,
    kCGMouseEventClickState,
    kCGMouseEventNumber,
    kCGMouseEventPressure,
    kCGMouseEventSubtype,
    kCGMouseEventWindowUnderMousePointer,
    kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent,
    kCGNullWindowID,
    kCGWindowListOptionAll,
)
from AppKit import (
    NSEvent,
    NSEventTypeLeftMouseDown,
    NSEventTypeLeftMouseDragged,
    NSEventTypeLeftMouseUp,
    NSEventTypeMouseMoved,
    NSRunningApplication,
    NSScreen,
    NSWorkspace,
)

try:
    from Quartz import CGPreflightScreenCaptureAccess
except ImportError:  # old pyobjc
    CGPreflightScreenCaptureAccess = None
try:
    from ApplicationServices import AXIsProcessTrusted
except ImportError:
    AXIsProcessTrusted = None

DEFAULT_TARGET = "/Applications/Whiteout Survival.app"
NAME_HINT = "Whiteout"

# --------------------------------------------------------------------------- ctypes: private SPIs


class CGPointC(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def _load(path):
    try:
        return ctypes.cdll.LoadLibrary(path)
    except OSError:
        return None


_CG = _load("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
_CF = _load("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_SL = _load("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight")


def sym(lib, name):
    if lib is None:
        return None
    try:
        return getattr(lib, name)
    except AttributeError:
        return None


CG_SYMBOLS = [
    "CGSMainConnectionID",
    "CGSGetWindowOwner",
    "CGSConnectionGetPID",
    "CGSGetConnectionIDForPID",
    "CGSPostMouseEventToProcess",
    "CGSPostKeyboardEventToProcess",
    "CGSSetConnectionProperty",
    "CGEventSetWindowLocation",
]
SL_SYMBOLS = ["SLPSPostEventRecordTo", "SLSMainConnectionID", "SLSSetConnectionProperty"]


def symbol_report():
    rep = {}
    for n in CG_SYMBOLS:
        rep["CoreGraphics." + n] = sym(_CG, n) is not None
    for n in SL_SYMBOLS:
        rep["SkyLight." + n] = sym(_SL, n) is not None
    return rep


_set_window_location = sym(_CG, "CGEventSetWindowLocation")
if _set_window_location is not None:
    _set_window_location.argtypes = [ctypes.c_void_p, CGPointC]
    _set_window_location.restype = None


def cgs_main_cid():
    f = sym(_CG, "CGSMainConnectionID")
    if f is None:
        return None
    f.restype = ctypes.c_int
    f.argtypes = []
    return f()


def cgs_window_owner(cid, wid):
    f = sym(_CG, "CGSGetWindowOwner")
    if f is None:
        return (None, None)
    f.restype = ctypes.c_int
    f.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    out = ctypes.c_int(0)
    err = f(cid, wid, ctypes.byref(out))
    return (err, out.value if err == 0 else None)


_cfstr_cache = {}


def cfstr(s):
    if s in _cfstr_cache:
        return _cfstr_cache[s]
    _CF.CFStringCreateWithCString.restype = ctypes.c_void_p
    _CF.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    ref = _CF.CFStringCreateWithCString(None, s.encode("utf-8"), 0x08000100)  # kCFStringEncodingUTF8
    _cfstr_cache[s] = ref
    return ref


def cf_bool(v):
    return ctypes.c_void_p.in_dll(_CF, "kCFBooleanTrue" if v else "kCFBooleanFalse").value


def cgs_set_frontmost(cid, target_cid, on):
    f = sym(_CG, "CGSSetConnectionProperty")
    if f is None:
        return None
    f.restype = ctypes.c_int
    f.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    return f(cid, target_cid, cfstr("SetFrontmost"), cf_bool(on))


def cgs_post_mouse(cid, pid, event_type, sx, sy, click_count):
    f = sym(_CG, "CGSPostMouseEventToProcess")
    if f is None:
        return None
    f.restype = ctypes.c_int
    f.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(CGPointC), ctypes.c_int]
    pt = CGPointC(sx, sy)
    return f(cid, pid, event_type, ctypes.byref(pt), click_count)


# --------------------------------------------------------------------------- target discovery


class Target:
    def __init__(self, app, pid, wid, bounds, name):
        self.app, self.pid, self.wid, self.bounds, self.name = app, pid, wid, bounds, name

    def screen_point(self, x, y):
        return (self.bounds[0] + x, self.bounds[1] + y)

    def is_active(self):
        return front_pid() == self.pid  # NSRunningApplication.isActive() is cached; see front_pid


def bundle_id_for(path):
    p = Path(path)
    candidates = [p / "Contents" / "Info.plist", p / "WrappedBundle" / "Info.plist"]
    candidates += list((p / "Wrapper").glob("*.app/Info.plist"))
    for c in candidates:
        if c.exists():
            with open(c, "rb") as f:
                return plistlib.load(f).get("CFBundleIdentifier")
    return None


def running_app(bundle_id, name_hint):
    if bundle_id:
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
        if apps and len(apps):
            return apps[0]
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if name_hint.lower() in (a.localizedName() or "").lower():
            return a
    return None


def window_for_pid(pid):
    best = None
    for w in CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID) or []:
        if int(w.get("kCGWindowOwnerPID", -1)) != pid:
            continue
        if int(w.get("kCGWindowLayer", 0)) != 0:
            continue
        b = w.get("kCGWindowBounds") or {}
        wd, ht = float(b.get("Width", 0)), float(b.get("Height", 0))
        if wd < 200 or ht < 200:
            continue
        if best is None or wd * ht > best[1]:
            best = (w, wd * ht)
    return best[0] if best else None


def resolve_target(path, launch=True):
    bid = bundle_id_for(path)
    app = running_app(bid, NAME_HINT)
    if app is None and launch:
        subprocess.run(["open", "-a", path], check=False)
        for _ in range(60):
            time.sleep(0.5)
            app = running_app(bid, NAME_HINT)
            if app is not None:
                break
        time.sleep(3.0)
    if app is None:
        sys.exit(f"target app not running and could not be launched: {path} (bundle id {bid})")
    pid = int(app.processIdentifier())
    w = None
    for _ in range(20):
        w = window_for_pid(pid)
        if w is not None:
            break
        time.sleep(0.5)
    if w is None:
        sys.exit(f"no layer-0 window found for pid {pid}")
    b = w["kCGWindowBounds"]
    bounds = (float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"]))
    return Target(app, pid, int(w["kCGWindowNumber"]), bounds, app.localizedName() or NAME_HINT)


# --------------------------------------------------------------------------- observation


def cursor_pos():
    p = CGEventGetLocation(CGEventCreate(None))
    return (round(float(p.x), 1), round(float(p.y), 1))


def _lsappinfo_front(key):
    # NSWorkspace.frontmostApplication() is cached per process and goes stale without a
    # run loop (observed: reported Terminal after the target was activated). Ask LaunchServices.
    asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True).stdout.strip()
    if not asn:
        return None
    out = subprocess.run(["lsappinfo", "info", "-only", key, asn], capture_output=True, text=True).stdout
    m = re.search(r'=\s*"?([^"\n]*)"?', out)
    return m.group(1) if m else None


def front_pid():
    v = _lsappinfo_front("pid")
    return int(v) if v and v.isdigit() else -1


def front_name():
    return _lsappinfo_front("name") or "?"


def capture(wid, path):
    r = subprocess.run(["screencapture", "-x", "-l", str(wid), path], capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0


def diff_count(a, b, thr=24):
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return -2  # pillow missing
    def window_only(path):
        # screencapture -l includes the drop shadow, which grows when the window becomes key
        # (710x1019 -> 754x1063 observed). Crop to the fully opaque region = the window itself.
        im = Image.open(path).convert("RGBA")
        bb = im.split()[3].point(lambda v: 255 if v == 255 else 0).getbbox()
        return im.crop(bb).convert("RGB") if bb else im.convert("RGB")

    ia, ib = window_only(a), window_only(b)
    if ia.size != ib.size:
        return -1
    d = ImageChops.difference(ia, ib).convert("L").point(lambda p: 255 if p > thr else 0)
    return d.histogram()[255]


def measured(t, args, action, label):
    """Run `action()` between two occluded-window screenshots; return a result dict."""
    tmp = tempfile.mkdtemp(prefix="bgtest-")
    before, after = os.path.join(tmp, "before.png"), os.path.join(tmp, "after.png")
    ok1 = capture(t.wid, before)
    c0, f0 = cursor_pos(), front_pid()
    t_start = time.monotonic()
    extra = action() or {}
    elapsed_ms = (time.monotonic() - t_start) * 1000
    time.sleep(args.settle_ms / 1000.0)
    c1, f1 = cursor_pos(), front_pid()
    ok2 = capture(t.wid, after)
    dc = diff_count(before, after) if (ok1 and ok2) else -3
    res = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "label": label,
        "state": args.state,
        "pid": t.pid,
        "wid": t.wid,
        "target_active_before": t.is_active(),
        "cursor_before": c0,
        "cursor_after": c1,
        "cursor_moved": c0 != c1,
        "front_before": f0,
        "front_after": f1,
        "target_got_focus": (f1 == t.pid and f0 != t.pid),
        "diff_count": dc,
        "post_ms": round(elapsed_ms, 1),
        "shots": [before, after],
    }
    res.update(extra)
    with open(args.log, "a") as f:
        f.write(json.dumps(res) + "\n")
    print(
        f"[{label}] state={args.state} diff={dc} cursor_moved={res['cursor_moved']} "
        f"target_got_focus={res['target_got_focus']} front={front_name()} {extra if extra else ''}"
    )
    return res


def countdown(s):
    for i in range(int(s), 0, -1):
        print(f"  posting in {i}s ... arrange the window state now", end="\r", flush=True)
        time.sleep(1)
    if s:
        print()


# --------------------------------------------------------------------------- event construction (Tier 1 recipe)

NS_TYPES = {
    "move": NSEventTypeMouseMoved,
    "down": NSEventTypeLeftMouseDown,
    "up": NSEventTypeLeftMouseUp,
    "drag": NSEventTypeLeftMouseDragged,
}
CG_TYPES = {
    "move": kCGEventMouseMoved,
    "down": kCGEventLeftMouseDown,
    "up": kCGEventLeftMouseUp,
    "drag": kCGEventLeftMouseDragged,
}
_evnum = [1000]
_builder_used = {"nsevent": 0, "cg": 0, "winloc_ok": 0, "winloc_fail": 0}


def build_event(kind, t, x, y, builder="nsevent", use_cmd=True):
    """Replicates silentmouse src/macos.rs make_mouse_event."""
    sx, sy = t.screen_point(x, y)
    _evnum[0] += 1
    ev = None
    if builder == "nsevent":
        try:
            ns = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                NS_TYPES[kind], (sx, sy), 0, 0.0, t.wid, None, _evnum[0], 1, 1.0
            )
            ev = ns.CGEvent() if ns is not None else None
        except Exception as e:  # pyobjc bridging problem: fall back, but say so
            print(f"  nsevent builder failed ({e}); falling back to CGEventCreateMouseEvent")
            ev = None
    if ev is not None:
        _builder_used["nsevent"] += 1
    else:
        ev = CGEventCreateMouseEvent(None, CG_TYPES[kind], (sx, sy), kCGMouseButtonLeft)
        _builder_used["cg"] += 1

    CGEventSetFlags(ev, kCGEventFlagMaskCommand if use_cmd else 0)
    CGEventSetLocation(ev, (sx, sy))
    CGEventSetIntegerValueField(ev, kCGMouseEventButtonNumber, 0)
    CGEventSetIntegerValueField(ev, kCGMouseEventSubtype, 3)  # silentmouse: MOUSE_SUBTYPE = 3
    CGEventSetIntegerValueField(ev, kCGMouseEventNumber, _evnum[0])
    CGEventSetIntegerValueField(ev, kCGMouseEventClickState, 1 if kind in ("down", "up", "drag") else 0)
    CGEventSetDoubleValueField(ev, kCGMouseEventPressure, 1.0 if kind in ("down", "drag") else 0.0)
    CGEventSetIntegerValueField(ev, kCGMouseEventWindowUnderMousePointer, t.wid)
    CGEventSetIntegerValueField(ev, kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent, t.wid)
    CGEventSetTimestamp(ev, time.monotonic_ns())
    if _set_window_location is not None:
        try:
            _set_window_location(ctypes.c_void_p(objc.pyobjc_id(ev)), CGPointC(x, y))
            _builder_used["winloc_ok"] += 1
        except Exception as e:
            _builder_used["winloc_fail"] += 1
            print(f"  CGEventSetWindowLocation call failed: {e}")
    return ev


def t1_post(t, x, y, move, use_cmd, builder, hold_ms):
    if move:
        CGEventPostToPid(t.pid, build_event("move", t, x, y, builder, use_cmd))
        time.sleep(0.03)
    CGEventPostToPid(t.pid, build_event("down", t, x, y, builder, use_cmd))
    time.sleep(hold_ms / 1000.0)
    CGEventPostToPid(t.pid, build_event("up", t, x, y, builder, use_cmd))
    return {"builder_stats": dict(_builder_used), "cmd_flag": use_cmd, "leading_move": move}


def t1_drag(t, x, y, x2, y2, use_cmd, builder, ms):
    steps = max(1, min(240, ms // 16))
    CGEventPostToPid(t.pid, build_event("down", t, x, y, builder, use_cmd))
    for i in range(1, steps + 1):
        time.sleep(ms / 1000.0 / steps)
        px, py = x + (x2 - x) * i / steps, y + (y2 - y) * i / steps
        CGEventPostToPid(t.pid, build_event("drag", t, px, py, builder, use_cmd))
    CGEventPostToPid(t.pid, build_event("up", t, x2, y2, builder, use_cmd))
    return {"steps": steps, "cmd_flag": use_cmd}


# --------------------------------------------------------------------------- Tier 0: real HID click


def t0_post(t, x, y, hold_ms):
    sx, sy = t.screen_point(x, y)
    t.app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
    time.sleep(0.6)
    mv = CGEventCreateMouseEvent(None, kCGEventMouseMoved, (sx, sy), kCGMouseButtonLeft)
    CGEventPost(kCGHIDEventTap, mv)
    time.sleep(0.05)
    dn = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, (sx, sy), kCGMouseButtonLeft)
    CGEventPost(kCGHIDEventTap, dn)
    time.sleep(hold_ms / 1000.0)
    up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (sx, sy), kCGMouseButtonLeft)
    CGEventPost(kCGHIDEventTap, up)
    return {"activated_target": True, "hid_session_click": True}


# --------------------------------------------------------------------------- Tier 2: micro-activation


def t2_post(t, x, y, move, use_cmd, builder, hold_ms):
    cid = cgs_main_cid()
    if cid is None or sym(_CG, "CGSSetConnectionProperty") is None or sym(_CG, "CGSGetWindowOwner") is None:
        return {"skipped": "CGSMainConnectionID / CGSGetWindowOwner / CGSSetConnectionProperty missing"}
    err_t, target_cid = cgs_window_owner(cid, t.wid)
    if target_cid is None:
        return {"skipped": f"CGSGetWindowOwner(target) err={err_t}"}
    prev_pid = front_pid()
    prev_cid = None
    if prev_pid != t.pid:
        pw = window_for_pid(prev_pid)
        if pw is not None:
            _, prev_cid = cgs_window_owner(cid, int(pw["kCGWindowNumber"]))
    t0_ = time.monotonic()
    r_on = cgs_set_frontmost(cid, target_cid, True)
    try:
        t1_post(t, x, y, move, use_cmd, builder, hold_ms)
    finally:
        r_off = cgs_set_frontmost(cid, target_cid, False)
        r_prev = cgs_set_frontmost(cid, prev_cid, True) if prev_cid is not None else None
    return {
        "target_cid": target_cid,
        "prev_cid": prev_cid,
        "rc_set_front_on": r_on,
        "rc_set_front_off": r_off,
        "rc_restore_prev": r_prev,
        "flip_window_ms": round((time.monotonic() - t0_) * 1000, 1),
        "cmd_flag": use_cmd,
        "leading_move": move,
    }


# --------------------------------------------------------------------------- Tier 3: CGSPostMouseEventToProcess


def t3_post(t, x, y, hold_ms, move=True):
    cid = cgs_main_cid()
    if cid is None or sym(_CG, "CGSPostMouseEventToProcess") is None:
        return {"skipped": "CGSPostMouseEventToProcess missing on this macOS"}
    sx, sy = t.screen_point(x, y)
    # ASSUMPTION: event_type uses CGEventType numbering (moved=5, leftDown=1, leftUp=2).
    # mac-cua declares the SPI but its release branch never calls it, so this is unverified.
    rcs = {}
    if move:
        rcs["move"] = cgs_post_mouse(cid, t.pid, 5, sx, sy, 0)
        time.sleep(0.03)
    rcs["down"] = cgs_post_mouse(cid, t.pid, 1, sx, sy, 1)
    time.sleep(hold_ms / 1000.0)
    rcs["up"] = cgs_post_mouse(cid, t.pid, 2, sx, sy, 1)
    return {"rc": rcs, "event_type_assumption": "CGEventType numbering"}


def t3_drag(t, x, y, x2, y2, ms):
    cid = cgs_main_cid()
    if cid is None or sym(_CG, "CGSPostMouseEventToProcess") is None:
        return {"skipped": "CGSPostMouseEventToProcess missing"}
    steps = max(1, min(240, ms // 16))
    sx, sy = t.screen_point(x, y)
    rcs = [cgs_post_mouse(cid, t.pid, 1, sx, sy, 1)]
    for i in range(1, steps + 1):
        time.sleep(ms / 1000.0 / steps)
        px, py = t.screen_point(x + (x2 - x) * i / steps, y + (y2 - y) * i / steps)
        rcs.append(cgs_post_mouse(cid, t.pid, 6, px, py, 1))  # leftMouseDragged = 6 (same assumption)
    ex, ey = t.screen_point(x2, y2)
    rcs.append(cgs_post_mouse(cid, t.pid, 2, ex, ey, 1))
    return {"rc_nonzero": [r for r in rcs if r], "steps": steps}


# --------------------------------------------------------------------------- commands


def cmd_probe(args):
    print("== permissions ==")
    print("  AXIsProcessTrusted:", AXIsProcessTrusted() if AXIsProcessTrusted else "n/a (ApplicationServices import failed)")
    print("  ScreenCapture preflight:", CGPreflightScreenCaptureAccess() if CGPreflightScreenCaptureAccess else "n/a")
    print("  python:", sys.executable)
    print("  frontmost app:", front_name(), front_pid())
    print("== private symbols ==")
    for k, v in symbol_report().items():
        print(f"  {k}: {v}")
    print("== target ==")
    t = resolve_target(args.target, launch=not args.no_launch)
    print(f"  name={t.name} pid={t.pid} wid={t.wid} active={t.is_active()}")
    print(f"  bounds (points, CG top-left origin): x={t.bounds[0]} y={t.bounds[1]} w={t.bounds[2]} h={t.bounds[3]}")
    print("  backing scale factor:", float(NSScreen.mainScreen().backingScaleFactor()))
    cid = cgs_main_cid()
    if cid is not None:
        err, owner = cgs_window_owner(cid, t.wid)
        print(f"  CGS main cid={cid} target window owner cid={owner} (err={err})")
    shot = os.path.join(tempfile.mkdtemp(prefix="bgtest-"), "probe.png")
    print("  occluded capture works:", capture(t.wid, shot), shot)
    print("  -> open that png. If it shows wallpaper or is blank, Screen Recording is not granted to this terminal app (relaunch after granting).")


def cmd_idle(args):
    t = resolve_target(args.target)
    for i in range(args.reps):
        measured(t, args, lambda: {"idle": True}, f"idle#{i + 1}")


def cmd_t0(args):
    t = resolve_target(args.target)
    print("Tier 0 moves the REAL cursor and activates the app. Hands off the mouse.")
    countdown(args.countdown)
    saved = cursor_pos()
    for i in range(args.reps):
        measured(t, args, lambda: t0_post(t, args.x, args.y, args.hold_ms), f"t0#{i + 1}")
        time.sleep(0.8)
    if args.restore_cursor:
        CGWarpMouseCursorPosition(saved)


def cmd_t1(args):
    t = resolve_target(args.target)
    countdown(args.countdown)
    for i in range(args.reps):
        use_cmd = (not args.no_cmd) and (not t.is_active())
        label = f"t1{'b' if args.move else 'a'}{'-nocmd' if args.no_cmd else ''}-{args.builder}#{i + 1}"
        measured(t, args, lambda: t1_post(t, args.x, args.y, args.move, use_cmd, args.builder, args.hold_ms), label)
        time.sleep(0.8)


def cmd_t2(args):
    t = resolve_target(args.target)
    print("Tier 2 flips window-server frontmost flags. Watch the menu bar and your active window. If focus gets stuck, click any window.")
    countdown(args.countdown)
    for i in range(args.reps):
        use_cmd = (not args.no_cmd) and (not t.is_active())
        label = f"t2{'b' if args.move else 'a'}{'-nocmd' if args.no_cmd else ''}#{i + 1}"
        measured(t, args, lambda: t2_post(t, args.x, args.y, args.move, use_cmd, args.builder, args.hold_ms), label)
        time.sleep(0.8)


def cmd_t3(args):
    t = resolve_target(args.target)
    countdown(args.countdown)
    for i in range(args.reps):
        measured(t, args, lambda: t3_post(t, args.x, args.y, args.hold_ms, move=args.move), f"t3#{i + 1}")
        time.sleep(0.8)


def cmd_drag(args):
    t = resolve_target(args.target)
    countdown(args.countdown)
    for i in range(args.reps):
        if args.tier == 1:
            use_cmd = (not args.no_cmd) and (not t.is_active())
            measured(t, args, lambda: t1_drag(t, args.x, args.y, args.x2, args.y2, use_cmd, args.builder, args.ms), f"drag-t1#{i + 1}")
        else:
            measured(t, args, lambda: t3_drag(t, args.x, args.y, args.x2, args.y2, args.ms), f"drag-t3#{i + 1}")
        time.sleep(0.8)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", default=DEFAULT_TARGET)
    p.add_argument("--state", choices=["A", "B", "C", "front"], default="A")
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--countdown", type=float, default=0)
    p.add_argument("--hold-ms", type=int, default=35)
    p.add_argument("--settle-ms", type=int, default=500)
    p.add_argument("--log", default="results.jsonl")
    p.add_argument("--no-launch", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe")
    sub.add_parser("idle")

    s0 = sub.add_parser("t0")
    s0.add_argument("--x", type=float, required=True)
    s0.add_argument("--y", type=float, required=True)
    s0.add_argument("--restore-cursor", action="store_true")

    for name in ("t1", "t2"):
        s = sub.add_parser(name)
        s.add_argument("--x", type=float, required=True)
        s.add_argument("--y", type=float, required=True)
        s.add_argument("--move", action="store_true", help="post a leading mouseMoved through the same channel")
        s.add_argument("--no-cmd", action="store_true", help="do not set the Command flag on inactive targets")
        s.add_argument("--builder", choices=["nsevent", "cg"], default="nsevent")

    s3 = sub.add_parser("t3")
    s3.add_argument("--x", type=float, required=True)
    s3.add_argument("--y", type=float, required=True)
    s3.add_argument("--move", action="store_true")

    sd = sub.add_parser("drag")
    sd.add_argument("--x", type=float, required=True)
    sd.add_argument("--y", type=float, required=True)
    sd.add_argument("--x2", type=float, required=True)
    sd.add_argument("--y2", type=float, required=True)
    sd.add_argument("--tier", type=int, choices=[1, 3], default=1)
    sd.add_argument("--ms", type=int, default=500)
    sd.add_argument("--no-cmd", action="store_true")
    sd.add_argument("--builder", choices=["nsevent", "cg"], default="nsevent")

    args = p.parse_args()
    if os.geteuid() == 0:
        sys.exit("do not run as root; TCC grants are per user session")
    {"probe": cmd_probe, "idle": cmd_idle, "t0": cmd_t0, "t1": cmd_t1, "t2": cmd_t2, "t3": cmd_t3, "drag": cmd_drag}[args.cmd](args)


if __name__ == "__main__":
    main()
