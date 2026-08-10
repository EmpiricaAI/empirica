"""GH #403: a plugin sync must not silently destroy local customization.

Plain `empirica setup` rmtree'd the installed plugin and re-copied from source,
discarding any local modification with no warning and no backup. Skipping the
sync is NOT the fix — a stale vendored hook is a documented fleet-wide hazard
worse than a lost edit — so the sync stays unconditional and the destruction
becomes recoverable and loud: modified files are backed up with relative paths
and each one is named in the output.
"""

from __future__ import annotations

from empirica.cli.command_handlers.setup_claude_code import _backup_locally_modified_plugin_files


def _tree(root, files: dict):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


def test_a_locally_modified_file_is_backed_up_and_named(tmp_path, capsys):
    """THE REGRESSION: the reporter's my_local_function, twice lost."""
    source = _tree(tmp_path / "source", {"scripts/statusline_empirica.py": "def stock(): pass\n"})
    installed = _tree(tmp_path / "plugin", {"scripts/statusline_empirica.py": "def my_local_function(): pass\n"})

    preserved = _backup_locally_modified_plugin_files(source, installed, "human")

    assert preserved == ["scripts/statusline_empirica.py"]
    backup = tmp_path / "plugin.bak" / "scripts" / "statusline_empirica.py"
    assert backup.read_text() == "def my_local_function(): pass\n", "the customization must survive the sync"
    out = capsys.readouterr().out
    assert "statusline_empirica.py" in out, "a backup nobody is told about recovers nothing"


def test_an_unmodified_file_is_not_backed_up(tmp_path):
    """Identical files are a no-op for the sync — backing them up would bury the
    one file that matters under a copy of the whole plugin."""
    source = _tree(tmp_path / "source", {"hooks/gate.py": "same\n", "scripts/a.py": "same2\n"})
    installed = _tree(tmp_path / "plugin", {"hooks/gate.py": "same\n", "scripts/a.py": "same2\n"})

    assert _backup_locally_modified_plugin_files(source, installed, "json") == []
    assert not (tmp_path / "plugin.bak").exists()


def test_pycache_and_version_stamp_are_ignored(tmp_path):
    source = _tree(tmp_path / "source", {"hooks/gate.py": "x\n"})
    installed = _tree(
        tmp_path / "plugin",
        {"hooks/gate.py": "x\n", "hooks/__pycache__/gate.cpython-314.pyc": "bin", ".plugin-version": "1.13.7\n"},
    )
    assert _backup_locally_modified_plugin_files(source, installed, "json") == []


def test_a_file_absent_from_source_is_preserved(tmp_path):
    """A wholly-local file (not vendored at all) is the strongest customization
    signal — the rmtree would delete it with nothing to re-copy."""
    source = _tree(tmp_path / "source", {"hooks/gate.py": "x\n"})
    installed = _tree(tmp_path / "plugin", {"hooks/gate.py": "x\n", "scripts/my_addon.py": "mine\n"})

    preserved = _backup_locally_modified_plugin_files(source, installed, "json")
    assert preserved == ["scripts/my_addon.py"]
    assert (tmp_path / "plugin.bak" / "scripts" / "my_addon.py").read_text() == "mine\n"


def test_no_installed_plugin_is_a_clean_noop(tmp_path):
    source = _tree(tmp_path / "source", {"hooks/gate.py": "x\n"})
    assert _backup_locally_modified_plugin_files(source, tmp_path / "missing", "json") == []
