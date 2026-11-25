"""A new store takes the practice's name from its tracked project.yaml, not the folder (goal dc55912b).

Per-person clones live at /srv/practices/<practice>/<person>, so naming the projects row after the folder made the
statusline show the person's name for every practice (mesh-support, measured on a shared consulting host).
"""

from __future__ import annotations

from types import SimpleNamespace

from empirica.cli.command_handlers import project_init


def _clone(tmp_path, yaml_text=None):
    root = tmp_path / "david"
    (root / ".empirica").mkdir(parents=True)
    if yaml_text is not None:
        (root / ".empirica" / "project.yaml").write_text(yaml_text)
    return root


def test_the_tracked_name_wins_over_the_folder(tmp_path):
    assert (
        project_init.default_project_name(_clone(tmp_path, "name: empirica-consulting\nai_id: empirica-consulting\n"))
        == "empirica-consulting"
    )


def test_the_folder_is_the_fallback(tmp_path):
    assert project_init.default_project_name(_clone(tmp_path)) == "david"
    assert project_init.default_project_name(_clone(tmp_path / "x", "ai_id: y\n")) == "david"


def test_non_interactive_init_uses_it(tmp_path):
    root = _clone(tmp_path, "name: branding\n")
    cfg = project_init._collect_project_config_from_args(SimpleNamespace(), root)
    assert cfg["project_name"] == "branding"


def test_an_explicit_name_still_wins(tmp_path):
    root = _clone(tmp_path, "name: branding\n")
    assert (
        project_init._collect_project_config_from_args(SimpleNamespace(project_name="other"), root)["project_name"]
        == "other"
    )
