"""Test that PATCH /artifacts/{id}/resolve passes canonicalized superseded_by to Qdrant sync.

The API endpoint was passing the raw body.get("superseded_by") value (possibly a prefix
or non-canonical reference) instead of the canonicalized finding id computed by
canonical_finding_id(). The Qdrant sync needs the canonical value to update the vector
metadata correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def api_db(tmp_path, monkeypatch):
    """A real DB with findings set up for supersession testing."""
    from empirica.api.routes import artifacts as A
    from empirica.data.session_database import SessionDatabase

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    db_file = str(tmp_path / "t.db")
    seed = SessionDatabase(db_path=db_file)
    try:
        c = seed.conn
        # Create two findings: old and new
        c.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data) "
            "VALUES ('old-finding-123456789','p','s','old claim',0.0,'{}')"
        )
        c.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data) "
            "VALUES ('new-finding-987654321','p','s','new claim',1.0,'{}')"
        )
        c.commit()
    finally:
        seed.close()

    monkeypatch.setattr(A, "get_cached_daemon_project", lambda: {"project_id": "p"})
    monkeypatch.setattr(A, "_resolve_project_dict", lambda *a, **k: {"project_id": "p", "project_path": str(tmp_path)})
    monkeypatch.setattr(A, "_open_db_for", lambda _proj: SessionDatabase(db_path=db_file))
    return db_file


@pytest.mark.asyncio
async def test_resolve_finding_passes_canonicalized_superseded_by_to_qdrant(api_db, monkeypatch):
    """Verify that the canonicalized superseded_by (not the raw prefix) is synced to Qdrant."""
    from empirica.api.routes.artifacts import resolve_artifact

    # Mock the _sync_resolution_to_qdrant to capture what it's called with
    mock_sync = MagicMock()
    monkeypatch.setattr(
        "empirica.cli.command_handlers.graph_commands._sync_resolution_to_qdrant",
        mock_sync,
    )

    # Resolve old finding as superseded by new finding using a prefix (8-char min for prefix matching)
    # Pass only the prefix, not the full id
    result = await resolve_artifact(
        "old-finding-123456789",
        {
            "resolved_by": "test",
            "superseded_by": "new-find",  # Only 8 chars of the new finding's id
        },
    )

    assert result["ok"] is True

    # Verify _sync_resolution_to_qdrant was called with the CANONICAL id, not the prefix
    mock_sync.assert_called_once()
    call_args = mock_sync.call_args

    # Extract the superseded_by argument (5th positional arg: db, table, artifact_id, kind, superseded_by)
    superseded_by_arg = call_args[0][4] if len(call_args[0]) > 4 else call_args[1].get("superseded_by")

    # Should be the full canonical id, not the prefix
    assert superseded_by_arg == "new-finding-987654321", f"Expected canonical id, got {superseded_by_arg}"


@pytest.mark.asyncio
async def test_resolve_finding_passes_none_superseded_by_when_not_provided(api_db, monkeypatch):
    """Verify that None is passed to Qdrant sync when superseded_by is not provided."""
    from empirica.api.routes.artifacts import resolve_artifact

    # Mock the _sync_resolution_to_qdrant to capture what it's called with
    mock_sync = MagicMock()
    monkeypatch.setattr(
        "empirica.cli.command_handlers.graph_commands._sync_resolution_to_qdrant",
        mock_sync,
    )

    # Resolve finding without superseded_by
    result = await resolve_artifact(
        "old-finding-123456789",
        {"resolved_by": "test"},
    )

    assert result["ok"] is True

    # Verify _sync_resolution_to_qdrant was called with None for superseded_by
    mock_sync.assert_called_once()
    call_args = mock_sync.call_args

    # Extract the superseded_by argument
    superseded_by_arg = call_args[0][4] if len(call_args[0]) > 4 else call_args[1].get("superseded_by")

    assert superseded_by_arg is None
