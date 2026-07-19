"""org→org parentage for GET /api/v1/entities.

**David 2026-07-19 correction:** organizations are flat and unique. A
brand/umbrella relationship (a child org belonging to a parent company) is
METADATA (``entity_registry.metadata.parent_org`` on the child org), NOT a
structural org→org ``entity_memberships`` edge. ``get_org_parent_map()`` reads
that metadata. This supersedes the earlier org→org membership-edge design, which
conflated a descriptive relationship with the one-org-per-contact membership
graph — org→org membership edges are no longer read as parentage.
"""

from __future__ import annotations

import json
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


def _org(repo, eid, parent=None):
    """Insert an active organization row, optionally with ``metadata.parent_org``."""
    now = time.time()
    meta = json.dumps({"parent_org": parent}) if parent else None
    repo._execute(
        "INSERT INTO entity_registry (entity_type, entity_id, display_name, source_db, source_table, "
        "status, metadata, created_at, updated_at) "
        "VALUES ('organization', ?, ?, 'test', 'test', 'active', ?, ?, ?)",
        (eid, eid, meta, now, now),
    )


def test_empty_when_no_orgs(repo):
    assert repo.get_org_parent_map() == {}


def test_maps_org_to_parent_from_metadata(repo):
    _org(repo, "umbrella")
    _org(repo, "brand-a", parent="umbrella")
    assert repo.get_org_parent_map() == {"brand-a": "umbrella"}


def test_org_without_parent_metadata_is_absent(repo):
    _org(repo, "umbrella")
    _org(repo, "brand-a", parent="umbrella")
    _org(repo, "independent")  # no parent_org → absent from the map
    assert repo.get_org_parent_map() == {"brand-a": "umbrella"}


def test_membership_edges_are_not_parentage(repo):
    # Post-2026-07-19 correction: an org→org membership edge is NOT read as
    # parentage — that structural relationship was removed; parent_org is
    # metadata only. Regression guard for the swept ERM change.
    _membership(repo, "organization", "brand-a", "organization", "umbrella")
    assert repo.get_org_parent_map() == {}


def test_multiple_orgs_from_metadata(repo):
    _org(repo, "umbrella")
    _org(repo, "brand-a", parent="umbrella")
    _org(repo, "brand-b", parent="umbrella")
    assert repo.get_org_parent_map() == {"brand-a": "umbrella", "brand-b": "umbrella"}


# ── get_contact_org_map (contact→org affiliation, the populated linkage) ──────


def test_contact_org_map_empty_when_no_edges(repo):
    assert repo.get_contact_org_map() == {}


def test_contact_org_map_maps_active_affiliation(repo):
    _membership(repo, "contact", "c-carly", "organization", "empirica-foundation", role="admiral")
    assert repo.get_contact_org_map() == {"c-carly": "empirica-foundation"}


def test_contact_org_map_ignores_closed_affiliation(repo):
    _membership(repo, "contact", "c-x", "organization", "old-org", left_at=time.time())
    assert repo.get_contact_org_map() == {}


def test_contact_org_map_ignores_non_org_groups(repo):
    # contact→engagement membership is not a contact→org affiliation.
    _membership(repo, "contact", "c-x", "engagement", "e1", role="member")
    assert repo.get_contact_org_map() == {}


def test_contact_org_map_latest_active_wins(repo):
    _membership(repo, "contact", "c-x", "organization", "org-old", joined=100.0)
    _membership(repo, "contact", "c-x", "organization", "org-new", joined=200.0)
    assert repo.get_contact_org_map()["c-x"] == "org-new"


# ── list_entities ?parent_org scoping (contacts in an org, honest-empty) ─────


def _contact(repo, eid, name="C"):
    now = time.time()
    repo._execute(
        "INSERT INTO entity_registry (entity_type, entity_id, display_name, source_db, source_table, "
        "status, created_at, updated_at) VALUES ('contact', ?, ?, 'test', 'test', 'active', ?, ?)",
        (eid, name, now, now),
    )


def test_parent_org_scopes_contacts_to_org(repo):
    _contact(repo, "c1")
    _contact(repo, "c2")
    _membership(repo, "contact", "c1", "organization", "acme", role="member")
    # c2 has no affiliation
    rows = repo.list_entities(parent_org="acme")
    assert {r["entity_id"] for r in rows} == {"c1"}


def test_parent_org_unknown_is_honest_empty(repo):
    _contact(repo, "c1")
    _membership(repo, "contact", "c1", "organization", "acme", role="member")
    # A bogus org must NOT leak the full set — it returns nothing.
    assert repo.list_entities(parent_org="BOGUS-NOPE") == []


def test_parent_org_excludes_closed_affiliation(repo):
    _contact(repo, "c1")
    _membership(repo, "contact", "c1", "organization", "acme", role="member", left_at=time.time())
    assert repo.list_entities(parent_org="acme") == []


def test_parent_org_with_non_contact_type_is_empty(repo):
    _contact(repo, "c1")
    _membership(repo, "contact", "c1", "organization", "acme", role="member")
    # parent_org implies a contact scope — pairing it with type=organization is contradictory.
    assert repo.list_entities(entity_type="organization", parent_org="acme") == []
