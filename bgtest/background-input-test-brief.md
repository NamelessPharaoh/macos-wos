# Background input to a Designed-for-iPad app on macOS 26.6.2 (Apple Silicon): test brief

Purpose: decide, with evidence, whether a pid-routed or window-server-routed mouse event can land in an iPad app running on the Mac while the app is NOT active and NOT visible. The earlier test used a bare `CGEventPostToPid` and is not a valid negative. This brief replicates what the working background-input tools actually send (verified by reading the source of `ph0ryn/silentmouse` and `hyprcat/mac-cua`).

Rules for the run:
- Execute tiers in order. Stop at the first PASS. Record every result in the table at the bottom.
- "Landed" means a visible state change in the app, verified by an occluded-window screenshot, three times in a row. A single flicker is not a pass.
- Never take coordinates from a screenshot without dividing by the backing scale factor. Screenshots are pixels; events are points.
- Do not write your own CGEvent code until Tier 1 has been run with silentmouse as shipped.

---

## Runbook for the Claude Code session (use `bgtest.py`, then silentmouse as a cross-check)

`bgtest.py` (same folder) implements Tiers 0 to 3 and the drag test, and logs every attempt to `results.jsonl` with diff count, cursor movement, and focus change. It has NOT been executed on a Mac yet; if a command errors, read the traceback before touching the tier logic. Most likely first failures are pyobjc bridging (`NSEvent.CGEvent()` or `objc.pyobjc_id` on the CGEventRef); the script already falls back to a CG-built event and reports which builder and whether the private window-location setter ran (`builder_stats` in the log).

```bash
# 0. environment, from Terminal.app (or iTerm2). That app gets the permissions.
python3 -m venv ~/bgtest-venv && ~/bgtest-venv/bin/pip install pyobjc pillow
# grant Accessibility + Screen Recording to the terminal app, QUIT and relaunch it
cargo install --git https://github.com/ph0ryn/silentmouse.git      # cross-check tool

# 1. probe: permissions, symbols, pid/wid/bounds, scale, occluded-capture check
~/bgtest-venv/bin/python bgtest.py probe
#    -> AXIsProcessTrusted must be True, preflight True, probe.png must show the app.
#    -> record the symbol list. Missing CGSSetConnectionProperty kills t2; missing CGSPostMouseEventToProcess kills t3.

# 2. choose the target button, get X Y in window-local POINTS (divide screenshot pixels by the scale factor)

# 3. idle noise (no events), write the number down
~/bgtest-venv/bin/python bgtest.py --state front idle --reps 3

# 4. Tier 0 calibration (moves real cursor, activates app). Must pass.
~/bgtest-venv/bin/python bgtest.py --state front t0 --x X --y Y --restore-cursor

# 5. Tier 1. Terminal is frontmost, so "state A" is automatic. --countdown gives time to cover the window for B.
~/bgtest-venv/bin/python bgtest.py --state A t1 --x X --y Y
~/bgtest-venv/bin/python bgtest.py --state A t1 --x X --y Y --move
~/bgtest-venv/bin/python bgtest.py --state B --countdown 5 t1 --x X --y Y --move
~/bgtest-venv/bin/python bgtest.py --state C --countdown 8 t1 --x X --y Y --move     # move app to another Space first
#    cross-check the same points with silentmouse (independent implementation of the same recipe):
silentmouse mouse move -w WID -x X -y Y && silentmouse click -w WID -x X -y Y
#    only if A fails: isolate the Cmd modifier
~/bgtest-venv/bin/python bgtest.py --state A t1 --x X --y Y --move --no-cmd
#    only if the nsevent builder errored: force the CG builder
~/bgtest-venv/bin/python bgtest.py --state A t1 --x X --y Y --move --builder cg

# 6. Tier 2 (only if probe shows CGSSetConnectionProperty + CGSGetWindowOwner). Watch menu bar / focus.
~/bgtest-venv/bin/python bgtest.py --state A t2 --x X --y Y --move
~/bgtest-venv/bin/python bgtest.py --state B --countdown 5 t2 --x X --y Y --move

# 7. Tier 3 (only if probe shows CGSPostMouseEventToProcess)
~/bgtest-venv/bin/python bgtest.py --state A t3 --x X --y Y --move
~/bgtest-venv/bin/python bgtest.py --state B --countdown 5 t3 --x X --y Y --move

# 8. only after a pass in B or C: drag
~/bgtest-venv/bin/python bgtest.py --state B --countdown 5 drag --x X --y Y --x2 X2 --y2 Y2 --tier 1
```

