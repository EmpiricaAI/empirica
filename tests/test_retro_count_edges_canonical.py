"""_retro_count_edges reads the canonical artifact_edges table, not legacy JSON.

Regression guard for the chronic false "0 edges": the counter used to read the
inline `<type>_data.edges` JSON (which neither log-artifacts nor the auto-edge
writer populate), so real edges in the canonical `artifact_edges` table (mig 041)
were invisible. It now counts artifacts that appear as an edge `from_id`.
"""

from __future__ import annotations

import sqlite3

from empirica.cli.command_handlers._workflow_shared import _retro_count_edges


def _db(with_edges_table: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_findings (id TEXT, session_id TEXT, transaction_id TEXT)")
    if with_edges_table:
        conn.execute(
            "CREATE TABLE artifact_edges (from_id TEXT, to_id TEXT, relation TEXT, "
            "PRIMARY KEY (from_id, to_id, relation))"
        )
    return conn


def test_counts_artifacts_with_a_canonical_edge():
    conn = _db()
    conn.execute("INSERT INTO project_findings VALUES ('a1', 's1', 'tx1')")  # has an edge
    conn.execute("INSERT INTO project_findings VALUES ('a2', 's1', 'tx1')")  # no edge
    conn.execute("INSERT INTO artifact_edges VALUES ('a1', 'g1', 'attached_to')")
    assert _retro_count_edges(conn.cursor(), "s1", "tx1") == 1


def test_scoped_to_transaction():
    conn = _db()
    conn.execute("INSERT INTO project_findings VALUES ('a1', 's1', 'tx1')")
    conn.execute("INSERT INTO project_findings VALUES ('a3', 's1', 'tx2')")
    conn.execute("INSERT INTO artifact_edges VALUES ('a1', 'g1', 'attached_to')")
    conn.execute("INSERT INTO artifact_edges VALUES ('a3', 'g1', 'attached_to')")
    assert _retro_count_edges(conn.cursor(), "s1", "tx1") == 1  # a3 is tx2, excluded


def test_pre_041_db_without_artifact_edges_is_zero_not_error():
    conn = _db(with_edges_table=False)
    conn.execute("INSERT INTO project_findings VALUES ('a1', 's1', 'tx1')")
    assert _retro_count_edges(conn.cursor(), "s1", "tx1") == 0
