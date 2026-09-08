"""State-age timeline: what a Whiteout Survival state unlocks at which age.

Community-sourced day numbers (whiteoutdata.com "State age and what to
expect", whiteoutsurvival.wiki "Server timeline / server age", 2026-09-08
research), checked against this account: Gen 2 heroes and Pet slot 2 were
unlocked by day 46 and Castle Battle was announced for day 52 (Mahmoud,
2026-09-08). Observation beats the table: `observed_day` records it.

    state_opened_on ─▶ age_days ─▶ next_milestones(age) ─▶ report --query
"""
from datetime import date, datetime, timezone

# (day, name, confidence, observed_day or None)
MILESTONES = (
    (0, "Gen 1 heroes; state opens", "high", None),
    (40, "Gen 2 heroes; alliance resource exchange", "high", 46),
    (46, "Pet slot 2 (this state; table said 54-60)", "observed", 46),
    (53, "Castle Battle", "high", 52),
    (54, "Pets and Fire Crystals (Beast Cage, Crystal Lab age begins)", "medium", None),
    (80, "First State of Power (SvS) and King of Icefield", "high", None),
    (120, "Gen 3 heroes; Experts (Tundra Trek)", "medium", None),
    (150, "FC5 furnace age; Crystal Laboratory super refinement", "medium", None),
    (180, "Legendary (red) chief gear", "medium", None),
    (200, "Gen 4 heroes (a new generation every ~80 days)", "medium", None),
    (225, "War Academy", "medium", None),
    (280, "Gen 5 heroes", "low", None),
    (320, "FC8 furnace age", "low", None),
    (500, "FC10 furnace age", "low", None),
)


def age_days(state_opened_on, today=None):
    if not state_opened_on:
        return None
    today = today or datetime.now(timezone.utc).date()
    try:
        return (today - date.fromisoformat(str(state_opened_on)[:10])).days
    except ValueError:
        return None


def next_milestones(age, limit=4):
    """[(days_until, day, name, confidence)] for the next milestones after `age`."""
    out = []
    for day, name, conf, observed in MILESTONES:
        due = observed if observed is not None else day
        if due > age:
            out.append((due - age, due, name, conf))
    return out[:limit]


def passed_milestones(age):
    return [(d if o is None else o, n) for d, n, c, o in MILESTONES if (d if o is None else o) <= age]


def render(age, limit=4):
    if age is None:
        return "state age unknown: set it with snapshot.py --state-opened YYYY-MM-DD"
    lines = [f"State age {age} days. Passed: " + "; ".join(f"day {d} {n.split(';')[0].split(' (')[0]}" for d, n in passed_milestones(age)[-3:])]
    for until, day, name, conf in next_milestones(age, limit):
        lines.append(f"  in {until:>3d} d (day {day:>3d}, {conf:<8s}) {name}")
    return "\n".join(lines)