Pass criterion per run: `diff_count` clearly above idle noise, `cursor_moved` false, `target_got_focus` false, 3/3 reps. Any pass with `cursor_moved` true or `target_got_focus` true is a foreground result and counts as FAIL for this brief. Deliver back: `results.jsonl`, the probe output, and the filled results table below.

Two things the script does not do: it cannot verify you actually covered the window for state B (do it, use the countdown), and Tier 3's event-type numbering is an assumption (CGEventType values) because the only public reference declares the SPI without calling it. If Tier 3 returns rc 0 but nothing lands, try the numbering 0/1/2 for move/down/up before declaring it dead.

---

## Step 0. Fixed facts and setup

0. Permissions, before anything else. The earlier run granted Accessibility mid-test without relaunching, which alone can produce false negatives: an untrusted process's `CGEventPost*` calls fail silently (no error, no effect).
   - TCC attributes the grant to the responsible host app, not the script. Running from Terminal.app means Terminal.app must be trusted; from iTerm2, iTerm2; from Claude Code inside the Claude desktop app, Claude.app. A grant on a bare `python3` or `cargo` binary does nothing for a script launched from a terminal.
   - Grant System Settings > Privacy & Security > Accessibility to that host app, then QUIT and relaunch it. Do the same for Screen Recording, otherwise `screencapture -l` of an occluded window returns wallpaper or blank, which looks like "no change" and fakes a fail. macOS 15+ re-prompts for Screen Recording periodically; if a capture suddenly goes blank mid-run, that is why.
   - Verify from inside the actual runner before Tier 0:

```python
from ApplicationServices import AXIsProcessTrusted
print("AX trusted:", AXIsProcessTrusted())
```

     and confirm `screencapture -x -l <WINDOW_ID> /tmp/t.png` of the app while it is covered by another window shows the app, not the desktop.
   - Any rebuilt binary (Tier 1c fork, a re-run `cargo install`) gets a new code hash and loses its grant. Re-grant and relaunch after every rebuild. silentmouse's README says the same: run it from a stable path.
   - Order matters for reading the old result: if the pid tests ran before the grant, they are void. If they ran after the grant in the same un-relaunched process, they are suspect. Either way, rerun.

1. Get the app's PID and CGWindowID (both in points):

```python
# python3 -c '...'  (pip install pyobjc-framework-Quartz --break-system-packages if needed)
from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID):
    if w.get("kCGWindowLayer") == 0 and "APPNAME" in (w.get("kCGWindowOwnerName") or ""):
        print(w["kCGWindowNumber"], w["kCGWindowOwnerPID"], w["kCGWindowBounds"])
```

   Confirm `kCGWindowOwnerPID` == `pgrep -x APPNAME`. If the window is owned by a different process, that process is the target for every tier below, not the app.

2. Pick a target button: something that changes the screen when tapped and can be untapped (open/close a panel). Record its window-local point coordinates: `(x_points, y_points)` measured from the window's top-left in points.

3. Verification command (works on occluded windows and other Spaces):

```bash
screencapture -x -l <WINDOW_ID> /tmp/before.png
# post the event
sleep 0.5
screencapture -x -l <WINDOW_ID> /tmp/after.png
magick compare -metric AE /tmp/before.png /tmp/after.png /tmp/diff.png 2>&1   # brew install imagemagick
```

   A pass is a diff count clearly above the app's idle animation noise. Measure the idle noise first (two captures with no event) and write the number down.

4. Symbol probe. On macOS 26 several SkyLight SPIs were removed. Run this before Tiers 2 to 4 and paste the output into the results table:

