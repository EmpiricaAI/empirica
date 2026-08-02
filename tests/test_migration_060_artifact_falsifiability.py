"""Migration 060 — permanent-constraint artifacts become falsifiable.

`project_dead_ends` (750 rows) and `mistakes_made` (133) had NO lifecycle columns.
They are permanent NEGATIVE guidance retrieved into later sessions to steer
practitioners away, and nothing ever retries them — so a mistaken one was invisible
by construction and silently removed a viable approach from the option space.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.session_database import SessionDatabase


@pytest.fixture
def db(tmp_path):
    # tmp_path, not mkdtemp: the old fixture closed the DB but never removed the
    # directory, so every run stranded one more. 820 of them on this box, and a
    # peer's /tmp hit 100% and failed tests in code nobody had touched.
    d = SessionDatabase(db_path=str(tmp_path / "t.db"))
    yield d
    d.close()


def _cols(db, table):
    return {r[1] for r in db.conn.execute(f"PRAGMA table_info({table})").fetchall()}


@pytest.mark.parametrize("table", ["project_dead_ends", "mistakes_made"])
def test_permanent_constraint_artifacts_can_be_invalidated(db, table):
    """Both types share ONE invalidation shape — 'no longer actionable' and 'was
    wrong' are practically the same state (David 2026-07-27), so two states nobody
    could tell apart would be worse than one."""
    assert {
        "is_invalidated",
        "invalidated_at",
        "invalidated_by",
        "invalidation_reason",
        "last_revisited_at",
    } <= _cols(db, table)


def test_dead_ends_carry_a_domain_for_scoped_staleness(db):
    """Age alone is weak evidence: a dead-end about a fast-moving dependency rots far
    faster than one about arithmetic."""
    assert "domain" in _cols(db, "project_dead_ends")


def test_blindspots_record_their_premises(db):
    """A blindspot is INFERRED, so it inherits the fate of its inputs. Storing them is
    what lets propagation flag stale_inputs and re-derive."""
    assert "derived_from" in _cols(db, "blindspot_events")


def test_never_assessed_is_preserved_not_backfilled(db):
    """Absence of evidence is a first-class state. A pre-existing row must read as
    'never assessed', never as a verdict nobody made."""
    db.conn.execute(
        "INSERT INTO project_dead_ends "
        "(id, project_id, session_id, approach, why_failed, created_timestamp, dead_end_data) "
        "VALUES (?,?,?,?,?,?,?)",
        ("de1", "p1", "s1", "tried X", "because Y", 0.0, "{}"),
    )
    db.conn.commit()
    row = db.conn.execute(
        "SELECT COALESCE(is_invalidated,0), invalidated_at, last_revisited_at FROM project_dead_ends WHERE id='de1'"
    ).fetchone()
    assert row[0] == 0, "not invalidated"
    assert row[1] is None and row[2] is None, "never assessed / never revisited — distinct from 'confirmed good'"


def test_migration_is_idempotent(db):
    """Re-running must not fail — every practice DB re-runs migrations on open."""
    from empirica.data.migrations import ALL_MIGRATIONS

    mig = next(m for m in ALL_MIGRATIONS if m[0] == "060_artifact_falsifiability")
    cur = db.conn.cursor()
    mig[2](cur)  # second application
    mig[2](cur)  # third
    db.conn.commit()
    assert "is_invalidated" in _cols(db, "project_dead_ends")


def test_blindspot_events_is_allowlisted_for_migrations():
    """The allowlist guards SQL identifier injection; a table absent from it cannot be
    altered at all. blindspot_events was missing, which is what caught this."""
    from empirica.data.migrations.migration_runner import column_exists

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE blindspot_events (id TEXT)")
    assert column_exists(conn.cursor(), "blindspot_events", "id") is True
    conn.close()
