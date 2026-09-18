"""Import a live `wos` CLI read (the wos-mcp protocol client) into the chief sheet.

    wos profile --out P.json  ─┐
    wos hospital status > H.json ─┴▶ build() ─▶ model.write_snapshot(source='wos-cli')
                                              ─▶ legacy db/players/<id>.json
    wos heroes --out R.json ──────▶ build_heroes() ─▶ its own snapshot at its read time
    wos store: cli_reads ──(--from-db profile|heroes)──▶ the same build()/build_heroes() path

The CLI reads the server's own values, so every mapped field is exact
(method='protocol'). Only fields that mean the same thing as the screen
readers' are mapped; the rest carry forward:
  - resources are not mapped: the sheet's meat/wood/coal/iron are the Overview
    screen's "owned" figures, the protocol has regular and secured stock.
  - charms are not mapped: the sheet stores the charm border colour rank, the
    protocol has the charm level.
Every reader section is 'skipped' (no screen reader ran), so unmapped paths
carry and --doctor never counts a CLI import as a failed reader. A heroes
import marks only 'heroes' ok: hero paths are dynamic, so readers of the
newest hero section see exactly the protocol roster (level, stars, star step,
rarity and every skill level).
"""
import argparse
import json
import sys
from datetime import datetime, timezone

from native import model, schema

SOURCE = "wos-cli"
METHOD = "protocol"
# Protocol rarity -> the gear reader's border colour. Only pairs seen on both
# sides on this account (2026-09-16 screen vs 2026-09-18 protocol); another
# rarity leaves tier/rank unwritten rather than guessed.
RARITY_TIER = {"Rare": "blue", "Epic": "purple"}
MAX_BASE_FURNACE = 30   # stove_lv above 30 is a Fire Crystal level whose encoding is unverified
HOSPITAL_WINDOW_S = 600  # a hospital read further than this from the profile is another moment


class ImportRefused(ValueError):
    """The input cannot be written without breaking a sheet invariant."""


def _put(doc, provenance, path, value, raw, frame):
    if value is None:
        return
    node = doc
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    provenance[path] = {"raw": raw, "frame": frame, "score": None, "method": METHOD, "exact": 1}


def _frame(file_path, evidence):
    op = (evidence or {}).get("operation")
    return f"{file_path}#op{op}" if op is not None else str(file_path)


def _observed(data, label):
    """(player id, UTC finished_at) of a live CLI observation, else ImportRefused."""
    obs = data.get("observation") or {}
    if obs.get("mode") != "live":
        raise ImportRefused(f"{label} observation mode is {obs.get('mode')!r}, not 'live'")
    if obs.get("player_id") is None or not obs.get("finished_at"):
        raise ImportRefused(f"{label} observation has no player_id or finished_at")
    stamp = datetime.fromisoformat(obs["finished_at"])
    if stamp.tzinfo is None:
        raise ImportRefused(f"{label} finished_at {obs['finished_at']!r} has no UTC offset")
    return str(obs["player_id"]), stamp.astimezone(timezone.utc)


