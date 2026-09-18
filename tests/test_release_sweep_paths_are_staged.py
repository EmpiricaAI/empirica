"""Every file the version sweep bumps exists, lives in the repo, and is staged.

The sweep list and the commit allowlist are two hand-kept copies of one set.
By 1.13.48 they had drifted both ways: the sweep named three repo files that
had been deleted (each a "Not found" warning nobody acted on) and one path on
the releasing box, and it bumped empirica_mcp/__init__.py without the
allowlist staging it, so the bump was left uncommitted in the tree.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RELEASE_PY = REPO / "scripts" / "release.py"


def _swept_paths(tmp_path, monkeypatch) -> list[Path]:
    """Run the sweep against an empty root; every path it names reports missing."""
    spec = importlib.util.spec_from_file_location("release_script_sweep", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    missing: list[str] = []
    monkeypatch.setattr(mod, "warning", lambda msg: missing.append(msg))
    monkeypatch.setattr(mod, "info", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "success", lambda *_a, **_k: None)

    mgr = mod.ReleaseManager.__new__(mod.ReleaseManager)
    mgr.repo_root = tmp_path
    mgr.version = "9.9.9"
    mgr.dry_run = True
    mgr.update_version_strings()

    prefix = "Not found: "
    paths = [Path(m[len(prefix) :]) for m in missing if m.startswith(prefix)]
    assert paths, "positive control: the sweep named no files at all"
    return paths


def test_the_sweep_touches_only_the_repo(tmp_path, monkeypatch):
    outside = [p for p in _swept_paths(tmp_path, monkeypatch) if not p.is_relative_to(tmp_path)]
    assert outside == [], f"a release must not write the box it runs on: {outside}"


def test_every_swept_file_exists(tmp_path, monkeypatch):
    rel = {p.relative_to(tmp_path) for p in _swept_paths(tmp_path, monkeypatch)}
    gone = sorted(str(r) for r in rel if not (REPO / r).exists())
    assert gone == [], f"sweep entries for files that no longer exist: {gone}"


def test_every_swept_file_is_staged_by_the_release_commit(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("release_script_allow", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    allow = mod.ReleaseManager._VERSION_COMMIT_PATHS

    def staged(rel: str) -> bool:
        return any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in allow)

    rel = {str(p.relative_to(tmp_path)) for p in _swept_paths(tmp_path, monkeypatch)}
    unstaged = sorted(r for r in rel if not staged(r))
    assert unstaged == [], f"bumped but never committed: {unstaged}"
