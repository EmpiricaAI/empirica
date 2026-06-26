"""Tests for `entity-delete` — soft-archive (default, reversible) vs hard cascade.

Design (mesh-converged, decision e5a25c7c): the default is a reversible
soft-archive (status='archived' + close memberships); `--hard` does an
irreversible dependent-order cascade and requires `--confirm`.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers.entity_commands import handle_entity_delete_command
from empirica.data.repositories.workspace_db import (
    WorkspaceDBRepository,
    _ensure_workspace_schema,
)


@pytest.fixture
def repo(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "workspace.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    r = WorkspaceDBRepository(conn)
    yield r
    r.close()


def _seed(repo) -> None:
    """An engagement entity + a membership (engagement→org) + one artifact link."""
    repo.upsert_entity("engagement", "e-x", "Engagement X", "workspace.db", "engagements")
    repo.upsert_entity("organization", "o-y", "Org Y", "workspace.db", "organizations")
    repo.upsert_entity_membership(
        entity_type="engagement", entity_id="e-x", group_type="organization", group_id="o-y", role="ticket_of"
    )
    repo._execute(
        "INSERT INTO entity_artifacts (id, artifact_type, artifact_id, entity_type, entity_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ea-1", "goal", "g-1", "engagement", "e-x", 0.0),
    )
    repo.commit()


def _active_membership_count(repo, et, eid) -> int:
    mem = repo.get_entity_memberships(et, eid)
    return sum(len(v) for v in mem.values()) if isinstance(mem, dict) else 0


# ── repo: soft-archive (default) ────────────────────────────────────────


def test_archive_entity_flips_status_and_closes_memberships(repo):
    _seed(repo)
    assert _active_membership_count(repo, "engagement", "e-x") == 1

    changed = repo.archive_entity("engagement", "e-x")

    assert changed is True
    assert repo.get_entity("engagement", "e-x")["status"] == "archived"  # row stays, status flips
    assert _active_membership_count(repo, "engagement", "e-x") == 0  # membership left_at stamped
    assert len(repo.get_entity_artifacts_by_entity("engagement", "e-x")) == 1  # artifacts untouched


def test_archive_entity_is_idempotent(repo):
    _seed(repo)
    assert repo.archive_entity("engagement", "e-x") is True
    assert repo.archive_entity("engagement", "e-x") is False  # already archived → no-op


# ── repo: hard cascade ──────────────────────────────────────────────────


def test_delete_entity_hard_cascades_dependent_order(repo):
    _seed(repo)
    counts = repo.delete_entity_hard("engagement", "e-x")

    assert counts["entity_artifacts"] == 1
    assert counts["entity_memberships"] == 1
    assert counts["entity_registry"] == 1
    # everything for the entity is gone
    assert repo.get_entity("engagement", "e-x") is None
    assert len(repo.get_entity_artifacts_by_entity("engagement", "e-x")) == 0
    assert _active_membership_count(repo, "engagement", "e-x") == 0


# ── handler: --hard safety gate ─────────────────────────────────────────


def _args(**kw):
    base = {
        "entity": None,
        "entity_type": None,
        "entity_id": None,
        "hard": False,
        "confirm": False,
        "dry_run": False,
        "output": "json",
        "verbose": False,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_handler_hard_without_confirm_is_refused(tmp_path, monkeypatch, capsys):
    import empirica.data.repositories.workspace_db as wdb

    dbp = tmp_path / "workspace.db"
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: dbp)
    with WorkspaceDBRepository.open(ensure_schema=True) as r:
        r.upsert_entity("engagement", "e-x", "Engagement X", "workspace.db", "engagements")

    with pytest.raises(SystemExit):
        handle_entity_delete_command(_args(entity="engagement:e-x", hard=True, confirm=False))

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["requires"] == "--confirm"
    # refused → entity untouched
    with WorkspaceDBRepository.open() as r:
        assert r.get_entity("engagement", "e-x") is not None


def test_handler_soft_archive_default(tmp_path, monkeypatch, capsys):
    import empirica.data.repositories.workspace_db as wdb

    dbp = tmp_path / "workspace.db"
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: dbp)
    with WorkspaceDBRepository.open(ensure_schema=True) as r:
        r.upsert_entity("engagement", "e-x", "Engagement X", "workspace.db", "engagements")

    handle_entity_delete_command(_args(entity="engagement:e-x"))  # no --hard → soft-archive

    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["action"] == "archived"
    with WorkspaceDBRepository.open() as r:
        assert r.get_entity("engagement", "e-x")["status"] == "archived"  # still recoverable
