"""Tests for the daemon ``GET /api/v1/sources/{source_id}/content`` endpoint.

Closes extension's prop_fzb63fnlx5 + prop_bcsecxo2rr: the daemon previously
served only metadata via ``/api/v1/sources``, so the extension's source
viewer rendered empty. This endpoint returns either a URL pointer (for
http/https sources, so the client can fetch directly without a localhost
proxy) or inline file content (for local-path sources, resolved against
the project root with fallback search prefixes).

Coverage:
1. URL source → ``{kind: "url", url, title, source_type}``.
2. File source (project-root relative) → ``{kind: "file", content, path, size_bytes, encoding}``.
3. File source under ``.empirica/sources/`` prefix fallback → resolved.
4. File source under ``docs/`` prefix fallback → resolved.
5. Absolute path inside project tree → resolved.
6. Unknown source_id → 404.
7. File path that doesn't exist on disk → 404 with hint listing the prefixes tried.
8. Path traversal attempt (``../etc/passwd``) → 422 refused.
9. Daemon not bound to project (no project_id and no ?path) → 503.
10. Binary file → base64 encoding + ``encoding="base64"``.
11. Oversized file → ``truncated=true``, ``content=None``, ``size_bytes`` set,
    plus a hint string.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from empirica.api.serve_app import create_serve_app

# ── Fixtures ──────────────────────────────────────────────────────────


def _make_project_with_db(tmp_path: Path, project_id: str) -> Path:
    """Create a tmp project tree with the minimal sessions.db needed by the
    content endpoint."""
    proj = tmp_path / f"proj-{project_id[:8]}"
    proj.mkdir()
    (proj / ".empirica").mkdir()
    (proj / ".empirica" / "project.yaml").write_text(
        f"name: test-project\nproject_id: {project_id}\n",
        encoding="utf-8",
    )
    db_dir = proj / ".empirica" / "sessions"
    db_dir.mkdir()
    db_path = db_dir / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE epistemic_sources (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            source_type TEXT,
            source_url TEXT,
            title TEXT,
            description TEXT,
            confidence REAL DEFAULT 0.5,
            epistemic_layer TEXT,
            discovered_by_ai TEXT,
            discovered_at TIMESTAMP NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return proj


def _insert_source(
    proj: Path,
    project_id: str,
    source_url: str,
    *,
    title: str = "test source",
    source_type: str = "doc",
) -> str:
    """Insert one epistemic_sources row and return its id."""
    sid = str(uuid.uuid4())
    db_path = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO epistemic_sources "
        "(id, project_id, source_type, source_url, title, description, "
        " confidence, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0.9, datetime('now'))",
        (sid, project_id, source_type, source_url, title, "desc"),
    )
    conn.commit()
    conn.close()
    return sid


@pytest.fixture
def reset_daemon_cache():
    """Clear the daemon's cached active-project resolution between tests."""
    from empirica.api import daemon_project

    daemon_project._cached_project = None
    yield
    daemon_project._cached_project = None


def _get_content(proj: Path, sid: str) -> tuple[int, dict]:
    """GET the content endpoint via the in-process TestClient. Returns
    ``(status_code, json_body)``."""
    with patch(
        "empirica.utils.session_resolver.InstanceResolver.project_path",
        return_value=str(proj),
    ):
        client = TestClient(create_serve_app())
        r = client.get(
            f"/api/v1/sources/{sid}/content",
            params={"path": str(proj)},
        )
    return r.status_code, r.json()


# ── URL source ───────────────────────────────────────────────────────


