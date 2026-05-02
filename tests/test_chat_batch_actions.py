"""Tests for batch artifact actions — Phase 11.

Mocks subprocess to verify the wrappers construct the right CLI
invocations and parse responses sensibly. Doesn't require a real
empirica CLI on PATH.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from empirica.core.chat.actions import (
    ActionError,
    delete_artifacts_batch,
    log_artifacts_from_file,
    resolve_artifacts_batch,
)

# ─── log_artifacts_from_file ────────────────────────────────────────


class TestLogArtifactsFromFile:
    def test_missing_file_raises(self, tmp_path):
        missing = tmp_path / "nope.json"
        with pytest.raises(ActionError, match="failed to read"):
            log_artifacts_from_file(str(missing))

    def test_empty_file_raises(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("   \n")
        with pytest.raises(ActionError, match="empty"):
            log_artifacts_from_file(str(f))

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_calls_cli_with_payload(self, mock_run, tmp_path):
        payload = {"nodes": [{"ref": "f1", "type": "finding", "data": {"finding": "x"}}]}
        f = tmp_path / "batch.json"
        f.write_text(json.dumps(payload))
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"ok": true, "nodes_created": 1, "edges_wired": 0}',
            stderr="",
        )
        result = log_artifacts_from_file(str(f))
        assert result["nodes_created"] == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["empirica", "log-artifacts", "-"]
        assert mock_run.call_args.kwargs["input"] == json.dumps(payload)

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_nonzero_exit_raises(self, mock_run, tmp_path):
        f = tmp_path / "batch.json"
        f.write_text('{"nodes": []}')
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="bad payload",
        )
        with pytest.raises(ActionError, match="exit 1"):
            log_artifacts_from_file(str(f))

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_non_json_response_returns_raw(self, mock_run, tmp_path):
        f = tmp_path / "batch.json"
        f.write_text('{"nodes": []}')
        mock_run.return_value = MagicMock(
            returncode=0, stdout="not json output",
            stderr="",
        )
        result = log_artifacts_from_file(str(f))
        assert "raw_output" in result
        assert "not json output" in result["raw_output"]


# ─── resolve_artifacts_batch ────────────────────────────────────────


class TestResolveArtifactsBatch:
    def test_empty_ids_raises(self):
        with pytest.raises(ActionError, match="at least one"):
            resolve_artifacts_batch([])

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_calls_cli_with_id_payload(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"ok": true, "resolved": 2}', stderr="",
        )
        result = resolve_artifacts_batch(["uuid-1", "uuid-2"])
        assert result["resolved"] == 2
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["empirica", "resolve-artifacts", "-"]
        sent = json.loads(mock_run.call_args.kwargs["input"])
        assert sent == {"unknown_ids": ["uuid-1", "uuid-2"]}

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_nonzero_exit_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=2, stdout="", stderr="not found",
        )
        with pytest.raises(ActionError, match="exit 2"):
            resolve_artifacts_batch(["x"])


# ─── delete_artifacts_batch ─────────────────────────────────────────


class TestDeleteArtifactsBatch:
    def test_empty_ids_raises(self):
        with pytest.raises(ActionError, match="at least one"):
            delete_artifacts_batch([])

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_calls_cli_with_id_payload(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"ok": true, "deleted": 3}', stderr="",
        )
        result = delete_artifacts_batch(["a", "b", "c"])
        assert result["deleted"] == 3
        sent = json.loads(mock_run.call_args.kwargs["input"])
        assert sent == {"ids": ["a", "b", "c"]}

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_non_json_returns_raw(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="success!", stderr="",
        )
        result = delete_artifacts_batch(["x"])
        assert result == {"raw_output": "success!"}
