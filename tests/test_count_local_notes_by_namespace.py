"""`_count_local_notes` must count the namespaces that exist, and the single refs among them (goal d897e750, medium item).

It walked a hard-coded list with a trailing-slash pattern (``refs/notes/<ref>/``). Two consequences, both silent:

* ``breadcrumbs`` and ``empirica-precompact`` are SINGLE refs, so the pattern never matched them: always 0.
* the list named ``empirica/sessions`` and ``empirica/handoffs``, which do not exist (the writers use ``session`` and
  ``handoff``), and omitted ``tasks``, ``decisions``, ``assumptions`` and ``sources``, so ~13,000 note refs on one store
  were invisible to the per-namespace breakdown that sync-status and the dry-run print.

Real git, in a scratch repo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import empirica.cli.command_handlers.sync_commands as sc

_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git(cwd: Path, *args: str):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd), **_ENV},
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-q", "-m", "c")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _note(repo: Path, ref: str):
    _git(repo, "notes", f"--ref={ref}", "add", "-f", "-m", "n", "HEAD")


def test_single_ref_namespaces_are_counted(repo):
    _note(repo, "breadcrumbs")
    _note(repo, "empirica-precompact")
    counts = sc._count_local_notes()
    assert counts["breadcrumbs"] == 1 and counts["empirica-precompact"] == 1


def test_namespaces_the_old_list_never_named_are_counted(repo):
    for ref in (
        "empirica/session/a",
        "empirica/session/b",
        "empirica/tasks/t",
        "empirica/decisions/d",
        "empirica/handoff/h",
    ):
        _note(repo, ref)
    counts = sc._count_local_notes()
    assert counts["empirica/session"] == 2
    assert counts["empirica/tasks"] == 1 and counts["empirica/decisions"] == 1 and counts["empirica/handoff"] == 1


def test_an_ordinary_namespace_still_counts_its_refs(repo):
    _note(repo, "empirica/findings/a")
    _note(repo, "empirica/findings/b")
    assert sc._count_local_notes()["empirica/findings"] == 2


def test_archive_refs_are_not_counted_as_a_namespace(repo):
    _note(repo, "empirica/findings/a")
    _note(repo, "empirica-archive/findings/old")
    counts = sc._count_local_notes()
    assert not any("archive" in k for k in counts), counts


def test_a_namespace_with_no_refs_reads_zero_not_missing(repo):
    """Control: the known namespaces stay in the answer at 0, so a reader can tell 'none' from 'not looked'."""
    _note(repo, "empirica/findings/a")
    counts = sc._count_local_notes()
    assert counts["empirica/goals"] == 0


def test_the_total_equals_the_replicable_refs_in_the_repo(repo):
    for ref in ("empirica/findings/a", "empirica/session/s", "breadcrumbs", "empirica-archive/x/y"):
        _note(repo, ref)
    assert sum(sc._count_local_notes().values()) == sc._count_all_local_note_refs() == 3
