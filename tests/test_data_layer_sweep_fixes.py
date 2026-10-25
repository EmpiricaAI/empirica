"""Phase 1 sweep fixes, data layer (goal 1cd7caa3): each defect reproduced at HEAD by an independent read-only run on 2026-10-25, then
pinned here with a negative control. Everything lives in a temp SessionDatabase with HOME pinned; nothing touches a real project.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    from empirica.data.session_database import SessionDatabase

    database = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield database
    database.close()


@pytest.fixture
def project(db):
    pid = db.create_project("p")
    sid = db.create_session(ai_id="t", project_id=pid)
    return pid, sid


# ── A5 abandoned goals are not current work ──────────────────────────────────


def test_an_abandoned_goal_is_not_reported_as_current_work(db, project):
    pid, sid = project
    live = db.goals.create_goal(sid, "LIVE goal one")
    gone = db.goals.create_goal(sid, "ABANDONED goal two")
    assert db.goals.abandon_goal(gone, "dead")
    result = db.goals.get_project_goals(pid)
    assert [g["objective"] for g in result["incomplete_work"]] == ["LIVE goal one"]
    assert [g["objective"] for g in result["goals"]] == ["LIVE goal one"]
    assert live  # control: the live goal is still listed (asserted above), not filtered with the abandoned one


def test_a_planned_goal_is_still_listed(db, project):
    pid, sid = project
    db.goals.create_goal(sid, "PLANNED goal", status="planned")
    assert [g["objective"] for g in db.goals.get_project_goals(pid)["incomplete_work"]] == ["PLANNED goal"]


# ── A6 + A17 a goal prefix must name exactly one goal ────────────────────────


def _mk(db, sid, goal_id, objective, status="planned", completed_days_ago=None):
    completed = status == "completed"
    db.conn.execute(
        "INSERT INTO goals (id, session_id, objective, scope, status, is_completed, completed_timestamp, created_timestamp, goal_data) "
        "VALUES (?, ?, ?, '{}', ?, ?, ?, ?, '{}')",
        (
            goal_id,
            sid,
            objective,
            status,
            1 if completed else 0,
            time.time() - (completed_days_ago or 0) * 86400 if completed else None,
            time.time(),
        ),
    )
    db.conn.commit()


def _state(db, prefix):
    return {
        r[0]: tuple(r[1:])
        for r in db.conn.execute("SELECT objective, status, archived FROM goals WHERE id LIKE ?", (f"{prefix}%",))
    }


def test_an_ambiguous_prefix_activates_nothing(db, project):
    _, sid = project
    _mk(db, sid, "abcd1234-0000-0000-0000-000000000001", "P1")
    _mk(db, sid, "abcd1234-0000-0000-0000-000000000002", "P2")
    assert db.goals.activate_goal("abcd1234") is False
    assert {s for s, *_ in _state(db, "abcd1234").values()} == {"planned"}, "neither goal may be touched"


def test_a_unique_prefix_still_activates_its_goal(db, project):
    _, sid = project
    _mk(db, sid, "abcd1234-0000-0000-0000-000000000001", "P1")
    _mk(db, sid, "ffff1234-0000-0000-0000-000000000002", "P2")
    assert db.goals.activate_goal("abcd1234") is True
    state = _state(db, "")
    assert state["P1"][0] == "in_progress" and state["P2"][0] == "planned"


def test_an_ambiguous_prefix_reopens_nothing_and_a_one_character_prefix_is_refused(db, project):
    _, sid = project
    _mk(db, sid, "abcd1234-0000-0000-0000-000000000001", "C1", "completed", 90)
    _mk(db, sid, "abcd1234-0000-0000-0000-000000000002", "C2", "completed", 90)
    assert db.goals.reopen_goal("abcd1234") is False
    assert db.goals.reopen_goal("a") is False
    assert {s for s, *_ in _state(db, "abcd1234").values()} == {"completed"}


def test_an_ambiguous_prefix_archives_nothing_and_a_unique_one_archives_only_its_goal(db, project):
    _, sid = project
    _mk(db, sid, "abcd1234-0000-0000-0000-000000000001", "C1", "completed", 90)
    _mk(db, sid, "abcd1234-0000-0000-0000-000000000002", "C2", "completed", 90)
    _mk(db, sid, "eeee9999-0000-0000-0000-000000000003", "C3", "completed", 90)
    assert db.goals.archive_stale_completed(goal_id="abcd1234", apply=True) == []
    assert all(not archived for _, archived in ((o, v[1]) for o, v in _state(db, "").items()))
    archived = db.goals.archive_stale_completed(goal_id="eeee9999", apply=True)
    assert [r["objective"] for r in archived] == ["C3"]
    assert _state(db, "eeee9999")["C3"][1] == 1 and _state(db, "abcd1234")["C1"][1] in (0, None)


def test_goal_status_does_not_answer_for_an_ambiguous_prefix(db, project):
    _, sid = project
    _mk(db, sid, "abcd1234-0000-0000-0000-000000000001", "P1")
    _mk(db, sid, "abcd1234-0000-0000-0000-000000000002", "P2", "completed", 1)
    assert db.goals.goal_status("abcd1234") is None
    assert db.goals.goal_status("abcd1234-0000-0000-0000-000000000001") == "planned"


# ── A2 facade/caller drift, A3 --tag-losers ──────────────────────────────────

VECTOR_NAMES = [
    "know", "do", "context", "clarity", "coherence", "signal", "density",
    "state", "change", "completion", "impact", "engagement", "uncertainty",
]  # fmt: skip


def _two_branches(db, sid):
    pre = dict.fromkeys(VECTOR_NAMES, 0.3)
    pre["uncertainty"] = 0.7
    ids = []
    for i, level in enumerate((0.9, 0.6)):
        branch = db.create_branch(sid, f"b{i}", f"path{i}", f"git{i}", pre)
        post = dict.fromkeys(VECTOR_NAMES, level)
        post["uncertainty"] = 0.2
        db.checkpoint_branch(branch, post, 1000, 10)
        ids.append(branch)
    return ids


def test_merge_branches_reports_the_losers_as_records_not_just_names(db, project):
    _, sid = project
    _two_branches(db, sid)
    merged = db.branches.merge_branches(session_id=sid, investigation_round=1)
    assert merged["other_branches"] == ["b1"], "names stay as they were, for existing readers"
    assert [(loser["branch_name"], round(loser["score"], 2) > 0) for loser in merged["losers"]] == [("b1", True)]
    assert merged["losers"][0]["branch_id"] != merged["winning_branch_id"]


def test_tag_losers_logs_a_dead_end_per_loser_instead_of_crashing_after_the_merge(db, project, tmp_path, monkeypatch):
    _, sid = project
    _two_branches(db, sid)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "s.db"))
    from empirica.cli.command_handlers.investigation_commands import (
        handle_investigate_merge_branches_command as handler,
    )

    args = type("A", (), {"session_id": sid, "round": "1", "tag_losers": True, "output": "json", "verbose": False})()
    handler(args)
    rows = db.conn.execute("SELECT approach FROM project_dead_ends WHERE session_id = ?", (sid,)).fetchall()
    assert [r[0] for r in rows] == ["Investigation branch: b1"]


def test_without_tag_losers_the_merge_logs_no_dead_ends(db, project, tmp_path, monkeypatch):
    _, sid = project
    _two_branches(db, sid)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "s.db"))
    from empirica.cli.command_handlers.investigation_commands import (
        handle_investigate_merge_branches_command as handler,
    )

    args = type("A", (), {"session_id": sid, "round": "1", "tag_losers": False, "output": "json", "verbose": False})()
    handler(args)
    assert db.conn.execute("SELECT COUNT(*) FROM project_dead_ends WHERE session_id = ?", (sid,)).fetchone()[0] == 0


def test_the_agent_aggregation_calls_merge_branches_with_the_real_keyword(db, project, tmp_path, monkeypatch):
    _, sid = project
    branch_ids = _two_branches(db, sid)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "s.db"))
    from types import SimpleNamespace as NS

    from empirica.core.agents.epistemic_agent import aggregate_agent_results

    results = [
        NS(
            branch_id=b,
            success=True,
            findings=[f"f{i}"],
            unknowns=[],
            merge_score=0.5 + i / 10,
            postflight_vectors={},
            persona_id=f"p{i}",
            learning_delta={},
        )
        for i, b in enumerate(branch_ids)
    ]
    summary = aggregate_agent_results(sid, results, investigation_round=1)
    assert "error" not in summary or "unexpected keyword" not in str(summary["error"])
    assert db.conn.execute("SELECT COUNT(*) FROM merge_decisions WHERE session_id = ?", (sid,)).fetchone()[0] == 1


def test_the_facade_forwards_transaction_id_to_create_branch_and_project_id_to_store_vectors(db, project):
    _, sid = project
    pre = dict.fromkeys(VECTOR_NAMES, 0.5)
    branch = db.create_branch(sid, "bx", "path", "git", pre, transaction_id="tx-123")
    assert (
        db.conn.execute("SELECT transaction_id FROM investigation_branches WHERE id = ?", (branch,)).fetchone()[0]
        == "tx-123"
    )
    db.store_vectors(sid, "CHECK", pre, project_id="proj-xyz")
    assert (
        db.conn.execute(
            "SELECT project_id FROM reflexes WHERE session_id = ? ORDER BY id DESC LIMIT 1", (sid,)
        ).fetchone()[0]
        == "proj-xyz"
    )
    # control: omitting both still works, as every existing caller does
    assert db.create_branch(sid, "by", "path", "git", pre)
    db.store_vectors(sid, "CHECK", pre)


# ── A8 the handoff delta pairs a POSTFLIGHT with the PREFLIGHT of the same transaction ──


def _vec(level):
    return dict.fromkeys(VECTOR_NAMES, level)


def _handoff_know_delta(db, pid):
    handoff = db.get_ai_epistemic_handoff(pid, "t")
    assert handoff is not None
    return round(handoff["deltas"]["foundation"]["know"], 2)


def test_the_handoff_delta_uses_the_preflight_of_the_same_transaction_not_the_sessions_first(db, project):
    pid, sid = project
    db.store_vectors(sid, "PREFLIGHT", _vec(0.2), transaction_id="txA")
    db.store_vectors(sid, "POSTFLIGHT", _vec(0.4), transaction_id="txA")
    db.store_vectors(sid, "PREFLIGHT", _vec(0.6), transaction_id="txB")
    db.store_vectors(sid, "POSTFLIGHT", _vec(0.9), transaction_id="txB")
    assert _handoff_know_delta(db, pid) == 0.3, "0.9 - 0.6 for transaction B, not 0.9 - 0.2 across two transactions"


def test_a_legacy_row_with_no_transaction_id_still_pairs_within_its_session(db, project):
    pid, sid = project
    db.store_vectors(sid, "PREFLIGHT", _vec(0.5))
    db.store_vectors(sid, "POSTFLIGHT", _vec(0.8))
    assert _handoff_know_delta(db, pid) == 0.3


# ── A9 workspace overview health is driven by sessions that exist, not by a counter nothing increments ──


def _overview_entry(db, name):
    overview = db.get_workspace_overview()
    return next(p for p in overview["projects"] if p["name"] == name)


def test_the_overview_counts_a_projects_sessions_live(db, project):
    pid, sid = project
    db.create_session(ai_id="t2", project_id=pid)
    db.log_dead_end(project_id=pid, session_id=sid, approach="a", why_failed="w")
    entry = _overview_entry(db, "p")
    assert entry["total_sessions"] == 2
    assert entry["dead_end_ratio"] == 0.5, "one dead end over two sessions"


def test_a_project_with_no_sessions_keeps_the_neutral_health(db):
    db.create_project("empty")
    entry = _overview_entry(db, "empty")
    assert entry["total_sessions"] == 0
    assert entry["dead_end_ratio"] == 0.0 and entry["health_score"] == 0.5


# ── #12 closing an engagement stamps ended_at and status, because the API reports both ──


@pytest.fixture
def engagements():
    import sqlite3

    from empirica.data.repositories.workspace_db import WorkspaceDBRepository, _ensure_workspace_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    repo = WorkspaceDBRepository(conn)
    repo.create_engagement("e1", "Ticket one")
    return repo


def test_closing_an_engagement_sets_ended_at_and_a_terminal_status(engagements):
    engagements.update_engagement("e1", lifecycle_state="closed", outcome="won")
    e = engagements.get_engagement("e1")
    assert e["ended_at"] is not None
    assert e["status"] == "completed"


@pytest.mark.parametrize(("outcome", "status"), [("lost", "lost"), ("defer", "deferred"), ("resolved", "completed")])
def test_the_status_follows_the_outcome_it_closed_with(engagements, outcome, status):
    engagements.update_engagement("e1", lifecycle_state="closed", outcome=outcome)
    assert engagements.get_engagement("e1")["status"] == status


def test_reopening_clears_ended_at_and_the_terminal_status(engagements):
    engagements.update_engagement("e1", lifecycle_state="closed", outcome="won")
    engagements.update_engagement("e1", lifecycle_state="in_progress")
    e = engagements.get_engagement("e1")
    assert e["ended_at"] is None and e["status"] == "active"


def test_an_update_that_does_not_touch_the_lifecycle_leaves_ended_at_and_status_alone(engagements):
    engagements.update_engagement("e1", title="Renamed")
    e = engagements.get_engagement("e1")
    assert e["title"] == "Renamed" and e["ended_at"] is None and e["status"] == "active"


# ── #18 the PostgreSQL schema path rewrites every SQLite-only construct ─────


def test_no_schema_keeps_a_sqlite_only_construct_after_the_postgresql_adaptation():
    from empirica.data.schema import ALL_SCHEMAS
    from empirica.data.schema.dialect import adapt_all_schemas

    leftovers = {}
    for sql in adapt_all_schemas(list(ALL_SCHEMAS), "postgresql"):
        for needle in ("datetime(", "strftime", "AUTOINCREMENT"):
            if needle.lower() in sql.lower():
                leftovers.setdefault(needle, []).append(sql.split("(")[0].strip()[-40:])
    assert leftovers == {}, "PostgreSQL has no datetime(), strftime() or AUTOINCREMENT: the CREATE fails"


def test_the_datetime_default_becomes_a_utc_text_expression_and_sqlite_is_untouched():
    from empirica.data.schema.dialect import adapt_schema_sql

    ddl = "CREATE TABLE t (created_at TEXT DEFAULT (datetime('now')))"
    assert adapt_schema_sql(ddl, "sqlite") == ddl
    pg = adapt_schema_sql(ddl, "postgresql")
    assert "datetime(" not in pg and "NOW() AT TIME ZONE 'UTC'" in pg


# ── #15 update-artifacts validates values, #16 resolve-artifacts refuses an unknown kind ──


def _run_handler(handler, config: dict, monkeypatch, capsys, **extra):
    import io
    import json
    from types import SimpleNamespace as NS

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(config)))
    capsys.readouterr()
    handler(NS(config="-", schema=False, output="json", verbose=False, file=None, apply=True, **extra))
    out = capsys.readouterr().out
    start = out.find("{")
    return json.loads(out[start:]) if start >= 0 else {}


@pytest.fixture
def artifacts(db, project, tmp_path, monkeypatch):
    pid, sid = project
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "s.db"))
    finding = db.log_finding(project_id=pid, session_id=sid, finding="f15", impact=0.5)
    finding2 = db.log_finding(project_id=pid, session_id=sid, finding="other", impact=0.5)
    goal = db.goals.create_goal(session_id=sid, objective="g15")
    return NS_(db=db, finding=finding, finding2=finding2, goal=goal)


class NS_:  # a tiny attribute bag; SimpleNamespace is imported lazily above
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _finding_row(db, fid):
    return tuple(
        db.conn.execute(
            "SELECT visibility, epistemic_source, impact FROM project_findings WHERE id = ?", (fid,)
        ).fetchone()
    )


def _update(artifacts, monkeypatch, capsys, **fields):
    from empirica.cli.command_handlers.graph_commands import handle_update_artifacts_command

    item = {"type": "finding", "id": artifacts.finding, **fields}
    return _run_handler(handle_update_artifacts_command, {"updates": [item]}, monkeypatch, capsys)


def test_update_artifacts_rejects_values_outside_the_closed_sets_and_leaves_the_row_alone(
    artifacts, monkeypatch, capsys
):
    before = _finding_row(artifacts.db, artifacts.finding)
    _update(artifacts, monkeypatch, capsys, visibility="sharred", epistemic_source="bogus", impact=7.5)
    assert _finding_row(artifacts.db, artifacts.finding) == before


def test_update_artifacts_still_applies_legal_values_including_the_range_ends(artifacts, monkeypatch, capsys):
    _update(artifacts, monkeypatch, capsys, visibility="public", epistemic_source="search", impact=1.0)
    assert _finding_row(artifacts.db, artifacts.finding) == ("public", "search", 1.0)
    _update(artifacts, monkeypatch, capsys, impact=0)
    assert _finding_row(artifacts.db, artifacts.finding)[2] == 0


def test_update_artifacts_refuses_an_unknown_goal_status_and_keeps_the_completion_columns_in_step(
    artifacts, monkeypatch, capsys
):
    from empirica.cli.command_handlers.graph_commands import handle_update_artifacts_command

    def goal_row():
        return tuple(
            artifacts.db.conn.execute(
                "SELECT status, is_completed, completed_timestamp FROM goals WHERE id = ?", (artifacts.goal,)
            ).fetchone()
        )

    def set_status(value):
        _run_handler(
            handle_update_artifacts_command,
            {"updates": [{"type": "goal", "id": artifacts.goal, "status": value}]},
            monkeypatch,
            capsys,
        )

    start = goal_row()
    set_status("banana")
    assert goal_row() == start, "an unknown status is refused, not stored"
    set_status("completed")
    status, is_completed, completed_at = goal_row()
    assert (status, is_completed) == ("completed", 1) and completed_at is not None
    set_status("in_progress")
    status, is_completed, completed_at = goal_row()
    assert (status, is_completed, completed_at) == ("in_progress", 0, None), (
        "leaving completed clears the sibling columns"
    )


def _resolve(artifacts, monkeypatch, capsys, **fields):
    from empirica.cli.command_handlers.graph_commands import handle_resolve_artifacts_command

    item = {"type": "finding", "id": artifacts.finding, "resolution": "done", **fields}
    return _run_handler(handle_resolve_artifacts_command, {"resolutions": [item]}, monkeypatch, capsys)


def _resolution_row(artifacts):
    return tuple(
        artifacts.db.conn.execute(
            "SELECT is_resolved, resolution_kind FROM project_findings WHERE id = ?", (artifacts.finding,)
        ).fetchone()
    )


def test_resolve_artifacts_refuses_a_typo_in_resolution_kind_instead_of_storing_null(artifacts, monkeypatch, capsys):
    result = _resolve(artifacts, monkeypatch, capsys, resolution_kind="retracteed")
    assert _resolution_row(artifacts) == (0, None), (
        "the finding stays open: a misclassified resolution is the exact thing being measured"
    )
    assert result.get("resolved", 0) == 0 and result.get("errors"), "the refusal is reported"


@pytest.mark.parametrize("kind", ["stale", "retracted", None])
def test_resolve_artifacts_still_applies_a_known_kind_or_none(artifacts, monkeypatch, capsys, kind):
    fields = {"resolution_kind": kind} if kind else {}
    _resolve(artifacts, monkeypatch, capsys, **fields)
    assert _resolution_row(artifacts) == (1, kind)


def test_require_resolution_kind_returns_known_kinds_and_none_and_raises_on_anything_else():
    from empirica.data.resolution_kind import require_resolution_kind

    assert (
        require_resolution_kind("stale") == "stale"
        and require_resolution_kind(None) is None
        and require_resolution_kind("") is None
    )
    with pytest.raises(ValueError, match="retracteed"):
        require_resolution_kind("retracteed")


def _api_project(tmp_path, pid):
    import sqlite3

    proj = tmp_path / "apiproj"
    (proj / ".empirica" / "sessions").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text(f"name: api-test\nproject_id: {pid}\n", encoding="utf-8")
    conn = sqlite3.connect(str(proj / ".empirica" / "sessions" / "sessions.db"))
    conn.executescript(
        """
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            finding TEXT NOT NULL, finding_data TEXT, subject TEXT, impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL, is_resolved INTEGER DEFAULT 0, resolution TEXT, resolved_timestamp REAL,
            superseded_by TEXT, resolution_kind TEXT
        );
        CREATE TABLE artifact_edges (
            from_id TEXT NOT NULL, to_id TEXT NOT NULL, relation TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (from_id, to_id, relation)
        );
        """
    )
    conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, finding_data, created_timestamp) VALUES (?, ?, 's', 'f', '{}', 1.0)",
        ("11111111-1111-1111-1111-111111111111", pid),
    )
    conn.commit()
    conn.close()
    return proj, proj / ".empirica" / "sessions" / "sessions.db"


@pytest.mark.parametrize(("kind", "status", "stored"), [("retracteed", 400, (0, None)), ("stale", 200, (1, "stale"))])
def test_the_resolve_route_refuses_an_unknown_resolution_kind_and_applies_a_known_one(
    tmp_path, monkeypatch, kind, status, stored
):
    import sqlite3
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    import empirica.api.daemon_project as dp
    from empirica.api.serve_app import create_serve_app

    dp._cached, dp._cached_project = False, None
    pid = "22222222-2222-2222-2222-222222222222"
    proj, db_path = _api_project(tmp_path, pid)
    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app(), raise_server_exceptions=False)
        resp = client.patch(
            "/api/v1/artifacts/11111111-1111-1111-1111-111111111111/resolve",
            json={"type": "finding", "resolution_kind": kind},
        )
    dp._cached, dp._cached_project = False, None
    assert resp.status_code == status, resp.text
    row = sqlite3.connect(str(db_path)).execute("SELECT is_resolved, resolution_kind FROM project_findings").fetchone()
    assert tuple(row) == stored
