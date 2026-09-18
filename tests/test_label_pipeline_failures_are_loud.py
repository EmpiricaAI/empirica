"""Blindspot/prevention label steps stay non-fatal but stop failing silently.

They returned 0 / [] on any exception, so a persistent failure (schema drift,
a renamed column) was indistinguishable from "no labels to write" for as long
as it lasted — a silent hole in training data. A table that does not exist yet
is still quiet; anything else is a WARNING with the cause.
"""

from __future__ import annotations

import logging
import sqlite3

from empirica.core.blindspots import outcomes
from empirica.core.prevention import detection
from empirica.utils.fail_loud import warn_unless_missing_table


class _DB:
    def __init__(self, conn):
        self.conn = conn


def test_a_missing_table_is_quiet(caplog):
    db = _DB(sqlite3.connect(":memory:"))  # no blindspot_events table
    with caplog.at_level(logging.WARNING):
        assert outcomes.resolve_blindspot_outcomes(db, "s") == 0
    assert caplog.text == ""


def test_schema_drift_warns_with_the_cause(caplog):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE blindspot_events (session_id TEXT)")  # no outcome column
    with caplog.at_level(logging.WARNING):
        assert outcomes.resolve_blindspot_outcomes(_DB(conn), "s") == 0
    assert "resolve_blindspot_outcomes FAILED" in caplog.text
    assert "no such column" in caplog.text


def test_prevention_detection_uses_the_same_rule(caplog):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE prevention_events (id TEXT)")  # drifted
    with caplog.at_level(logging.WARNING):
        assert detection.apply_prevention_detection(_DB(conn), "s") == 0
    assert "FAILED" in caplog.text


def test_helper_distinguishes_absent_from_broken(caplog):
    log = logging.getLogger("t")
    with caplog.at_level(logging.WARNING, logger="t"):
        warn_unless_missing_table(log, "x", sqlite3.OperationalError("no such table: t"))
        assert caplog.text == ""
        warn_unless_missing_table(log, "x", sqlite3.OperationalError("database is locked"))
    assert "database is locked" in caplog.text
