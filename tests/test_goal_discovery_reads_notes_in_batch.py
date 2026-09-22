"""goals-discover reads every goal note through one git process, not two per goal.

With 3167 goal refs on one store the per-goal path took about 25 s and a CLI
contract test with a 30 s timeout failed under parallel load. The batch reader
must return exactly what load_goal returns, including the first-note choice on
a ref that annotates several commits, and fall back to load_goal on failure.
Built in a throwaway git repo.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from empirica.core.canonical.empirica_git.goal_store import GitGoalStore


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True)
    for i in range(3):
        (tmp_path / f"f{i}").write_text(str(i))
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", f"c{i}"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _note(repo, goal_id, commit, payload):
    subprocess.run(
        ["git", "notes", f"--ref=refs/notes/empirica/goals/{goal_id}", "add", "-f", "-m", json.dumps(payload), commit],
        cwd=repo,
        check=True,
    )


def test_batch_matches_load_goal_including_multi_commit_refs(repo):
    commits = subprocess.run(["git", "rev-list", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.split()
    _note(repo, "g-one", commits[0], {"goal_id": "g-one", "ai_id": "a", "n": 1})
    # one ref annotating three commits: load_goal takes the first `notes list` line
    for i, c in enumerate(commits):
        _note(repo, "g-many", c, {"goal_id": "g-many", "ai_id": "b", "n": i})
    store = GitGoalStore(workspace_root=str(repo))
    refs = subprocess.run(
        ["git", "for-each-ref", "refs/notes/empirica/goals/"], cwd=repo, capture_output=True, text=True
    )
    batch = store._load_all_goal_notes(refs.stdout)
    assert batch is not None and set(batch) == {"g-one", "g-many"}
    for gid in batch:
        assert batch[gid] == store.load_goal(gid)


def test_discover_uses_the_batch_and_filters(repo, monkeypatch):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    _note(repo, "g-a", head, {"goal_id": "g-a", "ai_id": "a"})
    _note(repo, "g-b", head, {"goal_id": "g-b", "ai_id": "b"})
    store = GitGoalStore(workspace_root=str(repo))
    calls = []
    monkeypatch.setattr(store, "load_goal", lambda gid: calls.append(gid))
    assert [g["goal_id"] for g in store.discover_goals(from_ai_id="a")] == ["g-a"]
    assert calls == []  # the per-goal path was not needed


def test_a_failing_batch_falls_back_to_load_goal(repo, monkeypatch):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    _note(repo, "g-a", head, {"goal_id": "g-a", "ai_id": "a"})
    store = GitGoalStore(workspace_root=str(repo))
    monkeypatch.setattr(store, "_load_all_goal_notes", lambda _out: None)
    assert [g["goal_id"] for g in store.discover_goals()] == ["g-a"]
