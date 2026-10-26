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
