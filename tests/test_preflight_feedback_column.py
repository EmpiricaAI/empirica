"""Regression: PREFLIGHT prior-transaction feedback must read the right column.

The behavioral-feedback feature persists the POSTFLIGHT retrospective into the
`reflexes.reflex_data` JSON blob, but `_feedback_extract_retrospective` was
querying a non-existent `meta` column — so the query errored, got swallowed by
the caller's try/except, and the feature silently returned `None` on every
PREFLIGHT. This locks the column name so the regression can't return.
"""

from __future__ import annotations

import json
import sqlite3

from empirica.cli.command_handlers._workflow_preflight import (
    _feedback_extract_retrospective,
)


def _reflexes_db() -> sqlite3.Connection:
    # Minimal schema: just the columns the extractor touches. Crucially there is
    # NO `meta` column — so a query against `meta` raises OperationalError and
    # the test fails, exactly as the bug would.
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE reflexes (session_id TEXT, phase TEXT, timestamp REAL, reflex_data TEXT)"
    )
    return conn


def test_feedback_extracted_from_reflex_data_column():
    conn = _reflexes_db()
    retro = {
        "artifact_counts": {
            "findings": 1, "decisions": 1, "unknowns": 0,
            "dead_ends": 0, "mistakes": 0, "assumptions": 0,
        },
        "sources_discipline_note": "2 artifacts logged with 0 source_refs.",
    }
    conn.execute(
        "INSERT INTO reflexes (session_id, phase, timestamp, reflex_data) VALUES (?,?,?,?)",
        ("s1", "POSTFLIGHT", 1.0, json.dumps({"retrospective": retro})),
    )

    feedback, pf_meta = _feedback_extract_retrospective(conn.cursor(), "s1")

    # Was None for the whole life of the bug (wrong column → swallowed error).
    assert feedback is not None
    assert feedback["artifact_gaps"] == ["unknowns", "dead_ends", "mistakes", "assumptions"]
    assert "sources_discipline_warning" in feedback
    assert pf_meta["retrospective"]["artifact_counts"]["findings"] == 1


def test_feedback_none_when_no_postflight_row():
    conn = _reflexes_db()
    feedback, pf_meta = _feedback_extract_retrospective(conn.cursor(), "s1")
    assert feedback is None and pf_meta is None


def test_feedback_picks_latest_postflight():
    conn = _reflexes_db()
    for ts, n in ((1.0, 0), (2.0, 3)):
        rd = {"retrospective": {"artifact_counts": {"findings": n}}}
        conn.execute(
            "INSERT INTO reflexes (session_id, phase, timestamp, reflex_data) VALUES (?,?,?,?)",
            ("s1", "POSTFLIGHT", ts, json.dumps(rd)),
        )
    _, pf_meta = _feedback_extract_retrospective(conn.cursor(), "s1")
    # ORDER BY timestamp DESC LIMIT 1 → the ts=2.0 row.
    assert pf_meta["retrospective"]["artifact_counts"]["findings"] == 3
