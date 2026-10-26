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
