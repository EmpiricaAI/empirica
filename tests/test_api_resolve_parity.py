"""The API's resolve endpoint drifted behind the CLI's artifact lifecycles.

`PATCH /artifacts/{id}/resolve` refused findings, dead_ends and mistakes with a 422
reading *"has no resolve semantics"*. That was true when written and false from
migration 057 (finding resolution) and 060 (dead_end/mistake invalidation) onward.
The CLI gained those lifecycles; this parallel implementation did not.

Two-sources-of-truth drift, and the 422 is what made it survive: an explicit refusal
reads as a deliberate design decision, not as a gap. Daemon and extension seats
could not correct the three most numerous artifact types at all.

It also carried the same always-verified defect for assumptions that the CLI batch
path had (fixed 6bd55ff5f) — the identical bug in a second location, which is what
happens when two implementations of one contract are hand-maintained.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def api_db(tmp_path, monkeypatch):
    """A real DB with one row of each type, wired into the route module."""
    from empirica.api.routes import artifacts as A
    from empirica.data.session_database import SessionDatabase

    db_file = str(tmp_path / "t.db")
    seed = SessionDatabase(db_path=db_file)
    try:
        c = seed.conn
        c.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data) "
            "VALUES ('f-1','p','s','a claim',0.0,'{}')"
        )
        c.execute(
            "INSERT INTO project_dead_ends (id, project_id, session_id, approach, why_failed, "
            "created_timestamp, dead_end_data) VALUES ('d-1','p','s','an approach','it failed',0.0,'{}')"
        )
        c.execute(
            "INSERT INTO mistakes_made (id, session_id, mistake, why_wrong, created_timestamp, "
            "mistake_data, project_id) VALUES ('m-1','s','a slip','because',0.0,'{}','p')"
        )
        c.execute(
            "INSERT INTO assumptions (id, assumption, confidence, status, created_timestamp) "
            "VALUES ('a-1','a belief',0.5,'unverified',0.0)"
        )
        c.commit()
    finally:
        seed.close()

    monkeypatch.setattr(A, "get_cached_daemon_project", lambda: {"project_id": "p"})
    # Routes resolve scope per-request now (?project_id= / ?path=), so the seam is
    # `_resolve_project_dict` + `_open_db_for`, not the old single-project alias.
    monkeypatch.setattr(A, "_resolve_project_dict", lambda *a, **k: {"project_id": "p", "project_path": str(tmp_path)})
    monkeypatch.setattr(A, "_open_db_for", lambda _proj: SessionDatabase(db_path=db_file))
    return db_file


def _col(db_file, sql):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


async def _resolve(artifact_id, body):
    from empirica.api.routes.artifacts import resolve_artifact

    return await resolve_artifact(artifact_id, body)


# ── the drift ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_finding_can_be_resolved_at_all(api_db):
    """THE regression — this 422'd, so no daemon seat could resolve a finding."""
    out = await _resolve("f-1", {"resolved_by": "stale now"})

    assert out["ok"] is True
    assert _col(api_db, "SELECT is_resolved FROM project_findings WHERE id='f-1'") == 1


@pytest.mark.asyncio
async def test_a_finding_can_be_RETRACTED_not_merely_resolved(api_db):
    """The correction vocabulary must reach this surface too, or daemon seats can
    only ever record staleness — the exact degenerate state migration 061 exists
    to end."""
    await _resolve("f-1", {"resolved_by": "was false", "resolution_kind": "retracted"})

    assert _col(api_db, "SELECT resolution_kind FROM project_findings WHERE id='f-1'") == "retracted"


@pytest.mark.asyncio
async def test_an_invalid_resolution_kind_stores_null_rather_than_failing(api_db):
    await _resolve("f-1", {"resolved_by": "x", "resolution_kind": "nonsense"})

    assert _col(api_db, "SELECT is_resolved FROM project_findings WHERE id='f-1'") == 1
    assert _col(api_db, "SELECT resolution_kind FROM project_findings WHERE id='f-1'") is None


@pytest.mark.asyncio
async def test_dead_ends_are_INVALIDATED_not_resolved(api_db):
    """Migration 060 kept invalidation distinct on purpose: a dead-end is never
    'done', it is either still-constraining or wrong. dead_ends have no
    is_resolved column, so writing the wrong one would error or silently no-op."""
    out = await _resolve("d-1", {"resolved_by": "auto-captured tool noise"})

    assert out["ok"] is True
    assert _col(api_db, "SELECT is_invalidated FROM project_dead_ends WHERE id='d-1'") == 1
    assert (
        _col(api_db, "SELECT invalidation_reason FROM project_dead_ends WHERE id='d-1'") == "auto-captured tool noise"
    ), "the reason must be recorded, not just the flag"


@pytest.mark.asyncio
async def test_mistakes_are_invalidated_too(api_db):
    await _resolve("m-1", {"resolved_by": "prevention no longer applies"})

    assert _col(api_db, "SELECT is_invalidated FROM mistakes_made WHERE id='m-1'") == 1


# ── the always-verified defect, second location ───────────────────────


@pytest.mark.asyncio
async def test_an_assumption_defaults_to_falsified_not_verified(api_db):
    """Same defect as the CLI batch path carried (6bd55ff5f). A resolved-but-unstated
    assumption is far more often one that did NOT hold, and defaulting to verified
    manufactures confirmation the caller never claimed."""
    await _resolve("a-1", {"resolved_by": "checked"})

    assert _col(api_db, "SELECT status FROM assumptions WHERE id='a-1'") == "falsified"


@pytest.mark.asyncio
async def test_an_assumption_can_still_be_verified_explicitly(api_db):
    """The opposite event must stay reachable, or the fix just inverts the loss."""
    await _resolve("a-1", {"resolved_by": "checked", "verified": True})

    assert _col(api_db, "SELECT status FROM assumptions WHERE id='a-1'") == "verified"


