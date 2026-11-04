"""empirica-mcp is removed, and nothing in the repo still points at it (David, 2026-11-04).

The server was a thin wrapper: all 79 of its tools ran the `empirica` CLI as a subprocess (69 distinct verbs). The epistemic loop needs hooks, which exist
only in Claude Code, so outside it the MCP could submit assessments that nothing enforces; GUI users use Cortex, ecodex uses vendored hooks, and every
harness can run a command. It cost a second surface to keep in step with every verb, in CI, nightly, release, docs and hook hints, so it went with no
deprecation period. Published versions stay installable on PyPI; 1.14.20 is the last.

This test is what keeps it gone: a hook hint, a doc or a CI step naming it again would send a reader to something that does not exist. History (the
CHANGELOG, the upgrade guides, the archive) is allowed to name it, and so is the uninstall cleanup of a registration a user already has.
`mcp__empirica-crm__*` is a different server and is not matched.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import tomllib

ROOT = Path(__file__).parent.parent

PATTERN = re.compile(r"empirica[-_]mcp|mcp__empirica__|mcp-list-tools|empirica\[mcp\]|\.\[[a-z,]*\bmcp\b[a-z,]*\]")

#: History and cleanup, where naming the removed package is the point.
ALLOWED = (
    "CHANGELOG.md",
    "docs/archive/",
    "docs/guides/UPGRADE_TO_",
    "tests/test_empirica_mcp_is_gone.py",
    "empirica/cli/command_handlers/claude_code_uninstall.py",
    "empirica/cli/command_handlers/doctor.py",  # detects and reports a leftover registration
    "tests/test_doctor_mcp_live_config.py",
    "tests/test_claude_code_uninstall.py",
)

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".empirica",
}
TEXT_SUFFIXES = {
    ".ini",
    ".py",
    ".md",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".sh",
    ".txt",
    ".cfg",
    ".nuspec",
    ".ps1",
    ".rb",
    ".example",
}


def _files():
    """Tracked files only: local state (.claude/settings.local.json, reflex logs, build output) is not the repository."""
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for rel in filter(None, listed.split("\0")):
        path = ROOT / rel
        if not path.is_file() or rel.startswith(ALLOWED):
            continue
        if (
            path.suffix in TEXT_SUFFIXES
            or path.name in {"Dockerfile", "Dockerfile.alpine", "Makefile", ".cursorrules"}
            or path.suffix == ".Dockerfile"
        ):
            yield rel, path


def test_the_package_directory_is_gone():
    assert not (ROOT / "empirica-mcp").exists()


def test_the_mcp_extra_and_scripts_are_gone_from_the_project_metadata():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "mcp" not in meta["project"].get("optional-dependencies", {})
    assert not any("mcp" in name for name in meta["project"].get("scripts", {}))


def test_the_mcp_list_tools_verb_is_gone():
    import pytest

    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    assert parser.parse_args(["doctor"]).command == "doctor"  # positive control: the parser parses a verb that exists
    with pytest.raises(SystemExit):
        parser.parse_args(["mcp-list-tools"])


def test_no_file_outside_history_names_the_removed_package():
    hits = []
    for rel, path in _files():
        in_release_notes = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if rel == "README.md" and line.startswith("## "):
                # The generated What's New section is release notes, which is history; every other README section is the product as it is.
                in_release_notes = line.startswith("## What's New")
            if in_release_notes:
                continue
            if PATTERN.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()[:110]}")
    assert not hits, "references to the removed empirica-mcp package:\n" + "\n".join(hits[:60])
