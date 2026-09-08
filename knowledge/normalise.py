"""Turn the wosnerds source files into the knowledge-base schema.

Source keys differ per file ("Hunter's Hut", "research-center-lv", "Troop
Level"); every normaliser maps them onto the repo's slugs so the planner and
the readers speak one vocabulary. Values are ints (costs), seconds (times)
and None for unknowns; nothing is invented. A field the schema promises
(E3: costs, training figures, research cost/time) must be present upstream
or the row is a loud failure -- `_int(..., required=True)` -- never a
silently cheaper plan.

Standard library only (no `native/` import): this module sits below the
vision layer (native/screen.py pulls in OpenCV and a macOS driver).
"""
import re

TROOP_TYPES = ("infantry", "lancer", "marksman")

# The real research source spells a building requirement "research-center-lv"
# in two rows (coal_mining_iii L3, marksman_armor_iii L4) where every other
# row says "research-center" (C5). Map it before storing so both slugs
# resolve to the same building the buildings table uses.
BUILDING_ALIASES = {"research_center_lv": "research_center"}


def slug(name):
    """'Hunter's Hut' -> 'hunters_hut' (apostrophes dropped, not turned into
    separators), 'research-center' -> 'research_center'."""
    return re.sub(r"[^a-z0-9]+", "_", re.sub(r"['’]", "", str(name).lower())).strip("_")


def _building_slug(name):
    s = slug(name)
    return BUILDING_ALIASES.get(s, s)


def _int(v, required=False, context=None, key=None):
    """Numeric coercion for OCR-adjacent JSON: '-'/'--'/''/None -> 0.

    `v` is normally `container.get(key)`, so an absent upstream key and an
    absent value both arrive here as `None`; with `required=True` that is
    treated as a dropped field and raises `ValueError(f"{context}: missing
    {key}")` naming the row (E3). A present `0` is legal and passes through
    unchanged -- furnace level 0 is genuinely zero, not a missing field.
    """
    if v is None:
        if required:
            raise ValueError(f"{context}: missing {key}")
        return 0
    if v in ("", "-", "–", "—"):
        return 0
    return int(round(float(v)))


def buildings(raw):
    out = {}
    for name, levels in raw["buildingLevels"].items():
        bslug = slug(name)
        rows = {}
        for lv in levels:
            level = lv["level"]
            ctx = f"{bslug} L{level}"
            rows[str(level)] = {
                "meat": _int(lv.get("meat"), required=True, context=ctx, key="meat"),
                "wood": _int(lv.get("wood"), required=True, context=ctx, key="wood"),
                "coal": _int(lv.get("coal"), required=True, context=ctx, key="coal"),
                "iron": _int(lv.get("iron"), required=True, context=ctx, key="iron"),
                "fire_crystals": _int(lv.get("fireCrystals")),
                "refined_fire_crystals": _int(lv.get("refinedFireCrystals")),
                "seconds": _int(lv.get("seconds"), required=True, context=ctx, key="seconds"),
                "prerequisites": {slug(k): _int(v) for k, v in (lv.get("prerequisites") or {}).items()},
                "verified_in_game": None,
            }
        out[bslug] = rows
    return {"buildings": out}


def troops(raw):
    out = {}
    for ttype in TROOP_TYPES:
        rows = {}
        for r in raw["troopCosts"].get(ttype, []):
            tier = r["tier"]
            ctx = f"{ttype} T{tier}"
            rows[str(tier)] = {
                "points": _int(r.get("points"), required=True, context=ctx, key="points"),
                "seconds": _int(r.get("baseSeconds"), required=True, context=ctx, key="baseSeconds"),
                "meat": _int(r.get("meat"), required=True, context=ctx, key="meat"),
                "wood": _int(r.get("wood"), required=True, context=ctx, key="wood"),
                "coal": _int(r.get("coal"), required=True, context=ctx, key="coal"),
                "iron": _int(r.get("iron"), required=True, context=ctx, key="iron"),
            }
        out[ttype] = rows
    return {"training": out}


def troop_stats(raw):
    out = {}
    for ttype in TROOP_TYPES:
        rows = {}
        for r in raw.get("troop-stats", {}).get(ttype, []):
            key = f"{_int(r.get('Troop Level'))}-fc{_int(r.get('FC level'))}"
            rows[key] = {
                "name": r.get("troop level name"),
                "power": _int(r.get("power")),
                "attack": _int(r.get("attack")),
                "defense": _int(r.get("defense")),
                "lethality": _int(r.get("lethality")),
                "health": _int(r.get("health")),
                "load": _int(r.get("load")),
                "speed": _int(r.get("speed")),
            }
        out[ttype] = rows
    return {"stats": out}


def research(raw):
    out = {}
    for tree, nodes in raw.items():
        tslug = slug(tree)
        for node_id, node in nodes.items():
            key = slug(node_id)
            if key in out:
                raise ValueError(f"research node id {key!r} appears in both {out[key]['tree']} and {tslug}")
            levels = {}
            for lvln, lv in (node.get("levels") or {}).items():
                ctx = f"{key} L{lvln}"
                if "cost" not in lv:
                    raise ValueError(f"{ctx}: missing cost")
                if "research-time-seconds" not in lv:
                    raise ValueError(f"{ctx}: missing research-time-seconds")
                req = lv.get("requirements") or {}
                levels[str(lvln)] = {
                    "stat_addition": lv.get("stat-addition"),
                    "power": _int(lv.get("power")),
                    "cost": {k: _int(v) for k, v in lv["cost"].items()},
                    "seconds": _int(lv.get("research-time-seconds"), required=True, context=ctx,
                                    key="research-time-seconds"),
                    "requires_research": {slug(k): _int(v) for k, v in (req.get("research-items") or {}).items()},
                    "requires_buildings": {_building_slug(k): _int(v) for k, v in (req.get("buildings") or {}).items()},
                    "verified_in_game": None,
                }
            out[key] = {
                "tree": tslug,
                "name": node.get("name"),
                "stat": slug(node.get("stat") or ""),
                "troop_type": node.get("troop-type"),
                "row": node.get("row"),
                "levels": levels,
            }
    return {"research": out}


def calendar(raw):
    out = {}
    for eid, e in (raw.get("events") or {}).items():
        age = e.get("available-after-age")
        out[slug(eid)] = {
            "category": e.get("category"),
            "name": e.get("entry-name"),
            "repeat_every_days": _int(e.get("repeat-every-days")),
            "length_hours": _int(e.get("event-length-hours")),
            "anchor": e.get("global-start-time-anchor"),
            "available_after_age": None if age in (None, "unknown") else _int(age),
        }
    return {"events": out}


# calendar's source is optional (R3, F-b) but its normaliser ships in M1
# so the registry test (E7: SOURCES | OPTIONAL_SOURCES == NORMALISERS) holds
# as soon as it is registered.
NORMALISERS = {
    "buildings": buildings,
    "troops": troops,
    "troop_stats": troop_stats,
    "research": research,
    "calendar": calendar,
}
