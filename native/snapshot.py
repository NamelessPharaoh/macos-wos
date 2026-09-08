"""One account snapshot: run the readers, guard the money, write the model.

    lock ─▶ hud (no taps) ─▶ main account? ─no─▶ stopped, nothing written
                │ yes
                ▼ profile ─▶ id == main id? ─no─▶ abort row (identity)
                ▼ readers in order, budget-checked, gems re-read after each
                │        gems dropped ─▶ abort row (gems), stop
                ▼ merge docs + provenance ─▶ model.write_snapshot ─▶ legacy profile
                ▼ prune old run dirs ─▶ summary

Readers only ever press entries, tabs, tiles and close controls; the guards in
native/screen.py refuse anything else. A power rise during the run is a warning
(timers complete), never a stop; a gem drop is the abort tell.
"""
import json
import math
import os
import time
from datetime import datetime, timezone

from native import drive as drv
from native import model
from native.readers import STATUS_FAILED, STATUS_SKIPPED, ReaderResult
from native.readers import alliance as r_alliance
from native.readers import hud as r_hud
from native.readers import profile as r_profile
from native.readers import resources as r_resources
from native.readers import troops as r_troops
from native.screen import Screen, read_hud

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_ROOT = os.path.expanduser(os.environ.get("WOS_CHIEF_RUNS", "~/wos-chief"))
MAIN_POWER_FLOOR = 1_000_000   # the tutorial account reads five digits; main tens of millions
READERS = {
    "hud": r_hud.read,
    "profile": r_profile.read,
    "troops": r_troops.read,
    "resources": r_resources.read,
    "alliance": r_alliance.read,
}
ORDER = ("hud", "profile", "troops", "resources", "alliance")


class SnapshotStopped(RuntimeError):
    """The run ended before writing anything (tutorial account, no window)."""


def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def main_player_id(conn):
    row = conn.execute("SELECT id FROM players WHERE is_main = 1").fetchone()
    return row["id"] if row else os.environ.get("WOS_MAIN_ID")


def confirm_main(conn, player_id, now=None):
    """Mark one player as the main account (unique partial index enforces one)."""
    now = now or model.utc_now_iso()
    conn.execute("INSERT OR IGNORE INTO players (id, is_main, first_seen, last_seen) VALUES (?, 0, ?, ?)",
                 (player_id, now, now))
    conn.execute("UPDATE players SET is_main = 0 WHERE is_main = 1 AND id != ?", (player_id,))
    conn.execute("UPDATE players SET is_main = 1 WHERE id = ?", (player_id,))
    conn.commit()


def set_state_opened(conn, player_id, date_iso):
    conn.execute("UPDATE players SET state_opened_on = ? WHERE id = ?", (date_iso, player_id))
    conn.commit()


def state_age_days(conn, player_id, today=None):
    row = conn.execute("SELECT state_opened_on FROM players WHERE id = ?", (player_id,)).fetchone()
    if not row or not row["state_opened_on"]:
        return None
    today = today or datetime.now(timezone.utc).date()
    opened = datetime.fromisoformat(row["state_opened_on"]).date()
    return (today - opened).days


