"""A retrospective count that could not run is reported as unmeasured, not as zero logged.

_retro_count_artifacts writes 0 for any table whose COUNT raises so consumers
can keep summing, but 0 is also what "logged nothing" looks like - and that
reading drives the breadth nag and the calibration evidence. The failed labels
now ride alongside: WARN with the cause, excluded from types_missing, and
listed as artifact_counts_unmeasured in the retrospective.
"""

from __future__ import annotations

import logging
import sqlite3

from empirica.cli.command_handlers import _workflow_shared as ws


def _cursor_missing(table_missing: str):
    conn = sqlite3.connect(":memory:")
    for t in ("project_findings", "project_unknowns", "project_dead_ends", "mistakes_made", "assumptions", "decisions"):
        if t == table_missing:
            continue
        conn.execute(f"CREATE TABLE {t} (session_id TEXT, transaction_id TEXT)")
    conn.execute("INSERT INTO project_findings VALUES ('s', 't')")
    return conn.cursor()


def test_a_failed_count_is_zero_but_named_unmeasured(caplog):
    cur = _cursor_missing("decisions")
    unmeasured: list[str] = []
    with caplog.at_level(logging.WARNING, logger=ws.logger.name):
        counts = ws._retro_count_artifacts(cur, "s", "t", unmeasured)
    assert counts["findings"] == 1
    assert counts["decisions"] == 0  # consumers still sum ints
    assert unmeasured == ["decisions"]
    assert "could not count decisions" in caplog.text
    assert "UNMEASURED" in caplog.text


def test_retrospective_seed_excludes_unmeasured_from_missing():
    cur = _cursor_missing("assumptions")
    counts, unmeasured, retro = ws._retro_counts_with_gaps(cur, "s", "t")
    assert retro["artifact_counts"] is counts
    assert retro["artifact_counts_unmeasured"] == ["assumptions"]
    assert unmeasured == ["assumptions"]


def test_a_clean_store_reports_no_unmeasured_key():
    cur = _cursor_missing("")  # nothing missing
    counts, unmeasured, retro = ws._retro_counts_with_gaps(cur, "s", "t")
    assert unmeasured == []
    assert "artifact_counts_unmeasured" not in retro
    assert counts["findings"] == 1
