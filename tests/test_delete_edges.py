"""Edge deletion in delete-artifacts: specific + dangling prune/repair (prop_6jrfb5ek).

#268 prevents NEW dangling edges and #269 resolves inline endpoints; #270 added
delete + prune. This adds REPAIR-BEFORE-PRUNE (autonomy prop_ovf7iday): a dangling
endpoint that resolves to a real artifact (e.g. a short prefix) is rewired to the
full id rather than deleted — only the truly-unrecoverable is pruned. Safe by
default; ``repair: false`` forces pure prune.

``_process_edge_deletions`` returns ``(removed, repaired, items, errors)``.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from empirica.cli.command_handlers import graph_commands as gc

_FULL = "abcdef12-3456-7890-abcd-ef1234567890"  # a real artifact with a hex id


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


def _edges(db):
    return [tuple(r) for r in db.conn.execute("SELECT from_id, to_id FROM artifact_edges").fetchall()]


def _add_edge(db, frm, to, rel="evidence"):
    db.conn.execute("INSERT INTO artifact_edges (from_id, to_id, relation) VALUES (?, ?, ?)", (frm, to, rel))


# ── specific edge deletion ───────────────────────────────────────────────


def test_specific_edge_delete_dry_run_reports_without_mutating():
    db = _db()
    _add_edge(db, "real1", "real2", "evidence")
    removed, repaired, items, errors = gc._process_edge_deletions(
        db, {"edges": [{"from": "real1", "to": "real2"}]}, dry_run=True
    )
    assert removed == 0 and repaired == 0
    assert items and items[0]["action"] == "would_delete_edge"
    assert _edge_count(db) == 1  # untouched
    assert errors == []


def test_specific_edge_delete_removes():
    db = _db()
    _add_edge(db, "real1", "real2", "evidence")
    removed, _repaired, items, errors = gc._process_edge_deletions(
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
    removed, _repaired, _items, _errors = gc._process_edge_deletions(
        db, {"edges": [{"from": "real1", "to": "real2", "relation": "evidence"}]}, dry_run=False
    )
    assert removed == 1
    assert _edge_count(db) == 1  # grounded_by survives


def test_specific_edge_no_match_reports_error():
    db = _db()
    removed, _repaired, _items, errors = gc._process_edge_deletions(
        db, {"edges": [{"from": "real1", "to": "real2"}]}, dry_run=False
    )
    assert removed == 0
    assert any("no edge matches" in e for e in errors)


def test_edge_spec_missing_endpoint_errors():
    db = _db()
    removed, _repaired, _items, errors = gc._process_edge_deletions(db, {"edges": [{"from": "real1"}]}, dry_run=False)
    assert removed == 0
    assert any("missing" in e for e in errors)


# ── dangling prune (unrecoverable) ───────────────────────────────────────


def test_prune_dangling_removes_unrecoverable_edges():
    db = _db()
    _add_edge(db, "real1", "real2", "evidence")  # both exist — keep
    _add_edge(db, "real1", "ghost-000000000000", "attached_to")  # non-hex-resolvable — prune
    _add_edge(db, "ghost-111111111111", "real2", "sourced_from")  # non-resolvable — prune
    removed, repaired, items, errors = gc._process_edge_deletions(db, {"prune_dangling": True}, dry_run=False)
    assert removed == 2 and repaired == 0
    assert _edge_count(db) == 1  # the real1->real2 edge survives
    assert all(i["action"] == "pruned_dangling" for i in items)
    assert errors == []


def test_prune_dangling_dry_run_reports_without_mutating():
    db = _db()
    _add_edge(db, "real1", "ghost-000000000000", "attached_to")
    removed, repaired, items, _errors = gc._process_edge_deletions(db, {"prune_dangling": True}, dry_run=True)
    assert removed == 0 and repaired == 0
    assert items[0]["action"] == "would_prune_dangling"
    assert "to=ghost-000000000000" in items[0]["missing"]
    assert _edge_count(db) == 1  # untouched


def test_prune_dangling_noop_when_all_edges_valid():
    db = _db()
    _add_edge(db, "real1", "real2", "evidence")
    removed, repaired, items, _errors = gc._process_edge_deletions(db, {"prune_dangling": True}, dry_run=False)
    assert removed == 0 and repaired == 0
    assert items == []
    assert _edge_count(db) == 1


# ── repair-before-prune (recoverable) ────────────────────────────────────


def test_prune_repairs_resolvable_prefix():
    db = _db()
    db.conn.execute("INSERT INTO project_findings (id) VALUES (?)", (_FULL,))
    _add_edge(db, "real1", "abcdef12", "evidence")  # `to` is an 8-char hex prefix of _FULL
    removed, repaired, items, errors = gc._process_edge_deletions(db, {"prune_dangling": True}, dry_run=False)
    assert repaired == 1
    assert removed == 0  # rewired, not deleted
    assert ("real1", _FULL) in _edges(db)  # corrected edge exists
    assert ("real1", "abcdef12") not in _edges(db)  # dangling row gone
    assert items[0]["action"] == "repaired_dangling"
    assert items[0]["rewired_to"]["to"] == _FULL
    assert errors == []


def test_repair_false_forces_pure_prune():
    db = _db()
    db.conn.execute("INSERT INTO project_findings (id) VALUES (?)", (_FULL,))
    _add_edge(db, "real1", "abcdef12", "evidence")  # recoverable, but repair disabled
    removed, repaired, items, _errors = gc._process_edge_deletions(
        db, {"prune_dangling": True, "repair": False}, dry_run=False
    )
    assert repaired == 0
    assert removed == 1  # pure prune deletes it despite being recoverable
    assert items[0]["action"] == "pruned_dangling"


def test_prune_dry_run_reports_would_repair():
    db = _db()
    db.conn.execute("INSERT INTO project_findings (id) VALUES (?)", (_FULL,))
    _add_edge(db, "real1", "abcdef12", "evidence")
    removed, repaired, items, _errors = gc._process_edge_deletions(db, {"prune_dangling": True}, dry_run=True)
    assert removed == 0 and repaired == 0
    assert items[0]["action"] == "would_repair_dangling"
    assert items[0]["rewire_to"]["to"] == _FULL
    assert _edge_count(db) == 1  # untouched in dry-run
