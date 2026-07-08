"""Tests for source-archive CLI (SOURCES_LIFECYCLE_SPEC Phase 1)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from empirica.cli.command_handlers.artifact_log_commands import (
    _VALID_ARCHIVE_REASONS,
    _push_source_archive_to_cortex,
    _query_epistemic_sources,
    handle_source_archive_command,
)

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_home_and_cortex_env(monkeypatch, tmp_path):
    """Universal isolation — every test gets a fake HOME and clean Cortex env.

    Without this, tests that expect 'no Cortex creds' semantics fail
    on dev machines where ~/.empirica/credentials.yaml has a cortex
    block (and the credentials_loader falls through to it when env
    vars are cleared). The same pattern is used in
    test_cortex_credentials_loader.py + test_projects_discover.py.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("EMPIRICA_CREDENTIALS_PATH", raising=False)

    # Reset both singleton + module-level loader globals so the next
    # get_credentials_loader() call re-reads from the isolated HOME.
    from empirica.config import credentials_loader as cl_mod
    from empirica.config.credentials_loader import CredentialsLoader

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    cl_mod._loader = None


@pytest.fixture
def project_db(tmp_path: Path) -> Path:
    """Project-shaped sqlite DB with epistemic_sources schema (post-044)."""
    db_dir = tmp_path / ".empirica" / "sessions"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            source_type TEXT NOT NULL,
            source_url TEXT,
            title TEXT NOT NULL,
            description TEXT,
            confidence REAL DEFAULT 0.5,
            epistemic_layer TEXT,
            supports_vectors TEXT,
            related_findings TEXT,
            discovered_by_ai TEXT,
            discovered_at TIMESTAMP NOT NULL,
            source_metadata TEXT,
            archived BOOLEAN DEFAULT 0,
            archive_reason TEXT,
            archive_target_id TEXT,
            archived_at REAL,
            lifecycle_audit_log TEXT
        )
    """)
    conn.commit()
    conn.close()
    return tmp_path


def _seed_source(db_path: Path, project_id: str = "p1", **overrides) -> str:
    """Insert a source row, return its UUID."""
    sid = overrides.get("id", str(uuid.uuid4()))
    conn = sqlite3.connect(str(db_path / ".empirica" / "sessions" / "sessions.db"))
    conn.execute(
        "INSERT INTO epistemic_sources "
        "(id, project_id, source_type, title, description, confidence, "
        "epistemic_layer, discovered_at, archived) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (
            sid,
            project_id,
            overrides.get("source_type", "doc"),
            overrides.get("title", "Test source"),
            overrides.get("description", ""),
            overrides.get("confidence", 0.7),
            overrides.get("epistemic_layer", "noetic"),
            overrides.get("discovered_at", "2026-05-10T00:00:00"),
        ),
    )
    conn.commit()
    conn.close()
    return sid


def _make_args(**kwargs):
    """Build an argparse-shaped namespace for handler tests."""
    base = {
        "source_id": None,
        "reason": None,
        "target_id": None,
        "output": "json",
        "verbose": False,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


# ─── Reason validation ─────────────────────────────────────────────────


def test_valid_reasons_set():
    assert _VALID_ARCHIVE_REASONS == (
        "user_deleted",
        "file_missing",
        "url_unreachable",
        "superseded",
    )


def test_invalid_reason_rejected(project_db: Path, capsys):
    with patch("empirica.data.session_database.SessionDatabase") as MockDB:
        MockDB.return_value.conn = sqlite3.connect(
            str(project_db / ".empirica" / "sessions" / "sessions.db"),
        )
        rc = handle_source_archive_command(
            _make_args(
                source_id="abc",
                reason="garbage",
            )
        )
    assert rc == 1
    out = capsys.readouterr().out
    payload = json.loads(out.strip().split("\n")[-1])
    assert payload["ok"] is False
    assert "Invalid --reason" in payload["error"]


def test_superseded_requires_target_id(project_db: Path, capsys):
    with patch("empirica.data.session_database.SessionDatabase") as MockDB:
        MockDB.return_value.conn = sqlite3.connect(
            str(project_db / ".empirica" / "sessions" / "sessions.db"),
        )
        rc = handle_source_archive_command(
            _make_args(
                source_id="abc",
                reason="superseded",
            )
        )
    assert rc == 1
    out = capsys.readouterr().out
    payload = json.loads(out.strip().split("\n")[-1])
    assert "requires --target-id" in payload["error"]


# ─── Happy paths ───────────────────────────────────────────────────────


def test_archive_user_deleted_round_trip(project_db: Path, capsys):
    sid = _seed_source(project_db)

    with patch("empirica.data.session_database.SessionDatabase") as MockDB:
        MockDB.return_value.conn = sqlite3.connect(
            str(project_db / ".empirica" / "sessions" / "sessions.db"),
        )
        with patch(
            "empirica.cli.command_handlers.artifact_log_commands._hard_delete_source_chunks",
            return_value=0,
        ):
            rc = handle_source_archive_command(
                _make_args(
                    source_id=sid,
                    reason="user_deleted",
                )
            )

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["source_id"] == sid
    assert payload["archived"] is True
    assert payload["archive_reason"] == "user_deleted"
    assert payload["archive_target_id"] is None
    assert payload["chunks_deleted"] == 0
    assert len(payload["audit_log"]) == 1
    assert payload["audit_log"][0]["event"] == "archived"
    assert payload["audit_log"][0]["reason"] == "user_deleted"

    # Verify DB persistence
    conn = sqlite3.connect(str(project_db / ".empirica" / "sessions" / "sessions.db"))
    row = conn.execute(
        "SELECT archived, archive_reason, archive_target_id, archived_at FROM epistemic_sources WHERE id = ?",
        (sid,),
    ).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == "user_deleted"
    assert row[2] is None
    assert row[3] is not None  # epoch set


def test_archive_removes_memory_embed(project_db: Path, capsys):
    """source-archive must clear the source's metadata embed from the memory
    collection (else the archived source stays discoverable via sources-map)."""
    sid = _seed_source(project_db)

    with patch("empirica.data.session_database.SessionDatabase") as MockDB:
        MockDB.return_value.conn = sqlite3.connect(
            str(project_db / ".empirica" / "sessions" / "sessions.db"),
        )
        with patch(
            "empirica.cli.command_handlers.artifact_log_commands._hard_delete_source_chunks",
            return_value=0,
        ), patch(
            "empirica.cli.command_handlers.artifact_log_commands._hard_delete_source_memory_embed",
            return_value=1,
        ) as mock_mem:
            rc = handle_source_archive_command(
                _make_args(source_id=sid, reason="user_deleted")
            )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # the memory-embed cleanup was invoked with the resolved source id ...
    mock_mem.assert_called_once()
    assert mock_mem.call_args.args[1] == sid
    # ... and its result is surfaced in the payload
    assert payload["memory_embed_deleted"] == 1


def test_archive_superseded_with_target(project_db: Path, capsys):
    src = _seed_source(project_db, title="Old version")
    replacement = _seed_source(project_db, title="New version")

    with patch("empirica.data.session_database.SessionDatabase") as MockDB:
        MockDB.return_value.conn = sqlite3.connect(
            str(project_db / ".empirica" / "sessions" / "sessions.db"),
        )
        with patch(
            "empirica.cli.command_handlers.artifact_log_commands._hard_delete_source_chunks",
            return_value=0,
        ):
            rc = handle_source_archive_command(
                _make_args(
                    source_id=src,
                    reason="superseded",
                    target_id=replacement,
                )
            )

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["archive_reason"] == "superseded"
    assert payload["archive_target_id"] == replacement


def test_archive_idempotent(project_db: Path, capsys):
    """Re-archiving an already-archived source returns 200 with existing state."""
    sid = _seed_source(project_db)

    with patch("empirica.data.session_database.SessionDatabase") as MockDB:
        MockDB.return_value.conn = sqlite3.connect(
            str(project_db / ".empirica" / "sessions" / "sessions.db"),
        )
        with patch(
            "empirica.cli.command_handlers.artifact_log_commands._hard_delete_source_chunks",
            return_value=0,
        ):
            handle_source_archive_command(
                _make_args(
                    source_id=sid,
                    reason="user_deleted",
                )
            )
            capsys.readouterr()  # discard first run's output
            rc = handle_source_archive_command(
                _make_args(
                    source_id=sid,
                    reason="user_deleted",
                )
            )

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["already_archived"] is True
    assert payload["archived"] is True
    # Audit log preserved (no second event added for idempotent re-call)
    assert len(payload["audit_log"]) == 1


def test_archive_resolves_uuid_prefix(project_db: Path, capsys):
    """Source ID prefix matching matches log-artifacts UX."""
    sid = _seed_source(project_db)
    prefix = sid[:8]

    with patch("empirica.data.session_database.SessionDatabase") as MockDB:
        MockDB.return_value.conn = sqlite3.connect(
            str(project_db / ".empirica" / "sessions" / "sessions.db"),
        )
        with patch(
            "empirica.cli.command_handlers.artifact_log_commands._hard_delete_source_chunks",
            return_value=0,
        ):
            rc = handle_source_archive_command(
                _make_args(
                    source_id=prefix,
                    reason="file_missing",
                )
            )

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["source_id"] == sid  # full UUID resolved


def test_archive_source_not_found(project_db: Path, capsys):
    with patch("empirica.data.session_database.SessionDatabase") as MockDB:
        MockDB.return_value.conn = sqlite3.connect(
            str(project_db / ".empirica" / "sessions" / "sessions.db"),
        )
        rc = handle_source_archive_command(
            _make_args(
                source_id="nonexistent-id-9999",
                reason="user_deleted",
            )
        )
    assert rc == 1
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "not found" in payload["error"].lower()


# ─── Read filter ───────────────────────────────────────────────────────


def test_query_excludes_archived_by_default(project_db: Path):
    sid_active = _seed_source(project_db, title="Alive")
    sid_archived = _seed_source(project_db, title="Dead")
    # Manually flip archived flag
    conn = sqlite3.connect(str(project_db / ".empirica" / "sessions" / "sessions.db"))
    conn.execute("UPDATE epistemic_sources SET archived=1 WHERE id=?", (sid_archived,))
    conn.commit()

    db = SimpleNamespace(conn=conn)
    db.get_project_reference_docs = lambda pid: []  # no legacy refdocs

    sources = _query_epistemic_sources(db, "p1", None, "all", include_archived=False)
    ids = [s["id"] for s in sources]
    assert sid_active in ids
    assert sid_archived not in ids
    conn.close()


def test_query_includes_archived_when_flag_set(project_db: Path):
    sid_active = _seed_source(project_db, title="Alive")
    sid_archived = _seed_source(project_db, title="Dead")
    conn = sqlite3.connect(str(project_db / ".empirica" / "sessions" / "sessions.db"))
    conn.execute("UPDATE epistemic_sources SET archived=1 WHERE id=?", (sid_archived,))
    conn.commit()

    db = SimpleNamespace(conn=conn)
    db.get_project_reference_docs = lambda pid: []

    sources = _query_epistemic_sources(db, "p1", None, "all", include_archived=True)
    ids = [s["id"] for s in sources]
    assert sid_active in ids
    assert sid_archived in ids
    archived_row = next(s for s in sources if s["id"] == sid_archived)
    assert archived_row["archived"] is True
    conn.close()


# ─── Cortex sync (Phase 1.5) ───────────────────────────────────────────


def test_cortex_push_noop_when_env_unset(monkeypatch):
    """No CORTEX_REMOTE_URL / CORTEX_API_KEY → return None (no-op)."""
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    assert _push_source_archive_to_cortex("any-id", "user_deleted", None) is None


def test_cortex_push_skips_when_only_url_set(monkeypatch):
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.example.com")
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    assert _push_source_archive_to_cortex("any-id", "user_deleted", None) is None


def test_cortex_push_skips_when_only_key_set(monkeypatch):
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.setenv("CORTEX_API_KEY", "sk-test")
    assert _push_source_archive_to_cortex("any-id", "user_deleted", None) is None


def test_cortex_push_success_path(monkeypatch):
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "sk-test")

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    with patch("urllib.request.urlopen", return_value=_FakeResponse()):
        result = _push_source_archive_to_cortex("abc-id", "user_deleted", None)
    assert result == {"synced": True, "status": 200}


def test_cortex_push_returns_failure_on_http_error(monkeypatch):
    import urllib.error

    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "sk-test")

    err = urllib.error.HTTPError(
        url="x",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=err):
        result = _push_source_archive_to_cortex("missing-id", "user_deleted", None)
    assert result is not None
    assert result["synced"] is False
    assert result["status"] == 404


def test_cortex_push_returns_failure_on_network_error(monkeypatch):
    import urllib.error

    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "sk-test")

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
        result = _push_source_archive_to_cortex("any-id", "user_deleted", None)
    assert result is not None
    assert result["synced"] is False
    assert result["status"] == 0
    assert "URLError" in result["error"]


def test_cortex_push_uses_cortex_url_fallback(monkeypatch):
    """CORTEX_URL is accepted as an alias for CORTEX_REMOTE_URL."""
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.setenv("CORTEX_URL", "https://cortex.example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "sk-test")

    class _FakeResponse:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    with patch("urllib.request.urlopen", return_value=_FakeResponse()):
        result = _push_source_archive_to_cortex("abc-id", "superseded", "new-id")
    assert result == {"synced": True, "status": 204}
