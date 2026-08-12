"""GH #408: the weave gate must count ALL artifacts in the transaction and
resolve edges to same-batch (and prior) nodes.

The reporter (1.13.7) saw `total_artifacts: 1` for a transaction that created
two nodes + an edge, and `connected: 0` for a node whose edge was wired in the
same call. This exercises the two counters the gate is built from against a
transaction shaped exactly like the report — two artifacts, one edge between
them — and asserts the count is 2 and both are connected.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.cli.command_handlers._workflow_shared import (
    _retro_count_artifacts,
    _retro_count_edges,
)


def _db_with_two_findings_and_an_edge(same_tx=True):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE project_findings ("
        " id TEXT, session_id TEXT, transaction_id TEXT, finding TEXT, created_timestamp REAL)"
    )
    # The five other tables the counters scan — empty, but must exist.
    for t, cols in (
        ("project_unknowns", "unknown"),
        ("project_dead_ends", "approach"),
        ("mistakes_made", "mistake"),
        ("assumptions", "assumption"),
        ("decisions", "choice"),
    ):
        conn.execute(f"CREATE TABLE {t} (id TEXT, session_id TEXT, transaction_id TEXT, {cols} TEXT)")
    conn.execute("CREATE TABLE artifact_edges (from_id TEXT, to_id TEXT, relation TEXT)")

    tx_a = "TX1"
    tx_b = "TX1" if same_tx else "TX0"  # prior-transaction endpoint
    conn.execute("INSERT INTO project_findings VALUES ('f1','S1',?,'first',1.0)", (tx_a,))
    conn.execute("INSERT INTO project_findings VALUES ('f2','S1',?,'second',1.0)", (tx_b,))
    conn.execute("INSERT INTO artifact_edges VALUES ('f1','f2','grounded_by')")
    conn.commit()
    return types.SimpleNamespace(conn=conn)


def test_both_same_batch_nodes_are_counted_and_connected():
    db = _db_with_two_findings_and_an_edge(same_tx=True)
    cur = db.conn.cursor()
    counts = _retro_count_artifacts(cur, "S1", "TX1")
    assert sum(counts.values()) == 2, "both nodes created in the transaction must be counted"

    edges = _retro_count_edges(cur, "S1", "TX1")
    assert edges == 2, "an edge between two same-batch nodes connects BOTH (degree-based)"


def test_edge_to_a_prior_transaction_node_still_connects_this_one():
    """The docs explicitly encourage edges to PRIOR artifacts. A node in THIS
    transaction with an edge to a node from an EARLIER one must read as
    connected — its endpoint being out-of-transaction must not drop it."""
    db = _db_with_two_findings_and_an_edge(same_tx=False)
    cur = db.conn.cursor()
    # This transaction created exactly one node (f1); f2 is prior (TX0).
    assert sum(_retro_count_artifacts(cur, "S1", "TX1").values()) == 1
    # f1 has an incident edge (to the prior f2) → connected.
    assert _retro_count_edges(cur, "S1", "TX1") == 1, "an edge to a prior-tx node still connects this one"
