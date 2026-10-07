"""Tests verifying that malformed pending files don't abort consume_pending.

Ensures one broken file cannot prevent processing of other valid requests.
This tests the three-step fix:
1. from_path returns None when JSON is not a dict
2. listener_uninstall_request handles curl_pid parsing failures gracefully
3. consume_pending wraps from_path call and skips bad files
"""

from __future__ import annotations

import json

import pytest

from empirica.core.cockpit import listener_install_request as liir
from empirica.core.cockpit import listener_uninstall_request as lunr
from empirica.core.cockpit import loop_install_request as lir
from empirica.core.cockpit import loop_uninstall_request as lunreq


@pytest.fixture
def empirica_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(lir, "EMPIRICA_DIR", tmp_path)
    monkeypatch.setattr(liir, "EMPIRICA_DIR", tmp_path)
    monkeypatch.setattr(lunr, "EMPIRICA_DIR", tmp_path)
    monkeypatch.setattr(lunreq, "EMPIRICA_DIR", tmp_path)
    return tmp_path


class TestLoopInstallMalformed:
    """Test that malformed JSON in loop install pending files doesn't abort consume."""

    def test_non_dict_json_skipped(self, empirica_dir):
        """JSON that parses but is not a dict should be skipped."""
        # Write a valid request
        lir.write_pending(instance_id="tmux_1", name="good", interval="15m")
        # Write a malformed file that's valid JSON but not a dict
        bad_path = lir.pending_path("tmux_1", "bad")
        bad_path.write_text('"just a string"')

        reqs = lir.consume_pending("tmux_1")
        assert len(reqs) == 1
        assert reqs[0].name == "good"
        # Both files should be deleted
        assert not lir.pending_path("tmux_1", "good").exists()
        assert not bad_path.exists()

    def test_list_as_json_skipped(self, empirica_dir):
        """JSON array should be skipped (not a dict)."""
        lir.write_pending(instance_id="tmux_1", name="good", interval="15m")
        bad_path = lir.pending_path("tmux_1", "array")
        bad_path.write_text('["a", "b"]')

        reqs = lir.consume_pending("tmux_1")
        assert len(reqs) == 1
        assert reqs[0].name == "good"
        assert not bad_path.exists()

    def test_mixed_good_and_bad_files(self, empirica_dir):
        """Multiple valid and invalid files processed together."""
        lir.write_pending(instance_id="tmux_1", name="first", interval="15m")
        lir.write_pending(instance_id="tmux_1", name="third", interval="30m")

        # Insert a malformed file in between
        bad_path = lir.pending_path("tmux_1", "second-bad")
        bad_path.write_text("{ invalid json")

        reqs = lir.consume_pending("tmux_1")
        names = sorted(r.name for r in reqs)
        assert names == ["first", "third"]
        # All three files deleted
        assert not bad_path.exists()


class TestListenerInstallMalformed:
    """Test that malformed JSON in listener install pending files doesn't abort consume."""

    def test_non_dict_json_skipped(self, empirica_dir):
        """JSON that parses but is not a dict should be skipped."""
        liir.write_pending(instance_id="tmux_1", name="good", topic="ntfy:test")
        bad_path = liir.pending_path("tmux_1", "bad")
        bad_path.write_text("[1, 2, 3]")

        reqs = liir.consume_pending("tmux_1")
        assert len(reqs) == 1
        assert reqs[0].name == "good"
        assert not bad_path.exists()

    def test_malformed_with_valid(self, empirica_dir):
        """Malformed file alongside valid ones."""
        liir.write_pending(instance_id="tmux_1", name="valid-a", topic="ntfy:a")
        liir.write_pending(instance_id="tmux_1", name="valid-b", topic="ntfy:b")
        bad_path = liir.pending_path("tmux_1", "broken")
        bad_path.write_text('{"incomplete":')

        reqs = liir.consume_pending("tmux_1")
        names = sorted(r.name for r in reqs)
        assert names == ["valid-a", "valid-b"]
        assert not bad_path.exists()


