"""`--output json` must put exactly one JSON document on stdout.

project-init printed its result, then returned the same dict, which the
dispatcher printed again, so stdout held two documents and every `json.load`
consumer broke. session-create --auto-init had the same problem one layer up:
project-init's document came out ahead of session-create's own.

Runs the real CLI in a scratch repo. HOME is pinned along with the store,
because pinning EMPIRICA_SESSION_DB alone lets a session verb write the live
box's resolver context files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest


def _documents(text: str) -> list:
    decoder, i, docs = json.JSONDecoder(), 0, []
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return docs
        doc, i = decoder.raw_decode(text, i)
        docs.append(doc)


@pytest.fixture
def scratch(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
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
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, check=True, env=env)

    def run(*argv):
        return subprocess.run(
            [sys.executable, "-m", "empirica.cli", *argv],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    return run


def test_project_init_json_is_one_document(scratch):
    r = scratch("project-init", "--non-interactive", "--output", "json")
    docs = _documents(r.stdout)
    assert len(docs) == 1, f"stdout held {len(docs)} JSON documents: {r.stdout[:400]}"
    assert docs[0]["ok"] is True and docs[0]["project_id"]
    assert r.returncode == 0


def test_a_refused_init_is_one_document_and_exits_nonzero(scratch):
    """A second init is refused. Before the exit-code entry point, it printed
    ok:false and still exited 0, so a shell caller read the refusal as success."""
    assert scratch("project-init", "--non-interactive", "--output", "json").returncode == 0
    r = scratch("project-init", "--non-interactive", "--output", "json")
    docs = _documents(r.stdout)
    assert len(docs) == 1 and docs[0]["ok"] is False
    assert r.returncode == 1


def test_session_create_auto_init_json_is_one_document(scratch):
    """The same defect one layer up: auto-init let project-init print its own
    document ahead of session-create's."""
    r = scratch("session-create", "--ai-id", "scratch", "--auto-init", "--output", "json")
    docs = _documents(r.stdout)
    assert len(docs) == 1, f"stdout held {len(docs)} JSON documents: {r.stdout[:400]}"
    assert docs[0]["ok"] is True and docs[0]["session_id"]


# ── A missing store is repaired by a plain re-run, config untouched ──────────
# The only way past "already initialized" was --force, which rewrites
# project.yaml, even when the one thing missing was the database (a moved
# checkout, a deleted .empirica/sessions/, a newly pinned store).
def test_a_plain_rerun_recreates_a_missing_store_under_the_same_id(scratch, tmp_path):
    first = _documents(scratch("project-init", "--non-interactive", "--output", "json").stdout)[0]
    yaml_path = tmp_path / "repo" / ".empirica" / "project.yaml"
    before = yaml_path.read_bytes()
    (tmp_path / "sessions.db").unlink()

    r = scratch("project-init", "--non-interactive", "--output", "json")
    docs = _documents(r.stdout)
    assert r.returncode == 0 and len(docs) == 1
    assert docs[0]["ok"] is True and docs[0]["repaired"] == "store"
    assert docs[0]["project_id"] == first["project_id"], "the repair reuses the id, it does not mint one"
    assert yaml_path.read_bytes() == before, "project.yaml must not be touched"

    session = _documents(scratch("session-create", "--ai-id", "scratch", "--output", "json").stdout)[0]
    assert session["project_id"] == first["project_id"], "the repaired store serves the project"


def test_without_an_id_to_repair_against_it_still_refuses(scratch, tmp_path):
    """Positive control: the repair does not swallow the refusal it sits in front of."""
    scratch("project-init", "--non-interactive", "--output", "json")
    (tmp_path / "repo" / ".empirica" / "project.yaml").write_text("name: x\n")
    (tmp_path / "sessions.db").unlink()
    r = scratch("project-init", "--non-interactive", "--output", "json")
    docs = _documents(r.stdout)
    assert r.returncode == 1 and docs[0]["ok"] is False
    assert "already initialized" in docs[0]["error"]
