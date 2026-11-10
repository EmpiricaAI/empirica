"""Tests for suggest_links_for_artifact (Item 6 — *-log response enrichment).

Covers:
- Argument guarding: empty project_id / text returns []
- Qdrant unavailable: returns []
- Modern payloads (artifact_id present): direct resolution
- Legacy payloads (no artifact_id): SQLite reverse-hash fallback
- exclude_id filter: never returns the just-logged artifact
- Threshold gate: scores below similarity_threshold are dropped
- Top-K cap: respects the limit
- Cross-collection dedupe: same artifact_id from two collections kept once
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from empirica.core.bootstrap.suggested_links import (
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_TOP_K,
    _extract_summary,
    suggest_links_for_artifact,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _hash_id(uuid_str: str) -> int:
    """Reproduce the embed function's point_id hashing."""
    return int(hashlib.md5(uuid_str.encode()).hexdigest()[:15], 16)


def _make_qdrant_hit(point_id, score, payload):
    """Build a mock qdrant ScoredPoint."""
    hit = MagicMock()
    hit.id = point_id
    hit.score = score
    hit.payload = payload
    return hit


@pytest.fixture
def empty_project_db(tmp_path: Path) -> Path:
    """Create a project-shaped sqlite DB with empty artifact tables."""
    db_dir = tmp_path / ".empirica" / "sessions"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    for table in (
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "assumptions",
        "decisions",
    ):
        # Minimal columns to satisfy the fallback's queries
        text_col = {
            "project_findings": "finding",
            "project_unknowns": "unknown",
            "project_dead_ends": "approach",
            "mistakes_made": "mistake",
            "assumptions": "assumption",
            "decisions": "choice",
        }[table]
        cur.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, project_id TEXT, {text_col} TEXT)")
    conn.commit()
    conn.close()
    return tmp_path


# ── Guard rails ────────────────────────────────────────────────────────


def test_returns_empty_for_blank_project_id():
    assert suggest_links_for_artifact("", "some text", "some-uuid") == []


def test_returns_empty_for_blank_text():
    assert suggest_links_for_artifact("proj-1", "", "some-uuid") == []


def test_returns_empty_when_qdrant_client_unavailable():
    """No Qdrant client → graceful empty list, no exception."""
    with patch(
        "empirica.core.qdrant.connection.qdrant_search_client",
        return_value=None,
    ):
        out = suggest_links_for_artifact("proj-1", "some text", "self-id")
    assert out == []


def test_returns_empty_when_embedding_fails():
    """Embedding returns None → empty list."""
    fake_client = MagicMock()
    with (
        patch("empirica.core.qdrant.connection.qdrant_search_client", return_value=fake_client),
        patch("empirica.core.qdrant.connection._get_embedding_safe", return_value=None),
    ):
        out = suggest_links_for_artifact("proj-1", "some text", "self-id")
    assert out == []


# ── Modern payloads (artifact_id present) ──────────────────────────────


def test_returns_modern_hit_with_artifact_id():
    fake_client = MagicMock()
    response = MagicMock()
    response.points = [
        _make_qdrant_hit(
            123,
            0.85,
            {
                "artifact_id": "neighbor-1",
                "type": "finding",
                "text": "A neighbor finding about embeddings",
            },
        ),
    ]
    fake_client.query_points.return_value = response

    with (
        patch("empirica.core.qdrant.connection.qdrant_search_client", return_value=fake_client),
        patch("empirica.core.qdrant.connection._get_embedding_safe", return_value=[0.1] * 384),
    ):
        out = suggest_links_for_artifact("proj-1", "embedding stuff", "self-id")

    assert len(out) == 1
    assert out[0]["id"] == "neighbor-1"
    assert out[0]["type"] == "finding"
    assert out[0]["similarity_score"] == 0.85
    assert "neighbor finding" in out[0]["summary"]


