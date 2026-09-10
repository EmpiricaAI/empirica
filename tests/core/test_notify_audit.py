"""Tests for empirica.core.notify.audit — dispatcher audit log.

The audit log is the source of truth for cockpit dispatcher views. It
records every emit attempt (success, failure, fallback) with metadata
only — no titles/messages/rationale.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from empirica.core.notify.audit import (
    append_audit,
    emit_count,
    fell_back_count,
    last_emit_by_source,
    last_failure,
    read_recent,
)


def _row(**overrides):
    base = {
        "source": "loop:test",
        "severity": "info",
        "topic": None,
        "resolved_backend": "stdout",
        "fell_back": False,
        "fallback_reason": None,
        "ok": True,
        "response_code": None,
        "detail": "printed",
        "project_id": None,
    }
    base.update(overrides)
    return base


class TestAppendAudit:
    def test_writes_jsonl_line(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(), path=path)
        line = path.read_text().strip()
        parsed = json.loads(line)
        assert parsed["source"] == "loop:test"
        assert parsed["resolved_backend"] == "stdout"
        assert parsed["ok"] is True
        assert "ts" in parsed

    def test_metadata_only_no_content_fields(self, tmp_path):
        # Sharp edge: never include title/message/tags. Audit is telemetry.
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(), path=path)
        parsed = json.loads(path.read_text().strip())
        assert "title" not in parsed
        assert "message" not in parsed
        assert "rationale" not in parsed
        assert "tags" not in parsed

    def test_appends_not_overwrites(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(source="a"), path=path)
        append_audit(**_row(source="b"), path=path)
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_failure_swallowed(self, tmp_path):
        # Telemetry must never block emit. Missing parent dir is handled by mkdir,
        # but a path that can't be created (e.g. permission-denied) must not raise.
        path = tmp_path / "nonwritable" / "audit.jsonl"
        # Should not raise. mkdir(parents=True) handles the parent.
        append_audit(**_row(), path=path)
        assert path.exists()


class TestReadRecent:
    def test_empty_file_returns_empty(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        assert read_recent(path=path) == []

    def test_returns_last_n(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        for i in range(10):
            append_audit(**_row(source=f"src-{i}"), path=path)
        rows = read_recent(limit=3, path=path)
        assert len(rows) == 3
        assert [r["source"] for r in rows] == ["src-7", "src-8", "src-9"]

    def test_tolerates_malformed_lines(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(source="good-1"), path=path)
        # Inject garbage line.
        with open(path, "a") as f:
            f.write("not-json\n")
        append_audit(**_row(source="good-2"), path=path)
        rows = read_recent(limit=10, path=path)
        assert [r["source"] for r in rows] == ["good-1", "good-2"]


class TestLastFailure:
    def test_none_when_no_failures(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(ok=True), path=path)
        append_audit(**_row(ok=True), path=path)
        assert last_failure(path=path) is None

    def test_returns_most_recent_failure(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(source="ok-1", ok=True), path=path)
        append_audit(**_row(source="fail-1", ok=False, detail="4xx"), path=path)
        append_audit(**_row(source="ok-2", ok=True), path=path)
        append_audit(**_row(source="fail-2", ok=False, detail="5xx"), path=path)
        append_audit(**_row(source="ok-3", ok=True), path=path)
        result = last_failure(path=path)
        assert result is not None
        assert result["source"] == "fail-2"
        assert result["detail"] == "5xx"


class TestFellBackCount:
    def test_counts_fell_back_within_window(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        # 3 events, 2 with fell_back=True
        append_audit(**_row(source="a", fell_back=True), path=path)
        append_audit(**_row(source="b", fell_back=False), path=path)
        append_audit(**_row(source="c", fell_back=True), path=path)
        assert fell_back_count(window_hours=24.0, path=path) == 2

    def test_excludes_outside_window(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        # Manually craft a row with old ts.
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=48)).isoformat()
        with open(path, "w") as f:
            f.write(json.dumps({**_row(fell_back=True), "ts": old_ts}) + "\n")
        # Plus a recent one.
        append_audit(**_row(fell_back=True), path=path)
        assert fell_back_count(window_hours=24.0, path=path) == 1

    def test_zero_when_file_missing(self, tmp_path):
        assert fell_back_count(path=tmp_path / "missing.jsonl") == 0


class TestEmitCount:
    def test_counts_within_window(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        for i in range(5):
            append_audit(**_row(source=f"src-{i}"), path=path)
            time.sleep(0.001)  # ensure distinct timestamps
        assert emit_count(window_hours=24.0, path=path) == 5


class TestLastEmitBySource:
    def test_returns_latest_per_source(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(source="loop:a", resolved_backend="stdout"), path=path)
        append_audit(**_row(source="loop:b", resolved_backend="log"), path=path)
        append_audit(**_row(source="loop:a", resolved_backend="ntfy"), path=path)
        result = last_emit_by_source(["loop:a", "loop:b"], path=path)
        assert result["loop:a"]["resolved_backend"] == "ntfy"  # latest
        assert result["loop:b"]["resolved_backend"] == "log"

    def test_skips_unmatched_sources(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(source="loop:other"), path=path)
        result = last_emit_by_source(["loop:a"], path=path)
        assert result == {}

    def test_empty_sources_returns_empty(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(source="loop:a"), path=path)
        assert last_emit_by_source([], path=path) == {}


class TestRotation:
    def test_no_rotation_below_threshold(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(), path=path)
        assert path.exists()
        rotated = path.with_suffix(path.suffix + ".1")
        assert not rotated.exists()
