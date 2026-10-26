"""A rebuild from git notes keeps WHEN an artifact was resolved (pre-publish sweep, E2).

The resolve verbs (CLI and REST, single and bulk) stamp the note with `resolved_timestamp` (an epoch float). The rebuild and `profile_import` read
`resolved_at` (an ISO string) and, for a note without it, fell back to the REBUILD'S clock. So every resolved artifact came back resolved "just now":
the same saturation migration 076 repaired, where a stale resolution satisfies `resolved_timestamp >= scope_start` in every window and inflates the
resolution counts the calibration reads. Both readers now accept either key, and a numeric stamp.
"""

from __future__ import annotations

import sqlite3
import time
import types

import pytest

from empirica.cli.command_handlers.sync_commands import _rebuild_apply_resolution
from empirica.core.canonical.empirica_git.profile_import import ProfileImporter

THEN = 1_700_000_000.0  # well before "now", so a fallback to the rebuild clock cannot pass for it


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE project_findings (id TEXT, is_resolved INT, resolution TEXT, superseded_by TEXT, resolution_kind TEXT, resolved_timestamp REAL);
        CREATE TABLE project_unknowns (id TEXT, is_resolved INT, resolved_by TEXT, resolved_timestamp REAL);
        INSERT INTO project_findings (id, is_resolved) VALUES ('f1', 0);
        INSERT INTO project_unknowns (id, is_resolved) VALUES ('u1', 0);
        """
    )
    return types.SimpleNamespace(adapter=types.SimpleNamespace(conn=conn))


def _ts(db, table):
    return db.adapter.conn.execute(f"SELECT resolved_timestamp FROM {table}").fetchone()[0]


def test_an_unknown_note_stamped_with_resolved_timestamp_keeps_its_time(db):
    """The exact shape artifact_log_commands / graph_commands / the REST route write."""
    _rebuild_apply_resolution(db, "unknowns", "u1", {"resolved": True, "resolved_by": "x", "resolved_timestamp": THEN})
    assert _ts(db, "project_unknowns") == THEN


def test_a_finding_note_stamped_with_resolved_timestamp_keeps_its_time(db):
    item = {"is_resolved": True, "resolution": "wrong", "resolution_kind": "retracted", "resolved_timestamp": THEN}
    _rebuild_apply_resolution(db, "findings", "f1", item)
    assert _ts(db, "project_findings") == THEN


def test_resolved_at_still_wins_when_both_are_present(db):
    _rebuild_apply_resolution(
        db, "unknowns", "u1", {"resolved": True, "resolved_at": "2023-11-14T22:13:20+00:00", "resolved_timestamp": 5.0}
    )
    assert _ts(db, "project_unknowns") == pytest.approx(THEN)


def test_a_note_with_no_resolution_time_at_all_still_falls_back_to_now(db):
    """Control: the old behaviour is kept for a note that carries nothing, and it is the rebuild clock."""
    before = time.time()
    _rebuild_apply_resolution(db, "unknowns", "u1", {"resolved": True, "resolved_by": "x"})
    assert before - 1 <= _ts(db, "project_unknowns") <= time.time() + 1


def test_profile_import_parses_the_numeric_stamp_the_writers_emit():
    importer = ProfileImporter.__new__(ProfileImporter)
    assert importer._parse_timestamp(THEN) == THEN
    assert importer._parse_timestamp("2023-11-14T22:13:20+00:00") == pytest.approx(THEN)
    assert importer._parse_timestamp(None) is None and importer._parse_timestamp("not a time") is None
    assert importer._parse_timestamp(True) is None, "a bool is not a time"


# ── the importer's own call sites, and invalidation (second-pass review) ─────────────────────────────────────────


@pytest.fixture
def importer_db(monkeypatch):
    """A minimal database and an importer whose git-note discovery returns one canned note per call."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE project_findings (id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, goal_id TEXT, subtask_id TEXT, finding TEXT,
            created_timestamp REAL, finding_data TEXT, subject TEXT, impact REAL, transaction_id TEXT, is_resolved INT, resolution TEXT,
            resolved_timestamp REAL, superseded_by TEXT, resolution_kind TEXT);
        CREATE TABLE project_unknowns (id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, goal_id TEXT, subtask_id TEXT, unknown TEXT,
            is_resolved INT, resolved_by TEXT, created_timestamp REAL, resolved_timestamp REAL, unknown_data TEXT, subject TEXT, impact REAL,
            transaction_id TEXT);
        CREATE TABLE project_dead_ends (id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, goal_id TEXT, subtask_id TEXT, approach TEXT,
            why_failed TEXT, created_timestamp REAL, dead_end_data TEXT, subject TEXT, transaction_id TEXT, is_invalidated INT DEFAULT 0,
            invalidation_reason TEXT, invalidated_at REAL, invalidated_by TEXT);
        CREATE TABLE mistakes_made (id TEXT PRIMARY KEY, session_id TEXT, goal_id TEXT, project_id TEXT, mistake TEXT, why_wrong TEXT,
            cost_estimate TEXT, root_cause_vector TEXT, prevention TEXT, created_timestamp REAL, mistake_data TEXT, transaction_id TEXT,
            is_invalidated INT DEFAULT 0, invalidation_reason TEXT, invalidated_at REAL, invalidated_by TEXT);
        """
    )
    db = types.SimpleNamespace(conn=conn)

    def load(kind: str, note: dict) -> ProfileImporter:
        imp = ProfileImporter(workspace_root="/nonexistent")
        monkeypatch.setattr(imp, "_discover_refs", lambda _prefix: ["n1"])
        monkeypatch.setattr(imp, "_load_note", lambda _ref: note)
        return imp

    return db, load


def _one(db, sql):
    return db.conn.execute(sql).fetchone()


def test_import_unknowns_keeps_the_stamped_resolution_time(importer_db):
    db, load = importer_db
    note = {"unknown_id": "u1", "unknown": "q", "resolved": True, "resolved_by": "x", "resolved_timestamp": THEN}
    load("unknowns", note).import_unknowns(db)
    assert _one(db, "SELECT resolved_timestamp FROM project_unknowns")[0] == THEN


def test_import_findings_keeps_the_stamped_resolution_time(importer_db):
    db, load = importer_db
    note = {"finding_id": "f1", "finding": "c", "is_resolved": True, "resolution": "r", "resolved_timestamp": THEN}
    load("findings", note).import_findings(db)
    assert _one(db, "SELECT resolved_timestamp FROM project_findings")[0] == THEN


def test_an_unparseable_resolved_at_falls_through_to_the_stamped_timestamp(importer_db):
    """`a or b` took the garbage string, parsed it to None and dropped the good value beside it."""
    db, load = importer_db
    note = {"unknown_id": "u1", "unknown": "q", "resolved": True, "resolved_at": "garbage", "resolved_timestamp": THEN}
    load("unknowns", note).import_unknowns(db)
    assert _one(db, "SELECT resolved_timestamp FROM project_unknowns")[0] == THEN


def test_an_invalidated_dead_end_comes_back_invalidated(importer_db):
    db, load = importer_db
    note = {
        "dead_end_id": "d1",
        "approach": "a",
        "why_failed": "w",
        "is_invalidated": True,
        "invalidation_reason": "no longer applies",
        "invalidated_at": THEN,
        "invalidated_by": "me",
    }
    load("dead_ends", note).import_dead_ends(db)
    assert _one(
        db, "SELECT is_invalidated, invalidation_reason, invalidated_at, invalidated_by FROM project_dead_ends"
    ) == (1, "no longer applies", THEN, "me")


def test_an_invalidated_mistake_comes_back_invalidated(importer_db):
    db, load = importer_db
    note = {
        "mistake_id": "m1",
        "mistake": "x",
        "why_wrong": "y",
        "is_invalidated": True,
        "invalidation_reason": "wrong call",
    }
    load("mistakes", note).import_mistakes(db)
    row = _one(db, "SELECT is_invalidated, invalidation_reason FROM mistakes_made")
    assert row == (1, "wrong call")


def test_a_valid_dead_end_and_mistake_stay_valid(importer_db):
    """Control: only a note that says it was invalidated changes anything."""
    db, load = importer_db
    load("dead_ends", {"dead_end_id": "d1", "approach": "a", "why_failed": "w"}).import_dead_ends(db)
    load("mistakes", {"mistake_id": "m1", "mistake": "x", "why_wrong": "y"}).import_mistakes(db)
    assert _one(db, "SELECT is_invalidated FROM project_dead_ends")[0] == 0
    assert _one(db, "SELECT is_invalidated FROM mistakes_made")[0] == 0


def test_a_database_without_the_invalidation_columns_still_imports_the_row(importer_db):
    """An older project database has no lifecycle columns: the row is imported (as before), not lost to a failed UPDATE."""
    db, load = importer_db
    db.conn.executescript(
        """
        DROP TABLE project_dead_ends;
        CREATE TABLE project_dead_ends (id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, goal_id TEXT, subtask_id TEXT, approach TEXT,
            why_failed TEXT, created_timestamp REAL, dead_end_data TEXT, subject TEXT, transaction_id TEXT);
        """
    )
    note = {"dead_end_id": "d1", "approach": "a", "why_failed": "w", "is_invalidated": True, "invalidation_reason": "r"}
    assert load("dead_ends", note).import_dead_ends(db) == 1
    assert _one(db, "SELECT count(*) FROM project_dead_ends")[0] == 1
