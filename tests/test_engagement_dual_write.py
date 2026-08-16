"""Engagement dual-write atomicity (prop_rif7asmh, Philipp's autonomy).

An engagement needs an entity_registry row (what surfaces render) AND an
engagements sidecar row (dates/warmth/stage). Nothing linked the writes:
one fleet box measured 82 registry-only (visible, dateless) + 12 sidecar-only
(invisible). These pin the fixes:
- create_engagement registers in the same call (no sidecar without registry)
- validate_engagement_taxonomy lets callers fail BEFORE any write
- engagement_registry_drift surfaces both orphan classes for doctor
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from empirica.data.repositories.workspace_db import (
    WorkspaceDBRepository,
    _ensure_workspace_schema,
)


@pytest.fixture
def repo() -> WorkspaceDBRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    return WorkspaceDBRepository(conn)


def _registry_row(repo, eid):
    cur = repo._execute("SELECT * FROM entity_registry WHERE entity_type = 'engagement' AND entity_id = ?", (eid,))
    return cur.fetchone()


# ── create_engagement registers atomically ────────────────────────────────────


def test_create_engagement_registers_in_same_call(repo):
    eng = repo.create_engagement("e-test1", "Test engagement")
    assert eng["engagement_id"] == "e-test1"
    row = _registry_row(repo, "e-test1")
    assert row is not None, "sidecar created without registry row — the invisible-engagement class"
    assert row["display_name"] == "Test engagement"
    assert row["source_table"] == "engagements"


def test_create_engagement_registration_carries_description(repo):
    repo.create_engagement("e-test2", "Titled", description="the body")
    assert _registry_row(repo, "e-test2")["description"] == "the body"


def test_later_metadata_upsert_still_refreshes(repo):
    """The API path upserts again with metadata after create — must refresh in
    place, not conflict (idempotent registration)."""
    repo.create_engagement("e-test3", "T")
    repo.upsert_entity(
        "engagement",
        "e-test3",
        display_name="T",
        source_db="workspace",
        source_table="engagements",
        metadata='{"severity": "high"}',
    )
    assert _registry_row(repo, "e-test3")["metadata"] == '{"severity": "high"}'


# ── pre-write taxonomy validation ─────────────────────────────────────────────


def test_validate_taxonomy_raises_before_any_write(repo):
    with pytest.raises(ValueError):
        repo.validate_engagement_taxonomy(domain="no-such-domain")
    # Nothing written by validation
    assert repo._execute("SELECT COUNT(*) AS n FROM engagements").fetchone()["n"] == 0
    assert (
        repo._execute("SELECT COUNT(*) AS n FROM entity_registry WHERE entity_type='engagement'").fetchone()["n"] == 0
    )


def test_validate_taxonomy_passes_on_none(repo):
    repo.validate_engagement_taxonomy(domain=None, stage=None)  # must not raise


# ── drift surface ─────────────────────────────────────────────────────────────


def _seed_orphans(repo):
    now = time.time()
    # registry-only orphan (the 82-class)
    repo.upsert_entity(
        "engagement", "e-reg-only", display_name="ghost", source_db="workspace", source_table="engagements"
    )
    # sidecar-only orphan (the 12-class) — raw INSERT bypassing create_engagement
    repo._execute(
        "INSERT INTO engagements (engagement_id, title, engagement_type, status, lifecycle_state, "
        "started_at, created_at, updated_at) VALUES ('e-side-only', 'invisible', 'outreach', 'active', 'open', ?, ?, ?)",
        (now, now, now),
    )
    repo.commit()


def test_drift_reports_both_orphan_classes(repo):
    _seed_orphans(repo)
    drift = repo.engagement_registry_drift()
    assert drift["registry_only"] == ["e-reg-only"]
    assert drift["sidecar_only"] == ["e-side-only"]


def test_drift_clean_after_atomic_create(repo):
    repo.create_engagement("e-linked", "Linked")
    drift = repo.engagement_registry_drift()
    assert drift == {"registry_only": [], "sidecar_only": []}


def test_doctor_check_warns_on_drift(repo, monkeypatch):
    """The doctor check reads the same drift surface and WARNs with counts."""
    import importlib.util
    import sys
    from pathlib import Path

    _seed_orphans(repo)

    spec = importlib.util.spec_from_file_location(
        "doctor_test",
        Path(__file__).resolve().parent.parent / "empirica" / "cli" / "command_handlers" / "doctor.py",
    )
    doctor = importlib.util.module_from_spec(spec)
    sys.modules["doctor_test"] = doctor  # dataclass resolution needs the module registered
    try:
        spec.loader.exec_module(doctor)
    finally:
        sys.modules.pop("doctor_test", None)

    class _CM:
        def __enter__(self):
            return repo

        def __exit__(self, *a):
            return False

    import empirica.data.repositories.workspace_db as wdb

    monkeypatch.setattr(wdb.WorkspaceDBRepository, "open", classmethod(lambda cls, **kw: _CM()))
    check = doctor.check_engagement_registry_drift()
    assert check.status == doctor.WARN
    assert "1 registry-only" in check.detail
    assert "1 sidecar-only" in check.detail
