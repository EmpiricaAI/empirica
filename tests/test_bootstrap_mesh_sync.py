"""Tests for project-bootstrap mesh-agreements sync (trigger #1 of the
MESH_SHARING_AGREEMENTS.md sync contract).

Wires ``empirica.core.mesh_sharing.sync_from_cortex`` into
``project_bootstrap.handle_project_bootstrap_command`` as a non-fatal step
so every session-start refreshes the local entity_registry mirror.

Coverage:
1. Sync runs when cortex creds are present; uses returned SyncResult.
2. Sync silently skips when cortex creds are missing (e.g. offline-first
   install).
3. Sync logs a debug message on cortex transport error without raising.
4. Sync logs a debug message on any unexpected exception without raising.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch


def test_sync_runs_when_creds_present(caplog):
    """Happy path: creds resolve → WorkspaceDBRepository.open is called,
    sync_from_cortex result logged at INFO."""
    from empirica.cli.command_handlers.project_bootstrap import (
        _sync_mesh_sharing_agreements,
    )

    mock_result = MagicMock()
    mock_result.error = None
    mock_result.added = 2
    mock_result.updated = 1
    mock_result.marked_revoked = 0

    fake_repo = MagicMock()
    fake_repo.__enter__ = MagicMock(return_value=fake_repo)
    fake_repo.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "empirica.config.credentials_loader.get_credentials_loader",
        ) as cred_loader,
        patch(
            "empirica.data.repositories.workspace_db.WorkspaceDBRepository.open",
            return_value=fake_repo,
        ),
        patch(
            "empirica.core.mesh_sharing.sync_from_cortex",
            return_value=mock_result,
        ) as mock_sync,
        caplog.at_level(logging.INFO, logger="empirica.cli.command_handlers.project_bootstrap"),
    ):
        cred_loader.return_value.get_cortex_config.return_value = {
            "url": "https://example.com",
            "api_key": "ctx_test_key",
        }
        # A MagicMock would hand back a truthy token from cortex_access_token and
        # the api_key path would never run — the mock has to model a seat with no
        # OAuth session, not merely a seat.
        cred_loader.return_value.get_cortex_oauth.return_value = {}
        cred_loader.return_value.cortex_access_token.return_value = None
        _sync_mesh_sharing_agreements()

    mock_sync.assert_called_once()
    # Repo + url + credential passed through
    args, _kwargs = mock_sync.call_args
    assert args[0] is fake_repo
    assert args[1] == "https://example.com"
    assert args[2] == "ctx_test_key"
    # Result counts logged
    assert any("2 added" in r.message and "1 updated" in r.message for r in caplog.records)


def test_sync_skips_when_creds_missing(caplog):
    """No cortex url/key in config → skip with debug log, no exception."""
    from empirica.cli.command_handlers.project_bootstrap import (
        _sync_mesh_sharing_agreements,
    )

    with (
        patch(
            "empirica.config.credentials_loader.get_credentials_loader",
        ) as cred_loader,
        patch("empirica.core.mesh_sharing.sync_from_cortex") as mock_sync,
        caplog.at_level(logging.DEBUG, logger="empirica.cli.command_handlers.project_bootstrap"),
    ):
        cred_loader.return_value.get_cortex_config.return_value = {}
        cred_loader.return_value.get_cortex_oauth.return_value = {}
        cred_loader.return_value.cortex_access_token.return_value = None
        _sync_mesh_sharing_agreements()

    mock_sync.assert_not_called()
    # Asserts that a REASON was logged, not its exact wording — the previous
    # version pinned the string "creds missing" and broke on a message that says
    # the same thing better.
    assert any("skipped" in r.message for r in caplog.records)


def test_sync_runs_for_an_oauth_only_seat(caplog):
    """The capability this gate used to deny.

    A seat authenticated by `empirica auth login` has no api_key at all. The old
    gate read `url and api_key` and skipped the sync entirely, at debug level —
    so the mirror silently never refreshed and nothing said why. The fetcher
    already sends `Authorization: Bearer {...}`, so the token is a drop-in.
    """
    from empirica.cli.command_handlers.project_bootstrap import (
        _sync_mesh_sharing_agreements,
    )

    mock_result = MagicMock()
    mock_result.error = None
    mock_result.added = mock_result.updated = mock_result.marked_revoked = 0

    fake_repo = MagicMock()
    fake_repo.__enter__ = MagicMock(return_value=fake_repo)
    fake_repo.__exit__ = MagicMock(return_value=False)

    with (
        patch("empirica.config.credentials_loader.get_credentials_loader") as cred_loader,
        patch(
            "empirica.data.repositories.workspace_db.WorkspaceDBRepository.open",
            return_value=fake_repo,
        ),
        patch("empirica.core.mesh_sharing.sync_from_cortex", return_value=mock_result) as mock_sync,
        caplog.at_level(logging.DEBUG, logger="empirica.cli.command_handlers.project_bootstrap"),
    ):
        cred_loader.return_value.get_cortex_config.return_value = {"url": "https://example.com"}
        cred_loader.return_value.get_cortex_oauth.return_value = {"refresh_owner": "cli"}
        cred_loader.return_value.cortex_access_token.return_value = "oauth_access_token"
        _sync_mesh_sharing_agreements()

    mock_sync.assert_called_once()
    args, _ = mock_sync.call_args
    assert args[1] == "https://example.com"
    assert args[2] == "oauth_access_token", "the OAuth token must reach the fetcher, not an absent api_key"


def test_sync_transport_error_is_non_fatal(caplog):
    """sync_from_cortex returns SyncResult(error='...') → debug log, no
    exception."""
    from empirica.cli.command_handlers.project_bootstrap import (
        _sync_mesh_sharing_agreements,
    )

    mock_result = MagicMock()
    mock_result.error = "fetch failed: HTTP 503"
    mock_result.added = 0
    mock_result.updated = 0
    mock_result.marked_revoked = 0

    fake_repo = MagicMock()
    fake_repo.__enter__ = MagicMock(return_value=fake_repo)
    fake_repo.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "empirica.config.credentials_loader.get_credentials_loader",
        ) as cred_loader,
        patch(
            "empirica.data.repositories.workspace_db.WorkspaceDBRepository.open",
            return_value=fake_repo,
        ),
        patch(
            "empirica.core.mesh_sharing.sync_from_cortex",
            return_value=mock_result,
        ),
        caplog.at_level(logging.DEBUG, logger="empirica.cli.command_handlers.project_bootstrap"),
    ):
        cred_loader.return_value.get_cortex_config.return_value = {
            "url": "https://example.com",
            "api_key": "ctx_key",
        }
        _sync_mesh_sharing_agreements()  # must not raise

    assert any("HTTP 503" in r.message for r in caplog.records)


def test_sync_unexpected_exception_is_swallowed(caplog):
    """If any underlying call throws (DB locked, import error, etc.),
    bootstrap continues — _sync swallows + debug logs."""
    from empirica.cli.command_handlers.project_bootstrap import (
        _sync_mesh_sharing_agreements,
    )

    with (
        patch(
            "empirica.config.credentials_loader.get_credentials_loader",
            side_effect=RuntimeError("simulated import error"),
        ),
        caplog.at_level(logging.DEBUG, logger="empirica.cli.command_handlers.project_bootstrap"),
    ):
        _sync_mesh_sharing_agreements()  # must not raise

    assert any("skipped" in r.message and "simulated" in r.message for r in caplog.records)
