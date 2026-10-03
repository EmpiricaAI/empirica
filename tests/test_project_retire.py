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
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import empirica.data.repositories.workspace_db as wdb
from empirica.cli.command_handlers import project_update as pu
from empirica.cli.command_handlers.entity_commands import handle_entity_delete_command
from empirica.data.repositories.workspace_db import WorkspaceDBRepository

PID = "p-retire"

_REAL_PROJECT_YAML = Path(__file__).parent.parent / ".empirica" / "project.yaml"


@pytest.fixture(autouse=True)
def _the_real_practice_is_untouched():
    """A tripwire. `handle_project_update_command` resolves its project through a git-root lookup that is
    cached for the whole pytest process, and an earlier version of one test here rewrote THIS repository's
    own project.yaml (status archived, eleven keys dropped) and wrote an auto-captured issue into its store.
    Whatever a test in this module does, the real file must come out byte-identical."""
    before = _REAL_PROJECT_YAML.read_bytes() if _REAL_PROJECT_YAML.exists() else None
    yield
    after = _REAL_PROJECT_YAML.read_bytes() if _REAL_PROJECT_YAML.exists() else None
    assert after == before, "a test in this module changed the repository's own .empirica/project.yaml"


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


# ── reviewer findings (1.14.6 broccoli sweep) ───────────────────────────────


def test_a_registry_row_left_behind_by_an_earlier_force_delete_can_be_cleared(store, capsys):
    """delete_project commits first, and a --force delete before the fix left the registry row behind. With
    the project row already gone the verb answered ok / deleted:false and the row could never be cleared."""
    with WorkspaceDBRepository.open() as r:
        r.delete_project(PID, force=True)
        assert r.get_project_by_id(PID) is None and r.get_entity("project", PID) is not None

    rc = handle_entity_delete_command(_hard())

    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and "entity_row" in out
    with WorkspaceDBRepository.open() as r:
        assert r.get_entity("project", PID) is None


def test_an_interrupted_retirement_is_finished_by_running_it_again(store, capsys, monkeypatch):
    """The two deletes are not one transaction. If the second fails, a retry must complete it."""
    real = WorkspaceDBRepository.delete_entity_hard
    calls = {"n": 0}

    def fail_once(self, entity_type, entity_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(self, entity_type, entity_id)

    monkeypatch.setattr(WorkspaceDBRepository, "delete_entity_hard", fail_once)
    # handle_cli_error auto-captures the exception as an issue in the CWD project's store: here, this repository's own.
    monkeypatch.setattr("empirica.cli.command_handlers.entity_commands.handle_cli_error", lambda *_a, **_k: None)
    try:
        handle_entity_delete_command(_hard())
    except SystemExit:
        pass
    capsys.readouterr()
    with WorkspaceDBRepository.open() as r:
        assert r.get_project_by_id(PID) is None and r.get_entity("project", PID) is not None, "the half-done state"

    rc = handle_entity_delete_command(_hard())

    assert rc == 0
    with WorkspaceDBRepository.open() as r:
        assert r.get_entity("project", PID) is None


def test_the_human_view_shows_the_registry_row_removal_and_no_false_qdrant_line(store, capsys):
    with WorkspaceDBRepository.open() as r:
        r.delete_project(PID, force=True)
    capsys.readouterr()

    handle_entity_delete_command(_hard(output="human"))

    out = capsys.readouterr().out
    assert "registry" in out.lower()
    assert "UNCHECKED" not in out, (
        "no qdrant check was made for an absent project row, so none may be claimed unchecked"
    )


def test_project_update_reports_not_ok_when_the_workspace_sync_errors(tmp_path, monkeypatch, capsys):
    """`ok` is the field a caller checks. The failure the change exposed must not leave it true."""
    root = tmp_path / "co"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text("project_id: p-x\nname: co\ntype: software\n")
    monkeypatch.chdir(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    import empirica.config.path_resolver as pr

    # The handler's git-root lookup is cached per process; pin it to the scratch checkout.
    monkeypatch.setattr(pr, "get_git_root", lambda: root)
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"")  # exists, holds no tables
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: broken)

    pu.handle_project_update_command(SimpleNamespace(status="archived", output="json"))

    out = json.loads(capsys.readouterr().out)
    assert out["synced"]["workspace_db"].startswith("error")
    assert out["ok"] is False


def test_a_workspace_sync_that_updated_nothing_because_nothing_is_registered_is_not_an_error(store):
    """Control for the rule above: 'no row' is an honest state, not a failure."""
    assert pu._synced_ok({"sessions_db": "no database", "workspace_db": "no row for project_id"}) is True
    assert pu._synced_ok({"sessions_db": "updated", "workspace_db": "error: OperationalError: x"}) is False


def test_the_workspace_sync_keeps_metadata_keys_it_does_not_own(store):
    import sqlite3

    con = sqlite3.connect(store)
    con.execute("UPDATE global_projects SET metadata = ? WHERE id = ?", (json.dumps({"keep": "me", "other": 1}), PID))
    con.commit()
    con.close()

    assert pu._sync_workspace_db(_config()) == "updated"

    meta = json.loads(
        sqlite3.connect(store).execute("SELECT metadata FROM global_projects WHERE id = ?", (PID,)).fetchone()[0]
    )
    assert meta["keep"] == "me" and meta["other"] == 1 and "evidence_profile" in meta


# ── the sources-reconcile entity swap, live since the constructor fix ───────


def _link(con, artifact_id, entity_id):
    import uuid

    con.execute(
        "INSERT INTO entity_artifacts (id, artifact_type, artifact_id, entity_type, entity_id) "
        "VALUES (?, 'source', ?, 'project', ?)",
        (uuid.uuid4().hex, artifact_id, entity_id),
    )


def test_the_swap_does_not_create_a_workspace_db_that_is_not_there(tmp_path, monkeypatch):
    from empirica.cli.command_handlers.sources_reconcile_commands import _swap_workspace_entity_links

    dbp = tmp_path / "absent" / "workspace.db"
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: dbp)

    result = _swap_workspace_entity_links("L", "C")

    assert result.startswith("skipped") and not dbp.exists() and not dbp.parent.exists()


def test_one_conflicting_link_does_not_abort_the_swap_of_the_others(store):
    """UNIQUE(artifact_type, artifact_id, entity_type, entity_id): the cortex id may already be linked to an
    entity the local id is linked to. That local row is a duplicate and goes; the rest still swap."""
    import sqlite3

    from empirica.cli.command_handlers.sources_reconcile_commands import _swap_workspace_entity_links

    con = sqlite3.connect(store)
    _link(con, "L", "p1")
    _link(con, "C", "p1")  # the conflict
    _link(con, "L", "e1")
    con.commit()
    con.close()

    result = _swap_workspace_entity_links("L", "C")

    assert result.startswith("updated_")
    rows = (
        sqlite3.connect(store)
        .execute("SELECT artifact_id, entity_id FROM entity_artifacts ORDER BY entity_id")
        .fetchall()
    )
    assert rows == [("C", "e1"), ("C", "p1")], "no local id left, and the duplicate collapsed to one link"
