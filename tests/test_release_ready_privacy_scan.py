"""release-ready's privacy scan: gitignore and exclusion matching by path component, and a secret scan that covers every file.

Found by the 2026-10-06 pipeline sweep (U1): substring matching made 'build' ignore 'rebuild_tools' and '.env' ignore '.env.production'
(files the project does not ignore were never reported); exclusion tested the ABSOLUTE path so a checkout under .../build/... skipped
everything (a false pass); the secret scan read the first 100 of several hundred files and still printed "No sensitive content".
Each project is built under tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.cli.command_handlers.release_commands import EpistemicReleaseAgent


def _agent(root: Path) -> EpistemicReleaseAgent:
    root.mkdir(parents=True, exist_ok=True)
    return EpistemicReleaseAgent(project_root=root, quick=True)


@pytest.mark.parametrize(
    ("patterns", "path", "ignored"),
    [
        (["build"], "build/out.o", True),
        (["build"], "rebuild_tools/x.py", False),  # the substring bug
        (["build"], "src/build/out.o", True),
        ([".env"], ".env", True),
        ([".env"], ".env.production", False),  # the substring bug
        (["*.pem"], "keys/server.pem", True),
        (["*.pem"], "keys/server.pem.pub", False),
        (["/dist"], "dist/a.whl", True),
        (["/dist"], "pkg/dist/a.whl", False),  # a leading slash anchors to the root
        (["docs/build"], "docs/build/index.html", True),
        (["docs/build"], "other/docs/build/index.html", False),  # a slash inside anchors it too
        (["*.log", "!keep.log"], "a.log", True),
        (["*.log", "!keep.log"], "keep.log", False),  # '!' re-includes
        (["!keep.log", "*.log"], "keep.log", True),  # and order decides, as in git
        ([], "anything", False),
        ([""], "anything", False),
    ],
)
def test_gitignore_matching_is_by_component_not_substring(tmp_path, patterns, path, ignored):
    assert _agent(tmp_path)._is_gitignored(Path(path), patterns) is ignored


def test_an_absolute_path_is_judged_relative_to_the_root(tmp_path):
    agent = _agent(tmp_path / "build" / "proj")  # the checkout itself sits under a directory called build
    assert agent._is_gitignored(agent.root / "src" / "app.py", ["build"]) is False
    assert agent._is_gitignored(agent.root / "build" / "app.o", ["build"]) is True


def test_exclusion_ignores_directories_above_the_project_root(tmp_path):
    """A checkout under .../dist/... used to skip EVERY file, so the forbidden-file scan passed falsely."""
    agent = _agent(tmp_path / "dist" / "venv" / "proj")
    (agent.root / ".env").write_text("SECRET=1\n")
    issues = agent._scan_forbidden_files([".env"], ["build", "dist", "venv"], [])
    assert issues == ["FORBIDDEN (not gitignored): .env"]


@pytest.mark.parametrize(
    ("rel", "excluded"),
    [
        ("node_modules/x/.env", True),
        ("pkg.egg-info/PKG-INFO", True),
        ("src/.env", False),
        ("src/dist.py", False),
        ("a/build/b", True),
    ],
)
def test_exclusion_matches_components_and_globs_inside_the_project(tmp_path, rel, excluded):
    agent = _agent(tmp_path)
    assert agent._is_excluded(agent.root / rel, ["node_modules", "*.egg-info", "build", "dist"]) is excluded


def test_a_forbidden_file_that_only_looks_ignored_by_substring_is_reported(tmp_path):
    agent = _agent(tmp_path)
    (agent.root / ".env.production").write_text("TOKEN=x\n")
    issues = agent._scan_forbidden_files([".env.production"], [], [".env"])
    assert issues == ["FORBIDDEN (not gitignored): .env.production"]


def test_the_secret_scan_covers_every_package_file_not_the_first_hundred(tmp_path):
    agent = _agent(tmp_path)
    package = agent.root / "empirica"
    package.mkdir()
    for i in range(250):
        (package / f"mod_{i:03d}.py").write_text("x = 1\n")
    (package / "mod_240.py").write_text('API_KEY = "sk-live-0123456789abcdef"\n')
    issues = agent._scan_hardcoded_secrets([r"sk-live-[0-9a-f]+"])
    assert issues == ["SECRET: empirica/mod_240.py"]


def test_the_secret_scan_is_stable_across_runs(tmp_path):
    agent = _agent(tmp_path)
    package = agent.root / "empirica"
    package.mkdir()
    for name in ("b.py", "a.py", "c.py"):
        (package / name).write_text('TOKEN = "hunter2hunter2"\n')
    first = agent._scan_hardcoded_secrets([r"hunter2hunter2"])
    assert (
        first
        == ["SECRET: empirica/a.py", "SECRET: empirica/b.py", "SECRET: empirica/c.py"]
        == agent._scan_hardcoded_secrets([r"hunter2hunter2"])
    )
