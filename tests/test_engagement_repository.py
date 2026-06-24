"""Tests for the engagement-substrate repository methods (E2 / A3).

The engagement is the OPERATIONAL projection — plain SQL CRUD over the
engagements sidecar with app-side enum + domain/stage validation. Diagnostic
findings stay epistemic (not tested here — they're a different projection).
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from empirica.data.repositories.workspace_db import (
    ENGAGEMENT_LIFECYCLE_STATES,
    ENGAGEMENT_OUTCOMES,
    WorkspaceDBRepository,
    _ensure_workspace_schema,
)


@pytest.fixture
def repo() -> WorkspaceDBRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    return WorkspaceDBRepository(conn)


# ── enum sanity ──────────────────────────────────────────────────────────────


def test_enum_sets():
    assert {"open", "in_progress", "blocked", "closed"} == ENGAGEMENT_LIFECYCLE_STATES
    assert {"won", "lost", "resolved", "wont_fix", "defer", "superseded"} == ENGAGEMENT_OUTCOMES


# ── create / get ─────────────────────────────────────────────────────────────


def test_create_defaults_to_open(repo):
    e = repo.create_engagement("e1", "Ticket one", domain="support", stage="support.new")
    assert e["engagement_id"] == "e1"
    assert e["lifecycle_state"] == "open"
    assert e["title"] == "Ticket one"
    assert e["domain"] == "support"
    assert e["stage"] == "support.new"


def test_create_validates_domain_and_stage(repo):
    with pytest.raises(ValueError, match="unknown engagement domain"):
        repo.create_engagement("e2", "x", domain="not_a_domain")
    with pytest.raises(ValueError, match="unknown engagement stage"):
        repo.create_engagement("e3", "x", domain="support", stage="support.nope")
    with pytest.raises(ValueError, match="belongs to domain"):
        repo.create_engagement("e4", "x", domain="support", stage="sales.lead")


def test_get_missing_returns_none(repo):
    assert repo.get_engagement("nope") is None


# ── list + filters ───────────────────────────────────────────────────────────


def test_list_filters_by_domain_and_lifecycle(repo):
    repo.create_engagement("s1", "s1", domain="support")
    repo.create_engagement("o1", "o1", domain="outreach")
    repo.update_engagement("o1", lifecycle_state="closed", outcome="won")

    assert {e["engagement_id"] for e in repo.list_engagements(domain="support")} == {"s1"}
    assert {e["engagement_id"] for e in repo.list_engagements(lifecycle_state="closed")} == {"o1"}
    assert {e["engagement_id"] for e in repo.list_engagements(lifecycle_state="open")} == {"s1"}


def test_list_invalid_lifecycle_raises(repo):
    with pytest.raises(ValueError, match="invalid lifecycle_state"):
        repo.list_engagements(lifecycle_state="bogus")


def test_list_org_scoped_via_ticket_of_membership(repo):
    repo.create_engagement("t1", "ticket for acme", domain="support")
    repo.create_engagement("t2", "unrelated", domain="support")
    now = time.time()
    repo.conn.execute(
        "INSERT INTO entity_memberships (entity_type, entity_id, group_type, group_id, role, joined_at, created_at) "
        "VALUES ('engagement', 't1', 'organization', 'acme', 'ticket_of', ?, ?)",
        (now, now),
    )
    repo.conn.commit()
    scoped = repo.list_engagements(org_id="acme")
    assert {e["engagement_id"] for e in scoped} == {"t1"}


# ── update + enum enforcement ────────────────────────────────────────────────


def test_update_transitions_lifecycle_and_bumps_updated_at(repo):
    e = repo.create_engagement("u1", "u1", domain="support")
    before = e["updated_at"]
    time.sleep(0.01)
    updated = repo.update_engagement("u1", lifecycle_state="in_progress", stage="support.in_progress")
    assert updated["lifecycle_state"] == "in_progress"
    assert updated["stage"] == "support.in_progress"
    assert updated["updated_at"] > before


def test_update_rejects_bad_enums(repo):
    repo.create_engagement("u2", "u2", domain="support")
    with pytest.raises(ValueError, match="invalid lifecycle_state"):
        repo.update_engagement("u2", lifecycle_state="paused")
    with pytest.raises(ValueError, match="invalid outcome"):
        repo.update_engagement("u2", outcome="maybe")


def test_update_missing_returns_none(repo):
    assert repo.update_engagement("ghost", lifecycle_state="closed") is None


def test_update_no_fields_is_noop_read(repo):
    repo.create_engagement("u3", "u3")
    assert repo.update_engagement("u3")["engagement_id"] == "u3"


# ── definitions + practice domains ───────────────────────────────────────────


def test_list_domains_seeded(repo):
    assert {d["domain_id"] for d in repo.list_domains()} == {
        "outreach",
        "sales",
        "support",
        "security",
        "infra",
        "onboarding",
    }


def test_list_stages_for_domain_ordered(repo):
    stages = repo.list_stages("support")
    assert [s["stage_id"] for s in stages] == [
        "support.new",
        "support.triaged",
        "support.in_progress",
        "support.waiting_customer",
    ]


def test_join_practice_domain_idempotent(repo):
    repo.join_practice_domain("empirica.david.empirica-outreach", "outreach")
    repo.join_practice_domain("empirica.david.empirica-outreach", "outreach")  # idempotent
    domains = repo.get_practice_domains("empirica.david.empirica-outreach")
    assert [d["domain_id"] for d in domains] == ["outreach"]


def test_join_practice_domain_validates(repo):
    with pytest.raises(ValueError, match="unknown engagement domain"):
        repo.join_practice_domain("p", "not_a_domain")
