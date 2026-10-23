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
    parse_artifact_refs,
    resolve_artifacts_batch,
    summarize_batch,
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
            returncode=0,
            stdout='{"ok": true, "nodes_created": 1, "edges_wired": 0}',
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
            returncode=1,
            stdout="",
            stderr="bad payload",
        )
        with pytest.raises(ActionError, match="exit 1"):
            log_artifacts_from_file(str(f))

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_non_json_response_returns_raw(self, mock_run, tmp_path):
        f = tmp_path / "batch.json"
        f.write_text('{"nodes": []}')
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not json output",
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
    def test_sends_the_payload_the_verb_reads(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"ok": true, "resolved": 2}', stderr="")
        result = resolve_artifacts_batch(["uuid-1", "uuid-2"])
        assert result["resolved"] == 2
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["empirica", "resolve-artifacts", "-"]
        sent = json.loads(mock_run.call_args.kwargs["input"])
        assert [(r["type"], r["id"]) for r in sent["resolutions"]] == [("unknown", "uuid-1"), ("unknown", "uuid-2")]
        assert all(r["resolution"] for r in sent["resolutions"]), "the verb requires a resolution text"

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_nonzero_exit_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="not found")
        with pytest.raises(ActionError, match="exit 2"):
            resolve_artifacts_batch(["x"])


# ─── delete_artifacts_batch ─────────────────────────────────────────


class TestDeleteArtifactsBatch:
    def test_empty_items_raises(self):
        with pytest.raises(ActionError, match="at least one"):
            delete_artifacts_batch([])

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_previews_by_default_and_sends_typed_deletions(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"ok": true, "deleted": 0, "dry_run": true}', stderr="")
        delete_artifacts_batch([("finding", "a"), ("unknown", "b")])
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["empirica", "delete-artifacts", "-"] and "--apply" not in cmd
        sent = json.loads(mock_run.call_args.kwargs["input"])
        assert sent["deletions"] == [{"type": "finding", "id": "a"}, {"type": "unknown", "id": "b"}]

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_apply_is_an_explicit_flag(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"ok": true, "deleted": 1, "dry_run": false}', stderr=""
        )
        delete_artifacts_batch([("finding", "a")], apply=True)
        assert "--apply" in mock_run.call_args[0][0]

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_non_json_returns_raw(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="success!", stderr="")
        assert delete_artifacts_batch([("finding", "x")]) == {"raw_output": "success!"}


# ─── the payloads match the verbs' own schemas ──────────────────────


class TestPayloadsMatchTheVerbSchemas:
    """The wrapper tests above mock subprocess, so they pinned whatever shape the wrapper sent: `unknown_ids` and `ids` were
    green for as long as the verbs rejected them. The verbs publish their schemas; the payload is checked against those."""

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_resolve_payload_keys_are_in_the_schema(self, mock_run):
        from empirica.cli.command_handlers.graph_commands import RESOLVE_ARTIFACTS_SCHEMA

        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        resolve_artifacts_batch(["u1"])
        sent = json.loads(mock_run.call_args.kwargs["input"])
        assert set(sent) <= set(RESOLVE_ARTIFACTS_SCHEMA)
        assert set(sent["resolutions"][0]) <= set(RESOLVE_ARTIFACTS_SCHEMA["resolutions"][0])

    @patch("empirica.core.chat.actions.subprocess.run")
    def test_delete_payload_keys_are_in_the_schema(self, mock_run):
        from empirica.cli.command_handlers.graph_commands import DELETE_ARTIFACTS_SCHEMA

        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        delete_artifacts_batch([("finding", "f1")])
        sent = json.loads(mock_run.call_args.kwargs["input"])
        assert set(sent) <= set(DELETE_ARTIFACTS_SCHEMA)
        assert set(sent["deletions"][0]) <= set(DELETE_ARTIFACTS_SCHEMA["deletions"][0])

    def test_the_old_keys_are_not_in_either_schema(self):
        """Control: the check above can fail, because the keys the wrapper used to send are not in the schemas."""
        from empirica.cli.command_handlers.graph_commands import DELETE_ARTIFACTS_SCHEMA, RESOLVE_ARTIFACTS_SCHEMA

        assert "unknown_ids" not in RESOLVE_ARTIFACTS_SCHEMA and "ids" not in DELETE_ARTIFACTS_SCHEMA


