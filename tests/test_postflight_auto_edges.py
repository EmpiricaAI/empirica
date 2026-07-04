"""POSTFLIGHT auto-edges — persist structural goal-edges to artifact_edges.

Gated Artifact-Graph map, work-stream 3 (goal 43471346). The cortex-sync path
computes each artifact's `attached_to` (artifact→goal) edge but only ships it to
cortex; `_write_auto_structural_edges` persists those edges to the local
`artifact_edges` table so the graph is connected with zero AI effort. Idempotent
via the PK, best-effort on a pre-041 DB.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from empirica.cli.command_handlers import _workflow_postflight as wp


def _mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE artifact_edges (from_id TEXT, to_id TEXT, relation TEXT, PRIMARY KEY (from_id, to_id, relation))"
    )
    return SimpleNamespace(conn=conn), conn


def test_persists_goal_edges(monkeypatch):
    sdb, conn = _mem_db()
    monkeypatch.setattr(wp, "_get_db_for_session", lambda _sid: sdb)
    monkeypatch.setattr(
        wp,
        "_cortex_graph_artifact_nodes",
        lambda _s, _t: (
            [],
            set(),
            [
                {"from": "a1", "to": "g1", "relation": "attached_to"},
                {"from": "a2", "to": "g1", "relation": "attached_to"},
            ],
        ),
    )
    ensured = wp._write_auto_structural_edges("sess", "tx1")
    assert ensured == 2
    rows = {
        (r["from_id"], r["to_id"], r["relation"])
        for r in conn.execute("SELECT from_id, to_id, relation FROM artifact_edges").fetchall()
    }
    assert ("a1", "g1", "attached_to") in rows
    assert ("a2", "g1", "attached_to") in rows


def test_idempotent(monkeypatch):
    sdb, conn = _mem_db()
    monkeypatch.setattr(wp, "_get_db_for_session", lambda _sid: sdb)
    monkeypatch.setattr(
        wp,
        "_cortex_graph_artifact_nodes",
        lambda _s, _t: ([], set(), [{"from": "a1", "to": "g1", "relation": "attached_to"}]),
    )
    wp._write_auto_structural_edges("sess", "tx1")
    wp._write_auto_structural_edges("sess", "tx1")  # second run must not duplicate/raise
    assert conn.execute("SELECT COUNT(*) FROM artifact_edges").fetchone()[0] == 1


def test_no_transaction_id_is_noop():
    assert wp._write_auto_structural_edges("sess", "") == 0


def test_no_goal_edges_is_noop(monkeypatch):
    sdb, _conn = _mem_db()
    monkeypatch.setattr(wp, "_get_db_for_session", lambda _sid: sdb)
    monkeypatch.setattr(wp, "_cortex_graph_artifact_nodes", lambda _s, _t: ([], set(), []))
    assert wp._write_auto_structural_edges("sess", "tx1") == 0
