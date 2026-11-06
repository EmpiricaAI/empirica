"""A project's domain is the name of a compliance domain, and a transaction starts from it.

"Domain" used to mean two things: free text in project.yaml that described the project (`ai/measurement`) and the name of a domain file
that selects a transaction's checks. Nothing read the first, and the second never came from the project. David, 2026-11-06: the short
list is the one meaning, and the free text moves to tags.

- `project-update --domain` and `project-init --domain` accept a domain name and refuse anything else with the list.
- A project that still holds free text has it moved to its tags the next time the practice runs `project-update`. Nothing is lost and
  the move is reported as a change. Nobody else's project file is touched.
- PREFLIGHT, when it declares no domain, starts from the project's domain, then from the project type's default.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import empirica.cli.command_handlers._workflow_preflight as wp
import empirica.cli.command_handlers.project_update as pu
import empirica.config.path_resolver as pr
import empirica.data.repositories.workspace_db as wdb
from empirica.config import domain_registry as dr

SHIPPED = {p.stem for p in (Path(dr.__file__).parent / "domains").glob("*.yaml")}


@pytest.fixture(autouse=True)
def empty_home(tmp_path, monkeypatch):
    """No domain files of the box's own: ~/.empirica/domains is read by the registry."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "co"
    (root / ".empirica").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(pr, "get_git_root", lambda: root)
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: tmp_path / "absent" / "workspace.db")
    return root


def _write(root: Path, text: str) -> Path:
    path = root / ".empirica" / "project.yaml"
    path.write_text(text)
    return path


def _update(capsys, **flags) -> dict:
    pu.handle_project_update_command(SimpleNamespace(output="json", **flags))
    return json.loads(capsys.readouterr().out)


# ── the list ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_cheap_name_scan_and_the_registry_agree_on_what_ships():
    assert len(SHIPPED) >= 8  # positive control: the glob found the files
    assert set(dr.known_domain_names()) == set(dr.DomainRegistry().list_domains()) == SHIPPED


def test_a_domain_file_of_the_users_own_is_a_known_domain(empty_home):
    user_dir = empty_home / ".empirica" / "domains"
    user_dir.mkdir(parents=True)
    (user_dir / "biotech.yaml").write_text("domain: biotech\ndescription: lab work\ncriticalities: {}\n")
    assert "biotech" in dr.known_domain_names()


# ── project-update ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_domain_name_is_accepted(checkout, capsys):
    path = _write(checkout, "project_id: p\nname: co\ntype: research\n")
    out = _update(capsys, domain="research")
    assert out["ok"] is True
    assert yaml.safe_load(path.read_text())["domain"] == "research"


def test_free_text_is_refused_with_the_list_and_the_file_is_not_touched(checkout, capsys):
    before = "project_id: p\nname: co\ntype: cli\n"
    path = _write(checkout, before)
    out = _update(capsys, domain="ai/measurement")
    assert out["ok"] is False
    assert "ai/measurement" in out["error"]
    for name in SHIPPED:
        assert name in out["error"]
    assert "tag" in out["error"]  # says where free text goes
    assert path.read_text() == before


def test_existing_free_text_moves_to_tags_on_the_practices_next_update(checkout, capsys):
    path = _write(checkout, "project_id: p\nname: co\ntype: cli\ndomain: ai/measurement\ntags: [x]\n")
    out = _update(capsys, add_tag=["y"])
    written = yaml.safe_load(path.read_text())
    assert written["tags"] == ["x", "y", "ai/measurement"]
    assert not written.get("domain")
    assert any("ai/measurement" in change and "tags" in change for change in out["changes"])


def test_moving_free_text_and_setting_a_domain_in_one_call(checkout, capsys):
    path = _write(checkout, "project_id: p\nname: co\ntype: cli\ndomain: ai/measurement\n")
    _update(capsys, domain="research")
    written = yaml.safe_load(path.read_text())
    assert written["domain"] == "research"
    assert written["tags"] == ["ai/measurement"]


def test_replacing_every_tag_in_the_same_call_does_not_drop_the_moved_text(checkout, capsys):
    path = _write(checkout, "project_id: p\nname: co\ntype: cli\ndomain: ai/measurement\ntags: [x]\n")
    _update(capsys, tags=["a", "b"])
    assert yaml.safe_load(path.read_text())["tags"] == ["a", "b", "ai/measurement"]


