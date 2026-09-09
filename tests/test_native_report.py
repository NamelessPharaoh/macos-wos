"""native/report.py over a tiny database: header, deltas, warnings, html."""
from native import model, report


def _db(tmp_path):
    conn = model.connect(str(tmp_path / "t.sqlite"))
    conn.execute("INSERT INTO players (id, is_main, first_seen, last_seen) VALUES ('p', 1, 't', 't')")
    for sid, ta, power, gems in (("20260907T120000Z", "2026-09-07T12:00:00Z", 100, 5), ("20260908T120000Z", "2026-09-08T12:00:00Z", 130, 7)):
        model.write_snapshot(conn, player={"id": "p", "name": "N"}, snapshot_id=sid, taken_at=ta, source="native-app",
                             run_dir="r", status="ok", sections={"hud": "ok", "profile": "ok"}, duration_s=3,
                             gems_before=gems, gems_after=gems, power_before=power, power_after=power, power_rose=0,
                             doc={"progress": {"power": power}, "economy": {"gems": gems}, "identity": {"name": "N"}},
                             provenance={"progress.power": {"raw": str(power)}, "economy.gems": {"raw": str(gems)},
                                         "identity.name": {"raw": "N"}})
    return conn


def test_build_and_render_after_an_operator_row(tmp_path):
    conn = _db(tmp_path)
    model.write_operator(conn, player_id="p", path="manual.hero_generation", value="2",
                         taken_at="2026-09-08T13:00:00Z", snapshot_id="20260908T130000Z")
    data = report.build(conn, "p")
    assert data["snapshot"]["id"] == "20260908T120000Z"            # the run, not the --set row
    assert data["latest"]["manual.hero_generation"]["value_num"] == 2  # operator value still current
    assert data["deltas"]["progress.power"][2] == 30
    text = report.render_text(data)
    assert "gems 7 -> 7" in text and "(+30)" in text and "hero generation" in text
    html = report.render_html(data)
    assert "<title>" in html and "needs 2+ snapshots" not in html.split("progress.power")[1][:400]


def test_render_handles_missing_numbers(tmp_path):
    conn = _db(tmp_path)
    conn.execute("UPDATE snapshots SET gems_after = NULL, power_after = NULL")
    conn.commit()
    text = report.render_text(report.build(conn, "p"))
    assert "gems 7 -> -" in text and "power 130 -> -" in text


def test_doctor_needs_three_runs(tmp_path):
    conn = _db(tmp_path)
    assert "need 3" in report.doctor(conn, "p")


def test_render_text_shows_knowledge_freshness(tmp_path):
    """A9: the freshness line render_text prints comes from data["knowledge"]
    (populated by build() from kb.freshness()); render_text itself just
    formats whatever list it is handed, so this pins the exact wording."""
    conn = _db(tmp_path)
    data = report.build(conn, "p")
    data["knowledge"] = [("buildings", 12, False), ("research", 12, False)]
    text = report.render_text(data)
    assert "knowledge: buildings 12 d, research 12 d" in text


def test_render_text_flags_a_stale_knowledge_table(tmp_path):
    conn = _db(tmp_path)
    data = report.build(conn, "p")
    data["knowledge"] = [("buildings", 40, True)]
    text = report.render_text(data)
    assert "knowledge table buildings is 40 days old" in text
    assert "uv run python scripts/refresh_knowledge.py --table buildings" in text


def test_build_populates_knowledge_freshness_from_the_real_tables(tmp_path):
    """Sanity check on the real wiring (not just render_text's formatting):
    build() must call kb.freshness() and store a non-empty list, since the
    repo's committed knowledge/*.json all carry a _meta.fetched_at."""
    conn = _db(tmp_path)
    data = report.build(conn, "p")
    assert data["knowledge"]
    assert all(isinstance(t, str) and isinstance(age, int) and isinstance(stale, bool) for t, age, stale in data["knowledge"])


def test_doctor_flags_a_stale_knowledge_table(tmp_path):
    conn = _db(tmp_path)
    msg = report.doctor(conn, "p", runs=2, kb_freshness=[("buildings", 40, True)])
    assert "buildings" in msg and "40 days old" in msg
    assert "uv run python scripts/refresh_knowledge.py --table buildings" in msg
