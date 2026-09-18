"""Tests for `empirica sources-map` (goal 74d35435 — cross-mesh source map).

The Maven-POM-for-knowledge view: locally-owned sources + (with --global)
sources discoverable across other practices' Qdrant collections. v1 is
a read-only assembly over existing data — no new schema, no embedding
pipeline changes, no cortex dependency.

Coverage:
  1. owned mode (no --global): returns local sources, empty discoverable
  2. project_id resolution failure → ok=False
  3. --global flag wires search_cross_project + post-filters to type='source'
  4. type filter passes through to both owned + discoverable
  5. _query_cross_mesh_sources excludes the current project_id
  6. an unreachable index is reported as not searched (count None), never as 0 found
  7. JSON output schema is stable
  8. Discoverable empty when there are no cross-project source hits
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    """In-memory project-shaped sqlite with epistemic_sources schema.

    Mirrors the post-049 column set so the handler's _query_epistemic_sources
    finds something to list.
    """
    db_path = tmp_path / ".empirica" / "sessions" / "sessions.db"
    db_path.parent.mkdir(parents=True)
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
            visibility TEXT DEFAULT 'shared'
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

    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase",
        _FakeDB,
        raising=False,
    )
    return db_path


def _seed_owned_source(db_path, project_id, title="Local doc", source_type="document"):
    sid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO epistemic_sources (id, project_id, source_type, title, "
        "discovered_at, epistemic_layer, archived) "
        "VALUES (?, ?, ?, ?, ?, ?, 0)",
        (sid, project_id, source_type, title, "2026-06-07T00:00:00", "noetic"),
    )
    conn.commit()
    conn.close()
    return sid


def _make_args(**overrides) -> SimpleNamespace:
    defaults = {
        "project_id": str(uuid.uuid4()),
        "include_global": False,
        "query": None,
        "source_type": None,
        "limit": 20,
        "output": "json",
        "verbose": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _run(args, capsys) -> tuple[int, dict]:
    from empirica.cli.command_handlers.artifact_log_commands import (
        handle_sources_map_command,
    )

    rc = handle_sources_map_command(args)
    out = capsys.readouterr().out
    payload = json.loads(out) if out.strip() else {}
    return rc or 0, payload


# ── owned-only mode ────────────────────────────────────────────────────


def test_owned_mode_lists_local_sources(fake_db, capsys):
    project_id = "11111111-1111-1111-1111-111111111111"
    _seed_owned_source(fake_db, project_id, title="RFC 7519")
    _seed_owned_source(fake_db, project_id, title="Internal design doc")

    args = _make_args(project_id=project_id, include_global=False)
    rc, payload = _run(args, capsys)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["owned"]["count"] == 2
    titles = {s["title"] for s in payload["owned"]["sources"]}
    assert titles == {"RFC 7519", "Internal design doc"}
    # --global not set → discoverable empty + scope note explains
    assert payload["discoverable"]["count"] == 0
    assert "skipped" in payload["discoverable"]["scope"]


def test_no_project_id_returns_error(fake_db, capsys, monkeypatch):
    """When project_id can't resolve, handler returns ok=False."""
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.project_path",
        classmethod(lambda cls: None),
    )
    args = _make_args(project_id=None)
    rc, payload = _run(args, capsys)
    assert rc == 1
    assert payload["ok"] is False


def test_owned_filter_by_type(fake_db, capsys):
    project_id = "22222222-2222-2222-2222-222222222222"
    _seed_owned_source(fake_db, project_id, title="A doc", source_type="document")
    _seed_owned_source(fake_db, project_id, title="A url", source_type="web")
    args = _make_args(project_id=project_id, source_type="web")
    rc, payload = _run(args, capsys)
    assert rc == 0
    assert payload["owned"]["count"] == 1
    assert payload["owned"]["sources"][0]["title"] == "A url"


# ── --global / cross-mesh discoverable ─────────────────────────────────


@pytest.fixture(autouse=True)
def _qdrant_reachable(monkeypatch):
    """The --global path now asks whether Qdrant is reachable before searching.
    Pin it True so these tests do not measure whether this box runs Qdrant;
    the unreachable case sets it False explicitly."""
    monkeypatch.setattr("empirica.core.qdrant.global_sync._check_qdrant_available", lambda: True)


