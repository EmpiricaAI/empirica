"""resolved_timestamp is epoch seconds, never TEXT (goal 1cd283ff, unknown 962847d6's sibling: the 39 text rows).

SQLite orders every TEXT value above every REAL. `resolve-artifacts` (unknown branch) wrote `datetime('now')`, a TEXT string, into a column
every other writer fills with epoch seconds. The consequence was in the post-test collector: `resolved_timestamp >= scope_start` is true for
a text row in EVERY window, so each of the 39 text-resolved unknowns on this practice counted as resolved in every transaction, and the
triage "unknowns_resolved" evidence (do/know) saturated at 1.0 whatever the work did (measured: a window starting now still counted 39).

The writer now stores `time.time()`; migration 076 turns the existing UTC strings into epoch seconds.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import time

import pytest


@pytest.fixture
def wired(tmp_path, monkeypatch):
    import empirica.data.session_database as _sdb

    db_file = str(tmp_path / "t.db")
    real = _sdb.SessionDatabase
    monkeypatch.setattr(_sdb, "SessionDatabase", lambda *a, **k: real(db_path=db_file))
    return db_file, real


def _resolve(payload: dict) -> None:
    from empirica.cli.command_handlers.graph_commands import handle_resolve_artifacts_command

    class _Args:
        input = "-"
        output = "json"
        verbose = False

    stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
    try:
        handle_resolve_artifacts_command(_Args())
    finally:
        sys.stdin = stdin


def test_batch_resolving_an_unknown_stamps_epoch_seconds_not_a_string(wired):
    db_file, real = wired
    uid = "b1111111-0000-4000-8000-000000000001"
    db = real(db_path=db_file)
    db.conn.execute(
        "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp, unknown_data) "
        "VALUES (?, 'p', 's', 'q', 0.0, '{}')",
        (uid,),
    )
    db.conn.commit()
    db.close()

    before = time.time()
    _resolve({"resolutions": [{"type": "unknown", "id": uid, "resolution": "answered"}]})
    after = time.time()

    db = real(db_path=db_file)
    kind, stamp = db.conn.execute(
        "SELECT typeof(resolved_timestamp), resolved_timestamp FROM project_unknowns WHERE id = ?", (uid,)
    ).fetchone()
    db.close()
    assert kind == "real", f"stored as {kind}: {stamp!r}"
    assert before - 1 <= stamp <= after + 1


def run_076(cursor):
    from empirica.data.migrations.migrations import migration_076_normalize_text_resolved_timestamp

    migration_076_normalize_text_resolved_timestamp(cursor)


def _legacy_db():
    conn = sqlite3.connect(":memory:")
    for table in ("project_unknowns", "project_findings", "assumptions"):
        conn.execute(f"CREATE TABLE {table} (id TEXT, is_resolved INTEGER, resolved_timestamp)")
    return conn


def test_migration_turns_the_utc_strings_into_epoch_seconds():
    conn = _legacy_db()
    conn.execute("INSERT INTO project_unknowns VALUES ('u', 1, '2026-07-28 11:13:53')")
    run_076(conn.cursor())
    kind, stamp = conn.execute("SELECT typeof(resolved_timestamp), resolved_timestamp FROM project_unknowns").fetchone()
    assert kind == "real"
    assert stamp == pytest.approx(1785237233.0), "2026-07-28 11:13:53 UTC"


def test_after_the_migration_an_old_text_row_no_longer_counts_in_a_window_that_starts_later():
    """The actual harm: the collector's `resolved_timestamp >= scope_start`."""
    conn = _legacy_db()
    conn.execute("INSERT INTO project_unknowns VALUES ('old_text', 1, '2026-07-28 11:13:53')")
    conn.execute("INSERT INTO project_unknowns VALUES ('new_real', 1, ?)", (time.time(),))
    query = "SELECT id FROM project_unknowns WHERE is_resolved = 1 AND resolved_timestamp >= ? ORDER BY id"
    scope_start = time.time() - 60
    assert [r[0] for r in conn.execute(query, (scope_start,))] == ["new_real", "old_text"], "the defect, before"
    run_076(conn.cursor())
    assert [r[0] for r in conn.execute(query, (scope_start,))] == ["new_real"]


def test_the_migration_covers_findings_and_assumptions_too():
    conn = _legacy_db()
    conn.execute("INSERT INTO project_findings VALUES ('f', 1, '2026-07-28 11:13:53')")
    conn.execute("INSERT INTO assumptions VALUES ('a', 1, '2026-07-28 11:13:53')")
    run_076(conn.cursor())
    for table in ("project_findings", "assumptions"):
        assert conn.execute(f"SELECT typeof(resolved_timestamp) FROM {table}").fetchone()[0] == "real", table


def test_the_migration_leaves_real_rows_nulls_and_unparseable_text_alone_and_reruns_as_a_noop():
    conn = _legacy_db()
    conn.execute("INSERT INTO project_unknowns VALUES ('real', 1, 1790000000.5)")
    conn.execute("INSERT INTO project_unknowns VALUES ('null', 0, NULL)")
    conn.execute("INSERT INTO project_unknowns VALUES ('junk', 1, 'sometime last week')")
    run_076(conn.cursor())
    run_076(conn.cursor())
    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT id, typeof(resolved_timestamp), resolved_timestamp FROM project_unknowns")
    }
    assert rows["real"] == ("real", 1790000000.5)
    assert rows["null"] == ("null", None)
    assert rows["junk"] == ("text", "sometime last week"), (
        "what cannot be read stays as it was rather than becoming a wrong time"
    )


def test_the_migration_skips_a_table_that_is_absent():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_unknowns (id TEXT, resolved_timestamp)")
    run_076(conn.cursor())  # no project_findings / assumptions: must not raise
