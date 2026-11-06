"""project-update: a repeated tag flag keeps every value, and a rewrite that drops comments says so.

Both were reported by practices setting their project type on 1.14.21:

- `--add-tag service --add-tag library` kept only `library`. The flag stored one value, so the second overwrote the first with no error.
- The handler loads project.yaml, changes it and writes it back, which removes every comment. Nothing said so; one practice lost three
  comment lines and found out by reading the diff.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import empirica.cli.command_handlers.project_update as pu
import empirica.config.path_resolver as pr
import empirica.data.repositories.workspace_db as wdb
from empirica.cli.cli_core import create_argument_parser
from empirica.config.project_config_loader import ProjectConfig


def test_the_parser_keeps_every_repeated_tag():
    args = create_argument_parser().parse_args(
        ["project-update", "--add-tag", "service", "--add-tag", "library", "--remove-tag", "a", "--remove-tag", "b"]
    )
    assert args.add_tag == ["service", "library"]
    assert args.remove_tag == ["a", "b"]


def test_every_added_tag_lands_in_order_and_a_present_one_is_not_repeated():
    config = ProjectConfig({"name": "x", "tags": ["library"]})
    changes = pu._apply_list_updates(SimpleNamespace(add_tag=["service", "library", "rust"]), config)
    assert config.tags == ["library", "service", "rust"]
    assert changes == ["+tag: service", "+tag: rust"]


def test_every_removed_tag_goes():
    config = ProjectConfig({"name": "x", "tags": ["a", "b", "c"]})
    pu._apply_list_updates(SimpleNamespace(remove_tag=["a", "c", "not-there"]), config)
    assert config.tags == ["b"]


def test_a_caller_passing_one_tag_as_text_still_works():
    config = ProjectConfig({"name": "x", "tags": []})
    pu._apply_list_updates(SimpleNamespace(add_tag="solo"), config)
    assert config.tags == ["solo"]


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "co"
    (root / ".empirica").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    monkeypatch.chdir(root)
    # The handler's git-root lookup is cached per process; pin it to the scratch checkout.
    monkeypatch.setattr(pr, "get_git_root", lambda: root)
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: tmp_path / "absent" / "workspace.db")
    return root


WITH_COMMENTS = (
    "# who owns this\nproject_id: p-x\nname: co\n  # indented note\ntype: cli\ntags: []  # trailing, not counted\n"
)


def test_a_rewrite_that_drops_comment_lines_reports_how_many(checkout, capsys):
    (checkout / ".empirica" / "project.yaml").write_text(WITH_COMMENTS)

    pu.handle_project_update_command(SimpleNamespace(add_tag=["rust"], output="json"))

    out = json.loads(capsys.readouterr().out)
    assert out["comment_lines_dropped"] == 2
    assert "#" not in (checkout / ".empirica" / "project.yaml").read_text()  # the loss the count describes is real


def test_the_human_view_says_comments_were_dropped(checkout, capsys):
    (checkout / ".empirica" / "project.yaml").write_text(WITH_COMMENTS)

    pu.handle_project_update_command(SimpleNamespace(add_tag=["rust"], output="human"))

    assert "2 comment lines" in capsys.readouterr().out


def test_a_file_without_comments_reports_none_dropped(checkout, capsys):
    (checkout / ".empirica" / "project.yaml").write_text("project_id: p-x\nname: co\ntype: cli\n")

    pu.handle_project_update_command(SimpleNamespace(add_tag=["rust"], output="json"))

    assert json.loads(capsys.readouterr().out)["comment_lines_dropped"] == 0
