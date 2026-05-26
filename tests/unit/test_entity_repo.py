"""
Unit tests for WorkspaceDBRepository entity_* methods (Phase 2 of the
practice-model proposal — entity CLI surface backing).

Uses a tmpdir-isolated DB so tests don't depend on the user's actual
~/.empirica/workspace/workspace.db state.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from empirica.data.repositories.workspace_db import WorkspaceDBRepository


@pytest.fixture
def tmp_workspace_repo(tmp_path):
    """Return an open repo backed by a fresh tmp workspace.db."""
    db_path = tmp_path / "workspace.db"
    # Force the repo to use our tmp path
    with patch(
        "empirica.data.repositories.workspace_db._get_workspace_db_path",
        return_value=db_path,
    ):
        repo = WorkspaceDBRepository.open()
        yield repo
        repo.close()


def _insert_entity(
    repo, entity_type, entity_id, display_name,
    description="", source_db="workspace", source_table="test",
    status="active",
):
    now = time.time()
    repo.conn.execute(
        """INSERT INTO entity_registry
           (entity_type, entity_id, display_name, description,
            source_db, source_table, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entity_type, entity_id, display_name, description,
         source_db, source_table, status, now, now),
    )
    repo.conn.commit()


def _insert_membership(repo, entity_type, entity_id, group_type, group_id, role="member"):
    now = time.time()
    repo.conn.execute(
        """INSERT INTO entity_memberships
           (entity_type, entity_id, group_type, group_id,
            role, joined_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entity_type, entity_id, group_type, group_id, role, now, now),
    )
    repo.conn.commit()


class TestListEntities:
    def test_empty_db_returns_empty_list(self, tmp_workspace_repo):
        assert tmp_workspace_repo.list_entities() == []

    def test_no_filter_returns_active_by_default(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "p-1", "Project One")
        _insert_entity(tmp_workspace_repo, "project", "p-2", "Project Two", status="archived")
        results = tmp_workspace_repo.list_entities()
        assert len(results) == 1
        assert results[0]["entity_id"] == "p-1"

    def test_filter_by_type(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "p-1", "P")
        _insert_entity(tmp_workspace_repo, "contact", "c-1", "C")
        projects = tmp_workspace_repo.list_entities(entity_type="project")
        contacts = tmp_workspace_repo.list_entities(entity_type="contact")
        assert len(projects) == 1 and projects[0]["entity_type"] == "project"
        assert len(contacts) == 1 and contacts[0]["entity_type"] == "contact"

    def test_status_all_includes_archived(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "p-1", "P", status="active")
        _insert_entity(tmp_workspace_repo, "project", "p-2", "P", status="archived")
        all_results = tmp_workspace_repo.list_entities(status="all")
        assert len(all_results) == 2

    def test_limit_caps_results(self, tmp_workspace_repo):
        for i in range(5):
            _insert_entity(tmp_workspace_repo, "project", f"p-{i}", f"P{i}")
        assert len(tmp_workspace_repo.list_entities(limit=2)) == 2


class TestGetEntity:
    def test_returns_none_for_missing(self, tmp_workspace_repo):
        assert tmp_workspace_repo.get_entity("project", "missing") is None

    def test_exact_match(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "p-abc123", "P")
        got = tmp_workspace_repo.get_entity("project", "p-abc123")
        assert got is not None and got["display_name"] == "P"

    def test_prefix_match_when_unique(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "p-abc12345", "P")
        got = tmp_workspace_repo.get_entity("project", "p-abc")
        assert got is not None and got["entity_id"] == "p-abc12345"

    def test_ambiguous_prefix_returns_none(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "p-abc1", "P1")
        _insert_entity(tmp_workspace_repo, "project", "p-abc2", "P2")
        # Both match 'p-abc' — ambiguous, expect None
        assert tmp_workspace_repo.get_entity("project", "p-abc") is None


class TestSearchEntities:
    def test_matches_display_name_case_insensitive(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "contact", "c-1", "Adriaan Schakel")
        results = tmp_workspace_repo.search_entities("adriaan")
        assert len(results) == 1
        assert results[0]["display_name"] == "Adriaan Schakel"

    def test_matches_description(self, tmp_workspace_repo):
        _insert_entity(
            tmp_workspace_repo, "project", "p-1", "Foo",
            description="empirica-outreach voice loader fix",
        )
        results = tmp_workspace_repo.search_entities("voice loader")
        assert len(results) == 1

    def test_type_filter_narrows(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "p-1", "MastersOfDirt")
        _insert_entity(tmp_workspace_repo, "organization", "o-1", "MastersOfDirt")
        proj_results = tmp_workspace_repo.search_entities("Masters", entity_type="project")
        assert len(proj_results) == 1
        assert proj_results[0]["entity_type"] == "project"


class TestMemberships:
    def test_empty_when_no_edges(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "p-1", "P")
        m = tmp_workspace_repo.get_entity_memberships("project", "p-1")
        assert m == {"member_of": [], "members": []}

    def test_returns_outgoing_and_incoming(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "contact", "c-1", "C")
        _insert_entity(tmp_workspace_repo, "engagement", "e-1", "E")
        _insert_entity(tmp_workspace_repo, "organization", "o-1", "O")
        _insert_membership(tmp_workspace_repo, "contact", "c-1", "engagement", "e-1")
        _insert_membership(tmp_workspace_repo, "engagement", "e-1", "organization", "o-1")
        # contact c-1 → engagement e-1 (outgoing)
        c_m = tmp_workspace_repo.get_entity_memberships("contact", "c-1")
        assert len(c_m["member_of"]) == 1 and len(c_m["members"]) == 0
        # engagement e-1 has c-1 (incoming) AND o-1 (outgoing)
        e_m = tmp_workspace_repo.get_entity_memberships("engagement", "e-1")
        assert len(e_m["member_of"]) == 1 and len(e_m["members"]) == 1


class TestWalkEntityGraph:
    def test_missing_root_returns_none(self, tmp_workspace_repo):
        result = tmp_workspace_repo.walk_entity_graph("project", "missing", max_depth=2)
        assert result["root"] is None
        assert result["nodes"] == [] and result["edges"] == []

    def test_isolated_node_returns_itself(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "p-1", "P")
        result = tmp_workspace_repo.walk_entity_graph("project", "p-1", max_depth=2)
        assert result["root"] is not None
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["depth"] == 0
        assert result["edges"] == []
        assert result["truncated"] is False

    def test_bfs_traverses_two_hops(self, tmp_workspace_repo):
        # Triangle: c-1 → e-1 → o-1
        _insert_entity(tmp_workspace_repo, "contact", "c-1", "C")
        _insert_entity(tmp_workspace_repo, "engagement", "e-1", "E")
        _insert_entity(tmp_workspace_repo, "organization", "o-1", "O")
        _insert_membership(tmp_workspace_repo, "contact", "c-1", "engagement", "e-1")
        _insert_membership(tmp_workspace_repo, "engagement", "e-1", "organization", "o-1")
        result = tmp_workspace_repo.walk_entity_graph("contact", "c-1", max_depth=2)
        assert len(result["nodes"]) == 3
        depths = {n["entity_id"]: n["depth"] for n in result["nodes"]}
        assert depths == {"c-1": 0, "e-1": 1, "o-1": 2}

    def test_cycle_protection(self, tmp_workspace_repo):
        # a → b, b → a (cycle); walk shouldn't loop forever
        _insert_entity(tmp_workspace_repo, "project", "a", "A")
        _insert_entity(tmp_workspace_repo, "project", "b", "B")
        _insert_membership(tmp_workspace_repo, "project", "a", "project", "b")
        _insert_membership(tmp_workspace_repo, "project", "b", "project", "a")
        result = tmp_workspace_repo.walk_entity_graph("project", "a", max_depth=10)
        # Two unique nodes despite the cycle
        assert len(result["nodes"]) == 2

    def test_depth_zero_returns_root_only(self, tmp_workspace_repo):
        _insert_entity(tmp_workspace_repo, "project", "a", "A")
        _insert_entity(tmp_workspace_repo, "project", "b", "B")
        _insert_membership(tmp_workspace_repo, "project", "a", "project", "b")
        result = tmp_workspace_repo.walk_entity_graph("project", "a", max_depth=0)
        assert len(result["nodes"]) == 1
        assert result["truncated"] is True
