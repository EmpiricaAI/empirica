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


# ── reports_to (manager name) ─────────────────────────────────────────────────


def test_reports_to_map_resolves_active_manager_name():
    """get_contact_reports_to_map: contact_id → manager display_name via active
    reports_to edges. Closed edges + non-reports_to roles excluded; a manager
    with no registry row is omitted (JOIN, not LEFT JOIN)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entity_registry (entity_type TEXT, entity_id TEXT, display_name TEXT);
        CREATE TABLE entity_memberships (
            entity_type TEXT, entity_id TEXT, group_type TEXT, group_id TEXT,
            role TEXT, joined_at TEXT, left_at TEXT
        );
        INSERT INTO entity_registry VALUES
            ('contact','c-report','Frederike Lehmann'),
            ('contact','c-boss','Georg Fechter');
        INSERT INTO entity_memberships VALUES
            ('contact','c-report','contact','c-boss','reports_to','2026-01-01',NULL),
            ('contact','c-closed','contact','c-boss','reports_to','2026-01-01','2026-03-01'),
            ('contact','c-report','organization','o-x','member','2026-01-01',NULL),
            ('contact','c-noreg','contact','c-ghost','reports_to','2026-01-01',NULL);
        """
    )
    m = WorkspaceDBRepository(conn).get_contact_reports_to_map()
    # active reports_to only; org 'member' edge, closed edge, unregistered manager all excluded
    assert m == {"c-report": "Georg Fechter"}


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


# ── scoped artifacts (canonical-model Gap B) ──────────────────────────────────


def test_get_artifacts_for_entity_direct():
    """get_artifacts_for_entity: the DIRECT entity_artifacts scoped to an entity,
    each carrying artifact_type + artifact_source. entity_type disambiguates;
    unknown entity → [] (honest-empty, not error — the endpoint 200s not 404s)."""
    from empirica.data.repositories.workspace_db import _ensure_workspace_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    repo = WorkspaceDBRepository(conn)
    repo.add_entity_artifact("src-1", "source", "/p/.empirica", "engagement", "eng-x")
    repo.add_entity_artifact("find-1", "finding", "/p/.empirica", "engagement", "eng-x")
    repo.add_entity_artifact("src-2", "source", "/other/.empirica", "contact", "c-y")

    out = repo.get_artifacts_for_entity("eng-x")
    assert len(out) == 2
    assert {a["artifact_type"] for a in out} == {"source", "finding"}
    assert all(a.get("artifact_source") == "/p/.empirica" for a in out)  # §5 field present
    # entity_type disambiguates; the contact's artifact is not returned for the engagement id
    assert repo.get_artifacts_for_entity("eng-x", entity_type="contact") == []
    # unknown entity → empty, never a raise (backs the endpoint's 200-not-404)
    assert repo.get_artifacts_for_entity("no-such-entity") == []


def test_get_scoped_artifacts_transitive_fan_down():
    """§5b: scoped artifacts = direct ∪ one-hop members' direct, fan DOWN.
    engagement→contacts via engagement_contacts (left_at IS NULL); dedupe by
    (type,id) with direct winning; transitive rows tagged via <member>; contact
    is a leaf."""
    from empirica.data.repositories.workspace_db import _ensure_workspace_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS engagement_contacts (
               engagement_id TEXT, contact_id TEXT, role TEXT, joined_at REAL, left_at REAL,
               contribution_notes TEXT, PRIMARY KEY (engagement_id, contact_id))"""
    )
    repo = WorkspaceDBRepository(conn)
    repo.add_entity_artifact("src-eng", "source", "/p", "engagement", "eng-1")  # direct on engagement
    conn.execute("INSERT INTO engagement_contacts VALUES ('eng-1','c-a','participant',1,NULL,NULL)")
    conn.execute("INSERT INTO engagement_contacts VALUES ('eng-1','c-left','participant',1,2,NULL)")  # left
    repo.add_entity_artifact("src-a", "source", "/p", "contact", "c-a")  # active member's artifact
    repo.add_entity_artifact("src-left", "finding", "/p", "contact", "c-left")  # left member's artifact
    conn.commit()

    scoped = repo.get_scoped_artifacts("eng-1", "engagement")
    tagged = {a["artifact_id"]: a["via"] for a in scoped}
    assert tagged["src-eng"] is None  # direct
    assert tagged["src-a"] == "c-a"  # transitive, via the active member
    assert "src-left" not in tagged  # left contact (left_at set) excluded

    # contact is a leaf — no transitive fan-down
    assert all(a["via"] is None for a in repo.get_scoped_artifacts("c-a", "contact"))
    # unknown type → leaf (direct only), never raises
    assert repo.get_scoped_artifacts("nope", None) == []


# ── practitioner entity (B4 foundation) ───────────────────────────────────────


def test_practitioner_entity_upsert_and_list_by_practice():
    """upsert_practitioner_entity persists the durable practitioner entity +
    occupies→practice edge (idempotent); list_practitioner_entities answers
    'which practitioners, in which practice'."""
    import json as _j

    from empirica.data.repositories.workspace_db import _ensure_workspace_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    repo = WorkspaceDBRepository(conn)

    repo.upsert_practitioner_entity("cc-111", "empirica", summary="working on B4")
    repo.upsert_practitioner_entity("cc-222", "empirica-cortex")
    repo.upsert_practitioner_entity("cc-111", "empirica", summary="still B4")  # idempotent re-upsert

    all_p = repo.list_practitioner_entities()
    assert {p["entity_id"] for p in all_p} == {"cc-111", "cc-222"}  # no dupe on re-upsert
    meta = _j.loads(next(p for p in all_p if p["entity_id"] == "cc-111")["metadata"])
    assert meta["practice_ai_id"] == "empirica" and meta["summary"] == "still B4"

    # scoped by practice via the active occupies edge
    assert {p["entity_id"] for p in repo.list_practitioner_entities("empirica")} == {"cc-111"}
    assert {p["entity_id"] for p in repo.list_practitioner_entities("empirica-cortex")} == {"cc-222"}
    assert repo.list_practitioner_entities("no-such-practice") == []
