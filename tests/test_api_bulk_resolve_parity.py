"""POST /artifacts/resolve (bulk) resolves findings, dead ends and mistakes exactly as PATCH /artifacts/{id}/resolve does (goal 58789c18).

The bulk route handled unknowns, assumptions and goals and answered everything else `skipped: no resolve semantics`: the same false statement the
single route carried until 2026-07-31, when migrations 057/060/061 had long since given findings, dead ends and mistakes a lifecycle. A daemon
or extension seat could not batch-correct the three most numerous artifact types, and the refusal read as deliberate. Both routes now call one
`_apply_resolution`, so they cannot drift again; this file pins that they agree.
"""

from __future__ import annotations

import sqlite3
from typing import cast

import pytest

from tests.test_api_resolve_parity import _col, _resolve, api_db  # noqa: F401  # pyright: ignore[reportUnusedImport]
from tests.test_api_resolve_stamps_notes import _note, project_repo  # noqa: F401  # pyright: ignore[reportUnusedImport]


async def _bulk(body):
    from empirica.api.routes.artifacts import post_artifacts_resolve

    return await post_artifacts_resolve(body)


def _seed_second_finding(db_file, fid="f-2"):
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data) "
        "VALUES (?, 'p', 's', 'another claim', 0.0, '{}')",
        (fid,),
    )
    conn.commit()
    conn.close()


FINDING_STATE = "SELECT is_resolved, resolution, resolution_kind, superseded_by FROM project_findings WHERE id = '{}'"


@pytest.mark.asyncio
async def test_a_finding_resolved_in_bulk_ends_in_the_same_state_as_one_resolved_singly(api_db):  # noqa: F811
    _seed_second_finding(api_db)
    await _resolve("f-2", {"resolved_by": "was false", "resolution_kind": "retracted"})
    out = await _bulk({"items": [{"id": "f-1", "resolution_kind": "retracted"}], "resolved_by": "was false"})
    assert (
        out["resolved"] == 1 and out["results"][0]["type"] == "finding" and out["results"][0]["outcome"] == "resolved"
    )

    def state(fid):
        conn = sqlite3.connect(api_db)
        try:
            return conn.execute(FINDING_STATE.format(fid)).fetchone()
        finally:
            conn.close()

    assert state("f-1") == state("f-2") == (1, "was false", "retracted", None)


@pytest.mark.asyncio
async def test_a_dead_end_and_a_mistake_resolved_in_bulk_are_invalidated_not_resolved(api_db):  # noqa: F811
    out = await _bulk({"ids": ["d-1", "m-1"], "resolved_by": "no longer applies"})
    assert out["resolved"] == 2 and out["skipped"] == 0, out
    assert _col(api_db, "SELECT is_invalidated FROM project_dead_ends WHERE id='d-1'") == 1
    assert _col(api_db, "SELECT invalidation_reason FROM mistakes_made WHERE id='m-1'") == "no longer applies"


@pytest.mark.asyncio
async def test_a_finding_can_be_superseded_in_bulk(api_db):  # noqa: F811
    _seed_second_finding(api_db)
    await _bulk(
        {"items": [{"id": "f-1", "superseded_by": "f-2", "resolution_kind": "superseded"}], "resolved_by": "replaced"}
    )
    assert _col(api_db, "SELECT superseded_by FROM project_findings WHERE id='f-1'") == "f-2"


@pytest.mark.asyncio
async def test_one_bad_item_fails_alone_and_the_rest_of_the_batch_still_resolves(api_db):  # noqa: F811
    out = await _bulk(
        {
            "items": [
                {"id": "f-1", "resolution_kind": "no-such-kind"},
                {"id": "d-1"},
                {"id": "missing-id"},
            ],
            "resolved_by": "x",
        }
    )
    by_id = {r.get("id"): r for r in cast(list[dict], out["results"])}
    assert by_id["f-1"]["outcome"] == "failed" and "kind" in by_id["f-1"]["reason"].lower()
    assert not _col(api_db, "SELECT is_resolved FROM project_findings WHERE id='f-1'"), "the refused finding stays open"
    assert by_id["d-1"]["outcome"] == "resolved"
    assert by_id["missing-id"]["outcome"] == "not_found"
    assert out["resolved"] == 1


@pytest.mark.asyncio
async def test_assumptions_still_default_to_falsified_and_verified_true_is_borne_out(api_db):  # noqa: F811
    """Control: the types the bulk route already handled are unchanged."""
    out = await _bulk({"items": [{"id": "a-1", "verified": True}], "resolved_by": "x"})
    assert out["results"][0]["status"] == "verified"
    assert _col(api_db, "SELECT status FROM assumptions WHERE id='a-1'") == "verified"


