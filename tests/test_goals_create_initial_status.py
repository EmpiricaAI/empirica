"""goals-create's initial status: an explicit --status wins over a config's, and blocked is applied (goal 3043656d).

Found by the 1.17.20 review: with a config file or stdin the flag was never read (`echo '{...}' | empirica goals-create -
--status planned` made an in_progress goal), and only 'planned' was ever written, so --status blocked was dropped.
"""

from __future__ import annotations

import sqlite3
import types

import pytest

from empirica.cli.command_handlers import goal_commands as gc


@pytest.mark.parametrize(
    "flag, config, expected",
    [
        (None, None, "in_progress"),
        ("planned", None, "planned"),
        ("blocked", None, "blocked"),
        ("planned", {"objective": "x"}, "planned"),  # the flag reaches config mode
        ("in_progress", {"objective": "x", "status": "planned"}, "in_progress"),  # an explicit flag wins
        (None, {"objective": "x", "status": "planned"}, "planned"),  # the config's when no flag
        (None, {"objective": "x"}, "in_progress"),
    ],
)
def test_initial_status(flag, config, expected):
    assert gc._initial_status(types.SimpleNamespace(status=flag), config) == expected


def test_an_unknown_config_status_is_refused():
    with pytest.raises(ValueError, match="status"):
        gc._initial_status(types.SimpleNamespace(status=None), {"objective": "x", "status": "done"})


@pytest.mark.parametrize("status", ["planned", "blocked"])
def test_a_non_default_status_is_written(status):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE goals (id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO goals VALUES ('g1', 'in_progress')")
    gc._apply_initial_status(conn, "g1", status)
    assert conn.execute("SELECT status FROM goals").fetchone()[0] == status


def test_the_parser_leaves_status_unset_when_not_given():
    from empirica.cli.cli_core import create_argument_parser

    args = create_argument_parser().parse_args(["goals-create", "--objective", "x"])
    assert args.status is None
