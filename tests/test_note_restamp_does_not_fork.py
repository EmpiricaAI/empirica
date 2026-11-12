"""Rewriting a note after HEAD has moved replaces it; it does not leave a second note (1.16.0 sweep).

`git notes add -f <HEAD>` replaces only the note on that exact commit. The assumption, unknown, decision and source stores
wrote every rewrite at HEAD, so once a commit landed between creating an artifact and changing its state, the ref held two
notes and the readers took whichever `git notes list` printed first: the old state came back on a rebuild. Goals were fixed
for this already (goal_store._note_commit).
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.core.canonical.empirica_git.assumption_store import GitAssumptionStore
from empirica.core.canonical.empirica_git.decision_store import GitDecisionStore
from empirica.core.canonical.empirica_git.source_store import GitSourceStore
from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore


@pytest.fixture
def repo(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git("config", k, v)
    git("commit", "-q", "--allow-empty", "-m", "one")
    return tmp_path, git


def _notes(root, ref):
    out = subprocess.run(["git", "notes", f"--ref={ref}", "list"], cwd=root, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def test_rewrites_after_a_commit_keep_one_note_per_artifact(repo):
    root, git = repo
    a, u, d, s = (
        GitAssumptionStore(str(root)),
        GitUnknownStore(str(root)),
        GitDecisionStore(str(root)),
        GitSourceStore(str(root)),
    )
    assert a.store_assumption(
        assumption_id="a1", project_id="p", session_id="s", ai_id="t", assumption="x", confidence=0.5
    )
    assert u.store_unknown(unknown_id="u1", project_id="p", session_id="s", ai_id="t", unknown="why")
    assert d.store_decision(decision_id="d1", project_id="p", session_id="s", ai_id="t", choice="c", rationale="r")
    assert s.store_source(source_id="s1", project_id="p", session_id="s", title="t")
    git("commit", "-q", "--allow-empty", "-m", "two")

    assert a.store_assumption(
        assumption_id="a1", project_id="p", session_id="s", ai_id="t", assumption="x", confidence=0.5, status="verified"
    )
    assert u.store_unknown(unknown_id="u1", project_id="p", session_id="s", ai_id="t", unknown="why", resolved=True)
    assert d.store_decision(
        decision_id="d1",
        project_id="p",
        session_id="s",
        ai_id="t",
        choice="c",
        rationale="r",
        extra={"outcome": "reversed"},
    )
    assert s.store_source(source_id="s1", project_id="p", session_id="s", title="t", extra={"archived": True})

    for ref in ("empirica/assumptions/a1", "empirica/unknowns/u1", "empirica/decisions/d1", "empirica/sources/s1"):
        assert len(_notes(root, ref)) == 1, ref
    assert a.load_assumption("a1")["status"] == "verified"
    assert d.load_decision("d1")["outcome"] == "reversed"
    assert s.load_source("s1")["archived"] is True


def test_resolving_a_finding_after_a_commit_keeps_one_note_and_its_date(repo):
    # resolve_finding re-stored at HEAD and without created_at: a second note, and the finding dated to its resolution
    # (goal 73b02132).
    from empirica.core.canonical.empirica_git.finding_store import GitFindingStore

    root, git = repo
    store = GitFindingStore(str(root))
    assert store.store_finding(
        finding_id="f1", project_id="p", session_id="s", ai_id="t", finding="x", created_at="2026-01-01T00:00:00+00:00"
    )
    git("commit", "-q", "--allow-empty", "-m", "two")
    assert store.resolve_finding("f1", "answered")
    assert len(_notes(root, "empirica/findings/f1")) == 1
    note = store.load_finding("f1")
    assert note["is_resolved"] is True and note["created_at"] == "2026-01-01T00:00:00+00:00"
