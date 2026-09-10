"""Tests for empirica.core.cockpit.notify_dispatcher_view —
cockpit-facing view onto dispatcher state + audit telemetry.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from empirica.core.cockpit.notify_dispatcher_view import (
    annotate_loops_with_last_notify,
    build_notify_dispatcher_block,
)
from empirica.core.notify.audit import append_audit
from empirica.core.notify.config import NotifyConfig


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


class TestBuildBlock:
    def test_empty_audit_yields_baseline_shape(self, tmp_path):
        cfg = NotifyConfig(default_backend="stdout", backends={"stdout": {}})
        with (
            patch("empirica.core.cockpit.notify_dispatcher_view.read_recent", return_value=[]),
            patch("empirica.core.cockpit.notify_dispatcher_view.last_failure", return_value=None),
            patch("empirica.core.cockpit.notify_dispatcher_view.fell_back_count", return_value=0),
            patch("empirica.core.cockpit.notify_dispatcher_view.emit_count", return_value=0),
        ):
            block = build_notify_dispatcher_block(config=cfg)
        assert block["default_backend"] == "stdout"
        assert block["recent"] == []
        assert block["last_failure"] is None
        assert block["banner_failure"] is None
        assert block["fell_back_count_24h"] == 0
        assert block["emit_count_24h"] == 0
        # backends list reflects registered backends
        names = {b["name"] for b in block["backends"]}
        assert names == {"stdout", "log", "ntfy"}

    def test_ntfy_backend_includes_auth_method_and_server(self):
        cfg = NotifyConfig(
            default_backend="ntfy",
            backends={
                "ntfy": {
                    "server": "https://ntfy.example.com",
                    "auth_method": "bearer",
                    "auth_env": "__NEVER_SET",
                    "default_topic": "empirica",
                },
            },
        )
        with (
            patch("empirica.core.cockpit.notify_dispatcher_view.read_recent", return_value=[]),
            patch("empirica.core.cockpit.notify_dispatcher_view.last_failure", return_value=None),
            patch("empirica.core.cockpit.notify_dispatcher_view.fell_back_count", return_value=0),
            patch("empirica.core.cockpit.notify_dispatcher_view.emit_count", return_value=0),
        ):
            block = build_notify_dispatcher_block(config=cfg)
        ntfy = next(b for b in block["backends"] if b["name"] == "ntfy")
        assert ntfy["auth_method"] == "bearer"
        assert ntfy["server"] == "https://ntfy.example.com"
        assert ntfy["default_topic"] == "empirica"
        # secret never surfaced
        assert "auth_env" not in ntfy or ntfy.get("auth_env") is None

    def test_banner_failure_set_when_recent(self):
        cfg = NotifyConfig(default_backend="stdout")
        recent_ts = datetime.now(tz=timezone.utc).isoformat()
        with (
            patch("empirica.core.cockpit.notify_dispatcher_view.read_recent", return_value=[]),
            patch(
                "empirica.core.cockpit.notify_dispatcher_view.last_failure",
                return_value={"ts": recent_ts, "resolved_backend": "ntfy", "detail": "auth_env unset", "ok": False},
            ),
            patch("empirica.core.cockpit.notify_dispatcher_view.fell_back_count", return_value=1),
            patch("empirica.core.cockpit.notify_dispatcher_view.emit_count", return_value=5),
        ):
            block = build_notify_dispatcher_block(config=cfg)
        assert block["banner_failure"] is not None
        assert block["banner_failure"]["resolved_backend"] == "ntfy"
        assert "age_seconds" in block["banner_failure"]

    def test_banner_failure_none_when_old(self):
        cfg = NotifyConfig(default_backend="stdout")
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=4)).isoformat()
        with (
            patch("empirica.core.cockpit.notify_dispatcher_view.read_recent", return_value=[]),
            patch(
                "empirica.core.cockpit.notify_dispatcher_view.last_failure",
                return_value={"ts": old_ts, "resolved_backend": "ntfy", "detail": "old", "ok": False},
            ),
            patch("empirica.core.cockpit.notify_dispatcher_view.fell_back_count", return_value=0),
            patch("empirica.core.cockpit.notify_dispatcher_view.emit_count", return_value=0),
        ):
            block = build_notify_dispatcher_block(config=cfg)
        # last_failure is set (historical record), but no banner.
        assert block["last_failure"] is not None
        assert block["banner_failure"] is None

    def test_telemetry_failure_returns_empty_block(self):
        # If anything blows up internally, the block must be a safe empty.
        cfg = NotifyConfig(default_backend="stdout")
        with patch(
            "empirica.core.cockpit.notify_dispatcher_view.read_recent",
            side_effect=Exception("boom"),
        ):
            block = build_notify_dispatcher_block(config=cfg)
        assert block["default_backend"] is None
        assert block["backends"] == []
        assert block["recent"] == []


class TestAnnotateLoops:
    def test_matches_loops_by_source(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(source="loop:metrics", resolved_backend="ntfy", topic="m"), path=path)
        append_audit(**_row(source="loop:other", resolved_backend="log"), path=path)
        loops = {
            "metrics": {"kind": "monitor"},
            "idle": {"kind": "monitor"},
        }
        annotate_loops_with_last_notify(loops, audit_path=path)
        assert loops["metrics"]["last_notify"] is not None
        assert loops["metrics"]["last_notify"]["resolved_backend"] == "ntfy"
        assert loops["metrics"]["last_notify"]["topic"] == "m"
        assert loops["idle"]["last_notify"] is None

    def test_picks_most_recent_per_loop(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(**_row(source="loop:a", resolved_backend="stdout"), path=path)
        append_audit(**_row(source="loop:a", resolved_backend="ntfy", topic="t"), path=path)
        loops = {"a": {"kind": "monitor"}}
        annotate_loops_with_last_notify(loops, audit_path=path)
        assert loops["a"]["last_notify"]["resolved_backend"] == "ntfy"

    def test_fell_back_propagated(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        append_audit(
            **_row(source="loop:x", fell_back=True, fallback_reason="ntfy not configured"),
            path=path,
        )
        loops = {"x": {"kind": "monitor"}}
        annotate_loops_with_last_notify(loops, audit_path=path)
        assert loops["x"]["last_notify"]["fell_back"] is True

    def test_empty_dict_no_op(self):
        loops: dict[str, dict] = {}
        annotate_loops_with_last_notify(loops)
        assert loops == {}