```python
import ctypes, ctypes.util
cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
sl = ctypes.cdll.LoadLibrary("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight")
for name in ["CGSMainConnectionID","CGSGetWindowOwner","CGSConnectionGetPID",
             "CGSGetConnectionIDForPID","CGSPostMouseEventToProcess",
             "CGSPostKeyboardEventToProcess","CGSSetConnectionProperty",
             "CGEventSetWindowLocation"]:
    print(name, hasattr(cg, name))
for name in ["SLPSPostEventRecordTo","SLSMainConnectionID","SLSSetConnectionProperty"]:
    print(name, hasattr(sl, name))
```

   Known as of mac-cua's code comments: `CGSGetConnectionIDForPID` and `CGSPostKeyboardEventToProcess` are gone in macOS 26; `CGSConnectionGetPID` replaces the first. The mouse variant and `CGSSetConnectionProperty` are unverified on 26.6.2. The probe decides.

5. Three occlusion states to test at each tier:
   - A: app window visible, another app active (frontmost).
   - B: app window fully covered by another window.
   - C: app window on a different Space (or a BetterDisplay virtual display).
   The goal state is B or C. A passing only in A is a partial result, not a solution.

---

## Tier 0. Calibration (must pass or nothing else counts)

App frontmost. Post a session-level click at the target using the same coordinate pipeline you will use later:

```bash
cliclick c:<screen_x>,<screen_y>      # brew install cliclick; screen coords in points
```

Verify with the screenshot diff. If this does not land, the coordinates are wrong. Fix before continuing. Note whether a preceding `cliclick m:` (move) is required; the earlier run reported that it was.

---

## Tier 1. The full recipe, as shipped (silentmouse)

What silentmouse sends, per event (src/macos.rs):
- NSEvent created with `windowNumber = target CGWindowID`, converted to CGEvent.
- `CGEventFlags::MaskCommand` set when the target app is not active (AppKit click-through rule for non-key windows).
- Screen location set; button field 3 = 0; mouse subtype field 7 = 3.
- Fields 91 and 92 (`kCGMouseEventWindowUnderMousePointer`, `...ThatCanHandleThisEvent`) = target CGWindowID.
- Fresh timestamp.
- Private `CGEventSetWindowLocation(event, window_local_point)` resolved via dlsym.
- `CGEventPostToPid`.

Run:

```bash
cargo install --git https://github.com/ph0ryn/silentmouse.git
# grant Accessibility to the terminal when prompted

# 1a: click only (mac-cua deliberately skips the move because a pid-posted mouseMoved leaks to the real cursor)
silentmouse click -w <WINDOW_ID> -x <x_points> -y <y_points>

# 1b: move first, then click (the earlier session-tap run needed a leading move)
silentmouse mouse move -w <WINDOW_ID> -x <x_points> -y <y_points>
silentmouse click -w <WINDOW_ID> -x <x_points> -y <y_points>
```

For each of 1a and 1b, run states A, B, C, three repetitions each. Also record: did the real cursor move during `mouse move`? If yes, note it; the move step cannot be part of a background solution, but the result still answers whether the app accepts pid-routed down/up once its hover state is primed.

Tier 1c (only if 1a and 1b fail in state A): fork silentmouse, delete the `CGEvent::set_flags(Some(&event), CGEventFlags::MaskCommand);` line, rebuild, rerun 1b in state A. This isolates "app rejects Cmd-modified clicks" from "app rejects background clicks". Expect AppKit to swallow the click and activate the window instead; if the app becomes active, that is the expected fail, write it down.

---

## Tier 2. Micro-activation + Tier 1 post

Idea (mac-cua `skylight.micro_activate`): flip the window server's frontmost flag on the target's connection, post, flip it back, all inside ~10 ms. Their claim: no window ordering change, no visual change, no menu bar swap. This attacks the exact gate the earlier run found ("only lands when frontmost").

On macOS 26, mac-cua's implementation is a silent no-op because it looks up the connection via the removed `CGSGetConnectionIDForPID`. Get the connection from the window instead:

