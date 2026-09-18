"""_goalless_nudge asks about THIS transaction, not the whole project.

It fired only when the project had zero in_progress goals, so every practice
with live multi-week work had it off (2026-09-18: autonomy 18, core 3,
cortex 3, outreach 1 open goals; extension 0). Test shape proposed by
empirica-outreach: a transaction with no goal in play must nudge; each way of
having a goal in play must silence it.
"""

from __future__ import annotations

import importlib.util
import json
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


def _check(gate, cursor, tmp_path, monkeypatch, calls=6, counters=True):
    """Real layout: the transaction file carries NO count (it is workflow-owned);
    the count lives in the hook counters file at the writer's own path helper.
    The first version of this test put tool_call_count in the transaction file,
    so it passed while the deployed hook read 0 forever (mesh-support,
    prop_jqoi4xy3izelnl6vhctdykdtju)."""
    suffix = "_tmux_9"
    tx_file = tmp_path / f"active_transaction{suffix}.json"
    tx_file.write_text(json.dumps({"status": "open", "transaction_id": TX, "preflight_timestamp": PREFLIGHT_TS}))
    if counters:
        gate._hook_counters_path(tx_file, suffix).write_text(json.dumps({"tool_call_count": calls}))
    monkeypatch.setattr(gate, "_find_transaction_file", lambda *_a, **_k: tx_file)
    monkeypatch.setattr(gate, "_resolve_empirica_session_id", lambda *_: "s")
    return gate._check_goalless_work(cursor, "s", TX, PREFLIGHT_TS, "c", tmp_path, suffix)


def test_a_count_in_the_transaction_file_is_not_read(gate, cursor, tmp_path, monkeypatch):
    """Negative control on the channel: only the counters file counts."""
    suffix = "_tmux_9"
    tx_file = tmp_path / f"active_transaction{suffix}.json"
    tx_file.write_text(json.dumps({"status": "open", "tool_call_count": 50}))
    monkeypatch.setattr(gate, "_find_transaction_file", lambda *_a, **_k: tx_file)
    monkeypatch.setattr(gate, "_resolve_empirica_session_id", lambda *_: "s")
    assert gate._check_goalless_work(cursor, "s", TX, PREFLIGHT_TS, "c", tmp_path, suffix) == ""


def test_the_writer_uses_the_same_path_helper(gate):
    """Reader and writer cannot drift: the incrementer resolves its file via the helper."""
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
