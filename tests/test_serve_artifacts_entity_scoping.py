"""Engagement-scoped artifact resolution for the daemon /goals + /sources routes.

Ratified CRM/ERM boundary §4 (decision #2): an engagement's goals/sources must
resolve via ``entity_artifacts(entity_type='engagement')``, NOT the daemon
project's own artifacts. Without it the daemon leaked every project goal/source
for any engagement (the Contact-area data leak: an engagement with 0 linked
goals showed all 50 project goals). These pin ``_engagement_artifact_ids`` — the
cross-db resolver where that fix lives.
"""

from __future__ import annotations

from unittest.mock import patch

from empirica.api.routes import artifacts as art


class _FakeRepo:
    def __init__(self, links):
        self._links = links

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_entity_artifacts_by_entity(self, entity_type, entity_id, limit=500):
        return self._links


def _patch_repo(links):
    return patch(
        "empirica.data.repositories.workspace_db.WorkspaceDBRepository.open",
        return_value=_FakeRepo(links),
    )


def test_no_entity_scoping_returns_none():
    # None == "no scoping requested" → caller keeps project-wide behavior.
    assert art._engagement_artifact_ids(None, None, "goal") is None
    assert art._engagement_artifact_ids("engagement", None, "goal") is None
    assert art._engagement_artifact_ids(None, "e-1", "goal") is None


def test_unknown_entity_is_honest_empty_not_none():
    # An unknown entity type WITH an id must be a no-match (empty), never a silent
    # fall-through to the full project set.
    assert art._engagement_artifact_ids("organization", "o-1", "goal") == set()
    assert art._engagement_artifact_ids("project", "p-1", "source") == set()


def test_engagement_filters_by_artifact_type():
    links = [
        {"artifact_type": "goal", "artifact_id": "g1"},
        {"artifact_type": "goal", "artifact_id": "g2"},
        {"artifact_type": "source", "artifact_id": "s1"},
        {"artifact_type": "finding", "artifact_id": "f1"},
    ]
    with _patch_repo(links):
        assert art._engagement_artifact_ids("engagement", "e-1", "goal") == {"g1", "g2"}
    with _patch_repo(links):
        assert art._engagement_artifact_ids("engagement", "e-1", "source") == {"s1"}


def test_engagement_with_no_links_is_empty_not_leak():
    # THE leak fix: an engagement with 0 linked goals → empty set, so the route
    # returns honest-empty instead of every project goal.
    with _patch_repo([]):
        assert art._engagement_artifact_ids("engagement", "e-empty", "goal") == set()


def test_workspace_unreadable_is_honest_empty():
    # workspace.db absent/unreadable → never leak the full set.
    with patch(
        "empirica.data.repositories.workspace_db.WorkspaceDBRepository.open",
        side_effect=RuntimeError("workspace.db gone"),
    ):
        assert art._engagement_artifact_ids("engagement", "e-1", "goal") == set()


def test_links_missing_artifact_id_are_skipped():
    links = [
        {"artifact_type": "goal", "artifact_id": "g1"},
        {"artifact_type": "goal"},  # malformed — no artifact_id
        {"artifact_type": "goal", "artifact_id": None},
    ]
    with _patch_repo(links):
        assert art._engagement_artifact_ids("engagement", "e-1", "goal") == {"g1"}