def test_url_source_returns_kind_url(tmp_path, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    sid = _insert_source(
        proj,
        pid,
        "https://example.com/rfc7519",
        title="RFC 7519",
        source_type="url",
    )

    code, body = _get_content(proj, sid)
    assert code == 200
    assert body["kind"] == "url"
    assert body["url"] == "https://example.com/rfc7519"
    assert body["title"] == "RFC 7519"
    assert body["source_type"] == "url"
    assert "content" not in body


def test_http_source_also_returns_kind_url(tmp_path, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    sid = _insert_source(proj, pid, "http://example.com/x")
    code, body = _get_content(proj, sid)
    assert code == 200
    assert body["kind"] == "url"


# ── File source — direct + fallback prefixes ─────────────────────────


def test_file_source_project_root_relative(tmp_path, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    (proj / "spec.md").write_text("# Spec\n\nHello.\n", encoding="utf-8")
    sid = _insert_source(proj, pid, "spec.md", title="Spec doc")

    code, body = _get_content(proj, sid)
    assert code == 200
    assert body["kind"] == "file"
    assert body["path"] == "spec.md"
    assert body["encoding"] == "utf-8"
    assert "Hello." in body["content"]
    assert body["size_bytes"] == len("# Spec\n\nHello.\n")


def test_file_source_under_empirica_sources_prefix(tmp_path, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    (proj / ".empirica" / "sources").mkdir(parents=True)
    (proj / ".empirica" / "sources" / "rfc.txt").write_text("rfc content")
    sid = _insert_source(proj, pid, "rfc.txt")

    code, body = _get_content(proj, sid)
    assert code == 200
    assert body["kind"] == "file"
    assert body["path"] == ".empirica/sources/rfc.txt"
    assert body["content"] == "rfc content"


def test_file_source_under_docs_prefix(tmp_path, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    (proj / "docs").mkdir()
    (proj / "docs" / "guide.md").write_text("# Guide")
    sid = _insert_source(proj, pid, "guide.md")

    code, body = _get_content(proj, sid)
    assert code == 200
    assert body["path"] == "docs/guide.md"


def test_file_source_absolute_path_inside_project(tmp_path, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    target = proj / "abs.txt"
    target.write_text("absolute")
    sid = _insert_source(proj, pid, str(target.resolve()))

    code, body = _get_content(proj, sid)
    assert code == 200
    assert body["content"] == "absolute"


# ── Error paths ───────────────────────────────────────────────────────


def test_unknown_source_id_returns_404(tmp_path, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    code, body = _get_content(proj, "00000000-0000-0000-0000-000000000000")
    assert code == 404
    assert "not found" in body["detail"]


def test_file_source_missing_on_disk_returns_404(tmp_path, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    sid = _insert_source(proj, pid, "does-not-exist.md")

    code, body = _get_content(proj, sid)
    assert code == 404
    assert "does-not-exist.md" in body["detail"]
    assert ".empirica/sources" in body["detail"]


def test_path_traversal_attempt_returns_422(tmp_path, reset_daemon_cache):
    """A relative path that resolves outside the project tree must be
    refused — defense in depth against ``../../`` walks."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    # Plant a real file outside the project, then point a source row at it
    # via a relative ``..`` path.
    outside = tmp_path / "secret.txt"
    outside.write_text("oops")
    sid = _insert_source(proj, pid, "../secret.txt")

    code, body = _get_content(proj, sid)
    assert code == 422
    assert "outside project root" in body["detail"]


def test_daemon_no_project_returns_503(tmp_path, reset_daemon_cache):
    """Without ?project_id or ?path AND no daemon-cached project, the
    endpoint returns 503 with a hint to bind a project."""
    with (
        patch(
            "empirica.utils.session_resolver.InstanceResolver.project_path",
            return_value=None,
        ),
        patch(
            "empirica.api.routes.artifacts.get_cached_daemon_project",
            return_value=None,
        ),
    ):
        client = TestClient(create_serve_app())
        r = client.get("/api/v1/sources/abc/content")
    assert r.status_code == 503
    detail = r.json()["detail"]
    detail_text = detail if isinstance(detail, str) else str(detail)
    assert "not bound to a project" in detail_text.lower() or "no active project" in detail_text.lower()


# ── Encoding + truncation ─────────────────────────────────────────────


def test_binary_file_returns_base64(tmp_path, reset_daemon_cache):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    (proj / "image.bin").write_bytes(b"\x00\x01\x02PNG\xff\xfe")
    sid = _insert_source(proj, pid, "image.bin")

    code, body = _get_content(proj, sid)
    assert code == 200
    assert body["encoding"] == "base64"
    import base64

    assert base64.b64decode(body["content"]) == b"\x00\x01\x02PNG\xff\xfe"


def test_oversized_file_returns_truncation_marker(tmp_path, reset_daemon_cache, monkeypatch):
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    # Lower the cap so the test doesn't materialize 10MB
    import empirica.api.routes.artifacts as artifacts_mod

    monkeypatch.setattr(artifacts_mod, "_MAX_SOURCE_CONTENT_BYTES", 64)

    (proj / "big.txt").write_text("x" * 200)
    sid = _insert_source(proj, pid, "big.txt")

    code, body = _get_content(proj, sid)
    assert code == 200
    assert body["truncated"] is True
    assert body["content"] is None
    assert body["size_bytes"] == 200
    assert "exceeds" in body["hint"]


# ── Cortex-uuid alias resolution (Unified Source Identity, Option A) ──


def test_source_resolves_by_cortex_uuid_alias(tmp_path, reset_daemon_cache):
    """A source requested by its CATALOGUE uuid (not its local PK) still
    resolves — the two-id-space 404 fix (migration 055, dual-resolution).
    Extension always requests by the cortex uuid, which is the only id its
    projection gives it."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    # Simulate a migration-055 DB: add the alias column.
    db_path = proj / ".empirica" / "sessions" / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("ALTER TABLE epistemic_sources ADD COLUMN cortex_uuid TEXT")
    conn.commit()
    conn.close()

    (proj / "spec.md").write_text("# Spec\n\nHello.\n", encoding="utf-8")
    local_id = _insert_source(proj, pid, "spec.md", title="Spec doc")

    # Adopt the catalogue uuid as an alias (what `sources-reconcile` does by default).
    cortex_uuid = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE epistemic_sources SET cortex_uuid = ? WHERE id = ?",
        (cortex_uuid, local_id),
    )
    conn.commit()
    conn.close()

    # Request by the CORTEX uuid → resolves the local file (was a 404 before P1).
    code, body = _get_content(proj, cortex_uuid)
    assert code == 200
    assert body["kind"] == "file"
    assert "Hello." in body["content"]

    # The local id still resolves too — both address the same source.
    code2, body2 = _get_content(proj, local_id)
    assert code2 == 200
    assert body2["kind"] == "file"