def build(profile, profile_path, hospital=None, hospital_path=None):
    """(player, doc, provenance, observed_at, gems, notes) from CLI outputs. Pure: no DB."""
    player_id, observed_at = _observed(profile, "profile")
    if hospital is not None:
        hospital_player, hospital_at = _observed(hospital, "hospital")
        if hospital_player != player_id:
            raise ImportRefused("hospital status is for a different player than the profile")
        gap = abs((hospital_at - observed_at).total_seconds())
        if gap > HOSPITAL_WINDOW_S:
            raise ImportRefused(f"hospital status is {gap:.0f} s from the profile read "
                                f"(limit {HOSPITAL_WINDOW_S} s): not the same moment")

    doc, prov, notes = {}, {}, []
    p = profile["profile"]
    pf = str(profile_path)
    _put(doc, prov, "identity.id", player_id, player_id, pf)
    _put(doc, prov, "identity.name", p.get("name"), p.get("name"), pf)
    _put(doc, prov, "identity.state", p.get("state"), str(p.get("state")), pf)
    power = (p.get("power") or {}).get("total")
    _put(doc, prov, "progress.power", power, str(power), pf)
    level = p.get("furnace_level")
    if level is not None and level <= MAX_BASE_FURNACE:
        _put(doc, prov, "progress.furnace.level", level, f"stove_lv {level}", pf)
        _put(doc, prov, "city.buildings.furnace", level, f"stove_lv {level}", pf)

    stamina = (profile.get("recoverable_resources") or {}).get("stamina") or {}
    sf = _frame(pf, (profile.get("recoverable_resources") or {}).get("evidence"))
    _put(doc, prov, "economy.stamina.value", stamina.get("current"), str(stamina.get("current")), sf)
    _put(doc, prov, "economy.stamina.cap", stamina.get("recovery_cap"), str(stamina.get("recovery_cap")), sf)

    gear = profile.get("chief_gear") or {}
    gf = _frame(pf, gear.get("evidence"))
    for item in gear.get("items") or []:
        slot = item["slot"]
        raw = f"{item.get('rarity')} {item.get('stars')}* {item.get('name')}"
        tier = RARITY_TIER.get(item.get("rarity"))
        if tier is None:
            # Stars restart on a tier-up: new stars under a carried old tier
            # would read as a regression, so the slot is left whole.
            notes.append(f"gear {slot}: rarity {item.get('rarity')!r} has no verified colour, slot not written")
            continue
        _put(doc, prov, f"gear.chief.{slot}.stars", item.get("stars"), raw, gf)
        _put(doc, prov, f"gear.chief.{slot}.tier", tier, raw, gf)
        _put(doc, prov, f"gear.chief.{slot}.rank", model.rank_of_tier(tier), raw, gf)

    al = profile.get("alliance") or {}
    info = al.get("alliance") or {}
    af = _frame(pf, al.get("evidence"))
    for path, value in (("alliance.name", info.get("name")), ("alliance.tag", info.get("abbr")),
                        ("alliance.leader", info.get("leader_name")), ("alliance.power", info.get("power")),
                        ("alliance.state_rank", info.get("power_rank")), ("alliance.level", info.get("lv")),
                        ("alliance.members", info.get("count")), ("alliance.cap", info.get("member_max"))):
        _put(doc, prov, path, value, str(value), af)
    if al.get("rank") is not None:
        _put(doc, prov, "alliance.rank", f"R{al['rank']}", str(al["rank"]), af)

    gems = None
    if hospital is not None:
        hf = str(hospital_path)
        gems = hospital.get("gems")
        _put(doc, prov, "economy.gems", gems, str(gems), hf)
        wounded = hospital.get("wounded")
        if hospital.get("healing"):
            # Troops in a running heal are outside `wounded`; whether the
            # screen's Injured figure counts them is unverified.
            notes.append("wounded not written: a heal was running during the read")
        elif wounded is not None:
            total = sum(wounded.values())
            _put(doc, prov, "troops.wounded.value", total, json.dumps(wounded, sort_keys=True), hf)

    player = {"id": player_id, "name": p.get("name"), "state": p.get("state")}
    return player, doc, prov, observed_at, gems, notes


def build_heroes(heroes, heroes_path):
    """(player, doc, provenance, observed_at) from `wos heroes --out`. Pure: no DB."""
    player_id, observed_at = _observed(heroes, "heroes")
    rows = heroes.get("heroes")
    if not rows:
        raise ImportRefused("heroes read holds no heroes")
    doc, prov, hf = {}, {}, str(heroes_path)
    for hero in rows:
        base = f"heroes.{model.slug(hero['name'])}"
        raw = f"{hero['name']} (hero {hero['id']}, star row {hero['star_row']})"
        for key in ("name", "level", "stars", "star_step", "rarity"):
            _put(doc, prov, f"{base}.{key}", hero.get(key), raw, hf)
        for kind in ("exploration", "expedition"):
            for n, level in enumerate(hero.get(f"{kind}_skills") or [], 1):
                _put(doc, prov, f"{base}.{kind}_skill_{n}", level, raw, hf)
    return {"id": player_id}, doc, prov, observed_at


def write(conn, profile, profile_path, hospital=None, hospital_path=None):
    """Validate against the sheet and write one snapshot. Returns a summary."""
    player, doc, prov, stamp, gems, notes = build(profile, profile_path, hospital, hospital_path)
    power = (doc.get("progress") or {}).get("power")
    summary = _commit(conn, player, doc, prov, stamp, profile_path, gems=gems, power=power)
    summary["notes"] = notes
    return summary


def write_heroes(conn, heroes, heroes_path):
    """Write one heroes-only snapshot: section 'heroes' ok, the rest skipped and carried."""
    player, doc, prov, stamp = build_heroes(heroes, heroes_path)
    summary = _commit(conn, player, doc, prov, stamp, heroes_path, sections_ok=("heroes",))
    summary["notes"] = []
    return summary