def test_global_mode_wires_search_cross_project(fake_db, capsys):
    """--global → handler calls search_cross_project + post-filters to source."""
    project_id = "33333333-3333-3333-3333-333333333333"
    other_project = "44444444-4444-4444-4444-444444444444"
    _seed_owned_source(fake_db, project_id)

    fake_hits = [
        # A real source (type='source')
        {
            "type": "source",
            "item_id": "sid-a",
            "project_id": other_project,
            "text": "External canonical doc",
            "score": 0.93,
            "source_type": "document",
        },
        # Noise (type='finding') — must be filtered out
        {"type": "finding", "item_id": "fid-a", "project_id": other_project, "text": "Some finding"},
        # Another real source
        {
            "type": "source",
            "item_id": "sid-b",
            "project_id": other_project,
            "text": "Another doc",
            "score": 0.81,
            "source_type": "document",
        },
    ]
    with patch(
        "empirica.core.qdrant.global_sync.search_cross_project",
        return_value=fake_hits,
    ) as mock_search:
        args = _make_args(project_id=project_id, include_global=True, query="rfc")
        rc, payload = _run(args, capsys)
    assert rc == 0
    # Search was called and excluded the current project_id
    mock_search.assert_called_once()
    call_kwargs = mock_search.call_args.kwargs
    assert call_kwargs["exclude_project_id"] == project_id
    assert call_kwargs["query_text"] == "rfc"

    # Only the type='source' hits surface; the finding is dropped
    assert payload["discoverable"]["count"] == 2
    ids = [s["source_id"] for s in payload["discoverable"]["sources"]]
    assert set(ids) == {"sid-a", "sid-b"}
    assert payload["discoverable"]["scope"] == "cross-mesh"


def test_global_post_filter_by_source_type(fake_db, capsys):
    """--type web should drop type='source' but source_type!='web'."""
    project_id = "55555555-5555-5555-5555-555555555555"
    fake_hits = [
        {"type": "source", "item_id": "sid-doc", "project_id": "p2", "text": "doc", "source_type": "document"},
        {"type": "source", "item_id": "sid-web", "project_id": "p2", "text": "url", "source_type": "web"},
    ]
    with patch(
        "empirica.core.qdrant.global_sync.search_cross_project",
        return_value=fake_hits,
    ):
        args = _make_args(project_id=project_id, include_global=True, source_type="web")
        rc, payload = _run(args, capsys)
    assert rc == 0
    assert payload["discoverable"]["count"] == 1
    assert payload["discoverable"]["sources"][0]["source_id"] == "sid-web"


def test_global_search_failure_is_not_searched_rather_than_zero(fake_db, capsys):
    """search_cross_project raising → ok=True (discoverability is not a hard
    dependency) but the count is None and the scope says why. This test used to
    assert count == 0, which is exactly the answer "looked and found nothing":
    it pinned an unreachable index rendering as an empty mesh."""
    project_id = "66666666-6666-6666-6666-666666666666"
    with patch(
        "empirica.core.qdrant.global_sync.search_cross_project",
        side_effect=RuntimeError("Qdrant unreachable"),
    ):
        args = _make_args(project_id=project_id, include_global=True)
        rc, payload = _run(args, capsys)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["discoverable"]["count"] is None
    assert payload["discoverable"]["scope"].startswith("unavailable")


def test_global_unreachable_qdrant_is_not_searched(fake_db, capsys, monkeypatch):
    project_id = "66666666-6666-6666-6666-666666666667"
    monkeypatch.setattr("empirica.core.qdrant.global_sync._check_qdrant_available", lambda: False)
    with patch("empirica.core.qdrant.global_sync.search_cross_project") as mock_search:
        args = _make_args(project_id=project_id, include_global=True)
        rc, payload = _run(args, capsys)
    assert rc == 0
    mock_search.assert_not_called()
    assert payload["discoverable"]["count"] is None
    assert "qdrant unreachable" in payload["discoverable"]["scope"]


def test_global_empty_query_uses_neutral_anchor(fake_db, capsys):
    """No --query → handler uses 'epistemic source' as a neutral semantic anchor."""
    project_id = "77777777-7777-7777-7777-777777777777"
    with patch(
        "empirica.core.qdrant.global_sync.search_cross_project",
        return_value=[],
    ) as mock_search:
        args = _make_args(project_id=project_id, include_global=True)
        _run(args, capsys)
    call_kwargs = mock_search.call_args.kwargs
    # Empty query must NOT pass through as '' (would fail embedding) —
    # handler should substitute a safe anchor
    assert call_kwargs["query_text"] != ""
    assert "source" in call_kwargs["query_text"].lower()


# ── Output schema ──────────────────────────────────────────────────────


def test_json_output_schema_stable(fake_db, capsys):
    """Schema lock — owned + discoverable must always be present with
    consistent shape so extensions / scripts can rely on it."""
    project_id = "88888888-8888-8888-8888-888888888888"
    _seed_owned_source(fake_db, project_id)

    args = _make_args(project_id=project_id, include_global=False)
    rc, payload = _run(args, capsys)
    assert rc == 0
    # Top-level keys
    assert set(payload.keys()) == {"ok", "project_id", "owned", "discoverable"}
    # Nested shape
    assert set(payload["owned"].keys()) == {"count", "sources"}
    assert set(payload["discoverable"].keys()) == {"count", "sources", "scope"}