@pytest.mark.asyncio
async def test_bulk_resolving_a_dead_end_or_a_finding_stamps_the_note(project_repo):  # noqa: F811
    await _bulk(
        {
            "items": [{"id": "d-1"}, {"id": "f-1", "resolution_kind": "retracted"}],
            "resolved_by": "wrong: it works with flag Q",
        }
    )
    assert _note(project_repo, "dead_ends", "d-1")["is_invalidated"] is True
    note = _note(project_repo, "findings", "f-1")
    assert note["is_resolved"] is True and note["resolution_kind"] == "retracted" and note["finding"] == "a claim"


# ── pre-publish sweep: what the batch says when an item cannot be resolved (E1, E3) ──────────────────────────────


def _routes():
    from empirica.api.routes import artifacts as routes

    return routes


def _raising(monkeypatch, by_id):
    """Make _apply_resolution raise per artifact id (the real function for any id not listed)."""
    routes = _routes()
    real = routes._apply_resolution

    def fake(db, project, artifact_type, artifact_id, body):
        if artifact_id in by_id:
            raise by_id[artifact_id]
        return real(db, project, artifact_type, artifact_id, body)

    monkeypatch.setattr(routes, "_apply_resolution", fake)


@pytest.mark.asyncio
async def test_an_old_schema_item_is_failed_with_its_migration_hint_not_skipped(api_db, monkeypatch):  # noqa: F811
    """The single route returns the migration hint as a 422; the bulk route used to file the same 422 under 'no resolve semantics'."""
    from fastapi import HTTPException

    stale = HTTPException(
        status_code=422,
        detail={
            "error": "this project's database predates the finding lifecycle",
            "hint": "Missing columns from migration 057.",
        },
    )
    _raising(monkeypatch, {"f-1": stale})
    out = await _bulk({"ids": ["f-1", "d-1"], "resolved_by": "x"})
    by_id = {r["id"]: r for r in cast(list[dict], out["results"])}
    assert by_id["f-1"]["outcome"] == "failed" and "predates" in by_id["f-1"]["reason"]
    assert "migration 057" in by_id["f-1"]["hint"]
    assert by_id["d-1"]["outcome"] == "resolved", "one old-schema item does not stop the rest"
    assert (out["failed"], out["skipped"]) == (1, 0)


@pytest.mark.asyncio
async def test_an_artifact_type_that_genuinely_has_no_resolve_semantics_is_still_skipped(api_db, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    _raising(
        monkeypatch, {"f-1": HTTPException(status_code=422, detail="Artifact type 'decision' has no resolve semantics")}
    )
    out = await _bulk({"ids": ["f-1"], "resolved_by": "x"})
    assert out["results"][0]["outcome"] == "skipped" and out["results"][0]["reason"] == "no resolve semantics"


@pytest.mark.asyncio
async def test_an_unexpected_error_on_one_item_fails_that_item_and_the_batch_still_answers(api_db, monkeypatch):  # noqa: F811
    """A locked database used to escape as a 500 with no results, after earlier items had already committed."""
    _raising(monkeypatch, {"d-1": sqlite3.OperationalError("database is locked")})
    out = await _bulk({"ids": ["m-1", "d-1", "a-1"], "resolved_by": "x"})
    by_id = {r["id"]: r for r in cast(list[dict], out["results"])}
    assert by_id["d-1"]["outcome"] == "failed" and "database is locked" in by_id["d-1"]["reason"]
    assert by_id["m-1"]["outcome"] == "resolved" and by_id["a-1"]["outcome"] == "resolved"
    assert out["resolved"] == 2 and out["failed"] == 1


@pytest.mark.asyncio
async def test_a_non_string_id_fails_that_item_and_the_batch_still_answers(api_db):  # noqa: F811
    out = await _bulk({"items": [{"id": ["f-1"]}, {"id": {"a": 1}}, {"id": "d-1"}], "resolved_by": "x"})
    outcomes = [r["outcome"] for r in cast(list[dict], out["results"])]
    assert outcomes == ["failed", "failed", "resolved"], out
    assert "string" in out["results"][0]["reason"]


@pytest.mark.asyncio
async def test_a_failed_items_partial_write_is_rolled_back_and_not_committed_by_the_next_item(api_db, monkeypatch):  # noqa: F811
    """The rollback must be real: the failing item has WRITTEN (uncommitted) before it raises, and the next item's commit would otherwise
    persist it."""
    routes = _routes()
    real = routes._apply_resolution

    def half_done(db, project, artifact_type, artifact_id, body):
        if artifact_id == "f-1":
            db.conn.execute("UPDATE project_findings SET is_resolved = 1, resolution = 'half' WHERE id = 'f-1'")
            raise sqlite3.OperationalError("disk I/O error")
        return real(db, project, artifact_type, artifact_id, body)

    monkeypatch.setattr(routes, "_apply_resolution", half_done)
    out = await _bulk({"ids": ["f-1", "d-1"], "resolved_by": "x"})
    assert [r["outcome"] for r in cast(list[dict], out["results"])] == ["failed", "resolved"]
    assert not _col(api_db, "SELECT is_resolved FROM project_findings WHERE id='f-1'"), (
        "the half-written update must not persist"
    )
