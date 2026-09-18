"""A goal_id a caller names is resolved to a real goal, or refused.

It was used verbatim, so a short id ('7f7ccb9f') wrote an attached_to edge to a
goal that did not exist, while explicit edges in the same payload were
validated. The goal verbs accept a unique prefix; artifact logging now does too.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.cli.command_handlers.artifact_log_commands import _canonical_goal_id, _resolve_goal_for_artifact

FULL = "7f7ccb9f-f3a7-4704-8b12-84472ccea87b"


class _DB:
    def __init__(self, *ids: str):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE goals (id TEXT)")
        self.conn.executemany("INSERT INTO goals VALUES (?)", [(i,) for i in ids])


def test_full_id_passes_through():
    assert _canonical_goal_id(FULL, _DB(FULL)) == FULL


def test_unique_prefix_resolves_to_the_full_id():
    assert _canonical_goal_id("7f7ccb9f", _DB(FULL, "8928ad32-a1d1-4314-8f61-12c17799383f")) == FULL


def test_an_id_that_matches_nothing_is_refused():
    with pytest.raises(ValueError, match="matches no goal"):
        _canonical_goal_id("deadbeef", _DB(FULL))


def test_an_ambiguous_prefix_is_refused_with_candidates():
    with pytest.raises(ValueError, match="ambiguous"):
        _canonical_goal_id("7f", _DB(FULL, "7f000000-0000-0000-0000-000000000000"))


def test_the_single_verb_path_resolves_caller_ids():
    assert _resolve_goal_for_artifact("7f7ccb9f", "s", _DB(FULL)) == FULL


def test_log_artifacts_resolves_node_goal_ids_before_creating():
    import inspect

    from empirica.cli.command_handlers import graph_commands

    assert "_canonical_goal_id(node_goal, db)" in inspect.getsource(graph_commands)