```python
import ctypes, ctypes.util, subprocess, time
from Foundation import NSString
from CoreFoundation import kCFBooleanTrue, kCFBooleanFalse

cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
cg.CGSMainConnectionID.restype = ctypes.c_int
cg.CGSGetWindowOwner.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
cg.CGSSetConnectionProperty.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]

WID = <WINDOW_ID>
cid = cg.CGSMainConnectionID()
owner = ctypes.c_int(0)
assert cg.CGSGetWindowOwner(cid, WID, ctypes.byref(owner)) == 0, "CGSGetWindowOwner failed"
key = NSString.stringWithString_("SetFrontmost")

def set_front(target_cid, on):
    val = kCFBooleanTrue if on else kCFBooleanFalse
    return cg.CGSSetConnectionProperty(cid, target_cid, ctypes.c_void_p(key.__c_void_p__()),
                                       ctypes.c_void_p(val.__c_void_p__()))

t = time.monotonic()
set_front(owner.value, True)
subprocess.run(["silentmouse", "click", "-w", str(WID), "-x", "<x>", "-y", "<y>", "-d", "5"])
set_front(owner.value, False)
set_front(cid, True)
print("window ms", (time.monotonic() - t) * 1000)
```

Only run if the probe shows `CGSSetConnectionProperty` and `CGSGetWindowOwner` present. Check the return codes; a non-zero from `CGSSetConnectionProperty` means the SPI is gated on 26.6.2 and the tier is dead. Watch the menu bar and your own active window during the run; if either changes, the "invisible" claim does not hold on this OS version, record it.

---

## Tier 3. Window-server direct post

Only if the probe shows `CGSPostMouseEventToProcess`. Signature used by mac-cua:

```
CGError CGSPostMouseEventToProcess(CGSConnectionID cid, pid_t target_pid,
                                   int event_type, const CGPoint *point, int click_count)
```

Post `LeftMouseDown` (1) then `LeftMouseUp` (2) at the screen point (points, not window-local), 30 ms apart, states A/B/C. Combine with Tier 2's frontmost flip if Tier 3 alone fails.

---

## Tier 4. SLPSPostEventRecordTo

yabai uses it to focus windows without raising them by posting hand-built event records to a ProcessSerialNumber. Extending it to synthetic mouse records is reverse-engineering work, not a test. Do not start it without an explicit go from Mahmoud. Note only whether the symbol exists.

---

## Interpretation

- Tier 0 fails: coordinates or measurement broken. Everything before this was invalid too.
- Tier 1 passes in B or C: the native background path is alive. Next test is drag (`silentmouse drag`) because map scrolling matters, then a 30-minute soak for stuck modifier state.
- Tier 1 passes only in A: AppKit routing works but occlusion routing via fields 91/92 does not reach UIKit-on-Mac. Try Tier 3 for B/C.
- Tier 1 fails in A even with the leading move: the app most likely hit-tests against the real cursor position, not the event's location. Tier 2 is the only thing left; if it also fails, the earlier conclusion stands and the path is dead on 26.6.2. Move to WebDriverAgent on a real device, ADB on an emulator, or a concurrent Screen Sharing session.
- Any tier that passes only with a real cursor move is a foreground solution wearing a costume. Mark it FAIL for the purpose of this brief.

## Results table (filled 2026-09-07, macOS 26.6.2, Whiteout Survival pid 76707 wid 43654, target = World/City toggle at window-local (532, 903) points, scale 1.0)

Idle noise (no events, occluded-window capture, 500 ms apart): 8,942 / 13,684 / 11,018 px on the test account (2,581 to 5,573 on the tutorial account, with one 61,760 spike from an in-game cutscene). Any diff below ~15k = nothing landed.

