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
