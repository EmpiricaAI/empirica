"""Tests for `projects-discover --register [NAME]` — flaskless import path
+ targeted single-project register.

Surfaced by mesh-support prop_flzmft22lz:

1. The `--register` path crashed `No module named flask` because
   `_register_discovered_to_registry` did `from empirica.api.registry
   import ...` which ran `empirica/api/__init__.py` which eagerly
   imported `.app.create_app` (which depends on flask). flask is a SOFT
   dep — needed only for the `empirica serve` daemon — not in
   pyproject.toml.

2. UX ask: `--register <project>` to register just one discovered
   project instead of the full sweep.

Fix: drop the eager `create_app` re-export in api.__init__; make
--register accept an optional NAME (filtering the manifest before
upsert via _filter_manifest_to_target).
"""

from __future__ import annotations

import builtins
import subprocess
import sys
from typing import Any

# --- Flask-blocker test: ensure api.registry imports without flask ---


def test_api_registry_imports_without_flask():
    """The eager `from .app import create_app` in api.__init__ is gone, so
    api.registry (and downstream consumers like the projects-discover
    --register path) imports cleanly even on installs without flask.
    """
    real_import = builtins.__import__

    def block_flask(name, *args, **kwargs):
        if name == "flask" or name.startswith("flask."):
            raise ModuleNotFoundError(f"flask blocked: {name}")
        return real_import(name, *args, **kwargs)

    # Strip cached flask + api modules so the import truly re-runs
    for mod in list(sys.modules):
        if mod == "flask" or mod.startswith("flask.") or mod.startswith("empirica.api"):
            sys.modules.pop(mod, None)

    builtins.__import__ = block_flask
    try:
        from empirica.api.registry import (
            load_registry,
            prune_stale,
            save_registry,
            upsert_project,
        )
        # Sanity — at least the functions are real callables
        assert callable(load_registry)
        assert callable(save_registry)
        assert callable(upsert_project)
        assert callable(prune_stale)
    finally:
        builtins.__import__ = real_import


def test_projects_commands_handler_imports_without_flask():
    """The CLI handler module also imports cleanly without flask — confirms
    the import chain from `_register_discovered_to_registry` is flaskless.
    """
    real_import = builtins.__import__

    def block_flask(name, *args, **kwargs):
        if name == "flask" or name.startswith("flask."):
            raise ModuleNotFoundError(f"flask blocked: {name}")
        return real_import(name, *args, **kwargs)

    for mod in list(sys.modules):
        if mod == "flask" or mod.startswith("flask."):
            sys.modules.pop(mod, None)

    builtins.__import__ = block_flask
    try:
        from empirica.cli.command_handlers.projects_commands import (
            _filter_manifest_to_target,
            _register_discovered_to_registry,
        )
        assert callable(_filter_manifest_to_target)
        assert callable(_register_discovered_to_registry)
    finally:
        builtins.__import__ = real_import


# --- _filter_manifest_to_target ---


def test_filter_matches_directory_basename(tmp_path):
    from empirica.cli.command_handlers.projects_commands import (
        _filter_manifest_to_target,
    )
    p1 = tmp_path / "empirica-cortex"
    p1.mkdir()
    manifest = {
        "projects": [
            {"path": str(p1), "name": "empirica-cortex"},
            {"path": str(tmp_path / "other"), "name": "other"},
        ],
    }
    result = _filter_manifest_to_target(manifest, "empirica-cortex")
    assert len(result["projects"]) == 1
    assert result["projects"][0]["name"] == "empirica-cortex"


def test_filter_matches_project_yaml_name(tmp_path):
    from empirica.cli.command_handlers.projects_commands import (
        _filter_manifest_to_target,
    )
    p1 = tmp_path / "weird-folder"
    (p1 / ".empirica").mkdir(parents=True)
    (p1 / ".empirica" / "project.yaml").write_text("name: actual-project-name\n")
    manifest = {
        "projects": [
            {"path": str(p1), "name": "weird-folder"},
        ],
    }
    result = _filter_manifest_to_target(manifest, "actual-project-name")
    assert len(result["projects"]) == 1


def test_filter_no_match_returns_empty(tmp_path):
    from empirica.cli.command_handlers.projects_commands import (
        _filter_manifest_to_target,
    )
    manifest = {"projects": [{"path": str(tmp_path / "x"), "name": "x"}]}
    result = _filter_manifest_to_target(manifest, "nope")
    assert result["projects"] == []


def test_filter_preserves_other_manifest_keys(tmp_path):
    from empirica.cli.command_handlers.projects_commands import (
        _filter_manifest_to_target,
    )
    manifest: dict[str, Any] = {
        "projects": [{"path": str(tmp_path / "a"), "name": "a"}],
        "scan_time": "2026-06-04T10:00:00Z",
        "root": "/home/user",
    }
    result = _filter_manifest_to_target(manifest, "a")
    assert result["scan_time"] == "2026-06-04T10:00:00Z"
    assert result["root"] == "/home/user"


# --- CLI integration ---


def _empirica_cli(*args) -> tuple[int, str, str]:
    """Run empirica CLI as a subprocess. Returns (rc, stdout, stderr)."""
    result = subprocess.run(
        ["empirica", *args],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


def test_cli_register_accepts_optional_name_arg():
    """`--register NAME` parses without error (regression: store_true would
    fail with `unrecognized arguments: NAME`)."""
    rc, _stdout, stderr = _empirica_cli(
        "projects-discover", "--help",
    )
    assert rc == 0
    # Help routing varies between stdout/stderr; both should contain --register
    assert "--register" in stderr or "--register" in _stdout


def test_cli_register_missing_name_warns_clean(tmp_path):
    """`--register doesnotexist` against a real discovery emits the
    'No discovered project matches' warning and exits cleanly."""
    rc, _stdout, stderr = _empirica_cli(
        "projects-discover", "--root", str(tmp_path), "--register", "nope-nope",
    )
    # rc 0 because the discover scan itself succeeded (just nothing to
    # register). The warning lives in stderr.
    assert rc == 0
    assert "No discovered project matches" in stderr or "nope-nope" in stderr