# ─── refs and the card the chat prints ──────────────────────────────


class TestParseRefs:
    def test_type_colon_id_pairs(self):
        assert parse_artifact_refs(["finding:abc", "dead_end:def"]) == [("finding", "abc"), ("dead_end", "def")]

    @pytest.mark.parametrize("bad", ["abc", "finding:", ":abc", "lesson:abc", "nonsense:abc"])
    def test_a_ref_without_a_deletable_type_is_refused_with_the_usage(self, bad):
        with pytest.raises(ActionError, match="TYPE:ID"):
            parse_artifact_refs([bad])


class TestSummary:
    def test_a_preview_says_would_delete_never_deleted(self):
        msg = summarize_batch("delete", {"ok": True, "deleted": 0, "dry_run": True, "items": [{}, {}]}, 2, apply=False)
        assert "would delete 2" in msg and "--apply" in msg and "deleted 0" not in msg

    def test_an_applied_delete_says_how_many(self):
        assert "deleted 2 of 2" in summarize_batch(
            "delete", {"ok": True, "deleted": 2, "dry_run": False}, 2, apply=True
        )

    def test_errors_in_the_response_are_part_of_the_card(self):
        msg = summarize_batch(
            "resolve", {"ok": True, "resolved": 0, "errors": ["unknown: not found — no row matches id 'x'"]}, 1
        )
        assert "resolved 0 of 1" in msg and "not found" in msg

    def test_ok_false_is_reported_as_a_failure(self):
        msg = summarize_batch("delete", {"ok": False, "error": "Invalid JSON input"}, 1)
        assert "failed" in msg.lower() and "Invalid JSON input" in msg

    def test_a_clean_resolve_has_no_error_text(self):
        msg = summarize_batch("resolve", {"ok": True, "resolved": 2}, 2)
        assert msg == "/resolve-batch: resolved 2 of 2 unknowns"


class TestSummaryKeepsTheVerbsOwnWarnings:
    def test_a_delete_that_landed_outside_the_cwd_project_says_where(self):
        resp = {
            "ok": True,
            "deleted": 2,
            "dry_run": False,
            "target": {"db_path": "/other/sessions.db", "matches_cwd": False},
        }
        msg = summarize_batch("delete", resp, 2, apply=True)
        assert "deleted 2 of 2" in msg and "/other/sessions.db" in msg

    def test_a_verb_warning_is_shown(self):
        resp = {"ok": True, "deleted": 1, "dry_run": False, "warning": "targets the active project, not the cwd"}
        assert "targets the active project" in summarize_batch("delete", resp, 1, apply=True)

    def test_ok_false_after_rows_were_deleted_is_partial_not_failed(self):
        resp = {"ok": False, "deleted": 3, "errors": ["qdrant: connection refused"]}
        msg = summarize_batch("delete", resp, 3, apply=True)
        assert "PARTIALLY" in msg and "3 of 3" in msg and "qdrant" in msg and "failed" not in msg.lower()

    def test_ok_false_with_nothing_deleted_is_still_failed(self):
        assert "failed" in summarize_batch(
            "delete", {"ok": False, "deleted": 0, "error": "Invalid JSON"}, 1, apply=True
        )

    def test_a_clean_matching_target_adds_nothing(self):
        resp = {"ok": True, "deleted": 1, "dry_run": False, "target": {"matches_cwd": True}}
        assert summarize_batch("delete", resp, 1, apply=True) == "/delete-batch: deleted 1 of 1 artifacts"
