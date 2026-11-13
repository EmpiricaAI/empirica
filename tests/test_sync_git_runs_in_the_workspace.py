"""Sync runs git in the project it reads its configuration from (goal d897e750, pilot sweep).

The config came from the context project (project-switch), but every git call ran in the process's cwd, so a sync from
another directory counted, pushed and fetched another repository's notes.
"""

from __future__ import annotations

import subprocess

from empirica.cli.command_handlers import sync_commands as sc


def _repo(path):
    path.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "config", "commit.gpgsign", "false"],
        ["git", "commit", "-q", "--allow-empty", "-m", "i"],
    ):
        subprocess.run(cmd, cwd=path, check=True)
    return path


def test_git_runs_in_the_context_project_not_the_cwd(tmp_path, monkeypatch):
    project, elsewhere = _repo(tmp_path / "project"), _repo(tmp_path / "elsewhere")
    subprocess.run(["git", "notes", "--ref=empirica/findings/f1", "add", "-m", "{}", "HEAD"], cwd=project, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://example.invalid/project.git"], cwd=project, check=True)
    monkeypatch.setattr(sc, "_get_workspace_root", lambda: str(project))
    monkeypatch.chdir(elsewhere)
    assert sc._count_all_local_note_refs() == 1
    assert sc._get_remote_url() == "https://example.invalid/project.git"
    assert sc._check_remote("origin") is True
