"""A count that could not run says so; it never answers 0.

Goal 61da889f (silent value-returning handlers). Three sites returned a
confident value when they failed:

- `_count_all_local_note_refs` returned 0 when git failed, so replication read
  "nothing to replicate", and a push check read "replicated".
- `_count_goal_drift` returned 0 when its query failed, so goals-list read as
  having no status drift.
- `_is_cortex_configured` returned False with nothing said, dropping the mesh
  from the rendered prompt (kept False; now logged).
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess

import empirica.cli.command_handlers.sync_commands as sc
from empirica.cli.command_handlers import goal_commands as gc


def test_a_failed_local_note_count_is_none(monkeypatch):
    class _Bad:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Bad())
    assert sc._count_all_local_note_refs() is None


def test_a_raising_local_note_count_is_none(monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert sc._count_all_local_note_refs() is None


def test_replication_with_an_uncounted_local_side_is_unknown():
    verdict = sc._replication_verdict(None, 500, None)
    assert verdict["state"] == "unknown" and "could not be counted" in verdict["reason"]


def test_positive_control_a_counted_side_still_gets_a_verdict():
    assert sc._replication_verdict(10, 10, None)["state"] != "unknown"


def test_a_push_check_with_an_uncounted_local_side_is_unknown(monkeypatch):
    monkeypatch.setattr(sc, "_count_all_local_note_refs", lambda: None)
    monkeypatch.setattr(sc, "_count_remote_notes", lambda *_a, **_k: (500, None))
    verification, refuted = sc._verify_push_landed("forgejo", 400)
    assert verification["verdict"] == "unknown" and refuted is False


def test_a_failed_drift_count_is_none_and_says_so(caplog):
    cursor = sqlite3.connect(":memory:").cursor()  # no goals table
    with caplog.at_level(logging.WARNING):
        count = gc._count_goal_drift(cursor, "p", None, False)
    assert count is None and "drift count failed" in caplog.text
    result: dict = {}
    gc._annotate_goals_result(result, None, count)
    assert result["drift_count"] is None and "could not be counted" in result["drift_hint"]


def test_positive_control_zero_drift_adds_nothing():
    result: dict = {}
    gc._annotate_goals_result(result, None, 0)
    assert "drift_count" not in result


def test_the_cortex_check_failing_is_logged(monkeypatch, caplog):
    import empirica.core.auth.cortex_oauth as co
    from empirica.cli.command_handlers import setup_claude_code as scc

    def boom():
        raise RuntimeError("broken install")

    monkeypatch.setattr(co, "cortex_configured", boom)
    with caplog.at_level(logging.WARNING):
        assert scc._is_cortex_configured() is False
    assert "broken install" in caplog.text
