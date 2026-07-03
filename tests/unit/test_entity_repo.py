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
    repo,
    entity_type,
    entity_id,
    display_name,
    description="",
    source_db="workspace",
    source_table="test",
    status="active",
):
    now = time.time()
    repo.conn.execute(
        """INSERT INTO entity_registry
           (entity_type, entity_id, display_name, description,
            source_db, source_table, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entity_type, entity_id, display_name, description, source_db, source_table, status, now, now),
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
            tmp_workspace_repo,
            "project",
            "p-1",
            "Foo",
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


class TestUpsertEntityMembership:
    """The write peer to get_entity_memberships — used by the ERM graduation
    path (engagement member_of organization, role='ticket_of')."""

    def test_writes_a_new_edge(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        m = repo.get_entity_memberships("engagement", "e-1")
        assert len(m["member_of"]) == 1
        edge = m["member_of"][0]
        assert edge["group_type"] == "organization" and edge["group_id"] == "o-1"
        assert edge["role"] == "ticket_of" and edge["left_at"] is None

    def test_idempotent_no_duplicate(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        assert len(repo.get_entity_memberships("engagement", "e-1")["member_of"]) == 1

    def test_re_upsert_updates_role_and_notes(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="member")
        repo.upsert_entity_membership(
            "engagement", "e-1", "organization", "o-1", role="ticket_of", notes="reclassified"
        )
        edge = repo.get_entity_memberships("engagement", "e-1")["member_of"][0]
        assert edge["role"] == "ticket_of" and edge["notes"] == "reclassified"

    def test_preserves_joined_at_on_conflict(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        first = repo.get_entity_memberships("engagement", "e-1")["member_of"][0]["joined_at"]
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        second = repo.get_entity_memberships("engagement", "e-1")["member_of"][0]["joined_at"]
        assert first == second

    def test_org_scoping_query(self, tmp_workspace_repo):
        """The R2 query: open engagements where org=X (left_at IS NULL)."""
        repo = tmp_workspace_repo
        _insert_entity(repo, "engagement", "e-1", "E1", status="active")
        _insert_entity(repo, "engagement", "e-2", "E2", status="active")
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        repo.upsert_entity_membership("engagement", "e-2", "organization", "o-2", role="ticket_of")
        members = repo.get_entity_memberships("organization", "o-1")["members"]
        assert len(members) == 1 and members[0]["entity_id"] == "e-1"


class TestCloseEntityMembership:
    def test_soft_close_stamps_left_at_not_delete(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        assert repo.close_entity_membership("engagement", "e-1", "organization", "o-1") is True
        # Excluded from active memberships...
        assert repo.get_entity_memberships("engagement", "e-1")["member_of"] == []
        # ...but the row still exists (auditable history).
        cur = repo._execute("SELECT left_at FROM entity_memberships WHERE entity_type='engagement' AND entity_id='e-1'")
        rows = cur.fetchall()
        assert len(rows) == 1 and rows[0]["left_at"] is not None

    def test_close_nonexistent_returns_false(self, tmp_workspace_repo):
        assert tmp_workspace_repo.close_entity_membership("engagement", "nope", "organization", "o-1") is False

    def test_close_is_idempotent(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        assert repo.close_entity_membership("engagement", "e-1", "organization", "o-1") is True
        assert repo.close_entity_membership("engagement", "e-1", "organization", "o-1") is False

    def test_re_upsert_reactivates_closed_edge(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        repo.close_entity_membership("engagement", "e-1", "organization", "o-1")
        repo.upsert_entity_membership("engagement", "e-1", "organization", "o-1", role="ticket_of")
        active = repo.get_entity_memberships("engagement", "e-1")["member_of"]
        assert len(active) == 1 and active[0]["left_at"] is None


class TestDeliberationRecord:
    """B6 — deliberation record: contributes_to edge (practitioner ↔ engagement)
    + attributed reads. Parallel to B4's occupies edge, so it inherits
    idempotency + soft-close from entity_memberships."""

    def test_empty_engagement_has_no_reads(self, tmp_workspace_repo):
        assert tmp_workspace_repo.get_deliberation("eng-1") == []

    def test_record_and_read_attributed(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        # Two practitioners occupying a practice, each contributing a read.
        repo.upsert_practitioner_entity("sess-A", "empirica", summary="A's tl;dr")
        repo.upsert_practitioner_entity("sess-B", "empirica", summary="B's tl;dr")
        repo.record_deliberation_read("sess-A", "eng-1", read_summary="lean ship")
        repo.record_deliberation_read("sess-B", "eng-1", read_summary="hold, risk")

        reads = repo.get_deliberation("eng-1")
        assert len(reads) == 2
        by_sess = {r["practitioner_session_id"]: r for r in reads}
        assert by_sess["sess-A"]["read_summary"] == "lean ship"
        # attribution is enriched from the practitioner's durable registry row (B4)
        assert by_sess["sess-A"]["practice_ai_id"] == "empirica"
        assert by_sess["sess-A"]["summary"] == "A's tl;dr"
        assert by_sess["sess-B"]["read_summary"] == "hold, risk"

    def test_read_scoped_to_engagement(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_practitioner_entity("sess-A", "empirica")
        repo.record_deliberation_read("sess-A", "eng-1", read_summary="on eng-1")
        repo.record_deliberation_read("sess-A", "eng-2", read_summary="on eng-2")
        assert len(repo.get_deliberation("eng-1")) == 1
        assert repo.get_deliberation("eng-1")[0]["read_summary"] == "on eng-1"
        assert repo.get_deliberation("eng-2")[0]["read_summary"] == "on eng-2"

    def test_re_record_updates_position_not_duplicates(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_practitioner_entity("sess-A", "empirica")
        repo.record_deliberation_read("sess-A", "eng-1", read_summary="first position")
        repo.record_deliberation_read("sess-A", "eng-1", read_summary="revised position")
        reads = repo.get_deliberation("eng-1")
        assert len(reads) == 1  # latest position wins, no duplicate edge
        assert reads[0]["read_summary"] == "revised position"

    def test_read_surfaces_without_registry_row(self, tmp_workspace_repo):
        # An attributed read is real even if the practitioner entity wasn't
        # persisted; attribution fields are None (LEFT JOIN, honest-empty).
        repo = tmp_workspace_repo
        repo.record_deliberation_read("sess-ghost", "eng-1", read_summary="unattributed read")
        reads = repo.get_deliberation("eng-1")
        assert len(reads) == 1
        assert reads[0]["practitioner_session_id"] == "sess-ghost"
        assert reads[0]["read_summary"] == "unattributed read"
        assert reads[0]["practice_ai_id"] is None
        assert reads[0]["summary"] is None

    def test_closed_read_excluded_but_auditable(self, tmp_workspace_repo):
        repo = tmp_workspace_repo
        repo.upsert_practitioner_entity("sess-A", "empirica")
        repo.record_deliberation_read("sess-A", "eng-1", read_summary="withdrawn")
        # Practitioner withdraws from the deliberation → soft-close the edge.
        assert repo.close_entity_membership("practitioner", "sess-A", "engagement", "eng-1") is True
        assert repo.get_deliberation("eng-1") == []
        # ...but the row persists (auditable history), consistent with B4 semantics.
        cur = repo._execute(
            "SELECT left_at FROM entity_memberships "
            "WHERE entity_type='practitioner' AND entity_id='sess-A' AND group_type='engagement'"
        )
        rows = cur.fetchall()
        assert len(rows) == 1 and rows[0]["left_at"] is not None