class TestListenerUninstallMalformed:
    """Test listener uninstall handles malformed files and bad curl_pid values."""

    def test_non_dict_json_skipped(self, empirica_dir):
        """JSON that parses but is not a dict should be skipped."""
        lunr.write_pending(instance_id="tmux_1", name="good", monitor_task_id="tk-1", curl_pid=42)
        bad_path = lunr.pending_path("tmux_1", "bad")
        bad_path.write_text("null")

        reqs = lunr.consume_pending("tmux_1")
        assert len(reqs) == 1
        assert reqs[0].name == "good"
        assert not bad_path.exists()

    def test_curl_pid_non_integer_value(self, empirica_dir):
        """curl_pid field that can't be parsed as int should default to None."""
        lunr.write_pending(instance_id="tmux_1", name="good", monitor_task_id="tk-1", curl_pid=42)
        bad_path = lunr.pending_path("tmux_1", "bad-pid")
        bad_path.write_text(
            json.dumps(
                {
                    "instance_id": "tmux_1",
                    "name": "bad-pid",
                    "monitor_task_id": "tk-2",
                    "curl_pid": "not-an-int",
                    "requested_at": "2026-01-01T00:00:00Z",
                }
            )
        )

        reqs = lunr.consume_pending("tmux_1")
        assert len(reqs) == 2
        names = sorted(r.name for r in reqs)
        assert names == ["bad-pid", "good"]
        # The bad-pid request should have None for curl_pid
        bad_req = next(r for r in reqs if r.name == "bad-pid")
        assert bad_req.curl_pid is None
        # Both files should be deleted
        assert not bad_path.exists()

    def test_curl_pid_as_string_number(self, empirica_dir):
        """curl_pid as string number should convert successfully."""
        path = lunr.pending_path("tmux_1", "string-pid")
        path.write_text(
            json.dumps(
                {
                    "instance_id": "tmux_1",
                    "name": "string-pid",
                    "monitor_task_id": "tk",
                    "curl_pid": "12345",
                }
            )
        )
        req = lunr.ListenerUninstallRequest.from_path(path)
        assert req is not None
        assert req.curl_pid == 12345

    def test_curl_pid_as_float(self, empirica_dir):
        """curl_pid as float should convert to int."""
        path = lunr.pending_path("tmux_1", "float-pid")
        path.write_text(
            json.dumps(
                {
                    "instance_id": "tmux_1",
                    "name": "float-pid",
                    "monitor_task_id": "tk",
                    "curl_pid": 12345.0,
                }
            )
        )
        req = lunr.ListenerUninstallRequest.from_path(path)
        assert req is not None
        assert req.curl_pid == 12345

    def test_mixed_good_and_bad_files(self, empirica_dir):
        """Multiple files with curl_pid issues."""
        lunr.write_pending(instance_id="tmux_1", name="good-1", monitor_task_id="tk-1")
        lunr.write_pending(instance_id="tmux_1", name="good-2", monitor_task_id="tk-2", curl_pid=999)

        bad_path = lunr.pending_path("tmux_1", "bad")
        bad_path.write_text(
            json.dumps(
                {
                    "instance_id": "tmux_1",
                    "name": "bad",
                    "monitor_task_id": "tk-3",
                    "curl_pid": {"not": "an int"},
                }
            )
        )

        reqs = lunr.consume_pending("tmux_1")
        assert len(reqs) == 3
        names = sorted(r.name for r in reqs)
        assert names == ["bad", "good-1", "good-2"]
        # bad should have None curl_pid
        bad_req = next(r for r in reqs if r.name == "bad")
        assert bad_req.curl_pid is None
        assert not bad_path.exists()


class TestLoopUninstallMalformed:
    """Test that malformed JSON in loop uninstall pending files doesn't abort consume."""

    def test_non_dict_json_skipped(self, empirica_dir):
        """JSON that parses but is not a dict should be skipped."""
        lunreq.write_pending(instance_id="tmux_1", name="good", job_id="job-1")
        bad_path = lunreq.pending_path("tmux_1", "bad")
        bad_path.write_text("123")

        reqs = lunreq.consume_pending("tmux_1")
        assert len(reqs) == 1
        assert reqs[0].name == "good"
        assert not bad_path.exists()

    def test_multiple_requests_with_one_malformed(self, empirica_dir):
        """One malformed file doesn't prevent others from being processed."""
        lunreq.write_pending(instance_id="tmux_1", name="first", job_id="job-a")
        lunreq.write_pending(instance_id="tmux_1", name="third", job_id="job-c")
        bad_path = lunreq.pending_path("tmux_1", "second")
        bad_path.write_text("true")  # Valid JSON but not a dict

        reqs = lunreq.consume_pending("tmux_1")
        names = sorted(r.name for r in reqs)
        assert names == ["first", "third"]
        assert not bad_path.exists()


class TestFromPathReturnsNone:
    """Verify from_path returns None for non-dict JSON."""

    def test_loop_install_from_path_non_dict(self, empirica_dir):
        path = lir.pending_path("tmux_1", "bad")
        path.write_text('{"key": "value"}')
        result = lir.LoopInstallRequest.from_path(path)
        assert result is not None  # This is a valid dict

        path.write_text("[1, 2, 3]")
        result = lir.LoopInstallRequest.from_path(path)
        assert result is None

    def test_listener_install_from_path_non_dict(self, empirica_dir):
        path = liir.pending_path("tmux_1", "bad")
        path.write_text('"string"')
        result = liir.ListenerInstallRequest.from_path(path)
        assert result is None

    def test_listener_uninstall_from_path_non_dict(self, empirica_dir):
        path = lunr.pending_path("tmux_1", "bad")
        path.write_text("42")
        result = lunr.ListenerUninstallRequest.from_path(path)
        assert result is None

    def test_loop_uninstall_from_path_non_dict(self, empirica_dir):
        path = lunreq.pending_path("tmux_1", "bad")
        path.write_text("[]")
        result = lunreq.LoopUninstallRequest.from_path(path)
        assert result is None


def test_a_request_that_raises_while_loading_is_logged_by_name_not_dropped_unsaid(empirica_dir, monkeypatch, caplog):
    """The draft swallowed the exception silently; the skipped file is named in a warning so a lost request leaves a trace."""
    lir.write_pending(instance_id="tmux_1", name="good", interval="15m")
    bad = lir.pending_path("tmux_1", "explodes")
    bad.write_text("{}")
    original = lir.LoopInstallRequest.from_path

    def boom(path):
        if path.name == bad.name:
            raise RuntimeError("unexpected shape")
        return original(path)

    monkeypatch.setattr(lir.LoopInstallRequest, "from_path", staticmethod(boom))
    with caplog.at_level("WARNING"):
        reqs = lir.consume_pending("tmux_1")
    assert [r.name for r in reqs] == ["good"]
    assert any(bad.name in rec.getMessage() and "RuntimeError" in rec.getMessage() for rec in caplog.records)
