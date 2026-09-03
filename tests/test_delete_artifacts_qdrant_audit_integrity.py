"""Regression tests for issue #412 — delete-artifacts' three silent contract failures.

`delete-artifacts --apply` reported ``{"ok": true, "deleted": 1}`` while:

1. never removing the memory vector (writers derive the point id from a 15-hex
   md5 prefix; the deleter used 16 digits plus a modulo, so it addressed a point
   that does not exist),
2. never touching the eidetic mirror at all, and
3. never writing the promised audit row (the INSERT named ``project_decisions``,
   a table that exists nowhere in the schema).

All three were invisible because Qdrant answers a delete of an absent point with
``status: completed`` and every failure path was a bare ``except Exception: pass``.

These tests assert against the storage layer and the returned report — never
against the fact that the call did not raise.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from unittest.mock import MagicMock, patch

import pytest

from empirica.cli.command_handlers.graph_commands import (
    _delete_from_qdrant,
    _log_deletion_decision,
)
from empirica.core.qdrant.point_ids import artifact_point_id

PROJECT_ID = "00000000-0000-4000-8000-000000000000"
ARTIFACT_ID = "11111111-2222-4333-8444-555555555555"


def _memory_name(project_id: str = PROJECT_ID) -> str:
    return f"project_{project_id}_memory"


def _eidetic_name(project_id: str = PROJECT_ID) -> str:
    return f"project_{project_id}_eidetic"


def _client_with_point(present: bool) -> MagicMock:
    """A Qdrant client whose retrieve() reports the point as present or absent."""
    client = MagicMock()
    client.retrieve.return_value = [MagicMock()] if present else []
    return client


# ── 1. ID parity ────────────────────────────────────────────────────────────


def test_shared_helper_matches_the_writer_derivation():
    """The helper must reproduce the scheme already on disk — md5[:15], no modulo.

    Changing this derivation would orphan every vector ever written, so this
    pins the exact historical form rather than merely 'writer == deleter'.
    """
    for raw in (ARTIFACT_ID, PROJECT_ID, "not-a-uuid", ""):
        expected = int(hashlib.md5(raw.encode()).hexdigest()[:15], 16)
        assert artifact_point_id(raw) == expected


@pytest.mark.parametrize("seed", range(64))
def test_writer_and_deleter_agree_on_point_id(seed):
    """Property test: the 15-vs-16 digit divergence must never reappear.

    The old deleter used ``[:16] % 2**63``; over 20k random ids it agreed with
    the writer form zero times. Any reintroduction fails here.
    """
    raw = str(uuid.UUID(int=seed * 2654435761 % (2**128)))
    writer_side = int(hashlib.md5(raw.encode()).hexdigest()[:15], 16)
    old_broken = int(hashlib.md5(raw.encode()).hexdigest()[:16], 16) % (2**63)

    assert artifact_point_id(raw) == writer_side
    assert artifact_point_id(raw) != old_broken


def test_memory_and_eidetic_writers_use_the_shared_helper():
    """Both mirrors the deleter targets must derive ids through the same helper."""
    import inspect

    from empirica.core.qdrant import eidetic, memory

    for module in (memory, eidetic):
        src = inspect.getsource(module)
        assert "artifact_point_id" in src, f"{module.__name__} must use the shared helper"
        assert "hexdigest()[:16]" not in src, f"{module.__name__} reintroduced the broken width"


# ── 2. Both mirrors ─────────────────────────────────────────────────────────


def test_delete_removes_the_point_from_both_collections():
    """memory AND eidetic — the eidetic mirror had no code path at all."""
    client = _client_with_point(True)

    with patch("empirica.core.qdrant.connection._get_qdrant_client", return_value=client):
        report = _delete_from_qdrant(ARTIFACT_ID, PROJECT_ID)

    deleted_from = {call.kwargs["collection_name"] for call in client.delete.call_args_list}
    assert deleted_from == {_memory_name(), _eidetic_name()}

    point_id = artifact_point_id(ARTIFACT_ID)
    for call in client.delete.call_args_list:
        assert call.kwargs["points_selector"] == [point_id]

    assert report == {"memory": "deleted", "eidetic": "deleted"}


def test_delete_targets_the_id_the_writer_actually_stored():
    """The whole defect in one assertion: the deleter must address the real point."""
    client = _client_with_point(True)

    with patch("empirica.core.qdrant.connection._get_qdrant_client", return_value=client):
        _delete_from_qdrant(ARTIFACT_ID, PROJECT_ID)

    stored_by_writer = int(hashlib.md5(ARTIFACT_ID.encode()).hexdigest()[:15], 16)
    targeted = client.delete.call_args_list[0].kwargs["points_selector"]
    assert targeted == [stored_by_writer]


# ── 3. Nonexistent-point no-op must not read as success ─────────────────────


def test_absent_point_is_reported_absent_not_deleted():
    """Qdrant answers a delete of a missing point with success; we must not.

    This is the assertion that would have caught the original bug: the old code
    'succeeded' precisely because it deleted a point that was never there.
    """
    client = _client_with_point(False)

    with patch("empirica.core.qdrant.connection._get_qdrant_client", return_value=client):
        report = _delete_from_qdrant(ARTIFACT_ID, PROJECT_ID)

    assert report == {"memory": "absent", "eidetic": "absent"}
    client.delete.assert_not_called()


def test_unavailable_backend_is_reported_not_swallowed():
    with patch("empirica.core.qdrant.connection._get_qdrant_client", return_value=None):
        report = _delete_from_qdrant(ARTIFACT_ID, PROJECT_ID)

    assert report == {"memory": "unavailable", "eidetic": "unavailable"}


# ── 4. Audit decision ───────────────────────────────────────────────────────


def _decisions_db() -> sqlite3.Connection:
    """Minimal fixture mirroring projects_schema.decisions (id/REAL timestamp)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY,
            choice TEXT NOT NULL,
            description TEXT,
            alternatives TEXT,
            rationale TEXT NOT NULL,
            confidence_at_decision REAL,
            reversibility TEXT DEFAULT 'committal' CHECK(reversibility IN (
                'exploratory', 'committal', 'forced'
            )),
            entity_type TEXT NOT NULL DEFAULT 'project',
            entity_id TEXT,
            project_id TEXT,
            session_id TEXT,
            transaction_id TEXT,
            goal_id TEXT,
            outcome TEXT,
            outcome_assessed_at REAL,
            regret_score REAL,
            created_by_ai TEXT,
            created_timestamp REAL NOT NULL
        );
        """
    )
    return conn


def test_audit_decision_row_is_written_to_the_real_table():
    """The old INSERT named project_decisions — a table that does not exist."""
    conn = _decisions_db()
    session_id = "22222222-3333-4444-8555-666666666666"

    status = _log_deletion_decision(
        conn.cursor(),
        project_id=PROJECT_ID,
        session_id=session_id,
        choice="Deleted 1 artifact(s) + 0 edge(s) + repaired 0",
        rationale="stale test data",
    )

    assert status == "recorded"
    rows = conn.execute(
        "SELECT id, project_id, session_id, choice, rationale, reversibility, created_timestamp FROM decisions"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[1] == PROJECT_ID
    assert row[2] == session_id
    assert row[4] == "stale test data"
    assert row[5] == "committal"
    uuid.UUID(row[0])  # id column, a real uuid
    assert isinstance(row[6], float), "created_timestamp must be REAL epoch, not datetime('now') text"


def test_audit_failure_is_reported_not_swallowed():
    """A missing decisions table must degrade the report, not vanish."""
    conn = sqlite3.connect(":memory:")  # no decisions table

    status = _log_deletion_decision(
        conn.cursor(),
        project_id=PROJECT_ID,
        session_id="22222222-3333-4444-8555-666666666666",
        choice="Deleted 1 artifact(s)",
        rationale="stale test data",
    )

    assert status.startswith("error:")
    assert "project_decisions" not in status


# ── 5. Backend failure surfaces per collection ──────────────────────────────


def test_partial_failure_is_explicit_per_collection():
    """One mirror failing must not be reported as a clean delete."""
    client = _client_with_point(True)

    def _delete(collection_name, points_selector):
        if collection_name == _eidetic_name():
            raise RuntimeError("connection reset")

    client.delete.side_effect = _delete

    with patch("empirica.core.qdrant.connection._get_qdrant_client", return_value=client):
        report = _delete_from_qdrant(ARTIFACT_ID, PROJECT_ID)

    assert report["memory"] == "deleted"
    assert report["eidetic"].startswith("error:")
    assert "connection reset" in report["eidetic"]


def test_retrieve_failure_does_not_abort_the_other_collection():
    client = MagicMock()

    def _retrieve(collection_name, ids):
        if collection_name == _memory_name():
            raise RuntimeError("memory unreachable")
        return [MagicMock()]

    client.retrieve.side_effect = _retrieve

    with patch("empirica.core.qdrant.connection._get_qdrant_client", return_value=client):
        report = _delete_from_qdrant(ARTIFACT_ID, PROJECT_ID)

    assert report["memory"].startswith("error:")
    assert report["eidetic"] == "deleted"