def run(readers=None, *, dry_run=False, no_write=False, budget_s=900, report_dir=None,
        source="native-app", db_path=None, keep_days=2, log=print, chained=False):
    """Execute a snapshot; returns a summary dict. Never raises for reader
    failures (they become section statuses); raises SnapshotStopped when no
    write could happen, RunLocked when another run holds the lock."""
    started = time.time()
    snapshot_id = model.new_snapshot_id()
    taken_at = model.utc_now_iso()
    report_dir = report_dir or os.path.join(RUNS_ROOT, snapshot_id)
    drv.acquire_run_lock(owner="snapshot" + ("-chained" if chained else ""))
    sc = Screen(report_dir, dry_run=dry_run)
    wanted = list(readers or ORDER)
    if dry_run:
        wanted = ["hud"]
    sections = {name: STATUS_SKIPPED for name in READERS}
    doc, provenance, notes = {}, {}, []
    conn = None if no_write or dry_run else model.connect(db_path)

    # -- 1. HUD, no taps: main-account gate + money-guard baseline -------------
    hud = r_hud.read(sc)
    _absorb(hud, sections, doc, provenance, notes)
    power0 = hud.doc.get("progress", {}).get("power")
    gems0 = hud.doc.get("economy", {}).get("gems")
    sc.log(event="start", snapshot=snapshot_id, gems=gems0, power=power0, frame=hud.frames[-1] if hud.frames else None)
    if power0 is None or gems0 is None:
        raise SnapshotStopped("HUD did not read gems and power: not on the home screen, or not the main account")
    if power0 < MAIN_POWER_FLOOR:
        raise SnapshotStopped(f"power {power0:,} is below {MAIN_POWER_FLOOR:,}: this is the tutorial account, not main")
    if dry_run:
        _finish_log(sc, sections, doc, gems0, gems0, power0, power0)
        return {"snapshot_id": snapshot_id, "status": "dry-run", "sections": sections, "doc": doc,
                "gems": (gems0, gems0), "power": (power0, power0), "run_dir": report_dir}

    # -- 2. Profile: identity gate ----------------------------------------------
    player_id = None
    if "profile" in wanted:
        prof = r_profile.read(sc)
        _absorb(prof, sections, doc, provenance, notes)
        player_id = prof.doc.get("identity", {}).get("id")
        expected = main_player_id(conn) if conn else None
        if player_id is None:
            notes.append("profile: player id not read; identity unverified")
        elif expected and str(player_id) != str(expected):
            reason = f"identity mismatch: read id {player_id}, main is {expected}"
            sc.log(event="ABORT", reason=reason)
            if conn:
                model.write_abort(conn, player_id=str(player_id), snapshot_id=snapshot_id, taken_at=taken_at,
                                  source=source, run_dir=report_dir, reason=reason, sections=sections,
                                  gems_before=gems0, power_before=power0)
                conn.commit()
            return {"snapshot_id": snapshot_id, "status": "aborted", "reason": reason, "sections": sections,
                    "run_dir": report_dir}
        elif not expected:
            notes.append(f"no main id confirmed yet; run with --confirm-main {player_id} once (or set WOS_MAIN_ID)")
        sc.go_home()

    # -- 3. Remaining readers, budget and gems guard ------------------------------
    gems_last, aborted = gems0, None
    for name in wanted:
        if name in ("hud", "profile"):
            continue
        if time.time() - started > budget_s:
            sections[name] = STATUS_SKIPPED
            notes.append(f"{name}: skipped, budget {budget_s}s exhausted")
            continue
        res = READERS[name](sc)
        _absorb(res, sections, doc, provenance, notes)
        img, items, path = sc.frame("hud")
        h, w = img.shape[:2]
        g, p = read_hud(items, h, w)
        if g is None or p is None:
            sc.go_home()
            img, items, path = sc.frame("hud")
            h, w = img.shape[:2]
            g, p = read_hud(items, h, w)
        if g is not None and p is not None and g < gems_last:
            aborted = f"gems dropped {gems_last} -> {g} after reader {name}"
            sc.log(event="ABORT", reason=aborted, frame=path)
            break
        if g is not None:
            gems_last = g

    img, items, path = sc.frame("end")
    h, w = img.shape[:2]
    gems1, power1 = read_hud(items, h, w)
    gems1 = gems1 if gems1 is not None else gems_last
    power1 = power1 if power1 is not None else power0
    power_rose = int(power1 > power0)
    if power_rose:
        notes.append(f"power rose {power0:,} -> {power1:,} during the run (a timer completed, or a spend)")
    duration = int(time.time() - started)

    if aborted:
        if conn and player_id:
            model.write_abort(conn, player_id=str(player_id), snapshot_id=snapshot_id, taken_at=taken_at,
                              source=source, run_dir=report_dir, reason=aborted, sections=sections,
                              gems_before=gems0, power_before=power0)
            conn.commit()
        _finish_log(sc, sections, doc, gems0, gems1, power0, power1)
        return {"snapshot_id": snapshot_id, "status": "aborted", "reason": aborted, "sections": sections,
                "gems": (gems0, gems1), "power": (power0, power1), "run_dir": report_dir, "notes": notes}

    derive_furnace(doc, provenance)
    status = "ok" if all(s == "ok" for n, s in sections.items() if n in wanted) else "partial"
    summary = {"snapshot_id": snapshot_id, "status": status, "sections": sections, "doc": doc,
               "gems": (gems0, gems1), "power": (power0, power1), "power_rose": power_rose,
               "run_dir": report_dir, "notes": notes, "duration_s": duration}
    if conn and player_id:
        if state_age := state_age_days(conn, str(player_id)):
            doc.setdefault("identity", {})["state_age_days"] = state_age
            provenance["identity.state_age_days"] = {"raw": str(state_age), "frame": None, "score": None,
                                                     "method": "derived", "exact": 1}
        player = {"id": str(player_id), "name": doc.get("identity", {}).get("name"),
                  "state": doc.get("identity", {}).get("state")}
        counts = model.write_snapshot(conn, player=player, snapshot_id=snapshot_id, taken_at=taken_at,
                                      source=source, run_dir=report_dir, status=status, sections=sections,
                                      duration_s=duration, gems_before=gems0, gems_after=gems1,
                                      power_before=power0, power_after=power1, power_rose=power_rose,
                                      doc=doc, provenance=provenance)
        conn.commit()
        summary["counts"] = counts
        summary["write_through"] = write_through_profile(conn, str(player_id), doc, snapshot_id, notes)
        summary["pruned"] = model.prune_runs(RUNS_ROOT, keep_days=keep_days, current=os.path.basename(report_dir))
    elif conn and not player_id:
        notes.append("nothing written: the player id was not read")
        summary["status"] = "unwritten"
    _finish_log(sc, sections, doc, gems0, gems1, power0, power1)
    return summary


