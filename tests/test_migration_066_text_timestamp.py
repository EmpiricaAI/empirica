"""Migration 066 — normalize TEXT created_timestamp to REAL.

A TEXT value in an otherwise-numeric column sorts ABOVE every number in SQLite,
so a legacy row stored as text is returned as the NEWEST by every
`ORDER BY created_timestamp DESC` — including the breadcrumbs queries that build
injected session context (measured 2026-08-12: 13 such rows). These pin the
behaviour: the cast happens, it sorts right afterwards, REAL rows and non-epoch
text are left alone, and re-running is a no-op.
"""

import sqlite3

import pytest

from empirica.data.migrations.migrations import migration_066_normalize_text_created_timestamp as run


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_findings (id TEXT, created_timestamp)")
    return conn


def test_text_epoch_becomes_real():
    conn = _db()
    conn.execute("INSERT INTO project_findings VALUES ('legacy', '1699564800.0')")  # TEXT epoch
    run(conn.cursor())
    typ, val = conn.execute(
        "SELECT typeof(created_timestamp), created_timestamp FROM project_findings WHERE id='legacy'"
    ).fetchone()
    assert typ == "real"
    assert val == pytest.approx(1699564800.0)


def test_the_stale_row_no_longer_sorts_newest():
    """The actual harm: a TEXT epoch sorted above a newer REAL row. After the
    cast, DESC order is correct."""
    conn = _db()
    conn.execute("INSERT INTO project_findings VALUES ('old_text', '1699564800.0')")  # older, TEXT
    conn.execute("INSERT INTO project_findings VALUES (?, ?)", ("new_real", 1799564800.0))  # newer, REAL
    # Before: text sorts above the number, so old_text comes first — the bug.
    before = [r[0] for r in conn.execute("SELECT id FROM project_findings ORDER BY created_timestamp DESC").fetchall()]
    assert before[0] == "old_text"  # the defect

    run(conn.cursor())

    after = [r[0] for r in conn.execute("SELECT id FROM project_findings ORDER BY created_timestamp DESC").fetchall()]
    assert after == ["new_real", "old_text"]  # newest actually first


def test_real_rows_untouched():
    conn = _db()
    conn.execute("INSERT INTO project_findings VALUES (?, ?)", ("r", 1699564800.5))
    run(conn.cursor())
    typ, val = conn.execute(
        "SELECT typeof(created_timestamp), created_timestamp FROM project_findings WHERE id='r'"
    ).fetchone()
    assert typ == "real" and val == pytest.approx(1699564800.5)


def test_non_epoch_text_is_left_alone_not_mangled():
    """An ISO date is text too, but CAST AS REAL would turn '2025-01-01' into
    2025.0 — a wrong epoch. The 10-digit epoch GLOB must spare it."""
    conn = _db()
    conn.execute("INSERT INTO project_findings VALUES ('iso', '2025-01-01T00:00:00')")
    run(conn.cursor())
    typ, val = conn.execute(
        "SELECT typeof(created_timestamp), created_timestamp FROM project_findings WHERE id='iso'"
    ).fetchone()
    assert typ == "text" and val == "2025-01-01T00:00:00"


def test_idempotent():
    conn = _db()
    conn.execute("INSERT INTO project_findings VALUES ('legacy', '1699564800.0')")
    run(conn.cursor())
    run(conn.cursor())  # second run must be a clean no-op
    typ = conn.execute("SELECT typeof(created_timestamp) FROM project_findings WHERE id='legacy'").fetchone()[0]
    assert typ == "real"


def test_missing_table_is_skipped_defensively():
    """A partial/older DB may lack some of the snapshot tables — skip, not crash."""
    conn = sqlite3.connect(":memory:")  # no tables at all
    run(conn.cursor())  # must not raise
