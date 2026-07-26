"""Tests for `sources-reconcile --backfill-citations`.

Promotes legacy `source_refs` COLUMN citations into real `sourced_from` EDGES.
`--source` historically wrote only the column, so those citations were invisible
to the artifact graph — the daemon's `related_from` projection, `sources-map` and
`sanctify`'s zombie check all read edges.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.cli.command_handlers.sources_reconcile_commands import (
    _parse_source_refs,
    _run_citation_backfill,
)

PID = "proj-1"


class _DB:
    """Minimal stand-in for SessionDatabase — the backfill only needs `.conn`."""

    def __init__(self, conn):
        self.conn = conn


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY, project_id TEXT, title TEXT, archived INTEGER DEFAULT 0
        );
        CREATE TABLE project_findings (id TEXT PRIMARY KEY, project_id TEXT, source_refs TEXT);
        CREATE TABLE decisions (id TEXT PRIMARY KEY, project_id TEXT, source_refs TEXT);
        CREATE TABLE artifact_edges (
            from_id TEXT, to_id TEXT, relation TEXT, UNIQUE(from_id, to_id, relation)
        );
    """)
    return _DB(conn)


def _add_source(db, sid, *, archived=0):
    db.conn.execute(
        "INSERT INTO epistemic_sources (id, project_id, title, archived) VALUES (?, ?, ?, ?)",
        (sid, PID, f"src {sid}", archived),
    )


def _add_finding(db, fid, refs):
    db.conn.execute(
        "INSERT INTO project_findings (id, project_id, source_refs) VALUES (?, ?, ?)",
        (fid, PID, refs),
    )


def _edges(db):
    return set(db.conn.execute("SELECT from_id, to_id FROM artifact_edges WHERE relation='sourced_from'").fetchall())


# ── ref parsing ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["a", "b"]', ["a", "b"]),  # JSON list — the modern shape
        ("a,b", ["a", "b"]),  # comma string — the legacy shape
        (' ["a"] ', ["a"]),
        ("", []),
        ("[]", []),
        ("null", []),
        (None, []),
        ('"solo"', ["solo"]),  # JSON-encoded bare string
    ],
)
def test_parse_source_refs_accepts_both_historical_shapes(raw, expected):
    assert _parse_source_refs(raw) == expected


# ── backfill ──────────────────────────────────────────────────────────


def test_dry_run_reports_but_writes_nothing(db):
    _add_source(db, "s1")
    _add_finding(db, "f1", '["s1"]')

    result = _run_citation_backfill(db, PID, apply=False)

    assert result["ok"] and result["dry_run"]
    assert result["edges_to_create"] == 1
    assert result["edges_created"] == 0
    assert _edges(db) == set(), "dry-run must not write"


def test_apply_creates_edges_from_legacy_column(db):
    _add_source(db, "s1")
    _add_source(db, "s2")
    _add_finding(db, "f1", '["s1", "s2"]')

    result = _run_citation_backfill(db, PID, apply=True)

    assert result["edges_created"] == 2
    assert _edges(db) == {("f1", "s1"), ("f1", "s2")}


def test_is_idempotent(db):
    _add_source(db, "s1")
    _add_finding(db, "f1", '["s1"]')

    first = _run_citation_backfill(db, PID, apply=True)
    second = _run_citation_backfill(db, PID, apply=True)

    assert first["edges_created"] == 1
    assert second["edges_created"] == 0, "re-run must not duplicate"
    assert second["edges_already_present"] == 1
    assert len(_edges(db)) == 1


def test_never_fabricates_an_edge_to_a_missing_source(db):
    """A ref pointing at a source that doesn't exist is REPORTED, never written —
    planting dangling edges would corrupt the graph the backfill exists to repair."""
    _add_source(db, "s1")
    _add_finding(db, "f1", '["s1", "ghost-id"]')

    result = _run_citation_backfill(db, PID, apply=True)

    assert _edges(db) == {("f1", "s1")}
    assert [d["missing_source_id"] for d in result["dangling_refs"]] == ["ghost-id"]


def test_scans_every_citation_carrying_table(db):
    _add_source(db, "s1")
    _add_finding(db, "f1", '["s1"]')
    db.conn.execute(
        "INSERT INTO decisions (id, project_id, source_refs) VALUES (?, ?, ?)",
        ("d1", PID, '["s1"]'),
    )

    result = _run_citation_backfill(db, PID, apply=True)

    assert result["artifacts_with_source_refs"] == 2
    assert _edges(db) == {("f1", "s1"), ("d1", "s1")}


def test_missing_table_or_column_is_tolerated(db):
    """Schema drift across practices is normal — a project DB lacking one of the
    citation tables must not fail the whole backfill."""
    db.conn.execute("DROP TABLE decisions")
    _add_source(db, "s1")
    _add_finding(db, "f1", '["s1"]')

    result = _run_citation_backfill(db, PID, apply=True)

    assert result["ok"]
    assert result["edges_created"] == 1


# ── citation health ───────────────────────────────────────────────────


def test_health_scores_active_sources_only(db):
    """An archived source is retired — nothing SHOULD cite it, so counting it as
    uncited would inflate the gap gardening asks the practice to close."""
    _add_source(db, "live1")
    _add_source(db, "live2")
    _add_source(db, "retired", archived=1)

    health = _run_citation_backfill(db, PID, apply=False)["citation_health"]

    assert health["sources_active"] == 2
    assert health["sources_archived"] == 1
    assert health["sources_uncited"] == 2


def test_health_counts_a_source_as_cited_once_backfilled(db):
    _add_source(db, "s1")
    _add_source(db, "s2")
    _add_finding(db, "f1", '["s1"]')

    before = _run_citation_backfill(db, PID, apply=False)["citation_health"]
    after = _run_citation_backfill(db, PID, apply=True)["citation_health"]

    assert before["sources_cited"] == 0
    assert before["sources_uncited"] == 2
    assert after["sources_cited"] == 1
    assert after["sources_uncited"] == 1
