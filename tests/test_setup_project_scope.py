"""`setup-claude-code --scope project`: Empirica in one repository, nothing under ~/.claude.

People with an existing Claude Code setup want Empirica only in some projects (David, 2026-11-08). The user scope stays the
default; the project scope writes the hooks into the repo's .claude/settings.local.json (Claude Code's per-user, uncommitted
settings), runs them from the installed package, and loads the prompt through the repo's CLAUDE.md.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from empirica.cli.cli_core import create_argument_parser
from empirica.cli.command_handlers import setup_claude_code as scc


@pytest.fixture
def repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    monkeypatch.chdir(root)
    return root


def _args(*extra):
    return create_argument_parser().parse_args(["setup-claude-code", "--scope", "project", "--output", "json", *extra])


def test_the_parser_takes_scope_and_defaults_to_user():
    assert create_argument_parser().parse_args(["setup-claude-code"]).scope == "user"
    assert _args().scope == "project"


def test_project_scope_writes_hooks_into_the_repo_and_nothing_under_home(repo, tmp_path, capsys):
    rc = scc.setup_project_scope(_args(), repo)

    assert rc == 0
    settings = json.loads((repo / ".claude" / "settings.local.json").read_text())
    commands = [h["command"] for entries in settings["hooks"].values() for e in entries for h in e["hooks"]]
    assert any("sentinel-gate.py" in c for c in commands)
    source = str(scc._get_plugin_source_dir())
    assert all(source in c for c in commands if "sentinel-gate.py" in c)  # run from the package, not a copy
    assert "enabledPlugins" not in settings
    assert not (tmp_path / "home" / ".claude").exists()


def test_the_prompt_loads_through_the_repo_s_claude_md(repo):
    scc.setup_project_scope(_args(), repo)

    assert (repo / ".empirica" / "empirica-system-prompt.md").exists()
    assert "@.empirica/empirica-system-prompt.md" in (repo / "CLAUDE.md").read_text()


def test_an_existing_claude_md_keeps_its_text_and_gains_one_line(repo):
    (repo / "CLAUDE.md").write_text("# mine\n\nOur rules.\n")

    scc.setup_project_scope(_args(), repo)
    scc.setup_project_scope(_args(), repo)  # idempotent

    text = (repo / "CLAUDE.md").read_text()
    assert text.count("@.empirica/empirica-system-prompt.md") == 1
    assert "# mine\n\nOur rules.\n" in text


def test_a_second_run_registers_each_hook_once(repo):
    scc.setup_project_scope(_args(), repo)
    first = (repo / ".claude" / "settings.local.json").read_text()
    scc.setup_project_scope(_args(), repo)

    assert (repo / ".claude" / "settings.local.json").read_text() == first


def test_outside_a_git_repo_it_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)

    assert scc.setup_project_scope(_args(), None) == 1
    assert "git" in capsys.readouterr().out


def test_a_new_claude_md_keeps_agents_md_loaded(repo):
    (repo / "AGENTS.md").write_text("# agents\n")

    scc.setup_project_scope(_args(), repo)

    assert "@AGENTS.md" in (repo / "CLAUDE.md").read_text()


def test_the_local_files_are_ignored_by_git(repo):
    scc.setup_project_scope(_args(), repo)

    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=repo, check=False).returncode == 0

    assert ignored(".claude/settings.local.json")
    assert ignored(".empirica/empirica-system-prompt.md")
    assert not ignored("CLAUDE.md")
