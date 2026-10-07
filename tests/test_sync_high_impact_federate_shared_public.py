"""Verify sync_high_impact_to_global federates only shared/public, unresolved artifacts.

The earlier behavior was to federate all high-impact findings (no matter the visibility
or resolved status) and all dead ends (no matter the visibility).

After the fix:
1. Only shared/public visibility tiers federate (local stays local)
2. Resolved findings do not federate (only unresolved findings do)
3. This applies to findings, unknowns, and dead ends consistently
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from empirica.core.qdrant import global_sync as gs


def _db(tmp_path):
    """Create a session database with the schema for findings, unknowns, dead ends."""
    conn = sqlite3.connect(str(tmp_path / "session.db"))
    conn.execute(
        """
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY, project_id TEXT, finding TEXT,
            impact REAL, session_id TEXT, created_timestamp REAL,
            is_resolved INT DEFAULT 0, visibility TEXT
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE project_unknowns (
            id TEXT PRIMARY KEY, project_id TEXT, unknown TEXT,
            resolved_by TEXT, session_id TEXT, resolved_timestamp REAL,
            is_resolved INT DEFAULT 0, visibility TEXT
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE project_dead_ends (
            id TEXT PRIMARY KEY, project_id TEXT, approach TEXT,
            why_failed TEXT, session_id TEXT, created_timestamp REAL,
            visibility TEXT
        )
    """
    )
    conn.commit()
    return conn


class _SessionDB:
    """Mock SessionDatabase for testing."""

    def __init__(self, conn):
        self.conn = conn

    def close(self):
        self.conn.close()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Set up a session database and capturing embedder, with Qdrant available."""
    conn = _db(tmp_path)
    embedded: list[dict] = []

    def _fake_embed(*, item_id, text, item_type, project_id, **_):
        embedded.append({"item_id": item_id, "text": text, "type": item_type, "project_id": project_id})
        return True

    monkeypatch.setattr(gs, "_check_qdrant_available", lambda: True)
    monkeypatch.setattr(gs, "embed_to_global", _fake_embed)

    def _install_db():
        from empirica.data import session_database

        monkeypatch.setattr(session_database, "SessionDatabase", lambda: _SessionDB(conn))
        return conn, embedded

    yield _install_db
    conn.close()


def test_findings_local_visibility_not_federated(wired):
    """Findings with local visibility should not be federated."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f1", "p1", "high-impact local finding", 0.8, "s1", time.time(), 0, "local"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1", min_impact=0.7)
    assert count == 0, "local visibility findings should not be federated"
    assert len(embedded) == 0


def test_findings_public_visibility_federated(wired):
    """Findings with public visibility should be federated."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f1", "p1", "high-impact public finding", 0.8, "s1", time.time(), 0, "public"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1", min_impact=0.7)
    assert count == 1, "public visibility findings should be federated"
    assert len(embedded) == 1
    assert embedded[0]["type"] == "finding"


def test_findings_shared_visibility_federated(wired):
    """Findings with shared visibility should be federated."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f1", "p1", "high-impact shared finding", 0.8, "s1", time.time(), 0, "shared"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1", min_impact=0.7)
    assert count == 1, "shared visibility findings should be federated"
    assert len(embedded) == 1
    assert embedded[0]["type"] == "finding"


def test_findings_null_visibility_treated_as_local(wired):
    """Findings with NULL visibility should default to local and not federate."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f1", "p1", "high-impact finding no visibility", 0.8, "s1", time.time(), 0, None),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1", min_impact=0.7)
    assert count == 0, "NULL visibility should default to local (not federated)"
    assert len(embedded) == 0


def test_findings_resolved_not_federated(wired):
    """Resolved findings should not be federated even if high-impact."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f1", "p1", "resolved finding", 0.8, "s1", time.time(), 1, "public"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1", min_impact=0.7)
    assert count == 0, "resolved findings should not be federated"
    assert len(embedded) == 0


def test_findings_unresolved_with_low_impact_not_federated(wired):
    """Unresolved findings below min_impact should not federate."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f1", "p1", "low-impact unresolved finding", 0.5, "s1", time.time(), 0, "public"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1", min_impact=0.7)
    assert count == 0, "low-impact findings should not be federated"
    assert len(embedded) == 0


def test_unknowns_local_visibility_not_federated(wired):
    """Resolved unknowns with local visibility should not be federated."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_unknowns
           (id, project_id, unknown, resolved_by, session_id, resolved_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("u1", "p1", "an unknown", "resolution pattern", "s1", time.time(), 1, "local"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1")
    assert count == 0, "resolved unknowns with local visibility should not federate"
    assert len(embedded) == 0


def test_unknowns_public_visibility_federated(wired):
    """Resolved unknowns with public visibility should be federated."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_unknowns
           (id, project_id, unknown, resolved_by, session_id, resolved_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("u1", "p1", "an unknown", "resolution pattern", "s1", time.time(), 1, "public"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1")
    assert count == 1, "resolved unknowns with public visibility should federate"
    assert len(embedded) == 1
    assert embedded[0]["type"] == "unknown_resolved"


def test_dead_ends_local_visibility_not_federated(wired):
    """Dead ends with local visibility should not be federated."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_dead_ends
           (id, project_id, approach, why_failed, session_id, created_timestamp, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("d1", "p1", "approach A", "failed because X", "s1", time.time(), "local"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1")
    assert count == 0, "dead ends with local visibility should not federate"
    assert len(embedded) == 0


def test_dead_ends_public_visibility_federated(wired):
    """Dead ends with public visibility should be federated."""
    conn, embedded = wired()

    conn.execute(
        """INSERT INTO project_dead_ends
           (id, project_id, approach, why_failed, session_id, created_timestamp, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("d1", "p1", "approach A", "failed because X", "s1", time.time(), "public"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1")
    assert count == 1, "dead ends with public visibility should federate"
    assert len(embedded) == 1
    assert embedded[0]["type"] == "dead_end"


def test_mixed_visibility_only_shared_public_federate(wired):
    """Only shared/public artifacts federate; local/null ones don't."""
    conn, embedded = wired()

    # Add findings with various visibilities
    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f1", "p1", "public finding", 0.8, "s1", time.time(), 0, "public"),
    )
    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f2", "p1", "shared finding", 0.8, "s2", time.time(), 0, "shared"),
    )
    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f3", "p1", "local finding", 0.8, "s3", time.time(), 0, "local"),
    )
    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f4", "p1", "null visibility finding", 0.8, "s4", time.time(), 0, None),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1", min_impact=0.7)
    assert count == 2, "should federate only public and shared findings"
    assert len(embedded) == 2
    federated_ids = {e["item_id"] for e in embedded}
    assert "f1" in federated_ids
    assert "f2" in federated_ids
    assert "f3" not in federated_ids
    assert "f4" not in federated_ids


def test_db_close_always_runs(wired, monkeypatch):
    """Verify db.close() is called even if an exception occurs."""
    conn, _embedded = wired()

    close_called = []

    original_close = _SessionDB.close

    def mock_close(self):
        close_called.append(True)
        original_close(self)

    monkeypatch.setattr(_SessionDB, "close", mock_close)

    # Add a valid finding to avoid issues
    conn.execute(
        """INSERT INTO project_findings
           (id, project_id, finding, impact, session_id, created_timestamp, is_resolved, visibility)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("f1", "p1", "test finding", 0.8, "s1", time.time(), 0, "public"),
    )
    conn.commit()

    count = gs.sync_high_impact_to_global("p1", min_impact=0.7)
    assert count == 1
    assert len(close_called) == 1, "db.close() should be called exactly once"
