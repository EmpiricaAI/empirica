"""doctor --reconcile-notes merges note refs that hold more than one note (1.16.x, goal a34eef6d).

Before note_target, a rewrite after HEAD moved left a second note under the same ref; readers take the first one `git notes
list` prints, which can be the stale one. Goals had a repair; unknowns, assumptions, decisions and sources did not.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from empirica.core.canonical.empirica_git import note_lifecycle as nl


@pytest.fixture
def repo(tmp_path):
    def git(*a, stdin=None):
        return subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True, input=stdin).stdout

    git("init", "-q")
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git("config", k, v)
    git("commit", "-q", "--allow-empty", "-m", "one")
    return tmp_path, git


def _fork(git, ref, first, second):
    git("notes", f"--ref={ref}", "add", "-f", "-F", "-", "HEAD", stdin=json.dumps(first))
    git("commit", "-q", "--allow-empty", "-m", "two")
    git("notes", f"--ref={ref}", "add", "-f", "-F", "-", "HEAD", stdin=json.dumps(second))


def test_a_forked_unknown_note_is_merged_with_the_newer_state(repo):
    root, git = repo
    ref = "empirica/unknowns/u1"
    _fork(
        git,
        ref,
        {"unknown_id": "u1", "unknown": "why", "resolved": False},
        {"unknown_id": "u1", "resolved": True, "resolved_by": "x"},
    )
    plan = nl.plan_note_forks(str(root))
    assert plan == [{"ref": ref, "notes": 2}]
    assert nl.apply_note_forks(str(root), plan) == {"merged": 1, "failed": 0}
    listed = [line for line in git("notes", f"--ref={ref}", "list").splitlines() if line.strip()]
    assert len(listed) == 1
    merged = json.loads(git("notes", f"--ref={ref}", "show", listed[0].split()[1]))
    assert merged["resolved"] is True and merged["unknown"] == "why" and merged["resolved_by"] == "x"
    assert nl.plan_note_forks(str(root)) == []  # a second pass finds nothing


def test_refs_with_one_note_and_other_kinds_are_left_alone(repo):
    root, git = repo
    git("notes", "--ref=empirica/decisions/d1", "add", "-f", "-F", "-", "HEAD", stdin=json.dumps({"decision_id": "d1"}))
    _fork(git, "empirica/findings/f1", {"finding_id": "f1"}, {"finding_id": "f1", "x": 1})  # not this repair's kinds
    assert nl.plan_note_forks(str(root)) == []
