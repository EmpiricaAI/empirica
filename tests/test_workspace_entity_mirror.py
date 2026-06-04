"""Tests for the workspace.db entity_registry mirror of global_projects.

Surfaced by mesh-support's SER ser_542199e3 audit (prop_houwq47gu): every
project registered in workspace.db.global_projects must also have a matching
row in workspace.db.entity_registry (entity_type='project') so the Practice
Model surface (extension dashboard, entity-list/-show/-walk CLI verbs)
shows the practice.

Coverage:
1. _register_in_workspace_db dual-writes both tables on first insert.
2. _register_in_workspace_db re-runs idempotently and updates both rows.
3. ensure_workspace_schema (project_commands) now creates entity_registry +
   entity_memberships in lockstep with workspace_db._ensure_workspace_schema.
4. workspace-backfill-entities adds missing entity_registry rows from
   pre-existing global_projects rows.
5. Backfill is idempotent (re-running over a fully-mirrored DB is a no-op
   on the add count).
6. Backfill --dry-run reports the would-be changes without writing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# --- Schema parity: ensure_workspace_schema creates the entity tables ---


def test_ensure_workspace_schema_creates_entity_registry(tmp_path):
    """The project_commands.ensure_workspace_schema path must create
    entity_registry — older versions only created global_projects,
    instance_bindings, global_sessions, entity_artifacts, which left the
    dual-write writing into a missing table on fresh installs.
    """
    from empirica.cli.command_handlers.project_commands import (
        ensure_workspace_schema,
    )

    db = tmp_path / "workspace.db"
    conn = sqlite3.connect(str(db))
    ensure_workspace_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row[0] for row in cur.fetchall()}
    conn.close()

    # Both Practice Model tables must exist post-bootstrap
    assert "entity_registry" in tables
    assert "entity_memberships" in tables
    # And the legacy tables must still be present
    assert "global_projects" in tables
    assert "entity_artifacts" in tables


def test_ensure_workspace_schema_idempotent(tmp_path):
    """Calling ensure_workspace_schema twice must not raise — CREATE TABLE
    IF NOT EXISTS semantics apply to the entity tables too.
    """
    from empirica.cli.command_handlers.project_commands import (
        ensure_workspace_schema,
    )

    db = tmp_path / "workspace.db"
    conn = sqlite3.connect(str(db))
    ensure_workspace_schema(conn)
    ensure_workspace_schema(conn)
    conn.close()


# --- _register_in_workspace_db dual-write ---


def _override_workspace_home(tmp_path: Path):
    """Yield a context that points Path.home() at a tmp_path so workspace.db
    lands under <tmp>/.empirica/workspace/workspace.db.
    """
    return patch("empirica.cli.command_handlers.workspace_init.Path.home",
                 return_value=tmp_path)


def test_register_in_workspace_db_dual_writes_both_tables(tmp_path):
    """A first-time call must INSERT into both global_projects AND
    entity_registry with the SAME project_id.
    """
    from empirica.cli.command_handlers.workspace_init import (
        _register_in_workspace_db,
    )

    with _override_workspace_home(tmp_path):
        ok = _register_in_workspace_db(
            project_id="a0e24049-d159-4834-afcb-930ba64d0e2b",
            name="empirica-mesh-support",
            trajectory_path=str(tmp_path / "mesh-support/.empirica"),
            description="Mesh support practice",
            git_remote_url="https://example.com/empirica-mesh-support.git",
            project_type="software",
        )
    assert ok is True

    db = tmp_path / ".empirica/workspace/workspace.db"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    # global_projects row exists
    cur.execute("SELECT id, name FROM global_projects WHERE id = ?",
                ("a0e24049-d159-4834-afcb-930ba64d0e2b",))
    gp = cur.fetchone()
    assert gp is not None
    assert gp[1] == "empirica-mesh-support"

    # entity_registry row exists with matching UUID + source pointer back
    cur.execute(
        "SELECT entity_id, display_name, source_db, source_table, status, metadata "
        "FROM entity_registry WHERE entity_type = 'project' AND entity_id = ?",
        ("a0e24049-d159-4834-afcb-930ba64d0e2b",),
    )
    er = cur.fetchone()
    conn.close()
    assert er is not None
    assert er[0] == "a0e24049-d159-4834-afcb-930ba64d0e2b"
    assert er[1] == "empirica-mesh-support"
    assert er[2] == "workspace"
    assert er[3] == "global_projects"
    assert er[4] == "active"
    # metadata carries the git remote + project_type
    meta = json.loads(er[5])
    assert meta["git_remote_url"] == "https://example.com/empirica-mesh-support.git"
    assert meta["project_type"] == "software"


def test_register_in_workspace_db_reupsert_is_idempotent(tmp_path):
    """Calling _register_in_workspace_db twice with the same args must not
    add a second entity_registry row — UPSERT on (entity_type, entity_id).
    """
    from empirica.cli.command_handlers.workspace_init import (
        _register_in_workspace_db,
    )

    pid = "11111111-2222-3333-4444-555555555555"
    traj = str(tmp_path / "p/.empirica")
    with _override_workspace_home(tmp_path):
        _register_in_workspace_db(project_id=pid, name="proj-a", trajectory_path=traj)
        _register_in_workspace_db(project_id=pid, name="proj-a", trajectory_path=traj)

    db = tmp_path / ".empirica/workspace/workspace.db"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM global_projects WHERE id = ?", (pid,))
    assert cur.fetchone()[0] == 1
    cur.execute(
        "SELECT COUNT(*) FROM entity_registry "
        "WHERE entity_type = 'project' AND entity_id = ?",
        (pid,),
    )
    assert cur.fetchone()[0] == 1
    conn.close()


def test_register_in_workspace_db_update_refreshes_entity_registry(tmp_path):
    """When _register_in_workspace_db is called again with a new name for
    the same trajectory_path, BOTH tables get the updated name — no stale
    Practice Model row left behind.
    """
    from empirica.cli.command_handlers.workspace_init import (
        _register_in_workspace_db,
    )

    pid = "22222222-3333-4444-5555-666666666666"
    traj = str(tmp_path / "p/.empirica")
    with _override_workspace_home(tmp_path):
        _register_in_workspace_db(project_id=pid, name="old-name",
                                   trajectory_path=traj)
        _register_in_workspace_db(project_id=pid, name="new-name",
                                   trajectory_path=traj)

    db = tmp_path / ".empirica/workspace/workspace.db"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT name FROM global_projects WHERE id = ?", (pid,))
    assert cur.fetchone()[0] == "new-name"
    cur.execute(
        "SELECT display_name FROM entity_registry "
        "WHERE entity_type = 'project' AND entity_id = ?",
        (pid,),
    )
    assert cur.fetchone()[0] == "new-name"
    conn.close()


# --- workspace-backfill-entities ---


def _seed_legacy_global_projects(tmp_path: Path, projects: list[dict]) -> None:
    """Populate workspace.db.global_projects WITHOUT touching entity_registry —
    simulates the pre-dual-write state mesh-support's audit caught.
    """
    import time

    from empirica.cli.command_handlers.project_commands import (
        ensure_workspace_schema,
    )

    db = tmp_path / ".empirica/workspace/workspace.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    ensure_workspace_schema(conn)
    cur = conn.cursor()
    now = time.time()
    for p in projects:
        cur.execute(
            "INSERT INTO global_projects "
            "(id, name, description, trajectory_path, git_remote_url, "
            " status, project_type, created_timestamp, updated_timestamp) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (p["id"], p["name"], p.get("description"), p["trajectory_path"],
             p.get("git_remote_url"), p.get("project_type", "software"),
             now, now),
        )
    # Strip any entity_registry rows that may have been created indirectly
    cur.execute("DELETE FROM entity_registry WHERE entity_type = 'project'")
    conn.commit()
    conn.close()


def _run_backfill(tmp_path, *, dry_run=False, output="json"):
    """Invoke handle_workspace_backfill_entities_command in-process under
    a redirected ~/.empirica/workspace home.
    """
    from empirica.cli.command_handlers.workspace_commands import (
        handle_workspace_backfill_entities_command,
    )

    args = SimpleNamespace(dry_run=dry_run, output=output, verbose=False)
    with patch("empirica.data.repositories.workspace_db.Path.home",
               return_value=tmp_path):
        return handle_workspace_backfill_entities_command(args)


def test_backfill_adds_missing_entity_rows(tmp_path):
    """Seed 3 legacy projects, backfill, expect 3 added."""
    _seed_legacy_global_projects(tmp_path, [
        {"id": "uuid-1", "name": "p1", "trajectory_path": str(tmp_path / "p1/.empirica")},
        {"id": "uuid-2", "name": "p2", "trajectory_path": str(tmp_path / "p2/.empirica")},
        {"id": "uuid-3", "name": "p3", "trajectory_path": str(tmp_path / "p3/.empirica")},
    ])
    result = _run_backfill(tmp_path)
    assert result == {"ok": True, "added": 3, "updated": 0, "scanned": 3}

    db = tmp_path / ".empirica/workspace/workspace.db"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM entity_registry WHERE entity_type = 'project'"
    )
    assert cur.fetchone()[0] == 3
    conn.close()


def test_backfill_is_idempotent(tmp_path):
    """Second run reports 0 added (only updated)."""
    _seed_legacy_global_projects(tmp_path, [
        {"id": "uuid-A", "name": "pA",
         "trajectory_path": str(tmp_path / "pA/.empirica")},
    ])
    first = _run_backfill(tmp_path)
    second = _run_backfill(tmp_path)
    assert first["added"] == 1
    assert second["added"] == 0
    assert second["updated"] == 1  # row exists → UPSERT goes UPDATE


def test_backfill_dry_run_writes_nothing(tmp_path):
    """--dry-run reports the would-be add but leaves entity_registry empty."""
    _seed_legacy_global_projects(tmp_path, [
        {"id": "uuid-Z", "name": "pZ",
         "trajectory_path": str(tmp_path / "pZ/.empirica")},
    ])
    result = _run_backfill(tmp_path, dry_run=True)
    assert result == {"ok": True, "added": 1, "updated": 0, "scanned": 1}

    db = tmp_path / ".empirica/workspace/workspace.db"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM entity_registry WHERE entity_type = 'project'"
    )
    assert cur.fetchone()[0] == 0  # nothing written under dry-run
    conn.close()


def test_backfill_carries_description_and_metadata(tmp_path):
    """entity_registry.description + metadata reflect the global_projects
    row contents (git remote, project_type, trajectory_path)."""
    _seed_legacy_global_projects(tmp_path, [
        {
            "id": "uuid-meta",
            "name": "p-meta",
            "trajectory_path": str(tmp_path / "p-meta/.empirica"),
            "description": "A meta-rich project",
            "git_remote_url": "https://example.com/p-meta.git",
            "project_type": "research",
        },
    ])
    _run_backfill(tmp_path)

    db = tmp_path / ".empirica/workspace/workspace.db"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute(
        "SELECT description, metadata FROM entity_registry "
        "WHERE entity_type = 'project' AND entity_id = ?",
        ("uuid-meta",),
    )
    desc, raw_meta = cur.fetchone()
    conn.close()
    assert desc == "A meta-rich project"
    meta = json.loads(raw_meta)
    assert meta["git_remote_url"] == "https://example.com/p-meta.git"
    assert meta["project_type"] == "research"
    assert meta["trajectory_path"].endswith("/p-meta/.empirica")
