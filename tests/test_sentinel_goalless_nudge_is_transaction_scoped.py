"""_goalless_nudge asks about THIS transaction, not the whole project.

It fired only when the project had zero in_progress goals, so every practice
with live multi-week work had it off (2026-09-18: autonomy 18, core 3,
cortex 3, outreach 1 open goals; extension 0). Test shape proposed by
empirica-outreach: a transaction with no goal in play must nudge; each way of
having a goal in play must silence it.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
TX = "tx-now"
PREFLIGHT_TS = 1_000_000.0


@pytest.fixture
def gate():
    lib = str(_HOOK_DIR.parent / "lib")
    sys.path.insert(0, lib)
    try:
        spec = importlib.util.spec_from_file_location("sentinel_gate_goalless", _HOOK_DIR / "sentinel-gate.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.path.remove(lib)


@pytest.fixture
def cursor():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE goals (id TEXT, project_id TEXT, transaction_id TEXT, status TEXT);
        CREATE TABLE subtasks (id TEXT, goal_id TEXT, created_timestamp REAL, completed_timestamp REAL);
        CREATE TABLE project_findings (id TEXT, transaction_id TEXT, goal_id TEXT);
        -- A long-running in_progress goal elsewhere in the project: the thing
        -- that used to switch the nudge off for months.
        INSERT INTO goals VALUES ('old', 'p', 'tx-long-ago', 'in_progress');
        """
    )
    return conn.cursor()


def _check(gate, cursor, tmp_path, monkeypatch, calls=6):
    """The check reads the count THIS invocation's tracker wrote (_tool_call_count),
    not a second lookup of a file. Two earlier shapes of this test passed while
    the deployed hook read 0: first a count planted in the transaction file, then
    a counters file found by a reader whose locator could resolve a different
    transaction than the writer's (mesh-support, 2026-09-18)."""
    monkeypatch.setattr(gate, "_tool_call_count", calls)
    return gate._check_goalless_work(cursor, "s", TX, PREFLIGHT_TS)


def test_no_counted_session_means_no_nudge(gate, cursor, monkeypatch):
    monkeypatch.setattr(gate, "_tool_call_count", None)
    assert gate._check_goalless_work(cursor, "s", TX, PREFLIGHT_TS) == ""


def test_the_tracker_hands_its_own_count_to_the_check(gate, tmp_path, monkeypatch):
    """The writer's result, same invocation: no second locator to diverge."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "active_work_cs.json").write_text("{}")
    monkeypatch.setattr(gate, "_try_increment_tool_count", lambda *_a, **_k: (7, 0))
    monkeypatch.setattr(gate, "_tool_call_count", None)
    gate._track_tool_usage({"session_id": "cs"}, "Bash", {"command": "ls"})
    assert gate._tool_call_count == 7


def test_the_writer_uses_the_same_path_helper(gate):
    import inspect

    assert "_hook_counters_path(tx_path, suffix)" in inspect.getsource(gate._try_increment_tool_count)


def test_no_goal_in_this_transaction_nudges_despite_an_open_project_goal(gate, cursor, tmp_path, monkeypatch):
    assert "no goal in play" in _check(gate, cursor, tmp_path, monkeypatch)


def test_under_five_calls_is_quiet(gate, cursor, tmp_path, monkeypatch):
    assert _check(gate, cursor, tmp_path, monkeypatch, calls=4) == ""


def test_a_goal_created_in_this_transaction_silences_it(gate, cursor, tmp_path, monkeypatch):
    cursor.execute("INSERT INTO goals VALUES ('g', 'p', ?, 'in_progress')", (TX,))
    assert _check(gate, cursor, tmp_path, monkeypatch) == ""


def test_a_task_touched_since_preflight_silences_it(gate, cursor, tmp_path, monkeypatch):
    cursor.execute("INSERT INTO subtasks VALUES ('t', 'old', ?, NULL)", (PREFLIGHT_TS + 5,))
    assert _check(gate, cursor, tmp_path, monkeypatch) == ""


def test_a_task_touched_BEFORE_preflight_does_not(gate, cursor, tmp_path, monkeypatch):
    cursor.execute("INSERT INTO subtasks VALUES ('t', 'old', ?, ?)", (PREFLIGHT_TS - 50, PREFLIGHT_TS - 10))
    assert "no goal in play" in _check(gate, cursor, tmp_path, monkeypatch)


def test_a_finding_logged_against_a_goal_in_this_transaction_silences_it(gate, cursor, tmp_path, monkeypatch):
    cursor.execute("INSERT INTO project_findings VALUES ('f', ?, 'old')", (TX,))
    assert _check(gate, cursor, tmp_path, monkeypatch) == ""


def test_a_failing_check_says_so_in_the_channel_the_model_reads(gate, tmp_path, monkeypatch):
    """Not stderr: hook stderr reaches no file anyone reads. The cause rides the
    nudge, so "could not check" never looks like "no nudge needed"."""
    broken = sqlite3.connect(":memory:").cursor()  # no tables at all
    out = _check(gate, broken, tmp_path, monkeypatch)
    assert "could not run" in out and "UNKNOWN" in out
