"""_adopt_existing_project_id — adopt-before-mint for session-create --auto-init.

Regression guard for the recurring inverted-correction data-integrity bug
(mesh prop_xa6djztv5rfbnhvkcts63q6vba): --auto-init minted a fresh project_id
whenever project.yaml was absent, even when the path already carried a canonical
id in the local sessions.db / registry.yaml / workspace.db — then the mint
"corrected" the canonical workspace row away, stranding the live session under a
phantom id. The helper adopts the existing id (authority order: sessions.db →
registry.yaml → workspace.db); mints only when all three are empty.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from empirica.cli.command_handlers.workspace_init import _adopt_existing_project_id

_CANON = "e15a54eb-0000-4000-8000-000000000001"
_OTHER = "9e2b958f-0000-4000-8000-000000000002"


def _mk_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "empirica-screenshot"
    (repo / ".empirica").mkdir(parents=True)
    return repo


def _seed_local_sessions_db(repo: Path, project_id: str) -> None:
    db = repo / ".empirica" / "sessions" / "sessions.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE projects (id TEXT, name TEXT, created_timestamp REAL)")
    conn.execute("INSERT INTO projects VALUES (?, ?, 0)", (project_id, repo.name))
    conn.commit()
    conn.close()


def _seed_workspace_db(home: Path, trajectory_path: str, project_id: str) -> None:
    db = home / ".empirica" / "workspace" / "workspace.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE global_projects (id TEXT, trajectory_path TEXT)")
    conn.execute("INSERT INTO global_projects VALUES (?, ?)", (project_id, trajectory_path))
    conn.commit()
    conn.close()


def _seed_registry(home: Path, repo: Path, project_id: str) -> None:
    import yaml

    reg = home / ".empirica" / "registry.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(
        yaml.dump({"version": 1, "projects": [{"project_id": project_id, "path": str(repo)}]}),
        encoding="utf-8",
    )


def test_adopts_from_local_sessions_db(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    repo = _mk_repo(tmp_path)
    _seed_local_sessions_db(repo, _CANON)
    pid, source = _adopt_existing_project_id(repo)
    assert pid == _CANON
    assert source == "local sessions.db"


def test_adopts_from_registry_when_no_local_db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    repo = _mk_repo(tmp_path)
    _seed_registry(home, repo, _CANON)
    # DEFAULT_REGISTRY_PATH is bound at import time, so Path.home() patching
    # alone doesn't redirect load_registry() — point the constant at the tmp file.
    monkeypatch.setattr(
        "empirica.api.registry.DEFAULT_REGISTRY_PATH",
        home / ".empirica" / "registry.yaml",
    )
    pid, source = _adopt_existing_project_id(repo)
    assert pid == _CANON
    assert source == "registry.yaml"


def test_adopts_from_workspace_db_as_last_local_source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    repo = _mk_repo(tmp_path)
    _seed_workspace_db(home, str(repo / ".empirica"), _CANON)
    pid, source = _adopt_existing_project_id(repo)
    assert pid == _CANON
    assert source == "workspace.db"


def test_local_db_wins_over_workspace_on_disagreement(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    repo = _mk_repo(tmp_path)
    _seed_local_sessions_db(repo, _CANON)  # authoritative
    _seed_workspace_db(home, str(repo / ".empirica"), _OTHER)  # drifted
    pid, source = _adopt_existing_project_id(repo)
    assert pid == _CANON  # client-UUID-wins — sessions.db is source of truth
    assert source == "local sessions.db"


def test_returns_none_for_genuinely_new_project(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    repo = _mk_repo(tmp_path)  # no sessions.db, no registry, no workspace.db
    pid, source = _adopt_existing_project_id(repo)
    assert pid is None
    assert source is None
