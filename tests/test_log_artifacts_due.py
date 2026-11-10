"""`log-artifacts` takes a node's due date, as the single verbs take --due (1.15.0 sweep, D7).

The system prompt names log-artifacts the default route for logging, so a due date that only the single verbs accepted was
a date the default path could not set. Built in a throwaway git repo: the note stores resolve the repository from the
working directory.
"""

from __future__ import annotations

import subprocess
from datetime import date, timedelta

import pytest

from empirica.cli.command_handlers import graph_commands as gc


@pytest.fixture
def practice(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _log(practice, nodes):
    return gc.log_artifacts_graph(
        {"nodes": nodes, "edges": []}, session_id="sess-1", project_id="proj-1", project_path=str(practice)
    )


def _due_of(practice, table, artifact_id):
    import sqlite3

    with sqlite3.connect(practice / ".empirica" / "sessions" / "sessions.db") as db:
        return db.execute(f"SELECT due_at FROM {table} WHERE id = ?", (artifact_id,)).fetchone()[0]


def test_unknown_and_assumption_nodes_carry_their_due_date(practice):
    week = (date.today() + timedelta(days=7)).isoformat()
    out = _log(
        practice,
        [
            {"ref": "u", "type": "unknown", "data": {"unknown": "does x hold?", "due": "2026-12-01"}},
            {"ref": "a", "type": "assumption", "data": {"assumption": "y", "confidence": 0.6, "due": "+7d"}},
            {"ref": "n", "type": "unknown", "data": {"unknown": "no date"}},
        ],
    )
    assert out["ok"] and out["nodes_created"] == 3, out
    assert _due_of(practice, "project_unknowns", out["created"]["u"]) == "2026-12-01"
    assert _due_of(practice, "assumptions", out["created"]["a"]) == week
    assert _due_of(practice, "project_unknowns", out["created"]["n"]) is None  # control
    assert out["due"] == {"u": {"due_at": "2026-12-01"}, "a": {"due_at": week}}


@pytest.mark.parametrize(
    ("node", "says"),
    [
        ({"ref": "f", "type": "finding", "data": {"finding": "x", "impact": 0.5, "due": "+7d"}}, "only unknown and"),
        (
            {"ref": "u", "type": "unknown", "data": {"unknown": "x", "due": "next tuesday"}},
            "Failed to create unknown 'u'",
        ),
    ],
)
def test_a_due_date_that_cannot_be_set_is_refused_before_the_write(practice, node, says):
    """Both paths: the CLI's whole-graph validation, and the daemon's direct call into log_artifacts_graph."""
    assert gc._validate_graph({"nodes": [node], "edges": []}), "the CLI path refuses the batch"
    out = _log(practice, [node])
    assert out["created"] == {} and any(says in e for e in out["errors"]), out
