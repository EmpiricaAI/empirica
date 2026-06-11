"""Tests for `empirica sources-reconcile` — catalogue uuid adoption.

Covers the four phases against a fixture SQLite DB with mocked HTTP:
  1. Backfill: file-backed pre-050 rows get identity computed + persisted
  2. Discovery: degrades honestly when cortex is unreachable / unconfigured
  3. Matching: pairs by content_hash, skips already-reconciled rows
  4. Swap: PK + artifact_edges + archive_target_id + finding source_refs
     cascade in one transaction; dry-run never swaps; rejected pairs
     pass through untouched
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from empirica.cli.command_handlers.sources_reconcile_commands import (
    _backfill_identity,
    _discover_candidates,
    _propose_matches,
    _swap_source_id,
    handle_sources_reconcile_command,
)

PROJECT = str(uuid.uuid4())


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            source_type TEXT NOT NULL DEFAULT 'document',
            source_url TEXT,
            title TEXT NOT NULL,
            description TEXT,
            confidence REAL DEFAULT 0.5,
            epistemic_layer TEXT,
            discovered_by_ai TEXT,
            discovered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source_metadata TEXT,
            archived BOOLEAN DEFAULT 0,
            archive_target_id TEXT,
            visibility TEXT DEFAULT 'shared',
            content_hash TEXT,
            size_bytes INTEGER,
            canonical_path TEXT,
            mime_type TEXT
        );
        CREATE TABLE artifact_edges (
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT
        );
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_refs TEXT
        );
    """)


class _FakeDB:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))

    def close(self):
        self.conn.close()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(path))
    _schema(conn)
    conn.commit()
    conn.close()
    fake = _FakeDB(path)
    yield fake
    fake.close()


def _insert_source(db, source_id, title="src", content_hash=None,
                   canonical_path=None, source_url=None, archived=0,
                   archive_target_id=None, metadata=None):
    db.conn.execute(
        "INSERT INTO epistemic_sources (id, project_id, source_type, title, "
        "source_url, content_hash, canonical_path, archived, "
        "archive_target_id, source_metadata) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (source_id, PROJECT, "document", title, source_url, content_hash,
         canonical_path, archived, archive_target_id,
         json.dumps(metadata or {})),
    )
    db.conn.commit()


# ── Backfill ───────────────────────────────────────────────────────────


def test_backfill_computes_identity_for_file_backed_row(db, tmp_path):
    f = tmp_path / "doc.md"
    f.write_bytes(b"backfill me")
    sid = str(uuid.uuid4())
    _insert_source(db, sid, source_url=str(f))

    rows = [{"id": sid, "title": "src", "source_url": str(f),
             "content_hash": None, "size_bytes": None,
             "canonical_path": None, "mime_type": None,
             "source_metadata": "{}"}]
    n = _backfill_identity(db, rows)
    assert n == 1
    assert rows[0]["content_hash"].startswith("sha256:")
    cur = db.conn.execute(
        "SELECT content_hash, size_bytes FROM epistemic_sources WHERE id=?",
        (sid,))
    row = cur.fetchone()
    assert row[0].startswith("sha256:")
    assert row[1] == len(b"backfill me")


def test_backfill_skips_url_and_already_hashed(db):
    rows = [
        {"id": "a", "title": "url-row", "source_url": "https://x.example/d",
         "content_hash": None, "size_bytes": None, "canonical_path": None,
         "mime_type": None, "source_metadata": "{}"},
        {"id": "b", "title": "hashed", "source_url": None,
         "content_hash": "sha256:deadbeef", "size_bytes": 4,
         "canonical_path": "/x", "mime_type": None, "source_metadata": "{}"},
    ]
    assert _backfill_identity(db, rows) == 0


# ── Discovery degradation ──────────────────────────────────────────────


def test_discovery_skips_without_config():
    candidates, status = _discover_candidates(None, None, [])
    assert candidates == {}
    assert status == "skipped_no_cortex_config"


def test_discovery_skips_without_hashed_rows():
    rows = [{"content_hash": None}]
    _candidates, status = _discover_candidates("https://c.test", "k", rows)
    assert status == "skipped_no_hashed_rows"


def test_discovery_degrades_on_http_error():
    rows = [{"content_hash": "sha256:ab"}]
    err = urllib.error.HTTPError("u", 404, "nf", None, None)
    with patch(
        "empirica.cli.command_handlers.sources_reconcile_commands._http_json",
        side_effect=err,
    ):
        candidates, status = _discover_candidates("https://c.test", "k", rows)
    assert candidates == {}
    assert status == "unavailable_http_404"


def test_discovery_returns_candidates_by_hash():
    rows = [{"content_hash": "sha256:ab"}]
    with patch(
        "empirica.cli.command_handlers.sources_reconcile_commands._http_json",
        return_value={"sources": [
            {"id": "cat-1", "content_hash": "sha256:ab"},
            {"id": "cat-2", "content_hash": None},
        ]},
    ):
        candidates, status = _discover_candidates("https://c.test", "k", rows)
    assert status == "ok"
    assert candidates == {"sha256:ab": {"id": "cat-1", "content_hash": "sha256:ab"}}


# ── Matching ───────────────────────────────────────────────────────────


def test_propose_pairs_by_hash_and_skips_reconciled():
    rows = [
        {"id": "local-1", "content_hash": "sha256:aa", "canonical_path": "/a"},
        {"id": "cat-2", "content_hash": "sha256:bb", "canonical_path": "/b"},
        {"id": "local-3", "content_hash": None, "canonical_path": None},
    ]
    candidates = {
        "sha256:aa": {"id": "cat-1"},
        "sha256:bb": {"id": "cat-2"},  # already reconciled (same id)
    }
    proposed = _propose_matches(rows, candidates)
    assert len(proposed) == 1
    assert proposed[0]["local_uuid"] == "local-1"
    assert proposed[0]["cortex_uuid"] == "cat-1"
    assert proposed[0]["content_hash"] == "sha256:aa"


