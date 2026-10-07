"""check_git_status must not report 'Remote: up to date' for a branch that has no upstream.

Found by the 2026-10-06 pipeline sweep (U1): `git log @{u}..` exits 128 with empty stdout when there is no upstream, and an empty
stdout was read as "nothing unpushed". The repositories here are real git repos built under tmp_path.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.cli.command_handlers import release_commands as rc


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f.txt").write_text("x")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _check(root):
    return rc.EpistemicReleaseAgent(project_root=root, quick=True).check_git_status()


def test_a_branch_with_no_upstream_is_reported_not_called_up_to_date(repo):
    result = _check(repo)
    assert "Remote: up to date" not in result.details
    assert "No upstream configured: cannot verify the pushed state" in result.details
    assert result.status == rc.AssessmentStatus.WARN


def test_a_branch_level_with_its_upstream_is_up_to_date(repo, tmp_path_factory):
    remote = tmp_path_factory.mktemp("remote")
    _git(remote, "init", "-q", "--bare", "-b", "main")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    result = _check(repo)
    assert "Remote: up to date" in result.details and result.status == rc.AssessmentStatus.PASS


def test_unpushed_commits_are_still_counted(repo, tmp_path_factory):
    remote = tmp_path_factory.mktemp("remote")
    _git(remote, "init", "-q", "--bare", "-b", "main")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "g.txt").write_text("y")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ahead")
    result = _check(repo)
    assert "Unpushed commits: 1" in result.details and result.status == rc.AssessmentStatus.WARN
