from datetime import date

from native import timeline


def test_age_days_and_unknown():
    assert timeline.age_days("2026-07-24", today=date(2026, 9, 8)) == 46
    assert timeline.age_days(None) is None
    assert timeline.age_days("garbage") is None


def test_next_milestones_prefer_observed_days():
    nxt = timeline.next_milestones(46)
    assert nxt[0][:2] == (6, 52) and "Castle Battle" in nxt[0][2]
    assert any("State of Power" in n for _, _, n, _ in nxt)
    assert "Pet slot 2" not in " ".join(n for _, _, n, _ in nxt)   # observed at day 46: already passed


def test_render_mentions_castle_battle():
    text = timeline.render(46)
    assert "State age 46 days" in text and "Castle Battle" in text
    assert "unknown" in timeline.render(None)
