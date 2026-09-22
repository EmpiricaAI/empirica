"""A node logged through log-artifacts gets the same git note the single verb writes.

Git notes are the canonical log and a rebuild imports them back. Every single
`*-log` verb wrote its artifact to a store; the batch path wrote SQLite only,
from 2026-04-23. On core that left 2435 of 4981 findings with no note. Built in
a throwaway git repo: the stores resolve the repository from the working
directory, so the test chdirs first and never touches the checkout's notes.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.cli.command_handlers import graph_commands as gc

CONTEXT = {"project_id": "proj-1", "session_id": "sess-1", "goal_id": None, "ai_id": "empirica"}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _refs(repo, kind):
    out = subprocess.run(
        ["git", "for-each-ref", f"refs/notes/empirica/{kind}/", "--format=%(refname:lstrip=4)"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return set(out)


@pytest.mark.parametrize(
    ("node", "kind"),
    [
        ({"type": "finding", "data": {"finding": "x is true", "impact": 0.7}}, "findings"),
        ({"type": "unknown", "data": {"unknown": "is y"}}, "unknowns"),
        ({"type": "dead_end", "data": {"approach": "a", "why_failed": "b"}}, "dead_ends"),
        ({"type": "mistake", "data": {"mistake": "m", "why_wrong": "w", "prevention": "p"}}, "mistakes"),
        ({"type": "assumption", "data": {"assumption": "s", "confidence": 0.6}}, "assumptions"),
        ({"type": "decision", "data": {"choice": "c", "rationale": "r", "reversibility": "exploratory"}}, "decisions"),
    ],
)
def test_every_batch_node_type_gets_a_note(repo, node, kind):
    assert gc._store_node_git_notes(node, "11111111-2222-4333-8444-555555555555", CONTEXT) is True
    assert _refs(repo, kind) == {"11111111-2222-4333-8444-555555555555"}


def test_a_failed_note_write_warns_and_does_not_raise(repo, monkeypatch, caplog):
    import logging

    import empirica.core.canonical.empirica_git.finding_store as fs

    def boom(*_a, **_k):
        raise RuntimeError("git is gone")

    monkeypatch.setattr(fs.GitFindingStore, "store_finding", boom)
    with caplog.at_level(logging.WARNING):
        ok = gc._store_node_git_notes({"type": "finding", "data": {"finding": "x"}}, "f-1", CONTEXT)
    assert ok is False and "git notes not written" in caplog.text


def test_the_batch_loop_writes_the_note_before_it_embeds():
    import inspect

    source = inspect.getsource(gc.log_artifacts_graph)
    assert source.index("_store_node_git_notes(node, artifact_id, context)") < source.index(
        "_auto_embed_node(node, artifact_id, context)"
    )


def test_the_context_carries_the_practice(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "s.db"))
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    try:
        sid = db.create_session(ai_id="empirica-test")
        assert gc._session_ai_id(db, sid) == "empirica-test"
        assert gc._session_ai_id(db, "nope") is None
    finally:
        db.close()
