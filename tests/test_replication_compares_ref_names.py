"""Replication is judged by WHICH refs the remote holds, not how many (goal d897e750, medium item).

`_verify_push_landed` and `sync-status` compared a local COUNT with a remote COUNT. A remote shared by several devices holds refs
the local repo has never seen, so its count can equal or exceed the local one while local refs never replicated: the verdict read
"replicated" and the gap stayed invisible. The tests that existed pinned the counts, which is how the all-clear survived.

These run real git against a real bare remote. A remote holding the same NUMBER of refs but not the same refs is the case; the
control is the remote that holds every local ref (plus some of its own), which must still read as replicated.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import empirica.cli.command_handlers.sync_commands as sc

_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd), **_ENV},
    ).stdout


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """(local work repo, bare remote); the remote is registered as `origin`. Cwd is the work repo."""
    work, remote = tmp_path / "work", tmp_path / "remote.git"
    work.mkdir()
    _git(work, "init", "-q")
    (work / "f").write_text("x")
    _git(work, "add", "f")
    _git(work, "commit", "-q", "-m", "c")
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(work, "remote", "add", "origin", str(remote))
    monkeypatch.chdir(work)
    return work, remote


def _note(repo: Path, ref: str):
    _git(repo, "notes", f"--ref={ref}", "add", "-f", "-m", "n", "HEAD")


def _foreign_ref(repo: Path, remote: Path, ref: str):
    """A ref another device pushed: on the remote, absent here."""
    _note(repo, ref)
    _git(repo, "push", "-q", "origin", f"refs/notes/{ref}:refs/notes/{ref}")
    _git(repo, "update-ref", "-d", f"refs/notes/{ref}")


def test_a_remote_with_the_same_count_but_other_refs_is_not_replicated(repos):
    work, remote = repos
    _note(work, "empirica/findings/mine")
    _note(work, "empirica/findings/also-mine")
    _foreign_ref(work, remote, "empirica/findings/theirs-1")
    _foreign_ref(work, remote, "empirica/findings/theirs-2")
    assert sc._count_all_local_note_refs() == sc._count_remote_notes(str(remote))[0] == 2, "precondition: counts agree"

    assert sc._note_ref_gap(str(remote)) == 2
    block = sc._replication_block(str(remote), True, False)["replication"]
    assert block["state"] != "replicated" and block["behind"] == 2


def test_a_push_check_sees_the_gap_the_counts_hide(repos):
    work, remote = repos
    _note(work, "empirica/findings/mine")
    _foreign_ref(work, remote, "empirica/findings/theirs")
    verification, refuted = sc._verify_push_landed(str(remote), 1)
    assert verification["verdict"] in ("partial", "not_replicating") and verification["missing"] == 1
    assert verification["compared_by"] == "ref_names"
    assert refuted is True, "the remote did not move and a local ref is missing"


def test_a_remote_holding_every_local_ref_and_more_is_replicated(repos):
    work, remote = repos
    _note(work, "empirica/findings/mine")
    _git(work, "push", "-q", "origin", "refs/notes/empirica/findings/mine:refs/notes/empirica/findings/mine")
    _foreign_ref(work, remote, "empirica/findings/theirs")
    assert sc._note_ref_gap(str(remote)) == 0
    assert sc._replication_block(str(remote), True, False)["replication"]["state"] == "replicated"
    verification, refuted = sc._verify_push_landed(str(remote), 1)
    assert verification["verdict"] == "replicated" and refuted is False


def test_archive_refs_are_not_part_of_the_comparison(repos):
    work, remote = repos
    _note(work, "empirica/findings/mine")
    _note(work, "empirica-archive/findings/old")
    _git(work, "push", "-q", "origin", "refs/notes/empirica/findings/mine:refs/notes/empirica/findings/mine")
    assert sc._note_ref_gap(str(remote)) == 0


def test_an_unreachable_remote_has_no_gap_to_report(repos, tmp_path):
    work, _ = repos
    _note(work, "empirica/findings/mine")
    assert sc._note_ref_gap(str(tmp_path / "nowhere.git")) is None, "unknown, not zero"


def test_the_replication_verdict_takes_the_name_gap_over_the_counts():
    behind = sc._replication_verdict(10, 10, None, gap=3)
    assert behind["state"] == "behind" and behind["behind"] == 3
    assert sc._replication_verdict(10, 4, None, gap=0)["state"] == "replicated"
    assert sc._replication_verdict(10, 10, None)["state"] == "replicated", (
        "no gap supplied: the counts decide, as before"
    )
