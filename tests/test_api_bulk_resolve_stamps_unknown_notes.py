"""The REST BULK resolve route must stamp the note of every unknown it resolves (goal 4039b3c5, unknown 604b2ea9).

`PATCH /artifacts/{id}/resolve` was taught to mirror its change into the artifact's git note on 2026-10-19 (778d4bb6a), because notes are
the canonical log and a from-notes rebuild re-creates the row from them: a correction left only in sqlite is undone by the next rebuild.
`POST /artifacts/resolve` (the batch form the extension uses) was not given the same call, so an unknown it resolved came back open.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess

import pytest

from tests.test_api_resolve_parity import api_db  # noqa: F401  # pyright: ignore[reportUnusedImport]


@pytest.fixture
def repo_with_unknown_notes(tmp_path, api_db):  # noqa: F811
    def run(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, text=True, check=True)

    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (tmp_path / "f").write_text("x")
    run("add", "f")
    run("commit", "-qm", "i")
    head = run("rev-parse", "HEAD").stdout.strip()
    conn = sqlite3.connect(api_db)
    for uid in ("u-1", "u-2"):
        conn.execute(
            "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp, unknown_data) "
            "VALUES (?, 'p', 's', 'a question', 0.0, '{}')",
            (uid,),
        )
        payload = {"unknown_id": uid, "unknown": "a question", "resolved": False}
        run("notes", f"--ref=empirica/unknowns/{uid}", "add", "-f", "-m", json.dumps(payload), head)
    conn.commit()
    conn.close()
    return tmp_path


def _note(repo, uid):
    ref = f"empirica/unknowns/{uid}"
    listing = subprocess.run(
        ["git", "notes", f"--ref={ref}", "list"], cwd=repo, capture_output=True, text=True
    ).stdout.split()
    body = subprocess.run(["git", "cat-file", "-p", listing[0]], cwd=repo, capture_output=True, text=True).stdout
    return json.loads(body)


async def _bulk(body):
    from empirica.api.routes.artifacts import post_artifacts_resolve

    return await post_artifacts_resolve(body)


@pytest.mark.asyncio
async def test_bulk_resolving_an_unknown_stamps_its_note(repo_with_unknown_notes):
    out = await _bulk({"ids": ["u-1"], "resolved_by": "answered in finding X"})
    assert out["resolved"] == 1
    note = _note(repo_with_unknown_notes, "u-1")
    assert note["resolved"] is True and note["resolved_by"] == "answered in finding X"
    assert note["unknown"] == "a question", "the question text survives"


@pytest.mark.asyncio
async def test_an_unknown_the_batch_did_not_name_keeps_an_open_note(repo_with_unknown_notes):
    """Control: only the named unknown is stamped."""
    await _bulk({"ids": ["u-1"], "resolved_by": "x"})
    assert _note(repo_with_unknown_notes, "u-2")["resolved"] is False


@pytest.mark.asyncio
async def test_a_project_that_is_not_a_git_repo_still_resolves_in_sqlite(api_db):  # noqa: F811
    """Control: the stamp is best-effort, so no repository must not turn a correction into an error."""
    conn = sqlite3.connect(api_db)
    conn.execute(
        "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_timestamp, unknown_data) "
        "VALUES ('u-9', 'p', 's', 'q', 0.0, '{}')"
    )
    conn.commit()
    conn.close()
    out = await _bulk({"ids": ["u-9"], "resolved_by": "x"})
    assert out["resolved"] == 1
