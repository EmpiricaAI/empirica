"""`--source` now writes a canonical `sourced_from` edge, not only the column.

`finding-log --source <id>` historically serialized the ids into the `source_refs`
COLUMN — invisible to the artifact graph — so a practice could cite dozens of
sources and still show 0 `sourced_from` edges. The edge makes a citation a
first-class graph link; the column remains for the ordered list.
"""

from __future__ import annotations

import uuid

import pytest

from empirica.data.session_database import SessionDatabase

PROJECT_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "src.db"))
    yield d
    d.close()


def _sourced_edges(db, fid):
    return sorted(
        r[0]
        for r in db.conn.execute(
            "SELECT to_id FROM artifact_edges WHERE from_id = ? AND relation = 'sourced_from'", (fid,)
        ).fetchall()
    )


def test_source_writes_sourced_from_edge(db):
    fid = db.log_finding(PROJECT_ID, SESSION_ID, "cited finding", source_ids=["src-a", "src-b"])
    assert _sourced_edges(db, fid) == ["src-a", "src-b"]


def test_source_still_populates_column(db):
    fid = db.log_finding(PROJECT_ID, SESSION_ID, "cited finding", source_ids=["src-a"])
    col = db.conn.execute("SELECT source_refs FROM project_findings WHERE id = ?", (fid,)).fetchone()[0]
    assert "src-a" in col


def test_no_source_no_edge(db):
    fid = db.log_finding(PROJECT_ID, SESSION_ID, "uncited finding")
    assert _sourced_edges(db, fid) == []


def test_source_edges_idempotent(db):
    fid = db.log_finding(PROJECT_ID, SESSION_ID, "cited", source_ids=["src-a"])
    db.breadcrumbs._attach_sources(fid, ["src-a"])  # re-run must not duplicate
    assert _sourced_edges(db, fid) == ["src-a"]


def test_blank_source_ids_skipped(db):
    fid = db.log_finding(PROJECT_ID, SESSION_ID, "cited", source_ids=["", "  ", "src-real"])
    assert _sourced_edges(db, fid) == ["src-real"]
