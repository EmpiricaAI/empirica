"""`log-artifacts` must attach artifacts to the open goal, like the single verbs do.

`_resolve_graph_context` derived session_id, project_id and transaction_id from
context but read `goal_id` from the caller's payload only. Every artifact logged
through the batch verb therefore landed with `goal_id` NULL — and circle-1
retrieval filters `goal_id IN (<active goals>)`, so those artifacts were
unreachable by goal-scoped retrieval. Measured before the fix: 149 findings in 7
days, 0 attached.

The batch verb is the one the system prompt tells practitioners to PREFER, so the
recommended path was the broken one — which is why this went unnoticed.

It also interacts badly with `last_retrieved_at` (migration 063): an artifact no
path can reach accrues retrieval_count 0, identical to "surfaced and ignored".
Pruning on that signal would delete artifacts that were never offered.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.cli.command_handlers import graph_commands as gc


class _DB:
    """Minimal stand-in — the resolver touches `.conn` and `.get_session`."""

    def __init__(self, rows):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE goals (id TEXT, session_id TEXT, is_completed INT, created_timestamp REAL)")
        self.conn.executemany("INSERT INTO goals VALUES (?,?,?,?)", rows)
        self.conn.commit()

    def get_session(self, _):
        return {"project_id": "P1"}


def _args(**kw):
    # project_id supplied so the resolver never reaches its context fallback —
    # these tests are about goal_id, and a test that silently depends on the
    # ambient session would pass or fail for reasons unrelated to the assertion.
    base = {"session_id": "S1", "project_id": "P1", "transaction_id": "T1"}
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_autolinks_to_the_open_goal():
    db = _DB([("g-open", "S1", 0, 100.0)])
    ctx = gc._resolve_graph_context({}, _args(), db)
    assert ctx["goal_id"] == "g-open"


def test_explicit_goal_id_wins_over_autolink():
    """The payload is an override, not a suggestion — a caller naming a goal
    means it, even if a different goal is the most recent open one."""
    db = _DB([("g-open", "S1", 0, 100.0)])
    ctx = gc._resolve_graph_context({"goal_id": "g-explicit"}, _args(), db)
    assert ctx["goal_id"] == "g-explicit"


def test_most_recent_open_goal_wins():
    db = _DB([("g-old", "S1", 0, 100.0), ("g-new", "S1", 0, 200.0)])
    ctx = gc._resolve_graph_context({}, _args(), db)
    assert ctx["goal_id"] == "g-new"


def test_completed_goals_are_not_linked():
    """Attaching to a closed goal is worse than attaching to nothing: it would
    reopen retrieval on work that was deliberately finished."""
    db = _DB([("g-done", "S1", 1, 200.0), ("g-open", "S1", 0, 100.0)])
    ctx = gc._resolve_graph_context({}, _args(), db)
    assert ctx["goal_id"] == "g-open"


def test_no_open_goal_yields_none_not_a_crash():
    db = _DB([("g-done", "S1", 1, 100.0)])
    ctx = gc._resolve_graph_context({}, _args(), db)
    assert ctx["goal_id"] is None


def test_autolink_is_session_scoped_a_known_boundary():
    """NOT the behaviour we ultimately want — pinned so the limit stays visible.

    The resolver matches on `session_id`, so a goal opened before a compaction
    boundary is invisible to artifacts logged after it. Sessions are compaction
    boundaries, not lifecycle scopes, and `goals` already carries
    `transaction_id` — so the addressing for a better scope already exists.

    Reusing the single-verb resolver inherits this rather than inventing a
    second, differently-wrong answer: two resolvers disagreeing about which goal
    owns an artifact is the drift this repo keeps producing. Fixing the scope
    belongs to the session-descoping audit and must move BOTH call sites
    together. When it lands, this test should flip — not be deleted.
    """
    db = _DB([("g-other-session", "S0", 0, 100.0)])
    ctx = gc._resolve_graph_context({}, _args(session_id="S1"), db)
    assert ctx["goal_id"] is None
