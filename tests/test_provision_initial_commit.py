"""A new practice gets a first commit, so git notes work from its first artifact (goal db2cf3fa, T2).

Git notes are the canonical log and attach to a commit. project-init runs `git init` and commits nothing, so on a fresh
machine every artifact logged before the person's first commit was written to SQLite only (finding-log git_stored:false)
and a rebuild from notes would lose it (fresh-HOME probe, 2026-11-11).
"""

from __future__ import annotations

import subprocess

from empirica.cli.command_handlers import provision_practice_commands as pp


def _git(repo, *args, env=None):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env)


def _repo(tmp_path, *, identity=True):
    repo = tmp_path / "p"
    repo.mkdir()
    _git(repo, "init", "-q")
    if identity:
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
    return repo


def test_a_repo_with_no_commit_gets_an_empty_first_commit(tmp_path):
    repo = _repo(tmp_path)
    (repo / "untracked.txt").write_text("the person's own file")
    step = pp._initial_commit_step(repo, dry_run=False)
    assert step["changed"] is True and not step.get("error"), step
    assert _git(repo, "rev-parse", "--verify", "HEAD").returncode == 0
    assert _git(repo, "show", "--stat", "--format=", "HEAD").stdout.strip() == "", "empty: nobody's files are committed"
    assert "untracked.txt" in _git(repo, "status", "--short").stdout


def test_a_repo_that_already_has_commits_is_left_alone(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "theirs")
    before = _git(repo, "rev-parse", "HEAD").stdout
    step = pp._initial_commit_step(repo, dry_run=False)
    assert step["changed"] is False and _git(repo, "rev-parse", "HEAD").stdout == before


def test_without_a_git_identity_it_says_so_and_invents_none(tmp_path, monkeypatch):
    repo = _repo(tmp_path, identity=False)
    empty_home = tmp_path / "home"
    empty_home.mkdir()
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_home / "none"))
    step = pp._initial_commit_step(repo, dry_run=False)
    assert step.get("error") and "user.email" in step["hint"]
    assert _git(repo, "rev-parse", "--verify", "HEAD").returncode != 0


def test_dry_run_commits_nothing(tmp_path):
    repo = _repo(tmp_path)
    step = pp._initial_commit_step(repo, dry_run=True)
    assert step["changed"] is True and _git(repo, "rev-parse", "--verify", "HEAD").returncode != 0


def test_a_folder_that_is_not_a_repo_is_a_note_not_an_error(tmp_path):
    """project-init makes the repo and reports its own failure; in a dry run it has not run."""
    step = pp._initial_commit_step(tmp_path, dry_run=False)
    assert not step.get("error") and step["changed"] is False and "not its own git repository" in step["note"]


def test_a_folder_inside_another_repo_never_commits_to_that_repo(tmp_path):
    parent = _repo(tmp_path)
    inner = parent / "practice"
    inner.mkdir()
    step = pp._initial_commit_step(inner, dry_run=False)
    assert step["changed"] is False and not step.get("error")
    assert _git(parent, "rev-parse", "--verify", "HEAD").returncode != 0, "the parent repo got no commit"