| Tier | Variant | State | Cursor moved? | Diff count (idle noise = 9k-14k) | Reps landed /3 | Notes |
|------|---------|-------|---------------|-------------------------------|----------------|-------|
| 0    | HID click via bgtest t0 (activates app, moves cursor) | front | yes (by design) | 13,092 / 572,663 / 544,363 | 2/3 | Rep 1 swallowed in both t0 runs: the first click right after activation never registers, reps 2-3 toggle World/City. Coordinates + capture confirmed. Focus check reported target_got_focus=True on rep 1 after the lsappinfo fix. |
| 1a   | click only (bgtest, nsevent builder, Cmd flag, CGEventSetWindowLocation ok) | A | no | 572,314 / 542,188 / 572,726 | 3/3 | Terminal frontmost throughout. Repeated once more later (mislabelled B rows, see log corrections): 3/3 again. |
| 1a   | click only (bgtest) | B (covered by Arc 0..1920x31..1016, Arc frontmost) | no | 520,288 / 527,031 / 529,690 | 3/3 | PASS. Coverage verified before and after via on-screen window list. |
| 1a   | click only (bgtest) | C | not run | | | Stopped at first PASS in B per runbook. |
| 1b   | move + click (bgtest) | A | no | 541,603 / 553,190 / 542,091 | 3/3 | Leading move did not leak to the real cursor (cursor stayed at 2717,480 on display 3). |
| 1b   | move + click (bgtest) | B | no | 521,175 / 529,601 / 521,602 | 3/3 | PASS. |
| 1b   | move + click (bgtest) | C | not run | | | |
| 1a/1b | silentmouse click / move+click (cross-check) | A | no | 572,367 / 546,073 / 570,419 and 545,447 / 570,306 / 542,209 | 3/3 + 3/3 | rc 0, `background_flag=true window_location_setter=true`. Matches bgtest. |
| 1a/1b | silentmouse click / move+click (cross-check) | B | no | 525,025 / 521,540 / 529,601 and 521,279 / 528,172 / 522,196 | 3/3 + 3/3 | PASS, matches bgtest. |
| 1c   | no Cmd flag | A | not run | | | Not needed: 1a/1b passed in A. |
| 2    | frontmost flip + 1b | A / B / C | not run | | | Not needed: Tier 1 passed in B. Symbols present (CGSSetConnectionProperty, CGSGetWindowOwner) if ever needed. SPI return codes: n/a |
| 3    | CGSPostMouseEventToProcess | A / B / C | dead | | | Symbol absent on 26.6.2 (probe). |
| drag | Tier 1 recipe, (321,600) -> (321,350), 31 steps / 500 ms | B | no | 275,934 / 244,887 / 226,937 | 3/3 | Map scrolled (Arena tile came into view) while covered by Arc. |
| -    | any variant, window MINIMIZED | (M) | no | 0 | 0/3 | Not a brief state, hit by accident: minimized window leaves the on-screen list, screencapture returns a frozen frame, silentmouse refuses (rc 3 "not on screen"). Unmeasurable; do not minimize. |

Symbol probe output:

```
== permissions ==
  AXIsProcessTrusted: True
  ScreenCapture preflight: True
  python: /Users/melsawah1/bgtest-venv/bin/python
  frontmost app: Terminal 69721
== private symbols ==
  CoreGraphics.CGSMainConnectionID: True
  CoreGraphics.CGSGetWindowOwner: True
  CoreGraphics.CGSConnectionGetPID: True
  CoreGraphics.CGSGetConnectionIDForPID: False
  CoreGraphics.CGSPostMouseEventToProcess: False
  CoreGraphics.CGSPostKeyboardEventToProcess: False
  CoreGraphics.CGSSetConnectionProperty: True
  CoreGraphics.CGEventSetWindowLocation: True
  SkyLight.SLPSPostEventRecordTo: True
  SkyLight.SLSMainConnectionID: True
  SkyLight.SLSSetConnectionProperty: True
== target == (re-probe after account switch; first launch was pid 69315 wid 43133 at x=639 y=48)
  name=Whiteout Survival pid=76707 wid=43654 active=False
  bounds (points, CG top-left origin): x=961.0 y=31.0 w=642.0 h=951.0
  backing scale factor: 1.0
  CGS main cid=1823103 target window owner cid=1526419 (err=0)
  occluded capture works: True
```