def _stored(conn, command):
    """(read id, doc) of the newest live `command` read the wos CLI stored, else None."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cli_reads'").fetchone() is None:
        raise ImportRefused("no stored wos CLI reads in this database (cli_reads is missing)")
    row = conn.execute("SELECT id, doc FROM cli_reads WHERE command = ? AND kind = 'read' AND mode = 'live' "
                       "ORDER BY id DESC LIMIT 1", (command,)).fetchone()
    return (row["id"], json.loads(row["doc"])) if row else None


def write_from_db(conn, what):
    """Import the newest stored `what` ('profile' or 'heroes') read. A profile takes the newest
    hospital read only when it is the same moment; otherwise gems and wounded carry."""
    found = _stored(conn, what)
    if found is None:
        raise ImportRefused(f"no stored live {what} read")
    read_id, doc = found
    if what == "heroes":
        return write_heroes(conn, doc, f"db:cli_reads/{read_id}")
    notes, hospital, hospital_path = [], None, None
    paired = _stored(conn, "hospital status")
    if paired is not None:
        _, profile_at = _observed(doc, "profile")
        _, hospital_at = _observed(paired[1], "hospital")
        if abs((hospital_at - profile_at).total_seconds()) <= HOSPITAL_WINDOW_S:
            hospital, hospital_path = paired[1], f"db:cli_reads/{paired[0]}"
        else:
            notes.append(f"newest hospital read {paired[0]} is not the profile's moment; gems and wounded carry")
    summary = write(conn, doc, f"db:cli_reads/{read_id}", hospital, hospital_path)
    summary["notes"] = notes + summary["notes"]
    return summary


def _commit(conn, player, doc, prov, stamp, source_path, *, gems=None, power=None, sections_ok=()):
    from native.snapshot import derive_furnace, main_player_id, state_age_days

    main = main_player_id(conn)
    if not main:
        raise ImportRefused("no main account confirmed (snapshot.py --confirm-main ID)")
    if str(main) != player["id"]:
        raise ImportRefused(f"player {player['id']} is not the confirmed main account {main}")
    # The id is the observation's own UTC second (not new_snapshot_id's
    # process clock), so history orders by when the server was read.
    snapshot_id = stamp.strftime("%Y%m%dT%H%M%SZ")
    taken_at = stamp.isoformat().replace("+00:00", "Z")
    newest = conn.execute("SELECT MAX(id) AS id FROM snapshots").fetchone()["id"]
    if newest and snapshot_id <= newest:
        raise ImportRefused(f"observation {taken_at} is not newer than the latest snapshot {newest}")

    derive_furnace(doc, prov)
    age = state_age_days(conn, player["id"], today=stamp.date())
    if age is not None:
        _put(doc, prov, "identity.state_age_days", age, str(age), None)
        prov["identity.state_age_days"]["method"] = "derived"

    sections = {name: "ok" if name in sections_ok else "skipped" for name in schema.ALL_READERS}
    counts = model.write_snapshot(
        conn, player=player, snapshot_id=snapshot_id, taken_at=taken_at, source=SOURCE,
        run_dir=str(source_path), status="ok", sections=sections, duration_s=None,
        gems_before=gems, gems_after=gems, power_before=power, power_after=power,
        power_rose=False, doc=doc, provenance=prov)
    return {"snapshot_id": snapshot_id, "taken_at": taken_at, "player_id": player["id"], "doc": doc,
            "counts": counts}


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import live wos CLI output into the chief sheet")
    parser.add_argument("--profile", help="`wos profile --out` JSON")
    parser.add_argument("--heroes", help="`wos heroes --out` JSON, imported as its own snapshot")
    parser.add_argument("--hospital", help="`wos hospital status` JSON (gems, wounded)")
    parser.add_argument("--from-db", choices=("profile", "heroes"), help="import the newest read the wos CLI stored in this database")
    parser.add_argument("--db", help="database path (default: WOS_DB_PATH or db/wos.sqlite)")
    parser.add_argument("--no-legacy", action="store_true", help="skip the db/players/<id>.json write-through")
    args = parser.parse_args(argv)
    if args.from_db:
        if args.profile or args.heroes or args.hospital:
            parser.error("--from-db replaces --profile, --heroes and --hospital")
    elif bool(args.profile) == bool(args.heroes):
        parser.error("give --profile (optionally with --hospital), --heroes or --from-db")
    if args.hospital and not args.profile:
        parser.error("--hospital pairs with --profile")

    conn = model.connect(args.db)
    try:
        if args.from_db:
            summary = write_from_db(conn, args.from_db)
        elif args.heroes:
            summary = write_heroes(conn, _load(args.heroes), args.heroes)
        else:
            summary = write(conn, _load(args.profile), args.profile,
                            _load(args.hospital) if args.hospital else None, args.hospital)
    except ImportRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    notes = summary["notes"]
    if not args.no_legacy and (args.profile or args.from_db == "profile"):
        from native.snapshot import write_through_profile
        summary["write_through"] = write_through_profile(conn, summary["player_id"], summary["doc"],
                                                         summary["snapshot_id"], notes)
    summary.pop("doc")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