def test_excludes_self_id_from_results():
    fake_client = MagicMock()
    response = MagicMock()
    response.points = [
        _make_qdrant_hit(111, 0.99, {"artifact_id": "self-id", "type": "finding", "text": "self"}),
        _make_qdrant_hit(222, 0.85, {"artifact_id": "neighbor", "type": "finding", "text": "neighbor"}),
    ]
    fake_client.query_points.return_value = response

    with (
        patch("empirica.core.qdrant.connection.qdrant_search_client", return_value=fake_client),
        patch("empirica.core.qdrant.connection._get_embedding_safe", return_value=[0.1] * 384),
    ):
        out = suggest_links_for_artifact("proj-1", "text", "self-id")

    ids = [h["id"] for h in out]
    assert "self-id" not in ids
    assert "neighbor" in ids


def test_drops_hits_below_similarity_threshold():
    fake_client = MagicMock()
    response = MagicMock()
    # Qdrant won't actually return below-threshold when score_threshold is set,
    # but the helper double-checks defensively. Simulate a bad client.
    response.points = [
        _make_qdrant_hit(111, 0.50, {"artifact_id": "below", "type": "finding", "text": "low"}),
        _make_qdrant_hit(222, 0.80, {"artifact_id": "above", "type": "finding", "text": "high"}),
    ]
    fake_client.query_points.return_value = response

    with (
        patch("empirica.core.qdrant.connection.qdrant_search_client", return_value=fake_client),
        patch("empirica.core.qdrant.connection._get_embedding_safe", return_value=[0.1] * 384),
    ):
        out = suggest_links_for_artifact(
            "proj-1",
            "text",
            "self-id",
            similarity_threshold=0.7,
        )

    ids = [h["id"] for h in out]
    assert "above" in ids
    assert "below" not in ids


def test_respects_top_k_limit():
    fake_client = MagicMock()
    response = MagicMock()
    response.points = [
        _make_qdrant_hit(i, 0.9 - i * 0.01, {"artifact_id": f"n-{i}", "type": "finding", "text": f"hit {i}"})
        for i in range(20)
    ]
    fake_client.query_points.return_value = response

    with (
        patch("empirica.core.qdrant.connection.qdrant_search_client", return_value=fake_client),
        patch("empirica.core.qdrant.connection._get_embedding_safe", return_value=[0.1] * 384),
    ):
        out = suggest_links_for_artifact(
            "proj-1",
            "text",
            "self-id",
            top_k=3,
        )

    assert len(out) == 3
    # Sorted by similarity desc
    assert out[0]["similarity_score"] >= out[1]["similarity_score"] >= out[2]["similarity_score"]


def test_dedupes_same_artifact_id_across_collections():
    """If a hit appears in both memory and decisions collections (shouldn't but
    defensively), dedupe to highest score."""
    fake_client = MagicMock()
    response_mem = MagicMock()
    response_mem.points = [
        _make_qdrant_hit(111, 0.80, {"artifact_id": "x", "type": "finding", "text": "x in memory"}),
    ]
    response_dec = MagicMock()
    response_dec.points = [
        _make_qdrant_hit(111, 0.85, {"artifact_id": "x", "type": "decision", "choice": "x in decisions"}),
    ]
    response_assum = MagicMock()
    response_assum.points = []
    fake_client.query_points.side_effect = [
        response_mem,
        response_assum,
        response_dec,
    ]

    with (
        patch("empirica.core.qdrant.connection.qdrant_search_client", return_value=fake_client),
        patch("empirica.core.qdrant.connection._get_embedding_safe", return_value=[0.1] * 384),
    ):
        out = suggest_links_for_artifact("proj-1", "text", "self-id")

    assert len(out) == 1
    assert out[0]["id"] == "x"
    assert out[0]["similarity_score"] == 0.85


# ── Legacy payloads (artifact_id missing) ──────────────────────────────