def test_a_domain_name_already_in_the_file_is_left_alone(checkout, capsys):
    path = _write(checkout, "project_id: p\nname: co\ntype: cli\ndomain: operations\ntags: [x]\n")
    out = _update(capsys, add_tag=["y"])
    written = yaml.safe_load(path.read_text())
    assert written["domain"] == "operations"
    assert written["tags"] == ["x", "y"]
    assert out["changes"] == ["+tag: y"]


def test_free_text_already_among_the_tags_is_not_added_twice(checkout, capsys):
    path = _write(checkout, "project_id: p\nname: co\ntype: cli\ndomain: ai/measurement\ntags: [ai/measurement]\n")
    _update(capsys, add_tag=["y"])
    assert yaml.safe_load(path.read_text())["tags"] == ["ai/measurement", "y"]


# ── PREFLIGHT's starting point ──────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("project_yaml", "expected"),
    [
        ("type: cli\ndomain: research\n", ("research", "medium")),  # the project's domain wins over the type's default
        ("type: infrastructure\ndomain: operations\n", ("operations", "high")),  # criticality still comes from the type
        ("type: research\n", ("research", "medium")),  # no domain: the type's default, as before
        ("type: cli\n", ("default", "medium")),
        ("type: cli\ndomain: ai/measurement\n", ("default", "medium")),  # free text not yet moved is not a domain
        ("type: cli\ndomain: nosuchdomain\n", ("default", "medium")),  # a plain word that names no domain file
        ("type: cli\ndomain: ''\n", ("default", "medium")),
        ("type: research\ndomain: 'ai/measurement'\n", ("research", "medium")),
    ],
)
def test_a_transaction_starts_from_the_projects_domain_then_the_types_default(tmp_path, project_yaml, expected):
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text("project_id: p\nname: co\n" + project_yaml)
    assert wp._default_work_frame_of(tmp_path) == expected


def test_no_project_file_is_the_baseline(tmp_path):
    assert wp._default_work_frame_of(tmp_path) == ("default", "medium")


# ── project-init ────────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def cli(tmp_path):
    """The real CLI in a scratch repo, with HOME and the store pinned away from the box."""
    import os
    import sys

    repo, home = tmp_path / "repo", tmp_path / "clihome"
    repo.mkdir()
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "EMPIRICA_SESSION_DB": str(tmp_path / "sessions.db"),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    env.pop("EMPIRICA_SESSION_ID", None)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, check=True, env=env)

    def run(*argv):
        return subprocess.run(
            [sys.executable, "-m", "empirica.cli", *argv],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    run.repo = repo
    return run


def test_project_init_refuses_free_text_and_creates_nothing(cli):
    r = cli("project-init", "--non-interactive", "--domain", "ai/measurement", "--output", "json")
    out = json.loads(r.stdout)
    assert out["ok"] is False and "ai/measurement" in out["error"] and "research" in out["error"]
    assert r.returncode != 0
    assert not (cli.repo / ".empirica").exists()


def test_project_init_writes_a_domain_name(cli):
    r = cli("project-init", "--non-interactive", "--type", "cli", "--domain", "research", "--output", "json")
    assert json.loads(r.stdout)["ok"] is True, r.stdout[:400]
    assert yaml.safe_load((cli.repo / ".empirica" / "project.yaml").read_text())["domain"] == "research"


def test_the_interactive_answer_is_a_listed_name_or_nothing(capsys):
    from empirica.cli.command_handlers.project_init import _domain_from_prompt

    domains = sorted(SHIPPED)
    assert _domain_from_prompt(" Research ", domains) == "research"
    assert _domain_from_prompt("", domains) == ""
    assert capsys.readouterr().out == ""
    assert _domain_from_prompt("ai/measurement", domains) == ""
    assert "not a domain" in capsys.readouterr().out


@pytest.mark.parametrize("verb", ["project-init", "project-update"])
def test_the_help_names_every_domain_that_ships(verb):
    """The help lists the names by hand. This keeps the list equal to the files."""
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and verb in a.choices).choices[verb]
    text = next(a.help for a in sub._actions if "--domain" in a.option_strings)
    for name in SHIPPED:
        assert name in text, name
