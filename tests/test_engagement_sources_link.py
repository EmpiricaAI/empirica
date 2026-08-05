"""GET + POST /api/v1/engagements/{id}/sources — attach a source to an engagement.

Goal 1aa0988d described this as "mirroring the existing GET
/api/v1/engagements/{id}/sources". That GET did not exist — only `/tasks` did —
so the premise had decayed between the goal being written and being worked, and
both halves are new here.

The load-bearing behaviour is idempotency — and the key is NARROWER than the goal
specified. `entity_artifacts` enforces
`UNIQUE(artifact_type, artifact_id, entity_type, entity_id)`: relationship is an
attribute of the one edge, not part of its identity. So an engagement holds a
given source once, not once per relationship.

I asserted the opposite while writing this file — that the repository's
`IntegrityError` guard was unreachable because the PK is a fresh uuid4 — and
these tests refuted it on first run. The constraint is real and fires. What does
NOT hold is the goal's stated key, so the route surfaces that as an explicit 409
naming the existing relationship rather than delivering a narrower contract
silently.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.api.routes.engagements import _SOURCE_RELATIONSHIPS
from empirica.data.repositories.workspace_db import WorkspaceDBRepository, _ensure_workspace_schema


@pytest.fixture
def repo() -> WorkspaceDBRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    return WorkspaceDBRepository(conn)


def _link(repo, engagement_id, source_id, relationship="sourced_from"):
    return repo.add_entity_artifact(
        artifact_id=source_id,
        artifact_type="source",
        artifact_source="/tmp/practice",
        entity_type="engagement",
        entity_id=engagement_id,
        relationship=relationship,
        engagement_id=engagement_id,
    )


def test_relationship_vocabulary_is_closed():
    assert set(_SOURCE_RELATIONSHIPS) == {"sourced_from", "produced", "cited"}


def test_links_are_readable_back_for_the_engagement(repo):
    _link(repo, "e-1", "src-a")
    _link(repo, "e-1", "src-b", "cited")
    _link(repo, "e-2", "src-c")
    rows = repo.get_entity_artifacts_by_entity("engagement", "e-1")
    assert {r["artifact_id"] for r in rows} == {"src-a", "src-b"}


def test_non_source_artifacts_are_not_returned_as_sources(repo):
    """The engagement carries findings too — the sources view must not mix them in."""
    _link(repo, "e-1", "src-a")
    repo.add_entity_artifact(
        artifact_id="f-1",
        artifact_type="finding",
        artifact_source="/tmp/practice",
        entity_type="engagement",
        entity_id="e-1",
    )
    rows = [r for r in repo.get_entity_artifacts_by_entity("engagement", "e-1") if r["artifact_type"] == "source"]
    assert {r["artifact_id"] for r in rows} == {"src-a"}


def test_repeat_link_is_rejected_by_the_schema(repo):
    """The repository IS idempotent — `UNIQUE(artifact_type, artifact_id,
    entity_type, entity_id)` fires and `add_entity_artifact` returns None."""
    first = _link(repo, "e-1", "src-a")
    second = _link(repo, "e-1", "src-a")
    assert first and second is None


def test_relationship_is_not_part_of_the_identity(repo):
    """The gap between the goal and the schema, pinned.

    Goal 1aa0988d asked for idempotency on (engagement, source, relationship) so
    a source could be both `cited` and `produced`. The schema excludes
    relationship from the key, so the second link is rejected outright. The route
    turns this into a 409 that names the existing relationship instead of a bare
    integrity failure. If workspace later widens the key, this test flips and is
    the signal to relax the 409."""
    assert _link(repo, "e-1", "src-a", "sourced_from")
    assert _link(repo, "e-1", "src-a", "produced") is None


def test_unknown_engagement_reads_empty_not_error(repo):
    """Honest-empty, same contract as /tasks — and genuinely 'no links' rather
    than 'looked in the wrong place': the read has no second scope to drift."""
    assert repo.get_entity_artifacts_by_entity("engagement", "e-nope") == []
