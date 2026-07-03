"""Tests for the richer contact projection + engagement_tasks repo methods
(daemon-CRM: goals 8996f378 + the engagement_tasks route).

Uses an in-memory workspace.db with the minimal tables the queries read, so the
projection logic (tags JSON-parse, org name+role join, task scoping/ordering) is
pinned without touching the live DB.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.repositories.workspace_db import WorkspaceDBRepository


@pytest.fixture()
def repo() -> WorkspaceDBRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE contacts (
            contact_id TEXT PRIMARY KEY, email_primary TEXT, phone_primary TEXT,
            organization_title TEXT, tags TEXT, notes TEXT, contact_type TEXT, lifecycle_stage TEXT
        );
        CREATE TABLE entity_registry (entity_type TEXT, entity_id TEXT, display_name TEXT);
        CREATE TABLE entity_memberships (
            entity_type TEXT, entity_id TEXT, group_type TEXT, group_id TEXT,
            role TEXT, joined_at TEXT, left_at TEXT
        );
        CREATE TABLE engagement_tasks (
            task_id TEXT, engagement_id TEXT, title TEXT, description TEXT, status TEXT,
            assigned_to TEXT, due_at TEXT, completed_at TEXT, blocked_by TEXT, created_at TEXT
        );

        INSERT INTO contacts VALUES
            ('c-carly','carly@x.com','+1','Admiral','["vip","founder"]','deep notes','person','live'),
            ('c-bad',NULL,NULL,NULL,'not-json',NULL,NULL,NULL);
        INSERT INTO entity_registry VALUES ('organization','empirica-foundation','Empirica Foundation');
        INSERT INTO entity_memberships VALUES
            ('contact','c-carly','organization','empirica-foundation','admiral','2026-01-01',NULL),
            ('contact','c-closed','organization','empirica-foundation','member','2026-01-01','2026-03-01');
        INSERT INTO engagement_tasks VALUES
            ('t1','eng-1','Provision seat','d','open','carly',NULL,NULL,NULL,'2026-01-01'),
            ('t2','eng-1','Verify','d','done','carly',NULL,'2026-02-01',NULL,'2026-01-02'),
            ('t3','eng-2','Other','d','open','x',NULL,NULL,NULL,'2026-01-01');
        """
    )
    return WorkspaceDBRepository(conn)


# ── contact detail map ────────────────────────────────────────────────────────


def test_contact_detail_map_projects_crm_fields(repo):
    m = repo.get_contact_detail_map()
    c = m["c-carly"]
    assert c["email"] == "carly@x.com"
    assert c["phone"] == "+1"
    assert c["title"] == "Admiral"
    assert c["tags"] == ["vip", "founder"]  # JSON-parsed to a list
    assert c["notes"] == "deep notes"
    assert c["contact_type"] == "person" and c["lifecycle_stage"] == "live"


def test_contact_detail_map_malformed_tags_is_empty_list(repo):
    assert repo.get_contact_detail_map()["c-bad"]["tags"] == []


# ── contact→org details (name + role) ─────────────────────────────────────────


def test_contact_org_details_resolves_name_and_role(repo):
    m = repo.get_contact_org_details_map()
    assert m["c-carly"] == {
        "org_id": "empirica-foundation",
        "org_name": "Empirica Foundation",  # joined from entity_registry.display_name
        "role": "admiral",  # free-text role
    }


def test_contact_org_details_excludes_closed_edges(repo):
    # c-closed has left_at set → not an active affiliation
    assert "c-closed" not in repo.get_contact_org_details_map()


# ── engagement tasks ──────────────────────────────────────────────────────────


def test_get_engagement_tasks_scoped_and_ordered(repo):
    tasks = repo.get_engagement_tasks("eng-1")
    assert [t["task_id"] for t in tasks] == ["t1", "t2"]  # only eng-1, oldest first
    assert tasks[0]["status"] == "open" and tasks[1]["completed_at"] == "2026-02-01"


def test_get_engagement_tasks_empty_for_unknown(repo):
    assert repo.get_engagement_tasks("nope") == []


# ── resilience: optional tables absent (older/minimal workspace DBs) ───────────


def test_crm_projections_degrade_when_optional_tables_absent():
    """A workspace DB predating the ``contacts`` / ``engagement_tasks`` tables
    (or a fixture that only seeds the entity tables) must NOT raise
    ``OperationalError: no such table`` — the CRM projections degrade to empty.
    This is what a GET /api/v1/entities against such a DB relies on to 200
    instead of 500 (regression guard for the daemon-crm contact projection).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Only the entity tables — deliberately NO `contacts`, NO `engagement_tasks`.
    conn.executescript(
        """
        CREATE TABLE entity_registry (entity_type TEXT, entity_id TEXT, display_name TEXT);
        CREATE TABLE entity_memberships (
            entity_type TEXT, entity_id TEXT, group_type TEXT, group_id TEXT,
            role TEXT, joined_at TEXT, left_at TEXT
        );
        """
    )
    repo = WorkspaceDBRepository(conn)
    assert repo.get_contact_detail_map() == {}
    assert repo.get_engagement_tasks("eng-1") == []
    # entity_memberships IS present → the org-details map still works (returns {}).
    assert repo.get_contact_org_details_map() == {}