def derive_furnace(doc, provenance):
    """ordinal = level + 5*fc + sub is what the monotonic validator keys on; the
    base-level profile shows only 'Lv. 27', so fc/sub default to 0 until an FC
    reader exists. Derived fields carry method='derived' in provenance."""
    furnace = doc.get("progress", {}).get("furnace", {})
    level = furnace.get("level")
    if level is None:
        return
    fc = furnace.get("fc") or 0
    sub = furnace.get("sub") or 0
    furnace["fc"], furnace["sub"] = fc, sub
    furnace["ordinal"] = int(level) + 5 * int(fc) + int(sub)
    src = provenance.get("progress.furnace.level", {})
    for key in ("fc", "sub", "ordinal"):
        provenance[f"progress.furnace.{key}"] = {"raw": str(furnace[key]), "frame": src.get("frame"),
                                                "score": src.get("score"), "method": "derived", "exact": 1}


def _absorb(res, sections, doc, provenance, notes):
    sections[res.name] = res.status
    deep_merge(doc, res.doc)
    provenance.update(res.provenance)
    notes.extend(f"{res.name}: {n}" for n in res.notes)


def _finish_log(sc, sections, doc, gems0, gems1, power0, power1):
    sc.log(event="end", sections=sections, gems=(gems0, gems1), power=(power0, power1))


# ----------------------------------------------------------------------------- legacy profile
LEGACY_KEYS = ("name", "state", "furnace_level", "gems", "power", "stamina", "resources", "vip", "alliance",
               "command_center_level", "state_opened_on")


def write_through_profile(conn, player_id, doc, snapshot_id, notes):
    """Refresh the keys the emulator bot reads in db/players/<id>.json.

    Same rule as the sheet: the furnace goes through validate_furnace_read
    with the day-scaled allowance; a case the sheet accepted but the legacy
    validator rejected is reported as `divergence`. PLAYERS_DIR is cwd-relative,
    so the chdir is scoped to this function."""
    from core import player_profile as pp
    cwd = os.getcwd()
    os.chdir(REPO)
    try:
        profile = pp.load_profile(player_id)
        ident, prog, econ = doc.get("identity", {}), doc.get("progress", {}), doc.get("economy", {})
        if ident.get("name"):
            profile["name"] = ident["name"]
        if ident.get("state") is not None:
            profile["state"] = ident["state"]
        result = {"furnace": None}
        furnace = prog.get("furnace", {})
        if furnace.get("level") is not None and not furnace.get("fc"):
            prev = conn.execute("SELECT taken_at FROM snapshots WHERE player_id = ? AND id < ? AND source != 'operator' "
                                "AND status != 'aborted' ORDER BY id DESC LIMIT 1", (player_id, snapshot_id)).fetchone()
            allowance = 1
            if prev:
                allowance = max(1, math.ceil(model.days_since(prev["taken_at"], model.utc_now_iso())))
            level, reason = pp.validate_furnace_read(profile, furnace["level"], max_step=allowance)
            if level is not None:
                profile["furnace_level"] = level
            elif reason != "no-read":
                notes.append(f"divergence: sheet accepted furnace {furnace['level']}, legacy validator said {reason}")
            result["furnace"] = reason
        for key in ("gems", "power"):
            src = econ if key == "gems" else prog
            if src.get(key) is not None:
                profile[key] = src[key]
        if econ.get("stamina", {}).get("value") is not None:
            profile["stamina"] = econ["stamina"]["value"]
        res = econ.get("resources") or {}
        profile.setdefault("resources", {})
        for k in ("wood", "meat", "coal", "iron"):
            if res.get(k) is not None:
                profile["resources"][k] = res[k]
        if prog.get("vip", {}).get("level") is not None:
            profile.setdefault("vip", {})["level"] = int(prog["vip"]["level"])
        cc = doc.get("city", {}).get("buildings", {}).get("command_center")
        profile["command_center_level"] = cc if cc is not None else profile.get("command_center_level")
        row = conn.execute("SELECT state_opened_on FROM players WHERE id = ?", (player_id,)).fetchone()
        if row and row["state_opened_on"]:
            profile["state_opened_on"] = row["state_opened_on"]
        pp.save_profile(profile)
        al = doc.get("alliance", {})
        if al.get("name"):
            pp.set_alliance_state(profile, al["name"], al.get("members"))
        return result
    except OSError as exc:
        notes.append(f"legacy profile write failed: {exc}")
        return {"error": str(exc)}
    finally:
        os.chdir(cwd)
