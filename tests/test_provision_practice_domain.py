"""provision-practice takes --domain and hands it to project-init, as it does --type and --description.

The onboarding interview learns what a practice is for, which includes its domain (research, operations, consulting...).
Without the flag the interview could set the type and the description but had to tell the person to run project-update for
the domain afterwards. An unknown name is refused before the practice directory is created, because project-init would
refuse it only after provision-practice had already made the directory.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from empirica.cli.cli_core import create_argument_parser
from empirica.cli.command_handlers import provision_practice_commands as pp


@pytest.fixture(autouse=True)
def _empty_home(tmp_path, monkeypatch):
    """Only the built-in domain files count: a domain file in the real ~/.empirica/domains must not decide a test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _args(tmp_path, **over):
    base = {
        "name": "demo",
        "base_path": str(tmp_path / "practices"),
        "tenant": "t",
        "org": "o",
        "substrate": None,
        "no_cortex": True,
        "dry_run": False,
        "output": "json",
        "type": None,
        "description": None,
        "domain": None,
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


def test_a_listed_domain_reaches_project_init(tmp_path, commands, capsys):
    rc = pp.handle_provision_practice_command(_args(tmp_path, domain="research"))

    assert rc == 0
    init = _init_command(commands)
    assert init[init.index("--domain") + 1] == "research"


def test_without_it_project_init_gets_no_domain(tmp_path, commands, capsys):
    pp.handle_provision_practice_command(_args(tmp_path))

    assert "--domain" not in _init_command(commands)


def test_an_unknown_domain_is_refused_before_anything_is_created(tmp_path, commands, capsys):
    rc = pp.handle_provision_practice_command(_args(tmp_path, domain="calibration papers"))

    assert rc == 1
    assert commands == []
    assert not (tmp_path / "practices" / "demo").exists()
    error = json.loads(capsys.readouterr().out)["error"]
    assert "'calibration papers' is not a domain" in error
    assert "research" in error  # the valid names are listed
    assert "tag" in error  # and where descriptive text goes


def test_on_a_practice_that_already_exists_the_domain_is_not_applied_and_the_step_says_how(tmp_path, commands, capsys):
    proj = tmp_path / "practices" / "demo"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("project_id: p\nname: demo\n")

    pp.handle_provision_practice_command(_args(tmp_path, domain="research"))

    assert not any(c[:2] == ["empirica", "project-init"] for c in commands)
    steps = {s["step"]: s for s in json.loads(capsys.readouterr().out)["steps"]}
    note = steps["project-init"]["note"]
    assert "--domain" in note and "project-update --domain" in note


def test_the_parser_takes_domain():
    ns = create_argument_parser().parse_args(["provision-practice", "demo", "--domain", "research"])

    assert ns.domain == "research"
