"""CCR-2 (prop_kgrbsvrnfvab): org→org parentage for GET /api/v1/entities.

get_org_parent_map() resolves each org's parent org from active org→org
membership edges, so the extension org-tree can render live parents. Parentage
keys on the STRUCTURAL org→org edge (both ends organization, active) — NOT a
role string, because role is a free-text verb in entity-link (verified: existing
edges use 'member' / 'context' / 'ticket_of', no 'member_of' convention).
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from empirica.data.repositories.workspace_db import WorkspaceDBRepository, _ensure_workspace_schema


@pytest.fixture
def repo() -> WorkspaceDBRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    return WorkspaceDBRepository(conn)


def _membership(repo, etype, eid, gtype, gid, role="member_of", joined=None, left_at=None):
    """Insert a membership edge directly (lets us set joined_at / left_at for tests)."""
    now = joined if joined is not None else time.time()
    repo._execute(
        """INSERT INTO entity_memberships
           (entity_type, entity_id, group_type, group_id, role, joined_at, left_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (etype, eid, gtype, gid, role, now, left_at, now),
    )


def test_empty_when_no_org_edges(repo):
    assert repo.get_org_parent_map() == {}


def test_maps_active_org_to_parent(repo):
    _membership(repo, "organization", "brand-a", "organization", "umbrella")
    assert repo.get_org_parent_map() == {"brand-a": "umbrella"}


def test_ignores_non_org_edges(repo):
    # contact→engagement and engagement→org are not org→org parentage.
    _membership(repo, "contact", "c1", "engagement", "e1", role="member")
    _membership(repo, "engagement", "e1", "organization", "umbrella", role="ticket_of")
    assert repo.get_org_parent_map() == {}


def test_ignores_closed_edges(repo):
    # A soft-closed edge (left_at set) must not resolve as a live parent.
    _membership(repo, "organization", "brand-a", "organization", "old-parent", left_at=time.time())
    assert repo.get_org_parent_map() == {}


def test_structural_not_role_filtered(repo):
    # role is a free-text verb — parentage resolves regardless of the role value.
    _membership(repo, "organization", "brand-a", "organization", "umbrella", role="subsidiary")
    assert repo.get_org_parent_map() == {"brand-a": "umbrella"}


def test_most_recent_edge_wins(repo):
    # If an org somehow has two active parent edges, the latest joined_at wins.
    _membership(repo, "organization", "brand-a", "organization", "old", joined=100.0)
    _membership(repo, "organization", "brand-a", "organization", "new", joined=200.0)
    assert repo.get_org_parent_map()["brand-a"] == "new"


def test_multiple_orgs(repo):
    _membership(repo, "organization", "brand-a", "organization", "umbrella")
    _membership(repo, "organization", "brand-b", "organization", "umbrella")
    m = repo.get_org_parent_map()
    assert m == {"brand-a": "umbrella", "brand-b": "umbrella"}
