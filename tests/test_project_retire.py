"""Retiring a practice: `project-update --status archived` reaches global_projects, and a hard delete
is not blocked by the project's own registry row.

ecodex ran the verbs a peer named (prop_42avmw5tlnef7neqmw5vujsdou) and both failed, each silently or
in a way no flag could clear:

* `project-update` wrote project.yaml and reported ok while `global_projects` stayed active, because
  its workspace half built `WorkspaceDBRepository()` with no connection (a TypeError) inside
  `except Exception: pass`.
* `entity-delete project:<id> --hard --confirm` refused on `{'entity_registry': 1}`, which was the
  project's own row. The verb needs that row to exist before it starts, so the refusal could only
  be cleared with --force, which then leaves the row behind.

tests/test_project_delete_routes_not_reaches.py never gave its project an entity row, which is the
state project-init creates, so it pinned a world in which the second defect could not occur.
Everything is built under tmp_path.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import empirica.data.repositories.workspace_db as wdb
from empirica.cli.command_handlers import project_update as pu
from empirica.cli.command_handlers.entity_commands import handle_entity_delete_command
from empirica.data.repositories.workspace_db import WorkspaceDBRepository

PID = "p-retire"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A workspace.db holding what project-init leaves: the project row AND its own registry row."""
    dbp = tmp_path / "workspace.db"
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: dbp)
    monkeypatch.setenv("HOME", str(tmp_path))
    with WorkspaceDBRepository.open(ensure_schema=True) as r:
        r.upsert_project(project_id=PID, name="retire", trajectory_path=str(tmp_path / "retire" / ".empirica"))
        r.upsert_entity("project", PID, "retire", "workspace.db", "global_projects")
    _no_qdrant(monkeypatch)
    return dbp


def _no_qdrant(monkeypatch):
    import urllib.request

    class _R:
        def read(self):
            return b'{"result":{"collections":[]}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _R())


def _config(**over):
    base = {
        "project_id": PID,
        "type": "software",
        "tags": ["x"],
        "status": "archived",
        "domain": None,
        "classification": None,
        "evidence_profile": "auto",
        "languages": [],
        "contacts": [],
        "engagements": [],
        "edges": [],
    }
    return SimpleNamespace(**{**base, **over})


def _status() -> str | None:
    with WorkspaceDBRepository.open() as r:
        row = r.get_project_by_id(PID)
        return row["status"] if row else None


# ── project-update reaches the workspace row ────────────────────────────────


def test_project_update_status_reaches_global_projects(store):
    assert _status() == "active"

    assert pu._sync_workspace_db(_config()) == "updated"

    assert _status() == "archived"


def test_a_project_nothing_registered_is_reported_not_called_updated(store):
    assert pu._sync_workspace_db(_config(project_id="p-unregistered")) == "no row for project_id"


def test_no_workspace_db_is_reported_and_none_is_created(tmp_path, monkeypatch):
    dbp = tmp_path / "absent" / "workspace.db"
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: dbp)

    assert pu._sync_workspace_db(_config()) == "no database"
    assert not dbp.exists() and not dbp.parent.exists()


def test_a_failure_is_returned_as_an_error_not_swallowed(store, monkeypatch):
    import sqlite3

    def boom(*_a, **_k):
        raise TypeError("open() takes no connection here")

    monkeypatch.setattr(WorkspaceDBRepository, "open", classmethod(lambda _cls, *_a, **_k: boom()))

    result = pu._sync_workspace_db(_config())

    assert result.startswith("error: TypeError")
    assert sqlite3.connect(store).execute("SELECT status FROM global_projects WHERE id = ?", (PID,)).fetchone() == (
        "active",
    )


def test_a_missing_project_id_is_reported(store):
    assert pu._sync_workspace_db(_config(project_id=None)) == "no row for project_id"


def test_sync_to_db_reports_both_stores_and_syncs_the_workspace_without_a_sessions_db(store, tmp_path):
    """A practice that was bootstrapped but never ran a session has a workspace row and no sessions.db;
    the early return for the missing sessions.db used to skip the workspace sync as well."""
    root = tmp_path / "checkout"
    (root / ".empirica").mkdir(parents=True)

    synced = pu._sync_to_db(_config(), root)

    assert synced == {"sessions_db": "no database", "workspace_db": "updated"}
    assert _status() == "archived"


def test_no_call_site_builds_a_workspace_repository_without_its_connection():
    """The class behind the first defect: `WorkspaceDBRepository()` with no argument raises, and all
    three call sites sat inside a catch-all, so none ever said so."""
    root = Path(__file__).parent.parent / "empirica"
    bad = []
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "WorkspaceDBRepository"
                and not node.args
                and not node.keywords
            ):
                bad.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    assert bad == []


# ── a hard delete is not blocked by its own registry row ────────────────────


def test_the_projects_own_registry_row_is_not_a_reference(store):
    with WorkspaceDBRepository.open() as r:
        assert r.project_references(PID)["entity_registry"] == 0


def test_another_entity_carrying_the_id_still_blocks(store):
    with WorkspaceDBRepository.open() as r:
        r.upsert_entity("organization", PID, "an org with the same id", "workspace.db", "orgs")
        refs = r.project_references(PID)
        result = r.delete_project(PID)

    assert refs["entity_registry"] == 1
    assert result["ok"] is False and "entity_registry" in result["error"]


def test_a_membership_pointing_at_the_project_still_blocks(store):
    with WorkspaceDBRepository.open() as r:
        r.upsert_entity("contact", "c-1", "Someone", "workspace.db", "contacts")
        r.upsert_entity_membership("contact", "c-1", "project", PID, role="member")
        result = r.delete_project(PID)

    assert result["ok"] is False and "entity_memberships" in result["error"]


def _hard(**kw):
    base = {
        "entity": f"project:{PID}",
        "entity_type": None,
        "entity_id": None,
        "hard": True,
        "confirm": True,
        "dry_run": False,
        "force": False,
        "output": "json",
        "verbose": False,
    }
    return SimpleNamespace(**{**base, **kw})


def test_hard_delete_removes_the_project_row_and_its_own_registry_row_without_force(store, capsys):
    rc = handle_entity_delete_command(_hard())

    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["deleted"] is True
    assert "entity_row" in out
    with WorkspaceDBRepository.open() as r:
        assert r.get_project_by_id(PID) is None
        assert r.get_entity("project", PID) is None, "the project's own registry row must go with it"


def test_a_dry_run_deletes_nothing(store, capsys):
    handle_entity_delete_command(_hard(dry_run=True))

    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    with WorkspaceDBRepository.open() as r:
        assert r.get_project_by_id(PID) is not None and r.get_entity("project", PID) is not None


def test_a_refused_hard_delete_leaves_the_registry_row_alone(store, capsys):
    with WorkspaceDBRepository.open() as r:
        r.upsert_entity("contact", "c-1", "Someone", "workspace.db", "contacts")
        r.upsert_entity_membership("contact", "c-1", "project", PID, role="member")

    rc = handle_entity_delete_command(_hard())

    assert rc == 1 and json.loads(capsys.readouterr().out)["ok"] is False
    with WorkspaceDBRepository.open() as r:
        assert r.get_project_by_id(PID) is not None and r.get_entity("project", PID) is not None
