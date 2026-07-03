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
    ENGAGEMENT_DEFAULT_EXCLUDED_STATES,
    ENGAGEMENT_LIFECYCLE_STATES,
    ENGAGEMENT_OUTCOMES,
    ENGAGEMENT_PREACTIVE_STATES,
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
    assert {"planned", "open", "in_progress", "blocked", "closed"} == ENGAGEMENT_LIFECYCLE_STATES
    assert {"won", "lost", "resolved", "wont_fix", "defer", "superseded"} == ENGAGEMENT_OUTCOMES
    # planned (pre-active) + closed (terminal) are the two states off the active feed.
    assert {"planned"} == ENGAGEMENT_PREACTIVE_STATES
    assert {"planned", "closed"} == ENGAGEMENT_DEFAULT_EXCLUDED_STATES


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


# ── active-by-default (SER#183 part-2) ────────────────────────────────────────


def test_list_defaults_to_active_excludes_closed(repo):
    """No explicit lifecycle filter → terminal (closed) engagements are excluded."""
    repo.create_engagement("a1", "active", domain="support")
    repo.create_engagement("c1", "done", domain="support")
    repo.update_engagement("c1", lifecycle_state="closed", outcome="won")
    assert {e["engagement_id"] for e in repo.list_engagements()} == {"a1"}


def test_list_include_closed_returns_terminal_too(repo):
    """include_closed=True restores the full set (the Engagements-area opt-in)."""
    repo.create_engagement("a1", "active", domain="support")
    repo.create_engagement("c1", "done", domain="support")
    repo.update_engagement("c1", lifecycle_state="closed", outcome="won")
    assert {e["engagement_id"] for e in repo.list_engagements(include_closed=True)} == {"a1", "c1"}


def test_list_explicit_closed_overrides_default(repo):
    """An explicit lifecycle_state always wins — even for a terminal state."""
    repo.create_engagement("a1", "active", domain="support")
    repo.create_engagement("c1", "done", domain="support")
    repo.update_engagement("c1", lifecycle_state="closed", outcome="won")
    # Explicit closed returns closed despite active-by-default; include_closed is moot.
    assert {e["engagement_id"] for e in repo.list_engagements(lifecycle_state="closed")} == {"c1"}


def test_list_org_scoped_is_active_by_default(repo):
    """The org→engagement drill excludes closed unless opted in (the X2 'who now' view)."""
    repo.create_engagement("t1", "active acme ticket", domain="support")
    repo.create_engagement("t2", "closed acme ticket", domain="support")
    repo.update_engagement("t2", lifecycle_state="closed", outcome="won")
    now = time.time()
    for eid in ("t1", "t2"):
        repo.conn.execute(
            "INSERT INTO entity_memberships (entity_type, entity_id, group_type, group_id, role, joined_at, created_at) "
            "VALUES ('engagement', ?, 'organization', 'acme', 'ticket_of', ?, ?)",
            (eid, now, now),
        )
    repo.conn.commit()
    assert {e["engagement_id"] for e in repo.list_engagements(org_id="acme")} == {"t1"}
    assert {e["engagement_id"] for e in repo.list_engagements(org_id="acme", include_closed=True)} == {"t1", "t2"}


# ── planned (pre-active) + ?lifecycle=all fetch-everything ─────────────────────


def test_list_excludes_planned_by_default(repo):
    """Pre-active 'planned' is off the active feed (brackets it from the front)."""
    repo.create_engagement("a1", "active", domain="support")
    repo.create_engagement("p1", "queued", domain="support")
    repo.update_engagement("p1", lifecycle_state="planned")
    assert {e["engagement_id"] for e in repo.list_engagements()} == {"a1"}


def test_list_include_closed_still_excludes_planned(repo):
    """include_closed is terminal-only sugar — pre-active 'planned' stays out."""
    repo.create_engagement("a1", "active", domain="support")
    repo.create_engagement("p1", "queued", domain="support")
    repo.create_engagement("c1", "done", domain="support")
    repo.update_engagement("p1", lifecycle_state="planned")
    repo.update_engagement("c1", lifecycle_state="closed", outcome="won")
    assert {e["engagement_id"] for e in repo.list_engagements(include_closed=True)} == {"a1", "c1"}


def test_list_explicit_planned_returns_planned(repo):
    """An explicit lifecycle_state='planned' wins over the default exclusion."""
    repo.create_engagement("a1", "active", domain="support")
    repo.create_engagement("p1", "queued", domain="support")
    repo.update_engagement("p1", lifecycle_state="planned")
    assert {e["engagement_id"] for e in repo.list_engagements(lifecycle_state="planned")} == {"p1"}


