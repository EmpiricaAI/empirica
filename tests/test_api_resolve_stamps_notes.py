"""The REST resolve route must reach git notes the way the CLI does (finding 3 of the release sweep).

`PATCH /artifacts/{id}/resolve` updated sqlite and Qdrant and never the note, for findings, unknowns, dead ends and mistakes
alike. Notes are the canonical log and a from-notes rebuild re-creates the row from them, so a correction made through the daemon
or the extension was undone by the next rebuild. The CLI paths were fixed for dead ends and mistakes (0c3d0f976) and already
stamped findings; this brings the route in line, using the project path the route has already resolved.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tests.test_api_resolve_parity import _resolve, api_db  # noqa: F401  (the fixture and helper are the parity test's)


@pytest.fixture
def project_repo(tmp_path, api_db, monkeypatch):  # noqa: F811
    from empirica.api.routes import artifacts as A

    def run(*a):
        return subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, text=True, check=True)

    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (tmp_path / "f").write_text("x")
    run("add", "f")
    run("commit", "-qm", "i")
    head = run("rev-parse", "HEAD").stdout.strip()
    for namespace, aid, payload in (
        ("findings", "f-1", {"finding_id": "f-1", "finding": "a claim", "is_resolved": False}),
        ("dead_ends", "d-1", {"dead_end_id": "d-1", "approach": "an approach", "why_failed": "it failed"}),
        ("mistakes", "m-1", {"mistake_id": "m-1", "mistake": "a slip", "why_wrong": "because"}),
    ):
        run("notes", f"--ref=empirica/{namespace}/{aid}", "add", "-f", "-m", json.dumps(payload), head)
    monkeypatch.setattr(A, "_resolve_project_dict", lambda *a, **k: {"project_id": "p", "project_path": str(tmp_path)})
    return tmp_path


def _note(repo, namespace, aid):
    ref = f"empirica/{namespace}/{aid}"
    listing = subprocess.run(
        ["git", "notes", f"--ref={ref}", "list"], cwd=repo, capture_output=True, text=True
    ).stdout.split()
    body = subprocess.run(["git", "cat-file", "-p", listing[0]], cwd=repo, capture_output=True, text=True).stdout
    return json.loads(body)


@pytest.mark.asyncio
async def test_resolving_a_dead_end_through_the_route_stamps_its_note(project_repo):
    await _resolve("d-1", {"resolved_by": "wrong: it works with flag Q"})
    note = _note(project_repo, "dead_ends", "d-1")
    assert note["is_invalidated"] is True and "flag Q" in note["invalidation_reason"]
    assert note["approach"] == "an approach", "the claim text survives"


@pytest.mark.asyncio
async def test_resolving_a_mistake_through_the_route_stamps_its_note(project_repo):
    await _resolve("m-1", {"resolved_by": "no longer applies"})
    assert _note(project_repo, "mistakes", "m-1")["is_invalidated"] is True


@pytest.mark.asyncio
async def test_resolving_a_finding_through_the_route_stamps_its_note(project_repo):
    await _resolve("f-1", {"resolved_by": "was false", "resolution_kind": "retracted"})
    note = _note(project_repo, "findings", "f-1")
    assert note["is_resolved"] is True and note["resolution_kind"] == "retracted" and note["finding"] == "a claim"


@pytest.mark.asyncio
async def test_an_artifact_nobody_resolved_keeps_a_valid_note(project_repo):
    await _resolve("d-1", {"resolved_by": "x"})
    assert not _note(project_repo, "mistakes", "m-1").get("is_invalidated"), (
        "control: only the named artifact is stamped"
    )


@pytest.mark.asyncio
async def test_a_project_that_is_not_a_git_repo_still_resolves_in_sqlite(api_db):  # noqa: F811
    """The stamp is best-effort: no repository must not turn a correction into an error."""
    out = await _resolve("d-1", {"resolved_by": "x"})
    assert out["ok"] is True
