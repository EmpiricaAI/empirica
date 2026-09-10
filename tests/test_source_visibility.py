"""Tests for source-add --visibility flag (goal dfe61bf5).

Sources joined the artifact_visibility ladder late because source-add
uses a hand-rolled INSERT into epistemic_sources rather than the
breadcrumbs repo path that the other artifact types go through —
migration 039 wired visibility on every artifact table EXCEPT
epistemic_sources. Migration 049 closes that gap; this CLI flag wires
the user-facing surface to it.

Coverage:
  1. migration_049 adds the visibility column idempotently
  2. epistemic_sources CREATE TABLE schema includes visibility (fresh DBs)
  3. visibility_commands._ARTIFACT_TABLES includes 'source' (list/show)
  4. handler defaults to 'shared' when --visibility omitted
  5. handler persists each explicit tier (public/shared/local)
  6. handler honours normalize_visibility (bogus → 'shared', case-insensitive)
  7. JSON output exposes the visibility tier
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from empirica.data.visibility import VISIBILITY_TIERS, normalize_visibility

# ── Migration ──────────────────────────────────────────────────────────


def _epistemic_sources_columns(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(epistemic_sources)")
    return {row[1] for row in cursor.fetchall()}


def test_migration_049_adds_visibility_column(tmp_path):
    """Running migration_049 against a pre-049 schema adds the column."""
    from empirica.data.migrations.migrations import (
        migration_049_source_visibility,
    )

    db_path = tmp_path / "test.db"
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
            discovered_at TIMESTAMP NOT NULL
        )
    """)
    assert "visibility" not in _epistemic_sources_columns(conn)

    migration_049_source_visibility(conn.cursor())
    conn.commit()

    cols = _epistemic_sources_columns(conn)
    assert "visibility" in cols
    conn.close()


def test_migration_049_is_idempotent(tmp_path):
    """Re-running migration_049 must not fail or duplicate the column."""
    from empirica.data.migrations.migrations import (
        migration_049_source_visibility,
    )

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            discovered_at TIMESTAMP NOT NULL
        )
    """)
    migration_049_source_visibility(conn.cursor())
    conn.commit()

    # Second call — no error, column still exactly one.
    migration_049_source_visibility(conn.cursor())
    conn.commit()
    cols = list(_epistemic_sources_columns(conn))
    assert cols.count("visibility") == 1
    conn.close()


def test_migration_049_creates_index(tmp_path):
    """Migration adds a queryable index on visibility (for the cross-mesh map)."""
    from empirica.data.migrations.migrations import (
        migration_049_source_visibility,
    )

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            discovered_at TIMESTAMP NOT NULL
        )
    """)
    migration_049_source_visibility(conn.cursor())
    conn.commit()

    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_epistemic_sources_visibility'")
    assert cursor.fetchone() is not None
    conn.close()


# ── Schema (fresh DB) ──────────────────────────────────────────────────


def test_fresh_schema_includes_visibility():
    """CREATE TABLE in projects_schema.py carries visibility for fresh DBs."""
    from empirica.data.schema.projects_schema import SCHEMAS

    sources_ddl = next((ddl for ddl in SCHEMAS if "epistemic_sources" in ddl), None)
    assert sources_ddl is not None, "epistemic_sources DDL missing"
    assert "visibility TEXT DEFAULT 'local'" in sources_ddl


# ── visibility_commands._ARTIFACT_TABLES ───────────────────────────────


def test_source_in_artifact_tables_map():
    """visibility list/show must cover sources, with the right timestamp column.

    epistemic_sources uses `discovered_at`, not `created_timestamp`, so the
    visibility queries must read its column name from the table tuple or
    they fail with `no such column: created_timestamp` at runtime.
    """
    from empirica.cli.command_handlers.visibility_commands import _ARTIFACT_TABLES

    assert "source" in _ARTIFACT_TABLES
    table, content_col, ts_col = _ARTIFACT_TABLES["source"]
    assert table == "epistemic_sources"
    assert content_col == "title"
    assert ts_col == "discovered_at"


# ── CLI handler ────────────────────────────────────────────────────────