# ── Swap cascade ───────────────────────────────────────────────────────


def _seed_swap_graph(db, local_id):
    """Source cited by a finding, edged from a finding, superseding another."""
    finding_id = str(uuid.uuid4())
    db.conn.execute(
        "INSERT INTO project_findings (id, project_id, source_refs) VALUES (?,?,?)",
        (finding_id, PROJECT, json.dumps([local_id, "other-src"])),
    )
    db.conn.execute(
        "INSERT INTO artifact_edges (from_id, to_id, relation) VALUES (?,?,?)",
        (finding_id, local_id, "sourced_from"),
    )
    db.conn.execute(
        "INSERT INTO artifact_edges (from_id, to_id, relation) VALUES (?,?,?)",
        (local_id, "some-goal", "attached_to"),
    )
    old = str(uuid.uuid4())
    _insert_source(db, old, title="superseded", archived=1,
                   archive_target_id=local_id)
    db.conn.commit()
    return finding_id, old


def test_swap_cascades_all_local_references(db):
    local_id, cortex_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_source(db, local_id, content_hash="sha256:aa")
    finding_id, superseded_id = _seed_swap_graph(db, local_id)

    with patch(
        "empirica.cli.command_handlers.sources_reconcile_commands."
        "_swap_workspace_entity_links",
        return_value="updated_0",
    ):
        result = _swap_source_id(db, PROJECT, local_id, cortex_id)

    assert result["swapped"] is True
    assert result["edges"] == 2
    assert result["archive_targets"] == 1
    assert result["finding_refs"] == 1

    cur = db.conn.execute(
        "SELECT id FROM epistemic_sources WHERE id = ?", (cortex_id,))
    assert cur.fetchone() is not None
    cur = db.conn.execute(
        "SELECT to_id FROM artifact_edges WHERE from_id = ?", (finding_id,))
    assert cur.fetchone()[0] == cortex_id
    cur = db.conn.execute(
        "SELECT archive_target_id FROM epistemic_sources WHERE id = ?",
        (superseded_id,))
    assert cur.fetchone()[0] == cortex_id
    cur = db.conn.execute(
        "SELECT source_refs FROM project_findings WHERE id = ?", (finding_id,))
    refs = json.loads(cur.fetchone()[0])
    assert cortex_id in refs and local_id not in refs and "other-src" in refs


def test_swap_missing_row_reports_error_without_partial_writes(db):
    result = _swap_source_id(db, PROJECT, "ghost", "cat-x")
    assert result["swapped"] is False
    assert "not found" in result["error"]


# ── Handler end-to-end (mocked HTTP) ───────────────────────────────────


def _make_args(**overrides):
    defaults = {
        "apply": False, "project_id": PROJECT,
        "cortex_url": "https://c.test", "api_key": "k",
        "output": "json", "verbose": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _run(db_fixture, args, capsys, catalogue, confirm):
    with patch(
        "empirica.data.session_database.SessionDatabase",
        lambda *a, **kw: db_fixture,
    ), patch(
        "empirica.cli.command_handlers.sources_reconcile_commands._http_json",
        side_effect=[catalogue, confirm],
    ), patch(
        "empirica.cli.command_handlers.sources_reconcile_commands."
        "_swap_workspace_entity_links",
        return_value="updated_0",
    ):
        rc = handle_sources_reconcile_command(args)
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def test_dry_run_confirms_but_never_swaps(db, capsys):
    local_id, cortex_id = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_source(db, local_id, content_hash="sha256:aa")
    db.close_real = db.close
    db.close = lambda: None  # handler closes; fixture needs it after

    out = _run(
        db, _make_args(apply=False), capsys,
        catalogue={"sources": [{"id": cortex_id, "content_hash": "sha256:aa"}]},
        confirm={"confirmed": [
            {"local_uuid": local_id, "cortex_uuid": cortex_id}], "rejected": []},
    )
    assert out["dry_run"] is True
    assert len(out["confirmed"]) == 1
    assert out["swapped"] == []
    cur = db.conn.execute(
        "SELECT id FROM epistemic_sources WHERE id = ?", (local_id,))
    assert cur.fetchone() is not None  # untouched


def test_apply_swaps_confirmed_and_passes_rejected_through(db, capsys):
    l1, c1 = str(uuid.uuid4()), str(uuid.uuid4())
    l2, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_source(db, l1, content_hash="sha256:aa")
    _insert_source(db, l2, content_hash="sha256:bb")
    db.close = lambda: None

    out = _run(
        db, _make_args(apply=True), capsys,
        catalogue={"sources": [
            {"id": c1, "content_hash": "sha256:aa"},
            {"id": c2, "content_hash": "sha256:bb"},
        ]},
        confirm={
            "confirmed": [{"local_uuid": l1, "cortex_uuid": c1}],
            "rejected": [{"local_uuid": l2, "cortex_uuid": c2,
                          "reason": "hash_mismatch"}],
        },
    )
    assert out["dry_run"] is False
    assert out["swapped"][0]["swapped"] is True
    assert out["rejected"][0]["reason"] == "hash_mismatch"
    cur = db.conn.execute(
        "SELECT id FROM epistemic_sources WHERE id IN (?,?)", (c1, l2))
    ids = {r[0] for r in cur.fetchall()}
    assert ids == {c1, l2}  # l1 swapped to c1; rejected l2 untouched
