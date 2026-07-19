"""Release commits stage an explicit allowlist — never `git add -A`.

Regression guard for the 1.12.28 ERM-sweep: the release runs on the SHARED
develop working tree, so a broad `git add -A` sweeps a concurrent session's
uncommitted work into the release commit. release.py must only ever stage the
version/packaging allowlist (+ CHANGELOG for the bump commit).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

RELEASE_PY = Path(__file__).parent.parent / "scripts" / "release.py"


def _load_release_module():
    spec = importlib.util.spec_from_file_location("release_script", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_release_never_uses_git_add_dash_A():
    """No `git add -A` / `git add .` anywhere in the release script."""
    src = RELEASE_PY.read_text()
    # match a git "add" argv element followed by a broad "-A" or "." element
    assert not re.search(r'["\']add["\']\s*,\s*["\'](-A|\.|--all)["\']', src), (
        "release.py must stage an explicit allowlist, never `git add -A/./--all` (the shared-tree sweep hazard)"
    )


def test_version_commit_paths_are_explicit_and_include_pyproject():
    mod = _load_release_module()
    paths = mod.ReleaseManager._VERSION_COMMIT_PATHS
    assert isinstance(paths, tuple) and len(paths) >= 10
    assert "pyproject.toml" in paths
    assert "-A" not in paths and "." not in paths


def test_staged_release_paths_filters_to_existing(tmp_path, monkeypatch):
    mod = _load_release_module()
    mgr = mod.ReleaseManager(dry_run=True)
    monkeypatch.setattr(mgr, "repo_root", tmp_path)
    # only create two of the allowlisted files
    (tmp_path / "pyproject.toml").write_text("x")
    (tmp_path / "README.md").write_text("x")
    out = mgr._staged_release_paths("CHANGELOG.md")  # CHANGELOG absent → filtered out
    assert set(out) == {"pyproject.toml", "README.md"}
    assert "CHANGELOG.md" not in out  # doesn't exist → not staged (won't fail git add)


def test_commit_flag_stored_on_manager():
    """--commit wiring stores the flag on the manager."""
    mod = _load_release_module()
    mgr = mod.ReleaseManager(dry_run=True, commit_bump=True)
    assert mgr.commit_bump is True
    mgr_default = mod.ReleaseManager(dry_run=True)
    assert mgr_default.commit_bump is False
