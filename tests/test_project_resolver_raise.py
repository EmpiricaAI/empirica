"""Tests for project_resolver.resolve_project_id raise-instead-of-sys.exit refactor.

Pre-fix: resolve_project_id called sys.exit(1) on miss. SystemExit derives
from BaseException (not Exception), so library callers (POSTFLIGHT
pipeline stages and similar) couldn't catch it via `except Exception`,
and the process died — taking down POSTFLIGHT mid-pipeline. See #95
(pschwinger) for the live repro.

Post-fix: resolve_project_id raises ProjectNotFoundError (a normal
Exception subclass). Library callers can catch and recover. Top-level
CLI handlers' existing `except Exception` paths print the error and
exit cleanly via the standard handle_cli_error pipeline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from empirica.cli.utils.project_resolver import (
    ProjectNotFoundError,
    resolve_project_id,
)


class TestResolveProjectIdRaises:
    def test_raises_project_not_found_error_on_miss(self):
        """The function raises ProjectNotFoundError when the input can't
        resolve in either the local DB or workspace.db."""
        mock_db = MagicMock()
        mock_db.resolve_project_id.return_value = None

        with patch('empirica.utils.session_resolver.InstanceResolver') as mock_R:
            mock_R.resolve_workspace_project.return_value = None
            with pytest.raises(ProjectNotFoundError) as excinfo:
                resolve_project_id("nonexistent-project", db=mock_db)

        assert excinfo.value.project_id_or_name == "nonexistent-project"
        assert "nonexistent-project" in str(excinfo.value)
        assert "not found" in str(excinfo.value)

    def test_does_not_call_sys_exit(self):
        """The function must NOT call sys.exit on miss — that's the bug
        we're fixing. SystemExit derives from BaseException, escapes
        every `except Exception` wrapper, kills POSTFLIGHT."""
        import sys as _sys

        mock_db = MagicMock()
        mock_db.resolve_project_id.return_value = None

        with patch('empirica.utils.session_resolver.InstanceResolver') as mock_R, \
             patch.object(_sys, 'exit') as mock_exit:
            mock_R.resolve_workspace_project.return_value = None
            with pytest.raises(ProjectNotFoundError):
                resolve_project_id("missing", db=mock_db)
            mock_exit.assert_not_called()

    def test_returns_uuid_when_local_db_has_match(self):
        mock_db = MagicMock()
        mock_db.resolve_project_id.return_value = "a-real-uuid"

        result = resolve_project_id("project-name", db=mock_db)
        assert result == "a-real-uuid"

    def test_falls_back_to_workspace_db_when_local_misses(self):
        """If the local DB has no match, the workspace.db cross-project
        registry is the second-chance lookup — must run before raising."""
        mock_db = MagicMock()
        mock_db.resolve_project_id.return_value = None

        with patch('empirica.utils.session_resolver.InstanceResolver') as mock_R:
            mock_R.resolve_workspace_project.return_value = {
                'project_id': 'workspace-uuid',
            }
            result = resolve_project_id("name-not-in-local", db=mock_db)
            assert result == "workspace-uuid"

    def test_workspace_fallback_id_field_compatibility(self):
        """resolve_workspace_project may return either 'project_id' or 'id'
        — both must be accepted (backwards compat)."""
        mock_db = MagicMock()
        mock_db.resolve_project_id.return_value = None

        with patch('empirica.utils.session_resolver.InstanceResolver') as mock_R:
            mock_R.resolve_workspace_project.return_value = {'id': 'old-style-uuid'}
            result = resolve_project_id("legacy", db=mock_db)
            assert result == "old-style-uuid"


class TestProjectNotFoundError:
    def test_is_a_normal_exception(self):
        """The whole point of this refactor — ProjectNotFoundError must
        derive from Exception (not BaseException directly), so existing
        `except Exception` wrappers catch it cleanly. SystemExit's
        BaseException heritage was the actual bug."""
        err = ProjectNotFoundError("foo")
        assert isinstance(err, Exception)
        # Sanity check on the inheritance chain — must NOT be a BaseException-only subclass
        assert not isinstance(err, SystemExit)
        assert not isinstance(err, KeyboardInterrupt)

    def test_carries_input_for_error_messages(self):
        err = ProjectNotFoundError("my-project-name")
        assert err.project_id_or_name == "my-project-name"
        assert "my-project-name" in str(err)

    def test_can_be_caught_by_except_exception(self):
        """Smoke test the actual contract: a library caller can catch
        and recover. This is what cortex sync, POSTFLIGHT pipeline
        stages, and other library callers need."""
        try:
            raise ProjectNotFoundError("test-input")
        except Exception as e:
            # Caught successfully — this is the desired behavior.
            # Pre-fix (sys.exit), this `except Exception` would have
            # been bypassed and the process would have died.
            assert isinstance(e, ProjectNotFoundError)
            assert e.project_id_or_name == "test-input"  # type: ignore[attr-defined]
