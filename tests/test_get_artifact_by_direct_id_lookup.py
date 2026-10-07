"""Test that GET /artifacts/{id} fetches by direct id lookup, not row scan.

Verifies the fix for: "Fetch the single artifact by id in GET /artifacts/{id}
instead of scanning the 1000 newest project rows"

Previously, _list_one_by_type would call the list helpers with limit=1000,
scanning only the 1000 newest rows. An artifact beyond that limit would
incorrectly 404 even though it existed in the DB. This test creates 1100
findings to verify that the new direct lookup works for all of them.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from empirica.api.serve_app import create_serve_app


def _make_project_with_db(tmp_path: Path, project_id: str) -> Path:
    """Create a minimal project with sessions.db."""
    proj = tmp_path / f"proj-{project_id[:8]}"
    proj.mkdir()
    (proj / ".empirica").mkdir()
    (proj / ".empirica" / "project.yaml").write_text(
        f"name: test-project\nproject_id: {project_id}\n", encoding="utf-8"
    )
    db_dir = proj / ".empirica" / "sessions"
    db_dir.mkdir()
    db_path = db_dir / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE project_findings (
            id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT,
            goal_id TEXT, subtask_id TEXT, transaction_id TEXT,
            finding TEXT NOT NULL, finding_data TEXT,
            subject TEXT, impact REAL DEFAULT 0.5, epistemic_source TEXT,
            created_timestamp REAL NOT NULL,
            is_resolved INTEGER DEFAULT 0, resolution TEXT, resolved_timestamp REAL,
            superseded_by TEXT, resolution_kind TEXT
        );
        CREATE TABLE artifact_edges (
            from_id TEXT NOT NULL, to_id TEXT NOT NULL, relation TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            PRIMARY KEY (from_id, to_id, relation)
        );
    """)
    conn.commit()
    conn.close()
    return proj


@pytest.fixture
def reset_daemon_cache():
    import empirica.api.daemon_project as dp

    dp._cached = False
    dp._cached_project = None
    yield
    dp._cached = False
    dp._cached_project = None


def test_get_artifact_beyond_1000_row_limit(tmp_path, monkeypatch, reset_daemon_cache):
    """GET /artifacts/{id} should fetch an artifact beyond the first 1000 rows.

    The old implementation called _list_findings with limit=1000, so artifacts
    beyond row 1000 (sorted by created_timestamp DESC) would be incorrectly 404'd.
    The new implementation passes artifact_id to the helper, which queries
    directly by id.

    This test:
    1. Inserts 1100 findings (so the newest 1000 won't include the oldest 100)
    2. Tries to fetch one of the oldest findings
    3. Verifies it succeeds (would fail with the old implementation)
    """
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)
    db_path = proj / ".empirica" / "sessions" / "sessions.db"

    # Insert 1100 findings with evenly-spaced timestamps
    conn = sqlite3.connect(str(db_path))
    base_time = time.time()
    finding_ids = []
    for i in range(1100):
        art_id = str(uuid.uuid4())
        finding_ids.append(art_id)
        # Older findings have earlier timestamps; newest will be at i=1099
        ts = base_time - (1100 - i) * 1.0
        conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, finding_data, "
            "impact, created_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (art_id, pid, "sess", f"Finding {i}", "{}", 0.5, ts),
        )
    conn.commit()
    conn.close()

    # The first 100 findings are beyond the old limit=1000 when sorted DESC
    oldest_finding = finding_ids[0]
    newest_finding = finding_ids[-1]

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())

        # Both should succeed with the new implementation
        oldest_response = client.get(f"/api/v1/artifacts/{oldest_finding}")
        newest_response = client.get(f"/api/v1/artifacts/{newest_finding}")

    assert oldest_response.status_code == 200, (
        f"Oldest finding (index 0) should be fetchable with direct id lookup. Response: {oldest_response.json()}"
    )
    assert newest_response.status_code == 200

    # Verify the fetched data
    oldest_data = oldest_response.json()
    assert oldest_data["artifact"]["id"] == oldest_finding
    assert oldest_data["artifact"]["type"] == "finding"
    assert "Finding 0" in oldest_data["artifact"]["body"]

    newest_data = newest_response.json()
    assert newest_data["artifact"]["id"] == newest_finding
    assert newest_data["artifact"]["type"] == "finding"
    assert "Finding 1099" in newest_data["artifact"]["body"]


def test_get_artifact_still_404s_for_genuinely_absent_id(tmp_path, monkeypatch, reset_daemon_cache):
    """GET /artifacts/{id} should still 404 for ids that genuinely don't exist."""
    pid = str(uuid.uuid4())
    proj = _make_project_with_db(tmp_path, pid)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        client = TestClient(create_serve_app())
        response = client.get("/api/v1/artifacts/genuinely-absent-id")

    assert response.status_code == 404
