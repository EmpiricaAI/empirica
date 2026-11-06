"""A provisioned practice tracks its identity and nothing else under .empirica, and is created with a type and a description.

provision-practice was run for real on 2026-11-05. Its steps worked, and it left three things for the person to fix by hand:

- no .gitignore, so the whole .empirica directory showed as untracked, the sessions database included. A clone keeps the practice's
  identity only if .empirica/project.yaml is tracked, which sibling practices set up by hand;
- the description was "<name> project";
- the project type was unset.
"""

from __future__ import annotations

import json
import subprocess
import types
from pathlib import Path

import pytest

from empirica.cli.cli_core import create_argument_parser
from empirica.cli.command_handlers import provision_practice_commands as pp

# ── the .gitignore ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _ignored(repo: Path, path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=repo, check=False).returncode == 0


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "practice"
    (root / ".empirica" / "sessions").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / ".empirica" / "project.yaml").write_text("project_id: p\n")
    (root / ".empirica" / "sessions" / "sessions.db").write_text("x")
    (root / ".empirica" / "active_transaction_x.json").write_text("{}")
    return root


def test_a_new_practice_tracks_its_project_file_and_ignores_the_rest_of_empirica(repo):
    assert not _ignored(repo, ".empirica/sessions/sessions.db")  # positive control: nothing is ignored yet

    assert pp._ensure_gitignore(repo, dry_run=False) is True

    assert _ignored(repo, ".empirica/sessions/sessions.db")
    assert _ignored(repo, ".empirica/active_transaction_x.json")
    assert not _ignored(repo, ".empirica/project.yaml")
    assert not _ignored(repo, "README.md")


def test_an_existing_gitignore_keeps_its_lines_and_gains_the_rule(repo):
    (repo / ".gitignore").write_text("node_modules/\n*.log")  # no trailing newline, on purpose

    assert pp._ensure_gitignore(repo, dry_run=False) is True

    text = (repo / ".gitignore").read_text()
    assert text.startswith("node_modules/\n*.log\n")
    assert _ignored(repo, ".empirica/sessions/sessions.db") and not _ignored(repo, ".empirica/project.yaml")
    assert _ignored(repo, "node_modules/x")


def test_running_it_again_changes_nothing(repo):
    pp._ensure_gitignore(repo, dry_run=False)
    before = (repo / ".gitignore").read_text()

    assert pp._ensure_gitignore(repo, dry_run=False) is False
    assert (repo / ".gitignore").read_text() == before


def test_a_gitignore_that_ignores_the_whole_directory_is_left_alone_and_reported(repo):
    """`.empirica/` hides project.yaml too. That is the person's choice; it is not rewritten, and the step says so."""
    (repo / ".gitignore").write_text(".empirica/\n")

    assert pp._ensure_gitignore(repo, dry_run=False) is False
    assert (repo / ".gitignore").read_text() == ".empirica/\n"
    assert "project.yaml" in pp._gitignore_note(repo)


def test_a_dry_run_writes_nothing(repo):
    assert pp._ensure_gitignore(repo, dry_run=True) is True
    assert not (repo / ".gitignore").exists()


# ── type and description ────────────────────────────────────────────────────────────────────────────────────────────────────


def _args(tmp_path, **over):
    base = {
        "name": "demo",
        "base_path": str(tmp_path),
        "tenant": "t",
        "org": "o",
        "substrate": None,
        "no_cortex": True,
        "dry_run": False,
        "output": "json",
        "type": None,
        "description": None,
    }
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.fixture
def commands(monkeypatch):
    """Record the commands provision-practice would run; `project-init` leaves a project file behind, as the real one does."""
    ran: list[list[str]] = []

    def fake_run(cmd, cwd, dry_run):
        ran.append(list(cmd))
        if cmd[:2] == ["empirica", "project-init"] and not dry_run:
            (Path(cwd) / ".empirica").mkdir(exist_ok=True)
            (Path(cwd) / ".empirica" / "project.yaml").write_text("project_id: p\nname: demo\n")
        return True, ""

    monkeypatch.setattr(pp, "_run", fake_run)
    return ran


def _init_command(ran):
    return next(c for c in ran if c[:2] == ["empirica", "project-init"])


def test_the_type_and_description_reach_project_init(tmp_path, commands, capsys):
    rc = pp.handle_provision_practice_command(
        _args(tmp_path, type="research", description="Reads papers on calibration")
    )

    assert rc == 0
    init = _init_command(commands)
    assert init[init.index("--type") + 1] == "research"
    assert init[init.index("--project-description") + 1] == "Reads papers on calibration"
    steps = {s["step"]: s for s in json.loads(capsys.readouterr().out)["steps"]}
    assert steps["gitignore"]["changed"] is True
    assert (tmp_path / "demo" / ".gitignore").exists()


def test_without_them_project_init_is_called_as_before(tmp_path, commands, capsys):
    pp.handle_provision_practice_command(_args(tmp_path))
    init = _init_command(commands)
    assert "--type" not in init and "--project-description" not in init


def test_on_a_practice_that_already_exists_they_are_not_applied_and_the_step_says_so(tmp_path, commands, capsys):
    proj = tmp_path / "demo"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("project_id: p\nname: demo\n")

    pp.handle_provision_practice_command(_args(tmp_path, type="research", description="x"))

    assert not any(c[:2] == ["empirica", "project-init"] for c in commands)
    steps = {s["step"]: s for s in json.loads(capsys.readouterr().out)["steps"]}
    note = steps["project-init"]["note"]
    assert "--type" in note and "project-update" in note


def test_the_parser_takes_a_listed_type_and_refuses_an_unknown_one(capsys):
    parser = create_argument_parser()
    ns = parser.parse_args(["provision-practice", "demo", "--type", "research", "--description", "Reads papers"])
    assert ns.type == "research" and ns.description == "Reads papers"

    with pytest.raises(SystemExit):
        parser.parse_args(["provision-practice", "demo", "--type", "gizmo"])
    assert "Investigation that produces knowledge" in capsys.readouterr().err  # the list with meanings