# ── what must still refuse ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_genuinely_unresolvable_type_still_422s(api_db):
    """The fix must not turn the refusal into a rubber stamp — decisions have no
    resolve semantics and saying so is correct."""
    from fastapi import HTTPException

    from empirica.data.session_database import SessionDatabase

    conn = sqlite3.connect(api_db)
    conn.execute("INSERT INTO decisions (id, choice, rationale, created_timestamp) VALUES ('dec-1','c','r',0.0)")
    conn.commit()
    conn.close()
    assert SessionDatabase  # imported for schema side-effect above

    with pytest.raises(HTTPException) as exc:
        await _resolve("dec-1", {"resolved_by": "x"})
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_a_missing_artifact_still_404s(api_db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await _resolve("does-not-exist", {"resolved_by": "x"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_an_old_project_db_gets_422_with_the_migration_named_not_a_500(tmp_path, monkeypatch):
    """The daemon serves many projects at different schema vintages — its own module
    docstring calls that normal. A pre-057 project DB has no `is_resolved` column, so
    the UPDATE raises; surfacing that as a raw 500 reads as "the daemon is broken"
    rather than "this project needs migrating".
    """
    from fastapi import HTTPException

    from empirica.api.routes import artifacts as A

    db_file = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_file)
    # freshness: intentional-stale — a pre-057 schema is the POINT of this test,
    # which pins that an old project DB degrades to 422 rather than 500.
    conn.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY, finding TEXT)")
    conn.execute("INSERT INTO project_findings VALUES ('f-old','a pre-057 claim')")
    conn.commit()
    conn.close()

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(db_file)

        def close(self):
            self.conn.close()

    monkeypatch.setattr(A, "get_cached_daemon_project", lambda: {"project_id": "p"})
    monkeypatch.setattr(A, "_resolve_project_dict", lambda *a, **k: {"project_id": "p", "project_path": "/x"})
    monkeypatch.setattr(A, "_open_db_for", lambda _p: _DB())
    monkeypatch.setattr(A, "_resolve_artifact_by_id", lambda _db, _id: ("finding", "project_findings", "id"))

    with pytest.raises(HTTPException) as exc:
        await _resolve("f-old", {"resolved_by": "x"})

    assert exc.value.status_code == 422
    assert "057" in str(exc.value.detail), "the remedy must name the migration"


# ── multi-project reachability ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_foreign_project_artifact_is_correctable_via_project_id(tmp_path, monkeypatch):
    """THE regression, reported by a peer with a live 404.

    Reads honoured `?project_id=` (get_artifact used `_resolve_project_dict`);
    WRITES used the single-project `_open_db()` alias. One daemon serves the whole
    fleet, so every practice except the bound one had no correction path at all —
    the correction surface narrower than the read surface, a third time.

    Note the peer reported the capability as MISSING and it merely wasn't
    reachable from where they stood. For a shared daemon those are the same thing.
    """
    from empirica.api.routes import artifacts as A
    from empirica.data.session_database import SessionDatabase

    foreign = tmp_path / "other-practice"
    (foreign / ".empirica" / "sessions").mkdir(parents=True)
    db_file = str(foreign / ".empirica" / "sessions" / "sessions.db")
    seed = SessionDatabase(db_path=db_file)
    try:
        seed.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, "
            "finding_data, epistemic_source) VALUES ('far-1','other','s','a peer claim',0.0,'{}','search')"
        )
        seed.conn.commit()
    finally:
        seed.close()

    # The daemon is bound ELSEWHERE — exactly David's single-daemon setup.
    monkeypatch.setattr(A, "get_cached_daemon_project", lambda: {"project_id": "bound-elsewhere"})
    monkeypatch.setattr(
        A,
        "_resolve_project_dict",
        lambda pid, p: (
            {"project_id": "other", "project_path": str(foreign)}
            if (pid or p)
            else {"project_id": "bound-elsewhere", "project_path": "/nowhere"}
        ),
    )

    out = await _resolve("far-1", {"resolved_by": "was never true", "resolution_kind": "retracted"})

    assert out["ok"] is True
    assert _col(db_file, "SELECT resolution_kind FROM project_findings WHERE id='far-1'") == "retracted"


@pytest.mark.asyncio
async def test_provenance_is_correctable_on_a_foreign_project(tmp_path, monkeypatch):
    """`epistemic_source` correction is a PROVENANCE fix — distinct from
    `--kind`, which addresses a claim's truth or its type and neither of which
    touches where the belief came from."""
    from empirica.api.routes import artifacts as A
    from empirica.data.session_database import SessionDatabase

    foreign = tmp_path / "p2"
    (foreign / ".empirica" / "sessions").mkdir(parents=True)
    db_file = str(foreign / ".empirica" / "sessions" / "sessions.db")
    seed = SessionDatabase(db_path=db_file)
    try:
        seed.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, "
            "finding_data, epistemic_source) VALUES ('prov-1','other','s','cortex found X',0.0,'{}','search')"
        )
        seed.conn.commit()
    finally:
        seed.close()

    monkeypatch.setattr(A, "get_cached_daemon_project", lambda: {"project_id": "bound-elsewhere"})
    monkeypatch.setattr(
        A, "_resolve_project_dict", lambda pid, p: {"project_id": "other", "project_path": str(foreign)}
    )

    from empirica.api.routes.artifacts import patch_artifact

    out = await patch_artifact("prov-1", {"epistemic_source": "mixed"}, project_id="other")

    assert out["ok"] is True
    assert _col(db_file, "SELECT epistemic_source FROM project_findings WHERE id='prov-1'") == "mixed"