def test_legacy_payload_resolved_via_sqlite_reverse_hash(empty_project_db: Path):
    """A Qdrant hit with no artifact_id but a known point_id resolves
    through the SQLite reverse-hash map."""
    artifact_uuid = str(uuid.uuid4())
    point_id = _hash_id(artifact_uuid)

    # Seed a finding in SQLite at the project_id we'll search
    conn = sqlite3.connect(empty_project_db / ".empirica" / "sessions" / "sessions.db")
    conn.execute(
        "INSERT INTO project_findings (id, project_id, finding) VALUES (?, ?, ?)",
        (artifact_uuid, "proj-1", "Legacy finding text"),
    )
    conn.commit()
    conn.close()

    fake_client = MagicMock()
    response = MagicMock()
    # Legacy payload — no artifact_id field
    response.points = [
        _make_qdrant_hit(point_id, 0.82, {"type": "finding", "text": "legacy"}),
    ]
    fake_client.query_points.return_value = response

    with (
        patch("empirica.core.qdrant.connection.qdrant_search_client", return_value=fake_client),
        patch("empirica.core.qdrant.connection._get_embedding_safe", return_value=[0.1] * 384),
    ):
        out = suggest_links_for_artifact(
            "proj-1",
            "search text",
            "self-id",
            project_path=empty_project_db,
        )

    assert len(out) == 1
    assert out[0]["id"] == artifact_uuid
    assert out[0]["type"] == "finding"
    assert out[0]["summary"] == "Legacy finding text"
    assert out[0]["similarity_score"] == 0.82


def test_legacy_fallback_excludes_self_id(empty_project_db: Path):
    """Legacy fallback must also honour exclude_id."""
    self_uuid = str(uuid.uuid4())
    self_point = _hash_id(self_uuid)

    conn = sqlite3.connect(empty_project_db / ".empirica" / "sessions" / "sessions.db")
    conn.execute(
        "INSERT INTO project_findings (id, project_id, finding) VALUES (?, ?, ?)",
        (self_uuid, "proj-1", "Self finding"),
    )
    conn.commit()
    conn.close()

    fake_client = MagicMock()
    response = MagicMock()
    response.points = [
        _make_qdrant_hit(self_point, 0.99, {"type": "finding", "text": "self"}),
    ]
    fake_client.query_points.return_value = response

    with (
        patch("empirica.core.qdrant.connection.qdrant_search_client", return_value=fake_client),
        patch("empirica.core.qdrant.connection._get_embedding_safe", return_value=[0.1] * 384),
    ):
        out = suggest_links_for_artifact(
            "proj-1",
            "search text",
            self_uuid,
            project_path=empty_project_db,
        )

    assert out == []


# ── _extract_summary key fallback ──────────────────────────────────────


def test_extract_summary_prefers_text_field():
    payload = {"text": "primary", "assumption": "secondary"}
    assert _extract_summary(payload) == "primary"


def test_extract_summary_uses_assumption_field():
    payload = {"assumption": "from assumption embed"}
    assert _extract_summary(payload) == "from assumption embed"


def test_extract_summary_uses_choice_field_for_decisions():
    payload = {"choice": "from decision embed"}
    assert _extract_summary(payload) == "from decision embed"


def test_extract_summary_falls_back_to_full_field():
    payload = {"text_full": "full body when text was None"}
    assert _extract_summary(payload) == "full body when text was None"


def test_extract_summary_truncates_to_120_chars():
    payload = {"text": "a" * 200}
    assert len(_extract_summary(payload)) == 120


def test_extract_summary_returns_empty_for_empty_payload():
    assert _extract_summary({}) == ""


# ── Defaults sanity ────────────────────────────────────────────────────


def test_default_top_k_is_5():
    assert DEFAULT_TOP_K == 5


def test_default_threshold_aligns_with_circle_3():
    """Threshold should match the bootstrap aggregator's circle_3 default
    so AI sees consistent neighbour cutoffs across both surfaces."""
    assert DEFAULT_SIMILARITY_THRESHOLD == 0.65
