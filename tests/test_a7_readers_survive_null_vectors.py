"""Readers of the reflexes vector columns must cope with NULL = "not assessed" (sweep item A7, goal 1cd7caa3).

Written with rows inserted by direct SQL, so none of these tests depends on `store_vectors` having been changed: NULL rows already
exist in the table today (reflex_import writes them for vectors missing from a git note) and the writer change is what makes them
routine. Every reader below was found by the two read-only audits of the reader surface: each either raised TypeError on None or
re-invented a value (0.5, 0.0, 1.0) that the NULL exists to avoid.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

from empirica.data.session_database import SessionDatabase

HOOKS = Path(__file__).resolve().parents[1] / "empirica" / "plugins" / "claude-code-integration" / "hooks"
NAMES = [
    "engagement",
    "know",
    "do",
    "context",
    "clarity",
    "coherence",
    "signal",
    "density",
    "state",
    "change",
    "completion",
    "impact",
    "uncertainty",
]
FULL = {n: round(0.3 + 0.04 * i, 2) for i, n in enumerate(NAMES)}


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield d
    d.close()


def put(db, session_id, phase, vectors, *, ts=None, transaction_id=None):
    """Insert a reflexes row exactly as given: a vector that is absent from `vectors` is NULL."""
    cols = ", ".join(NAMES)
    marks = ", ".join("?" for _ in NAMES)
    db.conn.execute(
        f"INSERT INTO reflexes (session_id, phase, round, timestamp, {cols}, reflex_data, transaction_id) "
        f"VALUES (?, ?, 1, ?, {marks}, '{{}}', ?)",
        (session_id, phase, ts or time.time(), *[vectors.get(n) for n in NAMES], transaction_id),
    )
    db.conn.commit()


def hook(filename, name):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ── U1: the session-end auto-POSTFLIGHT must not become sparse ──


def test_the_session_end_snapshot_takes_each_vector_from_its_last_recorded_value(db, monkeypatch):
    """The auto-POSTFLIGHT is built from the latest row, which is usually a partial CHECK. With 0.5s gone from that row it would post
    4 vectors and store a POSTFLIGHT full of NULLs, which breaks the 'POSTFLIGHT is complete' assumption flow_state and the health
    score make. The honest completion is the last value each vector was actually recorded with, which is what its own reasoning text
    already says ('auto-captured from the last recorded vectors')."""
    sid = db.create_session(ai_id="a7-end")
    put(db, sid, "PREFLIGHT", FULL, ts=1000.0)
    put(db, sid, "CHECK", {"know": 0.91, "uncertainty": 0.11}, ts=2000.0)
    mod = hook("session-end-postflight.py", "session_end_a7")
    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase", lambda *a, **k: SessionDatabase(db_path=db.db_path)
    )
    state = mod.get_session_state(sid)
    last = state["last_vectors"]
    assert set(last) == set(NAMES), f"sparse auto-POSTFLIGHT: {sorted(set(NAMES) - set(last))} missing"
    assert last["know"] == 0.91 and last["uncertainty"] == 0.11, "the newest recorded value wins"
    assert last["clarity"] == FULL["clarity"], "an unsupplied vector keeps its last recorded value, not 0.5"
    assert state["needs_postflight"] is True


# ── U5: session-create's auto-close must not re-invent what the NULL exists to avoid ──


def _auto_closed_row(db, sid):
    db.conn.row_factory = __import__("sqlite3").Row
    return dict(
        db.conn.execute("SELECT * FROM reflexes WHERE session_id = ? AND phase = 'POSTFLIGHT'", (sid,)).fetchone()
    )


def _close(db, sid):
    from empirica.cli.command_handlers.session_create import auto_close_previous_sessions

    db.conn.execute("UPDATE sessions SET ai_id = 'a7-close', project_id = 'p1' WHERE session_id = ?", (sid,))
    db.conn.commit()
    return auto_close_previous_sessions(db, "a7-close", "p1")


def test_the_auto_close_keeps_a_real_zero_instead_of_turning_it_into_a_default(db):
    """`vector or 0.5` read a genuine 0.0 (change at the start of a transaction is 0.0; uncertainty can be 0.0) as missing."""
    sid = db.create_session(ai_id="a7-close")
    put(db, sid, "PREFLIGHT", {**FULL, "change": 0.0, "uncertainty": 0.0}, ts=1000.0)
    _close(db, sid)
    row = _auto_closed_row(db, sid)
    assert row["change"] == 0.0 and row["uncertainty"] == 0.0, "a real zero was replaced by a default"


def test_the_auto_close_takes_each_vector_from_its_last_recorded_value_and_leaves_unrecorded_ones_null(db):
    sid = db.create_session(ai_id="a7-close")
    partial_pre = {k: v for k, v in FULL.items() if k not in ("impact", "engagement")}
    put(db, sid, "PREFLIGHT", partial_pre, ts=1000.0)
    put(db, sid, "CHECK", {"know": 0.91}, ts=2000.0)
    _close(db, sid)
    row = _auto_closed_row(db, sid)
    assert row["know"] == 0.91, "the newest recorded value wins"
    assert row["clarity"] == FULL["clarity"], "an unsupplied vector keeps its last recorded value"
    assert row["impact"] is None and row["engagement"] is None, "never recorded means NULL, not an invented 0.5"
    assert row["completion"] == 1.0, "the existing 'session ended = complete' stays"


# ── U2: goals-ready compared None with a threshold ──


def _ready(db, sid, **kw):
    from empirica.cli.command_handlers.goal_commands import _handle_goals_ready_command_helper

    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, created_timestamp, goal_data, beads_issue_id) "
        "VALUES ('g-ready', ?, 'an objective', '{}', 0.0, '{}', 'bd-1')",
        (sid,),
    )
    db.conn.commit()
    ready: list = []
    _handle_goals_ready_command_helper(
        [{"id": "bd-1", "priority": 2}],
        None,
        db,
        kw.get("max_uncertainty", 0.35),
        kw.get("min_confidence", 0.7),
        ready,
        sid,
    )
    return ready


def test_goals_ready_does_not_raise_when_the_latest_row_is_a_sparse_check(db):
    """The latest row is usually a CHECK that omits know or uncertainty: `None < 0.7` raised TypeError for every candidate item."""
    sid = db.create_session(ai_id="a7-ready")
    put(db, sid, "PREFLIGHT", FULL | {"know": 0.9, "uncertainty": 0.1}, ts=1000.0)
    put(db, sid, "CHECK", {"clarity": 0.9}, ts=2000.0)
    ready = _ready(db, sid)
    assert len(ready) == 1
    assert ready[0]["last_check_confidence"] == 0.9 and ready[0]["preflight_uncertainty"] == 0.1, (
        "last RECORDED know/uncertainty"
    )
    assert ready[0]["epistemic_ready"] is True


def test_goals_ready_says_not_ready_when_know_was_never_recorded(db):
    sid = db.create_session(ai_id="a7-ready")
    put(db, sid, "CHECK", {"clarity": 0.9}, ts=2000.0)
    ready = _ready(db, sid)
    assert ready[0]["epistemic_ready"] is False and "recorded" in ready[0].get("why_not_ready", "").lower()


# ── U3: the compact analysis subtracted None ──


def test_the_compact_analysis_leaves_a_delta_unknown_when_the_first_check_did_not_assess_it(monkeypatch):
    """`post_check.get("know", 0)` returns None when the key is present with a NULL value, and `round(None - x)` aborted the whole
    analysis. A delta that cannot be computed is None, not a crash and not a delta against an invented 0."""
    import empirica.cli.command_handlers.compact_analysis as ca

    monkeypatch.setattr(ca, "get_ref_docs_path", lambda: Path("/nonexistent"))
    monkeypatch.setattr(
        ca,
        "load_pre_compact_snapshots",
        lambda _p: [
            {
                "timestamp": "2026-10-01T10-00-00",
                "session_id": "pre",
                "vectors_canonical": {"know": 0.8, "uncertainty": 0.2, "context": 0.7},
            }
        ],
    )
    monkeypatch.setattr(
        ca,
        "get_post_compact_sessions",
        lambda _db, _t: [{"session_id": "post", "ai_id": "real-ai", "know": 0.6, "uncertainty": 0.3, "context": 0.5}],
    )
    monkeypatch.setattr(ca, "is_test_session", lambda _a: False)
    monkeypatch.setattr(
        ca,
        "get_session_quality_metrics",
        lambda _d, _s: {"duration_seconds": 10**6, "findings_count": 3, "unknowns_count": 0, "has_work_evidence": True},
    )
    # A first CHECK that assessed context only: know and uncertainty are NULL (not assessed).
    monkeypatch.setattr(
        ca,
        "get_post_check_vectors",
        lambda _d, _s: {"know": None, "uncertainty": None, "context": 0.6, "timestamp": 1.0},
    )
    monkeypatch.setattr("empirica.config.path_resolver.get_session_db_path", lambda: Path("/nonexistent.db"))
    events = ca.analyze_compact_events(include_tests=True)
    assert len(events) == 1
    event = events[0]
    assert event["recovery"]["know"] is None and event["recovery"]["uncertainty"] is None
    assert event["net_loss"]["know"] is None and event["net_loss"]["uncertainty"] is None
    assert event["recovery"]["context"] == pytest.approx(0.1), "what WAS assessed is still computed"


# ── U6: the postflight delta read a PREFLIGHT row that holds NULL ──


def test_the_postflight_delta_skips_a_vector_the_preflight_never_recorded(db, monkeypatch):
    import empirica.cli.command_handlers._workflow_postflight as wp

    sid = db.create_session(ai_id="a7-delta")
    put(db, sid, "PREFLIGHT", {"know": 0.5, "clarity": 0.4}, ts=1000.0)  # every other vector NULL

    class _NoCheckpoint:
        def get_last_checkpoint(self, phase=None):
            return None

    monkeypatch.setattr(wp, "_get_db_for_session", lambda _sid: SessionDatabase(db_path=db.db_path))
    _pre, deltas, _issues = wp._calculate_postflight_deltas(
        _NoCheckpoint(), {"impact": 0.8, "know": 0.9, "clarity": 0.7}, sid
    )
    # `impact` comes FIRST on purpose: the TypeError is swallowed by an outer except, so a NULL met early silently drops every delta
    # after it, while one met last would leave the earlier deltas intact and let this test pass by accident.
    assert deltas == {"know": 0.4, "clarity": 0.3}, deltas
    assert "impact" not in deltas, "no PREFLIGHT value, so no delta against an invented one"


# ── U4: epistemics-show in text mode formatted None with :.2f ──


def test_epistemics_show_prints_n_a_for_a_vector_that_was_not_assessed(db, monkeypatch, capsys):
    """`f"{None:.2f}"` raised, and the outer handler turned it into {"ok": false} with exit 1, so the whole trajectory of a session
    that had one sparse CHECK could not be shown."""
    import types

    import empirica.cli.command_handlers.epistemics_commands as ec

    sid = db.create_session(ai_id="a7-show")
    put(db, sid, "PREFLIGHT", FULL, ts=1000.0)
    put(db, sid, "CHECK", {"know": 0.91, "uncertainty": 0.11}, ts=2000.0)  # context and completion NULL
    db.conn.row_factory = __import__("sqlite3").Row
    monkeypatch.setattr(ec, "SessionDatabase", lambda *a, **k: db)
    monkeypatch.setattr(db, "close", lambda: None)
    ec.handle_epistemics_stats_command(types.SimpleNamespace(session_id=sid, phase=None, output="human"))
    out = capsys.readouterr().out
    assert "Total Reflexes: 2" in out, out
    assert "Know: 0.91, Uncertainty: 0.11" in out
    assert "Context: n/a, Completion: n/a" in out


# ── readers that silently re-invented a value: they must use the newest RECORDED one ──


def test_the_workspace_overview_shows_the_last_recorded_know_and_uncertainty_not_a_neutral_half(db):
    """The overview took the project's newest reflex of ANY phase; a sparse CHECK on top made know and uncertainty NULL, which was
    shown as an invented 0.5 and fed the health score (know*0.6 + (1-uncertainty)*0.4)."""
    pid = db.create_project("p-overview")
    sid = db.create_session(ai_id="a7-ov", project_id=pid)
    put(db, sid, "PREFLIGHT", FULL | {"know": 0.9, "uncertainty": 0.1}, ts=1000.0)
    put(db, sid, "CHECK", {"clarity": 0.9}, ts=2000.0)
    entry = next(p for p in db.get_workspace_overview()["projects"] if p["name"] == "p-overview")
    assert entry["epistemic_state"] == {"know": 0.9, "uncertainty": 0.1}


def test_a_finding_logged_after_a_sparse_check_is_stamped_with_the_last_recorded_impact(db):
    """`_get_latest_impact_score` read the newest row's impact and fell back to 0.5 on NULL; its six callers stamp that on every
    finding logged without an explicit impact, so a recorded artifact value was invented."""
    sid = db.create_session(ai_id="a7-impact")
    put(db, sid, "PREFLIGHT", FULL | {"impact": 0.8}, ts=1000.0)
    put(db, sid, "CHECK", {"know": 0.9}, ts=2000.0)
    assert db._get_latest_impact_score(sid) == 0.8


def test_with_no_impact_ever_recorded_the_stamp_is_still_the_documented_neutral(db):
    """Control: the fallback for a session that never recorded an impact is unchanged."""
    sid = db.create_session(ai_id="a7-impact0")
    assert db._get_latest_impact_score(sid) == 0.5


def test_the_dashboard_gate_shows_the_last_recorded_values_and_keeps_a_real_zero(db, monkeypatch):
    """`know or 0.0` / `uncertainty or 1.0` made a genuine 0.0 uncertainty read 1.0 (gate failed) and a NULL one read 1.0 too."""
    import empirica.data.session_database as sdb
    from empirica.core.system_dashboard import SystemDashboard

    sid = db.create_session(ai_id="a7-dash")
    put(db, sid, "PREFLIGHT", FULL | {"know": 0.8, "uncertainty": 0.0}, ts=1000.0)
    put(db, sid, "CHECK", {"clarity": 0.9}, ts=2000.0)
    monkeypatch.setattr(sdb, "SessionDatabase", lambda *a, **k: db)
    monkeypatch.setattr(db, "close", lambda: None)
    gate = SystemDashboard(session_id=sid, node_id="a7")._query_gate()
    assert gate.know == 0.8 and gate.uncertainty == 0.0, "the last recorded values are shown, and a real 0.0 stays 0.0"
    assert gate.phase == "check"
    # ...but the LATEST check did not assess uncertainty, so it cannot be certified as passing (before NULL it read 0.5 and failed).
    assert gate.gate_passed is False


def test_the_dashboard_gate_passes_when_the_latest_check_itself_assessed_uncertainty_low(db, monkeypatch):
    import empirica.data.session_database as sdb
    from empirica.core.system_dashboard import SystemDashboard

    sid = db.create_session(ai_id="a7-dash2")
    put(db, sid, "PREFLIGHT", FULL | {"know": 0.8, "uncertainty": 0.5}, ts=1000.0)
    put(db, sid, "CHECK", {"know": 0.85, "uncertainty": 0.1}, ts=2000.0)
    monkeypatch.setattr(sdb, "SessionDatabase", lambda *a, **k: db)
    monkeypatch.setattr(db, "close", lambda: None)
    gate = SystemDashboard(session_id=sid, node_id="a7")._query_gate()
    assert gate.uncertainty == 0.1 and gate.gate_passed is True


# ── flow and health scores: a POSTFLIGHT with NULLs (a restored or imported one) has no score ──


def _project_with_postflights(db, name, rows):
    pid = db.create_project(name)
    for i, vectors in enumerate(rows):
        sid = db.create_session(ai_id=f"a7-{name}-{i}", project_id=pid)
        put(db, sid, "POSTFLIGHT", vectors, ts=1000.0 + i)
    return pid


def test_flow_and_health_skip_an_incomplete_postflight_instead_of_raising(db):
    """Both built a vectors dict from the columns and multiplied: `None * 0.25` raised TypeError. They are safe today only because
    POSTFLIGHT always supplies all 13; reflex_import writes NULL for a vector missing from a git note, so an imported one can
    break them. A score cannot be computed from a row that lacks its inputs, so that row is left out."""
    pid = _project_with_postflights(db, "p-flow", [FULL, {"know": 0.8, "uncertainty": 0.2}])
    flow = db.calculate_flow_metrics(pid)
    assert len(flow["flow_scores"]) == 1, "only the complete POSTFLIGHT has a flow score"
    health = db.calculate_health_score(pid)
    assert (
        len(health["health_scores"]) == 1
        and health["current_health"]["session_id"] == flow["flow_scores"][0]["session_id"]
    )


def test_a_project_whose_only_postflights_are_incomplete_has_no_score_not_an_error(db):
    pid = _project_with_postflights(db, "p-flow-none", [{"know": 0.8}])
    flow = db.calculate_flow_metrics(pid)
    assert flow["flow_scores"] == [] and flow["current_flow"] is None
    health = db.calculate_health_score(pid)
    assert health["health_scores"] == [] and health["current_health"] is None and health["average_health"] == 0.0


# ── the handoff report invented 0.0 for a vector either side did not assess ──


def _generator(tmp_path):
    from empirica.core.handoff.report_generator import EpistemicHandoffReportGenerator

    return EpistemicHandoffReportGenerator(db_path=str(tmp_path / "h.db"))


def test_a_delta_is_only_computed_between_two_vectors_that_were_both_assessed(tmp_path):
    """`before = preflight.get(k, 0.0)` / `after = postflight.get(k, 0.0)` turned a vector a sparse CHECK never assessed into a
    delta against zero: -0.6 for a context the practitioner simply did not re-rate."""
    gen = _generator(tmp_path)
    deltas = gen._calculate_deltas({"know": 0.5, "context": 0.6, "uncertainty": 0.4}, {"know": 0.8, "uncertainty": 0.2})
    assert deltas["know"] == 0.3 and deltas["uncertainty"] == -0.2
    assert "context" not in deltas, "context was not assessed after, so there is no delta"
    assert deltas["overall_confidence"] == 0.2


def test_no_overall_confidence_delta_without_uncertainty_on_both_sides(tmp_path):
    gen = _generator(tmp_path)
    assert "overall_confidence" not in gen._calculate_deltas({"know": 0.5, "uncertainty": 0.4}, {"know": 0.8})


def test_the_delta_table_says_n_a_for_what_was_not_assessed(tmp_path):
    gen = _generator(tmp_path)
    start = {"know": 0.5, "context": 0.6}
    end = {"know": 0.8}
    table = gen._build_delta_table(start, end, gen._calculate_deltas(start, end))
    context_row = next(line for line in table.splitlines() if line.startswith("| CONTEXT"))
    assert "n/a" in context_row and "+0.00" not in context_row and "0.00 | 0.00" not in context_row
    know_row = next(line for line in table.splitlines() if line.startswith("| KNOW"))
    assert "0.50" in know_row and "0.80" in know_row and "+0.30" in know_row


# ── the statusline: a sparse CHECK must not turn K, C, S or Δ into 0% ──

SCRIPTS = HOOKS.parent / "scripts"


def test_the_statusline_fills_a_vector_the_latest_row_did_not_assess_from_its_last_recorded_value(db):
    """The statusline shows the latest row's vectors. With the invented 0.5s gone, a CHECK that omits context shows `C` at the
    formatter's 0.0 default: a red 0% where it used to read 50%. Both are wrong; the last value the transaction recorded is right."""
    spec = importlib.util.spec_from_file_location("statusline_a7", SCRIPTS / "statusline_empirica.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    sid = db.create_session(ai_id="a7-line")
    put(db, sid, "PREFLIGHT", FULL | {"context": 0.66, "state": 0.55}, ts=1000.0, transaction_id="tx-1")
    put(db, sid, "CHECK", {"know": 0.91, "uncertainty": 0.11}, ts=2000.0, transaction_id="tx-1")
    put(
        db, sid, "PREFLIGHT", FULL | {"context": 0.11}, ts=500.0, transaction_id="tx-other"
    )  # another instance's: must not bleed in
    phase, vectors, _decision = mod.get_latest_vectors(db, sid, None, "tx-1")
    assert phase == "CHECK"
    assert vectors["know"] == 0.91 and vectors["uncertainty"] == 0.11, "the latest row's own values win"
    assert vectors["context"] == 0.66 and vectors["state"] == 0.55, "an unassessed vector shows its last recorded value"
    assert len(vectors) == 13


def test_the_statusline_does_not_borrow_across_transactions(db):
    spec = importlib.util.spec_from_file_location("statusline_a7b", SCRIPTS / "statusline_empirica.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    sid = db.create_session(ai_id="a7-line2")
    put(db, sid, "PREFLIGHT", FULL, ts=1000.0, transaction_id="tx-old")
    put(db, sid, "CHECK", {"know": 0.91}, ts=2000.0, transaction_id="tx-new")
    _phase, vectors, _d = mod.get_latest_vectors(db, sid, None, "tx-new")
    assert vectors == {"know": 0.91}, "tx-old's values belong to another transaction and must not be shown for tx-new"


# ── second review of the A7 delta: readers the two audits missed ──


def test_the_workspace_map_shows_the_last_recorded_know_and_uncertainty_and_keeps_a_real_zero(db, monkeypatch):
    """The CLI keeps its own copy of the overview's subquery: `row[4] if row[4] else 0.5` invented 0.5 for NULL and for a real 0.0."""
    import empirica.cli.command_handlers.workspace_commands as wc

    pid = db.create_project("p-wsmap")
    db.conn.execute("UPDATE projects SET repos = ? WHERE id = ?", ('["https://example.test/r.git"]', pid))
    sid = db.create_session(ai_id="a7-wsmap", project_id=pid)
    put(db, sid, "PREFLIGHT", FULL | {"know": 0.0, "uncertainty": 0.1}, ts=1000.0)
    put(db, sid, "CHECK", {"clarity": 0.9}, ts=2000.0)
    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", lambda *a, **k: db)
    monkeypatch.setattr(db, "close", lambda: None)
    repos = [{"path": "/x", "name": "r", "remote_url": "https://example.test/r.git", "has_remote": True}]
    wc._wsmap_match_empirica_projects(repos)
    project = repos[0]["empirica_project"]
    assert project["know"] == 0.0 and project["uncertainty"] == 0.1


def test_the_bootstrap_handoff_prints_n_a_for_a_vector_that_was_not_assessed(capsys):
    """`vectors.get(key, "N/A")` followed by `:.2f` cannot format the string it just defaulted to, and a None cannot be formatted
    either: an auto-closed session (completion only, every other vector NULL) crashed project-bootstrap's human output."""
    from empirica.cli.command_handlers.project_bootstrap_formatter import _format_handoff_section

    _format_handoff_section(
        {
            "ai_id": "x",
            "vectors": {
                "engagement": None,
                "uncertainty": None,
                "foundation": {"know": None, "do": 0.5, "context": None},
            },
            "deltas": {"foundation": {"know": None}},
        }
    )
    out = capsys.readouterr().out
    assert "Engagement: n/a" in out and "Uncertainty: n/a" in out
    assert "know=n/a" in out and "do=0.50" in out and "context=n/a" in out


def _investigation_report(db, check_vectors):
    from empirica.core.handoff.report_generator import EpistemicHandoffReportGenerator

    sid = db.create_session(ai_id="a7-report")
    put(db, sid, "PREFLIGHT", FULL | {"uncertainty": 0.6}, ts=1000.0)
    put(db, sid, "CHECK", check_vectors, ts=2000.0)
    gen = EpistemicHandoffReportGenerator.__new__(EpistemicHandoffReportGenerator)
    gen.db = db
    return gen, gen.generate_handoff_report(sid, "task", [], [], "ctx", handoff_subtype="investigation")


def test_an_investigation_handoff_whose_check_skipped_uncertainty_still_renders(db):
    """`overall_confidence_delta` became None (uncertainty not assessed on both sides) and the markdown formatted it with :+.3f."""
    _gen, report = _investigation_report(db, {"know": 0.9, "do": 0.9, "context": 0.9})
    assert report["overall_confidence_delta"] is None
    assert "Overall Confidence Change:** n/a" in report["markdown"]


def test_missing_uncertainty_is_not_read_as_zero_so_the_handoff_does_not_say_ready_for_execution(db):
    """`uncertainty = vectors.get("uncertainty", 0)` made 'not assessed' into 'no uncertainty', and a CHECK that rated know, do and
    context at 0.9 and skipped uncertainty was told 'Ready for execution - strong epistemic foundation'. The old invented 0.5 said
    'Continue investigation'. A NULL must not flip a decision to the permissive side."""
    _gen, report = _investigation_report(db, {"know": 0.9, "do": 0.9, "context": 0.9})
    steps = " | ".join(report["recommended_next_steps"])
    assert "Ready for execution" not in steps
    assert "uncertainty was not assessed" in steps.lower()


def test_ready_for_execution_still_appears_when_uncertainty_was_assessed_low(db):
    """Control: the recommendation is still given when its inputs are all there."""
    _gen, report = _investigation_report(db, {"know": 0.9, "do": 0.9, "context": 0.9, "uncertainty": 0.1})
    assert any("Ready for execution" in s for s in report["recommended_next_steps"])


def test_the_heuristic_does_not_certify_calibration_it_cannot_assess():
    """With know or uncertainty absent from the deltas both defaulted to 0.0 and the check answered 'well calibrated: knowledge gain
    matches uncertainty reduction' about a comparison it never made."""
    from empirica.core.handoff.report_generator import EpistemicHandoffReportGenerator

    gen = EpistemicHandoffReportGenerator.__new__(EpistemicHandoffReportGenerator)
    assert gen._heuristic_calibration_check({"know": 0.3})["status"] == "not_assessable"
    assert gen._heuristic_calibration_check({})["status"] == "not_assessable"
    assert gen._heuristic_calibration_check({"know": 0.3, "uncertainty": -0.3})["status"] == "well_calibrated"


# ── a third review: the same class in four more places ──


def test_stagnation_is_not_detected_from_values_nobody_assessed():
    """Earlier CHECKs that skipped know and uncertainty were read as 0.5 each, so a current CHECK at know 0.5 / uncertainty 0.45 looked
    stagnant and the gate said 'proceed' at an uncertainty above its own 0.35 threshold. A delta needs a value on both sides."""
    from empirica.cli.command_handlers._workflow_check import _check_detect_diminishing_returns

    earlier = [
        {"clarity": 0.9},
        {"clarity": 0.9},
        {"clarity": 0.9},
    ]  # dicts as the readers return them: NULLs are dropped
    result = _check_detect_diminishing_returns(earlier, 0.5, 0.45)
    assert result["detected"] is False and result["recommend_proceed"] is False


def test_real_stagnation_is_still_detected():
    """Control: three assessed rounds that really did not move are still diminishing returns."""
    from empirica.cli.command_handlers._workflow_check import _check_detect_diminishing_returns

    earlier = [{"know": 0.5, "uncertainty": 0.45}] * 3
    result = _check_detect_diminishing_returns(earlier, 0.5, 0.45)
    assert result["detected"] is True


def test_the_pre_compact_snapshot_takes_each_vector_from_its_last_recorded_value(db):
    """The continuation PREFLIGHT is built from the newest row, usually a sparse CHECK; a missing uncertainty then became 0.3 + 0.05 and
    a missing state 0.7, numbers nobody assessed, and post-compact deltas were measured against them."""
    from empirica.cli.command_handlers.session_commands import _compact_vectors_to_save

    sid = db.create_session(ai_id="a7-compact")
    put(db, sid, "PREFLIGHT", FULL | {"uncertainty": 0.4, "state": 0.55}, ts=1000.0)
    put(db, sid, "CHECK", {"know": 0.91, "uncertainty": 0.12}, ts=2000.0)
    vectors = _compact_vectors_to_save(db, sid, None)
    assert vectors["know"] == 0.91 and vectors["uncertainty"] == 0.12
    assert vectors["state"] == 0.55, "an unassessed vector keeps its last recorded value"
    assert set(vectors) == set(NAMES)


def test_hook_supplied_current_vectors_win_over_history(db):
    from empirica.cli.command_handlers.session_commands import _compact_vectors_to_save

    sid = db.create_session(ai_id="a7-compact2")
    put(db, sid, "PREFLIGHT", FULL, ts=1000.0)
    assert _compact_vectors_to_save(db, sid, {"know": 0.7}) == {"know": 0.7}


def test_the_handoff_summary_line_says_n_a_for_a_delta_that_was_not_computed():
    from empirica.cli.command_handlers.handoff_commands import _deltas_summary

    line = _deltas_summary({"know": 0.3, "context": -0.1})
    assert "KNOW +0.30" in line and "CONTEXT -0.10" in line and "STATE n/a" in line
    assert "STATE +0.00" not in line


def test_the_statusline_completion_is_the_last_recorded_one_not_zero(db):
    """get_open_counts and get_active_goal read the newest row's completion and turned NULL into 0.0, so the bar contradicted the
    vectors segment (which now fills from the last recorded value)."""
    spec = importlib.util.spec_from_file_location("statusline_a7c", SCRIPTS / "statusline_empirica.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    sid = db.create_session(ai_id="a7-comp")
    put(db, sid, "PREFLIGHT", FULL | {"completion": 0.7}, ts=1000.0)
    put(db, sid, "CHECK", {"know": 0.9}, ts=2000.0)
    assert mod.get_open_counts(db, sid)["completion"] == 0.7
    goal = mod.get_active_goal(db, sid)
    assert goal is None or goal["completion"] == 0.7
