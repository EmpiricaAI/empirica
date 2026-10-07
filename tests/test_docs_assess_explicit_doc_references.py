"""Staleness check 5 (memory that names a doc file) reads the columns the tables really have, scoped to the project.

Found by the 2026-10-06 pipeline sweep (U1): the query selected `text` and filtered on `timestamp`, which do not exist, the
OperationalError was swallowed, and the check has never returned a result; it also read every project's memory. The database
is a real sqlite schema under tmp_path with the columns SessionDatabase creates.
"""

from __future__ import annotations

import logging
import sqlite3
import time

import pytest

from empirica.cli.command_handlers.docs_commands import EpistemicDocsAgent

NOW = time.time()
DAY = 24 * 3600


class _DB:
    def __init__(self, conn):
        self.conn = conn

    def close(self):
        self.closed = True


def _conn(rows_findings=(), rows_dead_ends=(), with_dead_ends=True):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_findings (id TEXT, project_id TEXT, finding TEXT, created_timestamp REAL)")
    if with_dead_ends:
        conn.execute(
            "CREATE TABLE project_dead_ends (id TEXT, project_id TEXT, approach TEXT, why_failed TEXT, created_timestamp REAL)"
        )
    for i, (project, text, age_days) in enumerate(rows_findings):
        conn.execute("INSERT INTO project_findings VALUES (?, ?, ?, ?)", (f"f{i}", project, text, NOW - age_days * DAY))
    for i, (project, text, age_days) in enumerate(rows_dead_ends):
        conn.execute(
            "INSERT INTO project_dead_ends VALUES (?, ?, ?, '', ?)", (f"d{i}", project, text, NOW - age_days * DAY)
        )
    conn.commit()
    return conn


@pytest.fixture
def agent(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ARCHITECTURE.md").write_text("# arch\n")
    a = EpistemicDocsAgent(tmp_path)
    monkeypatch.setattr(a, "_detect_project_id", lambda: "mine")
    return a


def _run(agent, monkeypatch, conn):
    db = _DB(conn)
    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", lambda *a, **k: db)
    return agent._check_explicit_doc_references(agent.root / "docs"), db


def test_a_finding_that_names_an_existing_doc_is_reported(agent, monkeypatch):
    refs, db = _run(agent, monkeypatch, _conn([("mine", "The layout in ARCHITECTURE.md is out of date", 3)]))
    assert [r["doc_path"] for r in refs] == ["ARCHITECTURE.md"] and refs[0]["memory_type"] == "finding"
    assert getattr(db, "closed", False)


def test_a_dead_end_is_read_from_its_approach_column(agent, monkeypatch):
    refs, _ = _run(
        agent, monkeypatch, _conn(rows_dead_ends=[("mine", "Tried editing docs/ARCHITECTURE.md by hand", 1)])
    )
    assert [r["memory_type"] for r in refs] == ["dead_end"]


def test_another_projects_memory_is_not_read_as_this_ones(agent, monkeypatch):
    refs, _ = _run(agent, monkeypatch, _conn([("someone-else", "See ARCHITECTURE.md", 1)]))
    assert refs == []


def test_a_memory_older_than_thirty_days_is_ignored(agent, monkeypatch):
    refs, _ = _run(agent, monkeypatch, _conn([("mine", "See ARCHITECTURE.md", 45)]))
    assert refs == []


def test_a_doc_that_does_not_exist_is_not_reported(agent, monkeypatch):
    refs, _ = _run(agent, monkeypatch, _conn([("mine", "See MISSING.md for details", 2)]))
    assert refs == []


def test_a_table_that_cannot_be_read_is_logged_by_name_and_the_other_still_answers(agent, monkeypatch, caplog):
    conn = _conn([("mine", "ARCHITECTURE.md is stale", 1)], with_dead_ends=False)
    with caplog.at_level(logging.WARNING):
        refs, _ = _run(agent, monkeypatch, conn)
    assert [r["memory_type"] for r in refs] == ["finding"]
    assert any("project_dead_ends" in r.getMessage() for r in caplog.records)


def test_no_resolvable_project_means_no_unscoped_read(agent, monkeypatch):
    monkeypatch.setattr(agent, "_detect_project_id", lambda: None)
    refs, db = _run(agent, monkeypatch, _conn([("mine", "See ARCHITECTURE.md", 1)]))
    assert refs == [] and not hasattr(db, "closed")


def test_results_are_capped_at_five(agent, monkeypatch):
    rows = [("mine", f"see ARCHITECTURE.md item {i}", i) for i in range(1, 9)]
    refs, _ = _run(agent, monkeypatch, _conn(rows))
    assert len(refs) == 5


def test_the_real_table_columns_are_the_ones_queried(tmp_path):
    """Guard against the original defect: the names the query uses must exist in a freshly created SessionDatabase."""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    try:
        findings = {r[1] for r in db.conn.execute("PRAGMA table_info(project_findings)")}
        dead_ends = {r[1] for r in db.conn.execute("PRAGMA table_info(project_dead_ends)")}
    finally:
        db.close()
    assert {"finding", "created_timestamp", "project_id"} <= findings
    assert {"approach", "created_timestamp", "project_id"} <= dead_ends
