"""B1: filter-based bulk resolve in resolve-artifacts (_resolve_by_filter).

The gardening path — resolve OPEN findings/unknowns by (project_id, older_than,
matching) in one call instead of enumerating ids / hand-writing SQL. Dry-run by
default; apply=True commits. SQLite-only, matching the per-id resolve path.
"""

from __future__ import annotations

import sqlite3
import time
import types

from empirica.cli.command_handlers.graph_commands import _resolve_by_filter


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE project_findings (id TEXT PRIMARY KEY, project_id TEXT, finding TEXT, "
        "created_timestamp REAL, is_resolved INT DEFAULT 0, resolution TEXT, resolved_timestamp REAL)"
    )
    conn.execute(
        "CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, project_id TEXT, unknown TEXT, "
        "created_timestamp REAL, is_resolved INT DEFAULT 0, resolved_by TEXT, resolved_timestamp REAL)"
    )
    return types.SimpleNamespace(conn=conn)


def _add_finding(db, fid, text, project="p1", ts=None, resolved=0):
    db.conn.execute(
        "INSERT INTO project_findings (id, project_id, finding, created_timestamp, is_resolved) VALUES (?,?,?,?,?)",
        (fid, project, text, ts if ts is not None else time.time(), resolved),
    )
    db.conn.commit()


def _open_count(db, table="project_findings"):
    return db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE is_resolved IS NOT 1").fetchone()[0]


# ── dry-run ───────────────────────────────────────────────────────────────────
def test_dry_run_reports_matches_without_mutating():
    db = _db()
    _add_finding(db, "f1", "real finding")
    _add_finding(db, "f2", "test noise")
    res = _resolve_by_filter(db, {"type": "finding", "matching": "test %"}, "garden", apply=False)
    assert res["ok"] and res["dry_run"] is True
    assert res["matched"] == 1
    assert _open_count(db) == 2, "dry-run must not mutate"


def test_apply_resolves_matching_only():
    db = _db()
    _add_finding(db, "f1", "real finding")
    _add_finding(db, "f2", "test noise")
    res = _resolve_by_filter(db, {"type": "finding", "matching": "test %"}, "garden hygiene", apply=True)
    assert res["ok"] and res["dry_run"] is False
    assert res["resolved"] == 1
    row = db.conn.execute("SELECT is_resolved, resolution FROM project_findings WHERE id='f2'").fetchone()
    assert row[0] == 1 and row[1] == "garden hygiene"
    assert db.conn.execute("SELECT is_resolved FROM project_findings WHERE id='f1'").fetchone()[0] == 0


def test_older_than_filter():
    db = _db()
    old = time.mktime(time.strptime("2026-01-01", "%Y-%m-%d"))
    new = time.mktime(time.strptime("2026-06-01", "%Y-%m-%d"))
    _add_finding(db, "f1", "old", ts=old)
    _add_finding(db, "f2", "new", ts=new)
    res = _resolve_by_filter(db, {"type": "finding", "older_than": "2026-05-01"}, "stale", apply=True)
    assert res["resolved"] == 1
    assert db.conn.execute("SELECT is_resolved FROM project_findings WHERE id='f1'").fetchone()[0] == 1
    assert db.conn.execute("SELECT is_resolved FROM project_findings WHERE id='f2'").fetchone()[0] == 0


def test_project_id_filter_scopes():
    db = _db()
    _add_finding(db, "f1", "mine", project="p1")
    _add_finding(db, "f2", "theirs", project="p2")
    res = _resolve_by_filter(db, {"type": "finding", "project_id": "p2"}, "consolidate", apply=True)
    assert res["resolved"] == 1
    assert db.conn.execute("SELECT is_resolved FROM project_findings WHERE id='f1'").fetchone()[0] == 0


def test_already_resolved_excluded():
    db = _db()
    _add_finding(db, "f1", "open")
    _add_finding(db, "f2", "already", resolved=1)
    res = _resolve_by_filter(db, {"type": "finding"}, "x", apply=False)
    assert res["matched"] == 1  # only the open one


def test_unknown_type_uses_resolved_by_column():
    db = _db()
    db.conn.execute(
        "INSERT INTO project_unknowns (id, project_id, unknown, created_timestamp, is_resolved) VALUES "
        "('u1','p1','stale q',?,0)",
        (time.time(),),
    )
    db.conn.commit()
    res = _resolve_by_filter(db, {"type": "unknown", "matching": "stale%"}, "answered", apply=True)
    assert res["resolved"] == 1
    row = db.conn.execute("SELECT is_resolved, resolved_by FROM project_unknowns WHERE id='u1'").fetchone()
    assert row[0] == 1 and row[1] == "answered"


def test_bad_type_errors():
    db = _db()
    res = _resolve_by_filter(db, {"type": "goal"}, "x", apply=True)
    assert res["ok"] is False and "filter.type" in res["error"]


def test_bad_older_than_errors():
    db = _db()
    res = _resolve_by_filter(db, {"type": "finding", "older_than": "not-a-date"}, "x", apply=True)
    assert res["ok"] is False and "older_than" in res["error"]
