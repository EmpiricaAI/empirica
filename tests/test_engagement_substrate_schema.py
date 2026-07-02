"""Behavior + drift-guard tests for the vendored engagement substrate.

empirica core vendors the engagement-substrate schema (3 definition tables + a
minimal engagements table + 6-domain/24-stage seeds) so a fresh install without
empirica-workspace still gets the tables the engagement CLI + daemon read. The
canonical source of truth is empirica-workspace; the drift-guard tests assert
the vendored copy stays in parity, and the behavior tests assert both ensure
paths stand up an identical substrate.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.repositories import workspace_db as wdb


def _fresh_db() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


_SUBSTRATE_TABLES = {"domain_definitions", "stage_definitions", "practice_domains", "engagements"}


# ── behavior ─────────────────────────────────────────────────────────────────


def test_apply_creates_substrate_tables():
    conn = _fresh_db()
    wdb._apply_engagement_substrate(conn.cursor())
    conn.commit()
    assert _tables(conn) >= _SUBSTRATE_TABLES


def test_additive_migration_heals_pre_e1_engagements_table():
    """Regression (1.12.10 hard-crash, mesh-support/Philipp repro): a workspace.db
    whose `engagements` table predates the E1 sidecar columns must be
    ALTER-migrated on open — CREATE TABLE IF NOT EXISTS no-ops on the existing
    table, so without the additive pass the idx_engagements_lifecycle index below
    crashes with `no such column: lifecycle_state`."""
    conn = _fresh_db()
    # Pre-E1 engagements table: no lifecycle_state/stage/domain/updated_at.
    conn.execute("CREATE TABLE engagements (engagement_id TEXT PRIMARY KEY, title TEXT, status TEXT)")
    conn.commit()
    wdb._apply_engagement_substrate(conn.cursor())  # must NOT raise
    conn.commit()
    assert {"lifecycle_state", "stage", "domain", "updated_at", "outcome"} <= _cols(conn, "engagements")
    # The index that used to crash now exists.
    idx = {r[1] for r in conn.execute("PRAGMA index_list(engagements)").fetchall()}
    assert "idx_engagements_lifecycle" in idx


def test_engagements_has_e1_cols_and_no_contacts_fk():
    conn = _fresh_db()
    wdb._apply_engagement_substrate(conn.cursor())
    assert {"lifecycle_state", "stage", "domain", "updated_at"} <= _cols(conn, "engagements")
    # The minimal CREATE drops the contacts FK → no foreign keys.
    assert conn.execute("PRAGMA foreign_key_list(engagements)").fetchall() == []


def test_seeds_six_domains_twentyfive_stages():
    conn = _fresh_db()
    wdb._apply_engagement_substrate(conn.cursor())
    conn.commit()
    domains = {r[0] for r in conn.execute("SELECT domain_id FROM domain_definitions").fetchall()}
    assert domains == {"outreach", "sales", "support", "security", "infra", "onboarding"}
    # 25 = 24 + support.resolved (CCR-1 terminal stage). Lockstep with the
    # empirica-workspace canonical seed + its parity drift-guard.
    assert conn.execute("SELECT COUNT(*) FROM stage_definitions").fetchone()[0] == 25


def test_support_resolved_is_terminal():
    conn = _fresh_db()
    wdb._apply_engagement_substrate(conn.cursor())
    conn.commit()
    row = conn.execute(
        "SELECT ordinal, is_terminal FROM stage_definitions WHERE stage_id = 'support.resolved'"
    ).fetchone()
    assert row is not None, "support.resolved must be seeded"
    assert row[0] == 50  # ordinal
    assert row[1] == 1  # is_terminal


def test_apply_is_idempotent():
    conn = _fresh_db()
    cur = conn.cursor()
    wdb._apply_engagement_substrate(cur)
    wdb._apply_engagement_substrate(cur)  # second run must not raise or duplicate
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM domain_definitions").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM stage_definitions").fetchone()[0] == 25


def test_ensure_workspace_schema_creates_substrate():
    conn = _fresh_db()
    wdb._ensure_workspace_schema(conn)
    assert _tables(conn) >= _SUBSTRATE_TABLES


def test_both_ensure_paths_produce_identical_substrate():
    a = _fresh_db()
    wdb._ensure_workspace_schema(a)

    from empirica.cli.command_handlers.project_commands import ensure_workspace_schema

    b = _fresh_db()
    ensure_workspace_schema(b)

    for t in _SUBSTRATE_TABLES:
        assert _cols(a, t) == _cols(b, t), f"{t} columns diverge between the two ensure paths"


# ── drift-guard vs empirica-workspace canonical source ───────────────────────


def _workspace_schema():
    try:
        from empirica_workspace.data import workspace_schema  # type: ignore

        return workspace_schema
    except Exception:
        return None


@pytest.mark.skipif(_workspace_schema() is None, reason="empirica-workspace not installed")
def test_drift_guard_definition_tables_match_canonical():
    """The 3 vendored definition tables must match empirica-workspace's canonical
    DDL. Failure = empirica-workspace evolved the engagement substrate and the
    vendored copy in workspace_db.py needs re-syncing. (The engagements table is
    intentionally NOT compared — core vendors a minimal subset.)"""
    ws = _workspace_schema()
    canon = _fresh_db()
    cur = canon.cursor()
    for stmt in ws.SCHEMAS:
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            pass  # ALTERs whose target isn't created yet in the canonical ordering
    canon.commit()

    core = _fresh_db()
    wdb._apply_engagement_substrate(core.cursor())
    core.commit()

    for t in ("domain_definitions", "stage_definitions", "practice_domains"):
        assert _cols(core, t) == _cols(canon, t), f"{t} drifted from empirica-workspace canonical schema"


def test_vendored_seed_ids_are_the_documented_canonical_set():
    """Guards the vendored seed constants against accidental edits. The canonical
    set (6 domains + 25 stage ids, incl. support.resolved) is documented in
    empirica-workspace WorkspaceDatabase._seed_engagement_domains."""
    assert {d[0] for d in wdb._DEFAULT_ENGAGEMENT_DOMAINS} == {
        "outreach",
        "sales",
        "support",
        "security",
        "infra",
        "onboarding",
    }
    stage_ids = [s[0] for s in wdb._DEFAULT_ENGAGEMENT_STAGES]
    assert len(stage_ids) == 25
    assert len(set(stage_ids)) == 25  # no dupes
    assert "support.resolved" in stage_ids
    # every stage namespaced under one of the 6 domains
    domains = {d[0] for d in wdb._DEFAULT_ENGAGEMENT_DOMAINS}
    assert all(sid.split(".", 1)[0] in domains for sid in stage_ids)
