"""Edge-endpoint existence validation + edges-only repair path (prop_6jrfb5ek).

Two silent-success fixes in log-artifacts' graph writer:

  1. A UUID-shaped edge endpoint that matches NO artifact used to be accepted
     (``_is_uuid`` only checks shape) and stored as a dangling ``artifact_edges``
     row, counted as ``edges_wired``. A dangling row silently corrupts weave-gate
     connectivity + the commit-context walker. Now: endpoints are validated to
     EXIST, dangling edges are skipped with a loud ``edge_warnings`` entry and
     NOT counted.
  2. ``nodes=[]`` was rejected outright ("No nodes provided"), so a mis-wired
     edge could not be repaired via the CLI. Now an edges-only payload is
     accepted (endpoints must be existing UUIDs) — the edge-repair path.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from empirica.cli.command_handlers import graph_commands as gc

_UUID_A = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_UUID_B = "11111111-2222-3333-4444-555555555555"


def _db() -> SimpleNamespace:
    """In-memory db with the artifact tables _store_edge / _artifact_exists touch."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_findings (id TEXT, finding_data TEXT)")
    conn.execute("CREATE TABLE project_unknowns (id TEXT, unknown_data TEXT)")
    conn.execute("CREATE TABLE project_dead_ends (id TEXT, dead_end_data TEXT)")
    conn.execute("CREATE TABLE mistakes_made (id TEXT, mistake_data TEXT)")
    conn.execute("CREATE TABLE assumptions (id TEXT)")
    conn.execute("CREATE TABLE decisions (id TEXT)")
    conn.execute("CREATE TABLE goals (id TEXT, goal_data TEXT)")
    conn.execute(
        "CREATE TABLE artifact_edges (from_id TEXT, to_id TEXT, relation TEXT, metadata TEXT, "
        "PRIMARY KEY (from_id, to_id, relation))"
    )
    conn.execute("INSERT INTO project_findings (id) VALUES ('real1')")
    conn.execute("INSERT INTO decisions (id) VALUES ('real2')")
    return SimpleNamespace(conn=conn)


def _edge_count(db) -> int:
    return db.conn.execute("SELECT COUNT(*) FROM artifact_edges").fetchone()[0]


# ── _artifact_exists ─────────────────────────────────────────────────────


def test_artifact_exists_true_for_real_id():
    db = _db()
    assert gc._artifact_exists(db, "real1") is True
    assert gc._artifact_exists(db, "real2") is True  # decisions table too


def test_artifact_exists_false_for_unknown_id():
    db = _db()
    assert gc._artifact_exists(db, "deadbeef-0000-0000-0000-000000000000") is False
    assert gc._artifact_exists(db, "") is False


# ── _wire_edges: dangling detection ──────────────────────────────────────


def test_wire_skips_and_warns_dangling_endpoint():
    db = _db()
    wired, warnings = gc._wire_edges(
        db,
        [{"from": "real1", "to": "deadbeef-0000-0000-0000-000000000000", "relation": "attached_to"}],
        {},
    )
    assert wired == 0  # not counted
    assert len(warnings) == 1
    assert "matches no existing artifact" in warnings[0]
    assert _edge_count(db) == 0  # not stored — no dangling row


def test_wire_stores_edge_between_existing_artifacts():
    db = _db()
    wired, warnings = gc._wire_edges(db, [{"from": "real1", "to": "real2", "relation": "evidence"}], {})
    assert wired == 1
    assert warnings == []
    assert _edge_count(db) == 1


def test_wire_trusts_freshly_created_refs_via_ref_map():
    db = _db()
    # n1/n2 aren't in the DB, but ref_map says they were just created this batch.
    wired, warnings = gc._wire_edges(
        db,
        [{"from": "n1", "to": "n2", "relation": "evidence"}],
        {"n1": _UUID_A, "n2": _UUID_B},
    )
    assert wired == 1
    assert warnings == []


def test_wire_partial_success_good_edge_lands_bad_edge_warns():
    db = _db()
    wired, warnings = gc._wire_edges(
        db,
        [
            {"from": "real1", "to": "real2", "relation": "evidence"},  # good
            {"from": "real1", "to": "ffffffff-0000-0000-0000-000000000000", "relation": "attached_to"},  # dangling
        ],
        {},
    )
    assert wired == 1  # the good one still lands
    assert len(warnings) == 1
    assert _edge_count(db) == 1


# ── _validate_graph: edges-only repair path ──────────────────────────────


def test_validate_allows_edges_only_with_uuid_endpoints():
    g = {"nodes": [], "edges": [{"from": _UUID_A, "to": _UUID_B, "relation": "evidence"}]}
    assert gc._validate_graph(g) == []  # no "No nodes provided"


def test_validate_rejects_both_empty():
    errors = gc._validate_graph({"nodes": [], "edges": []})
    assert any("No nodes or edges" in e for e in errors)


def test_validate_edges_only_requires_uuid_endpoints():
    # A non-UUID endpoint with no nodes to resolve it against is still an error.
    g = {"nodes": [], "edges": [{"from": "notauuid", "to": _UUID_B, "relation": "evidence"}]}
    errors = gc._validate_graph(g)
    assert any("not found in nodes" in e for e in errors)
