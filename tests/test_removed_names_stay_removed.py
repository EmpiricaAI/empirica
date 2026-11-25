"""A removed name does not come back as current (goal 95c3ee16).

empirica-mcp was removed in 1.14, yet a leftover install and stale text let an AI describe it as a live product. Every
name in config/removed_names.yaml is searched for across docs, skills, hooks, templates and code; a hit outside history
and the entry's allowed_in list fails, naming the file and what replaced the name.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = yaml.safe_load((ROOT / "empirica" / "config" / "removed_names.yaml").read_text())["removed"]
SCANNED = [ROOT / "docs", ROOT / "empirica", ROOT / "README.md", ROOT / "AGENTS.md"]
HISTORY = ("CHANGELOG.md", "/UPGRADE_TO_", "/_archive/", "__pycache__", "removed_names.yaml")


def _files():
    """Tracked files only: untracked logs and local archives are no one else's text."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "--", *[p.relative_to(ROOT).as_posix() for p in SCANNED]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for rel in tracked:
        path = ROOT / rel
        if path.suffix in (".md", ".py", ".yaml", ".yml", ".json", ".sh", ".txt") and path.is_file():
            if not any(h in "/" + rel for h in HISTORY):
                yield rel, path


FILES = list(_files())


@pytest.mark.parametrize("entry", REGISTRY, ids=lambda e: e["name"])
def test_a_removed_name_appears_only_where_allowed(entry):
    allowed = tuple(entry.get("allowed_in") or ())
    hits = [
        rel for rel, path in FILES if entry["name"] in path.read_text(errors="ignore") and not rel.startswith(allowed)
    ]
    assert not hits, f"{entry['name']} (removed {entry['removed_in']}; use {entry['replaced_by']}) reappears in: {hits}"
