"""A git-notes store distinguishes "not a repo" from "git could not answer".

Each store checked `git rev-parse` with a 5s timeout and returned False on
TimeoutExpired or FileNotFoundError, which is the same answer as "not a git
repository". The write was then skipped with a DEBUG line nobody sees, leaving a
SQLite row with no note for `rebuild` to import: the divergence `doctor` reports,
from a cause no log recorded.
"""

from __future__ import annotations

import logging
import subprocess

import pytest

from empirica.core.canonical.empirica_git.finding_store import GitFindingStore


def test_a_git_timeout_is_loud(tmp_path, monkeypatch, caplog):
    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(subprocess, "run", slow)
    with caplog.at_level(logging.WARNING):
        store = GitFindingStore(workspace_root=str(tmp_path))
    assert store._git_available is False
    assert any("git could not answer" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("exc", [FileNotFoundError("git")])
def test_a_missing_git_binary_is_loud_too(tmp_path, monkeypatch, caplog, exc):
    def missing(*_a, **_k):
        raise exc

    monkeypatch.setattr(subprocess, "run", missing)
    with caplog.at_level(logging.WARNING):
        GitFindingStore(workspace_root=str(tmp_path))
    assert any("FileNotFoundError" in r.getMessage() for r in caplog.records)


def test_a_directory_that_is_not_a_repo_stays_quiet(tmp_path, caplog):
    """Positive control: an honest "not a repo" is not a warning."""
    with caplog.at_level(logging.WARNING):
        store = GitFindingStore(workspace_root=str(tmp_path))
    assert store._git_available is False
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
