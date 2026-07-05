"""Edge deletion in delete-artifacts: specific edges + dangling prune (prop_6jrfb5ek).

#268 prevents NEW dangling edges and #269 resolves inline endpoints, but neither
removes an EXISTING edge. ``_process_edge_deletions`` adds that:

  - ``edges`` — delete a specific edge (optionally relation-filtered);
  - ``prune_dangling`` — sweep + delete every edge whose endpoint matches no
    existing artifact (reuses ``_artifact_exists``).

Both honor ``dry_run`` (report without mutating). Completes edge CRUD.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from empirica.cli.command_handlers import graph_commands as gc


def _db() -> SimpleNamespace:
    conn = sqlite3.connect(":memory:")
    for t in ("project_findings", "project_unknowns", "project_dead_ends", "mistakes_made", "assumptions", "decisions"):
        conn.execute(f"CREATE TABLE {t} (id TEXT)")
    conn.execute("CREATE TABLE goals (id TEXT)")
    conn.execute(
        "CREATE TABLE artifact_edges (from_id TEXT, to_id TEXT, relation TEXT, metadata TEXT, "
        "PRIMARY KEY (from_id, to_id, relation))"
    )
    conn.execute("INSERT INTO project_findings (id) VALUES ('real1')")
    conn.execute("INSERT INTO decisions (id) VALUES ('real2')")
    return SimpleNamespace(conn=conn)


def _edge_count(db) -> int:
    return db.conn.execute("SELECT COUNT(*) FROM artifact_edges").fetchone()[0]


def _add_edge(db, frm, to, rel="evidence"):
    db.conn.execute("INSERT INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)", (frm, to, rel))


# ── specific edge deletion ───────────────────────────────────────────────


def test_specific_edge_delete_dry_run_reports_without_mutating():
    db = _db()
    _add_edge(db, "real1", "real2", "evidence")
    removed, items, errors = gc._process_edge_deletions(db, {"edges": [{"from": "real1", "to": "real2"}]}, dry_run=True)
    assert removed == 0
    assert items and items[0]["action"] == "would_delete_edge"
    assert _edge_count(db) == 1  # untouched
    assert errors == []


def test_specific_edge_delete_removes():
    db = _db()
    _add_edge(db, "real1", "real2", "evidence")
    removed, items, errors = gc._process_edge_deletions(
        db, {"edges": [{"from": "real1", "to": "real2"}]}, dry_run=False
    )
    assert removed == 1
    assert items[0]["action"] == "deleted_edge"
    assert _edge_count(db) == 0
    assert errors == []


def test_specific_edge_delete_relation_filtered():
    db = _db()
    _add_edge(db, "real1", "real2", "evidence")
    _add_edge(db, "real1", "real2", "grounded_by")
    # Only the evidence edge should go.
    removed, _, _ = gc._process_edge_deletions(
        db, {"edges": [{"from": "real1", "to": "real2", "relation": "evidence"}]}, dry_run=False
    )
    assert removed == 1
    assert _edge_count(db) == 1  # grounded_by survives


def test_specific_edge_no_match_reports_error():
    db = _db()
    removed, _items, errors = gc._process_edge_deletions(
        db, {"edges": [{"from": "real1", "to": "real2"}]}, dry_run=False
    )
    assert removed == 0
    assert any("no edge matches" in e for e in errors)


def test_edge_spec_missing_endpoint_errors():
    db = _db()
    removed, _, errors = gc._process_edge_deletions(db, {"edges": [{"from": "real1"}]}, dry_run=False)
    assert removed == 0
    assert any("missing" in e for e in errors)


# ── dangling prune ───────────────────────────────────────────────────────


def test_prune_dangling_removes_only_edges_with_missing_endpoints():
    db = _db()
    _add_edge(db, "real1", "real2", "evidence")  # both exist — keep
    _add_edge(db, "real1", "ghost-000000000000", "attached_to")  # to missing — prune
    _add_edge(db, "ghost-111111111111", "real2", "sourced_from")  # from missing — prune
    removed, items, errors = gc._process_edge_deletions(db, {"prune_dangling": True}, dry_run=False)
    assert removed == 2
    assert _edge_count(db) == 1  # the real1->real2 edge survives
    assert all(i["action"] == "pruned_dangling" for i in items)
    assert errors == []


def test_prune_dangling_dry_run_reports_without_mutating():
    db = _db()
    _add_edge(db, "real1", "ghost-000000000000", "attached_to")
    removed, items, _ = gc._process_edge_deletions(db, {"prune_dangling": True}, dry_run=True)
    assert removed == 0
    assert items[0]["action"] == "would_prune_dangling"
    assert "to=ghost-000000000000" in items[0]["missing"]
    assert _edge_count(db) == 1  # untouched


def test_prune_dangling_noop_when_all_edges_valid():
    db = _db()
    _add_edge(db, "real1", "real2", "evidence")
    removed, items, _ = gc._process_edge_deletions(db, {"prune_dangling": True}, dry_run=False)
    assert removed == 0
    assert items == []
    assert _edge_count(db) == 1