def test_list_all_returns_everything(repo):
    """lifecycle_state='all' — the Engagements-area fetch-everything (planned + closed included)."""
    repo.create_engagement("a1", "active", domain="support")
    repo.create_engagement("p1", "queued", domain="support")
    repo.create_engagement("c1", "done", domain="support")
    repo.update_engagement("p1", lifecycle_state="planned")
    repo.update_engagement("c1", lifecycle_state="closed", outcome="won")
    assert {e["engagement_id"] for e in repo.list_engagements(lifecycle_state="all")} == {"a1", "p1", "c1"}


def test_update_accepts_planned(repo):
    """'planned' is a valid lifecycle_state for a transition into pre-active."""
    repo.create_engagement("e1", "x", domain="support")
    updated = repo.update_engagement("e1", lifecycle_state="planned")
    assert updated["lifecycle_state"] == "planned"


# ── contact scoping (engagement_contacts edge) ────────────────────────────────


def _seed_engagement_contacts(repo, edges):
    """engagement_contacts is workspace-managed (not core-vendored) — create it +
    seed edges. Each edge: (engagement_id, contact_id, left_at)."""
    repo.conn.execute(
        """CREATE TABLE IF NOT EXISTS engagement_contacts (
               engagement_id TEXT NOT NULL, contact_id TEXT NOT NULL,
               role TEXT DEFAULT 'participant', joined_at REAL NOT NULL, left_at REAL,
               contribution_notes TEXT, PRIMARY KEY (engagement_id, contact_id))"""
    )
    now = time.time()
    for eid, cid, left_at in edges:
        repo.conn.execute(
            "INSERT INTO engagement_contacts (engagement_id, contact_id, joined_at, left_at) VALUES (?, ?, ?, ?)",
            (eid, cid, now, left_at),
        )
    repo.conn.commit()


def test_list_contact_scoped_via_engagement_contacts(repo):
    repo.create_engagement("t1", "carly ticket", domain="support")
    repo.create_engagement("t2", "carly ticket 2", domain="support")
    repo.create_engagement("t3", "unrelated", domain="support")
    _seed_engagement_contacts(
        repo,
        [("t1", "c-carly", None), ("t2", "c-carly", None), ("t3", "c-other", None)],
    )
    assert {e["engagement_id"] for e in repo.list_engagements(contact_id="c-carly")} == {"t1", "t2"}


def test_list_contact_scope_excludes_left_edges(repo):
    repo.create_engagement("t1", "active edge", domain="support")
    repo.create_engagement("t2", "left edge", domain="support")
    _seed_engagement_contacts(repo, [("t1", "c-carly", None), ("t2", "c-carly", time.time())])
    assert {e["engagement_id"] for e in repo.list_engagements(contact_id="c-carly")} == {"t1"}


def test_list_contact_and_org_compose(repo):
    repo.create_engagement("t1", "acme + carly", domain="support")
    repo.create_engagement("t2", "acme only", domain="support")
    now = time.time()
    for eid in ("t1", "t2"):
        repo.conn.execute(
            "INSERT INTO entity_memberships (entity_type, entity_id, group_type, group_id, role, joined_at, created_at) "
            "VALUES ('engagement', ?, 'organization', 'acme', 'ticket_of', ?, ?)",
            (eid, now, now),
        )
    repo.conn.commit()
    _seed_engagement_contacts(repo, [("t1", "c-carly", None)])
    # org AND contact both apply → only t1 (acme ticket carly participates in)
    assert {e["engagement_id"] for e in repo.list_engagements(org_id="acme", contact_id="c-carly")} == {"t1"}


def test_list_contact_scope_empty_when_linkage_table_absent(repo):
    """Core-only DB (no engagement_contacts table) → contact filter honest-empty,
    never a `no such table` 500."""
    repo.create_engagement("t1", "some ticket", domain="support")
    assert repo.list_engagements(contact_id="c-carly") == []


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
        "support.resolved",  # CCR-1 terminal stage (ordinal 50)
    ]
    # the terminal stage carries is_terminal=1
    assert stages[-1]["is_terminal"] == 1


def test_join_practice_domain_idempotent(repo):
    repo.join_practice_domain("empirica.david.empirica-outreach", "outreach")
    repo.join_practice_domain("empirica.david.empirica-outreach", "outreach")  # idempotent
    domains = repo.get_practice_domains("empirica.david.empirica-outreach")
    assert [d["domain_id"] for d in domains] == ["outreach"]


def test_join_practice_domain_validates(repo):
    with pytest.raises(ValueError, match="unknown engagement domain"):
        repo.join_practice_domain("p", "not_a_domain")
