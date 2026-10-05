"""refs/notes/empirica-archive/* is never pushed, so it must not be counted on either side of the comparison.

archive_note writes archive refs and no sync verb pushes or pulls them (by design, 1.14.8). The local count took
EVERY ref under refs/notes/, so a repo holding archived notes counted refs the remote can never hold:
sync-status read `behind` forever and a push that added nothing new read `not_replicating` (ok:false, exit 1).
Found by the 2026-10-05 deep sweep in four independent places (pilot 2 sync reader, docs cluster D, phase 1).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import empirica.cli.command_handlers.sync_commands as sc

_ENV_KEYS = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd), **_ENV_KEYS},
    )
    return out.stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "f").write_text("x")
    _git(root, "add", "f")
    _git(root, "commit", "-q", "-m", "c")
    monkeypatch.chdir(root)
    return root


def _note(root: Path, ref: str):
    _git(root, "notes", f"--ref={ref}", "add", "-f", "-m", "n", "HEAD")


def test_the_local_count_skips_archive_refs(repo):
    _note(repo, "empirica/findings/aaa")
    _note(repo, "empirica/unknowns/bbb")
    _note(repo, "empirica-archive/findings/ccc")
    assert sc._count_all_local_note_refs() == 2


def test_control_a_repo_with_only_live_refs_counts_them_all(repo):
    _note(repo, "empirica/findings/aaa")
    _note(repo, "breadcrumbs")
    assert sc._count_all_local_note_refs() == 2


def test_the_remote_count_skips_archive_refs_too(repo, tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _note(repo, "empirica/findings/aaa")
    _note(repo, "empirica-archive/findings/ccc")
    # someone pushed the archive ref by hand: the remote now holds one more ref than a sync ever would
    _git(repo, "push", "-q", "origin", "refs/notes/empirica/findings/aaa", "refs/notes/empirica-archive/findings/ccc")

    count, reason = sc._count_remote_notes(str(remote))

    assert reason is None and count == 1


def test_a_full_push_reads_as_replicated_when_archive_refs_exist(repo, tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _note(repo, "empirica/findings/aaa")
    _note(repo, "empirica-archive/findings/ccc")
    _git(repo, "push", "-q", "origin", "refs/notes/empirica/*:refs/notes/empirica/*")

    local = sc._count_all_local_note_refs()
    count, reason = sc._count_remote_notes(str(remote))
    verdict = sc._replication_verdict(local, count, reason)

    assert verdict["state"] == "replicated", verdict
