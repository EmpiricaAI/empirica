"""A project root whose own config cannot name the project is said so, not hidden.

session-create lets the cwd's `.empirica/project.yaml` outrank the context
files. When that file exists but cannot be parsed, or carries no project_id, the
fallbacks answer instead: from a stale context file on a lived-in box, or with no
project at all. Measured in a scratch repo: both cases printed `ok: true` with
`project_id: null` and nothing else in the JSON.

Runs the real CLI with HOME pinned beside the store, so no context file exists
and no verb can write the live box's.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest


@pytest.fixture
def repo(tmp_path):
    root, home = tmp_path / "repo", tmp_path / "home"
    root.mkdir()
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
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=root, check=True, env=env)

    def run(*argv):
        r = subprocess.run(
            [sys.executable, "-m", "empirica.cli", *argv],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return r, json.JSONDecoder().raw_decode(r.stdout.lstrip())[0]

    _r, init = run("project-init", "--non-interactive", "--output", "json")
    assert init["ok"] is True
    return root, run


def _create(run):
    _r, out = run("session-create", "--ai-id", "scratch", "--output", "json")
    assert out["ok"] is True
    return out


def test_a_healthy_project_yaml_binds_and_warns_nothing(repo):
    """Positive control: the warning is not simply always present."""
    _root, run = repo
    out = _create(run)
    assert out["project_id"], "a readable project.yaml names the project"
    assert "project_root_warning" not in out


def test_an_unparseable_project_yaml_is_named(repo):
    root, run = repo
    (root / ".empirica" / "project.yaml").write_text("name: x\nproject_id: [unclosed\n")
    out = _create(run)
    warning = out.get("project_root_warning")
    assert warning, f"a broken project.yaml bound {out['project_id']!r} with no warning"
    assert "could not be parsed" in warning["warning"]
    assert "NO project" in warning["warning"], "with no context file, nothing else can answer"


def test_a_project_yaml_without_an_id_is_named(repo):
    root, run = repo
    (root / ".empirica" / "project.yaml").write_text("name: x\n")
    warning = _create(run).get("project_root_warning")
    assert warning and "has no project_id" in warning["warning"]