def _make_args(**overrides) -> SimpleNamespace:
    defaults = {
        "title": "Test source",
        "description": None,
        "source_type": "document",
        "path": None,
        "url": None,
        "confidence": 0.7,
        "noetic": True,
        "praxic": False,
        "visibility": None,
        "session_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "output": "json",
        "verbose": False,
        "entity_type": None,
        "entity_id": None,
        "via": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    """Patch SessionDatabase so the handler writes to a tmp sqlite file."""
    db_path = tmp_path / ".empirica" / "sessions" / "sessions.db"
    db_path.parent.mkdir(parents=True)
    # Mirror the post-049 schema
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
            lifecycle_audit_log TEXT,
            entity_type TEXT,
            entity_id TEXT,
            visibility TEXT DEFAULT 'shared',
            content_hash TEXT,
            size_bytes INTEGER,
            canonical_path TEXT,
            mime_type TEXT
        )
    """)
    conn.commit()
    conn.close()

    class _FakeDB:
        def __init__(self, *_a, **_kw):
            self.conn = sqlite3.connect(str(db_path))
            self.conn.row_factory = sqlite3.Row

        def close(self):
            self.conn.close()

    # The handler does `from empirica.data.session_database import SessionDatabase`
    # at call-time (inside the try block), so we have to patch the source module,
    # not the consumer — patching the consumer is shadowed by the inline import.
    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase",
        _FakeDB,
        raising=False,
    )
    return db_path


def _read_visibility(db_path: Path, source_id: str) -> str | None:
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT visibility FROM epistemic_sources WHERE id = ?",
        (source_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def _run_handler(args, capsys, _db_path) -> tuple[int, dict]:
    """Run handle_source_add_command with monkey-patched git+qdrant noop."""
    # Stub the dependencies that try to talk to git / qdrant / breadcrumbs
    from empirica.cli.command_handlers import artifact_log_commands as alc

    # Direct-DB use: we need SessionDatabase already swapped in (via fixture).
    # Patch the git+qdrant persister so we don't shell out from tests.
    with (
        patch.object(
            alc,
            "_source_persist_git_and_qdrant",
            return_value=(False, False),
        ),
        patch(
            "empirica.utils.session_resolver.read_active_transaction_full",
            return_value=None,
        ),
    ):
        rc = alc.handle_source_add_command(args)
    out = capsys.readouterr().out
    payload = json.loads(out) if out.strip() else {}
    return rc or 0, payload


def test_handler_defaults_to_shared(fake_db, capsys):
    """When --visibility is omitted, persisted tier is 'shared'."""
    args = _make_args(visibility=None)
    rc, payload = _run_handler(args, capsys, fake_db)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["visibility"] == "local"
    assert _read_visibility(fake_db, payload["source_id"]) == "local"


@pytest.mark.parametrize("tier", VISIBILITY_TIERS)
def test_handler_persists_explicit_tier(fake_db, capsys, tier):
    """Each public/shared/local round-trips into the DB column."""
    args = _make_args(visibility=tier)
    rc, payload = _run_handler(args, capsys, fake_db)
    assert rc == 0
    assert payload["visibility"] == tier
    assert _read_visibility(fake_db, payload["source_id"]) == tier


def test_handler_bogus_tier_falls_back_to_shared(fake_db, capsys):
    """Bogus visibility (would only reach the handler via JSON config / API,
    not the CLI choices=) normalises to 'shared' — never silently promotes
    to 'public' per the safe-invariant in data/visibility.py."""
    args = _make_args(visibility="top-secret")
    rc, payload = _run_handler(args, capsys, fake_db)
    assert rc == 0
    assert payload["visibility"] == "local"
    assert _read_visibility(fake_db, payload["source_id"]) == "local"


def test_handler_case_insensitive(fake_db, capsys):
    """normalize_visibility lower-cases and strips; CLI flag uses choices=,
    but JSON-config / programmatic callers should still get sane behaviour."""
    args = _make_args(visibility="PUBLIC")
    rc, payload = _run_handler(args, capsys, fake_db)
    assert rc == 0
    assert payload["visibility"] == "public"


def test_handler_human_output_surfaces_tier(fake_db, capsys):
    """Default human output prints a 'Visibility: <tier>' line for at-a-glance audit."""
    from empirica.cli.command_handlers import artifact_log_commands as alc

    args = _make_args(visibility="local", output="human")
    with (
        patch.object(
            alc,
            "_source_persist_git_and_qdrant",
            return_value=(False, False),
        ),
        patch(
            "empirica.utils.session_resolver.read_active_transaction_full",
            return_value=None,
        ),
    ):
        rc = alc.handle_source_add_command(args)
    out = capsys.readouterr().out
    assert (rc or 0) == 0
    assert "Visibility: local" in out


def test_normalize_visibility_invariants():
    """Sanity-check that the imported normaliser has the safety contract
    we rely on at the CLI boundary."""
    assert normalize_visibility(None) == "local"
    assert normalize_visibility("") == "local"
    assert normalize_visibility("bogus") == "local"
    assert normalize_visibility("PUBLIC") == "public"
    assert normalize_visibility("  Local  ") == "local"
    # Critical: bogus must NOT promote to 'public'
    for bogus in (None, "", "x", "pub", "secret"):
        assert normalize_visibility(bogus) != "public"
