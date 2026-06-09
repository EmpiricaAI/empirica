"""Tests for `empirica mesh migrate-topics` — closes empirica's slice of
SER ser_dd1955ae07e04949a28bd5bc (canonical ntfy channel model).

The verb detects retired ntfy topics in ~/.empirica/credentials.yaml and
~/.empirica/listener_active_*.json markers and rewrites them to the
per-tenant canonical resolved from cortex's notification-channels
endpoint. Dry-run by default.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import patch

from empirica.cli.command_handlers.mesh_commands import (
    _is_retired_topic,
    _migrate_credentials_topic,
    _migrate_listener_active_markers,
    _strip_ntfy_topic_url,
    handle_mesh_migrate_topics_command,
)

# ── pure classifier ────────────────────────────────────────────────────


def test_strip_drops_scheme_and_tags():
    assert _strip_ntfy_topic_url("ntfy:empirica-orchestration-events-david?tags=cortex") == \
        "empirica-orchestration-events-david"
    assert _strip_ntfy_topic_url("empirica-orchestration-events-david") == \
        "empirica-orchestration-events-david"
    assert _strip_ntfy_topic_url("") == ""


def test_bare_orchestration_events_is_retired():
    assert _is_retired_topic("orchestration-events") is True


def test_pre_tenant_per_org_form_is_retired():
    """`<org>-orchestration-events` without the `-<tenant>` segment is the
    pre-T16/T17 per-org form and now retired."""
    assert _is_retired_topic("empirica-orchestration-events") is True


def test_per_practice_topic_is_retired():
    """A per-practice topic (no `-orchestration-events-` segment at all)
    is retired per the canonical model."""
    assert _is_retired_topic("ffp-archive") is True
    assert _is_retired_topic("autonomy") is True


def test_per_tenant_canonical_is_kept():
    assert _is_retired_topic("empirica-orchestration-events-david") is False
    assert _is_retired_topic("mod-orchestration-events-philipp") is False


def test_empty_is_not_retired():
    """Empty topic returns False (skip path; nothing to migrate)."""
    assert _is_retired_topic("") is False


# ── credentials.yaml migration ─────────────────────────────────────────


def _patch_creds(tmp_path: Path, monkeypatch, body: str | None) -> None:
    """Point credentials.yaml resolution at tmp_path and reset the loader cache."""
    from empirica.config.credentials_loader import CredentialsLoader
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(tmp_path / "credentials.yaml"))
    monkeypatch.delenv("ORCHESTRATION_NTFY_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_TOPIC", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_TOKEN", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_USER", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_PASS", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    if body is not None:
        (tmp_path / "credentials.yaml").write_text(body, encoding="utf-8")


def test_credentials_migration_dry_run_does_not_write(tmp_path, monkeypatch):
    _patch_creds(tmp_path, monkeypatch,
        "ntfy:\n"
        "  url: https://ntfy.example\n"
        "  topic: orchestration-events\n"
        "  token: tk_test_keep_x123\n",
    )
    report = _migrate_credentials_topic("empirica-orchestration-events-david", apply=False)
    assert report["action"] == "rewrite"
    assert report["current"] == "orchestration-events"
    assert report["canonical"] == "empirica-orchestration-events-david"
    assert report["applied"] is False
    # Disk unchanged
    on_disk = (tmp_path / "credentials.yaml").read_text()
    assert "topic: orchestration-events" in on_disk


def test_credentials_migration_apply_rewrites_topic_only(tmp_path, monkeypatch):
    _patch_creds(tmp_path, monkeypatch,
        "ntfy:\n"
        "  url: https://ntfy.example\n"
        "  topic: orchestration-events\n"
        "  token: tk_test_keep_x123\n",
    )
    report = _migrate_credentials_topic("empirica-orchestration-events-david", apply=True)
    assert report["action"] == "rewrite"
    assert report["applied"] is True
    on_disk = (tmp_path / "credentials.yaml").read_text()
    assert "empirica-orchestration-events-david" in on_disk
    # url + token preserved on partial-update
    assert "https://ntfy.example" in on_disk
    assert "tk_test_keep_x123" in on_disk


def test_credentials_canonical_topic_is_kept(tmp_path, monkeypatch):
    _patch_creds(tmp_path, monkeypatch,
        "ntfy:\n"
        "  url: https://ntfy.example\n"
        "  topic: empirica-orchestration-events-david\n"
        "  token: tk_test_keep_x123\n",
    )
    report = _migrate_credentials_topic("empirica-orchestration-events-david", apply=True)
    assert report["action"] == "keep"


def test_credentials_no_topic_is_skipped(tmp_path, monkeypatch):
    _patch_creds(tmp_path, monkeypatch,
        "ntfy:\n"
        "  url: https://ntfy.example\n"
        "  token: tk_test_x\n",
    )
    report = _migrate_credentials_topic("empirica-orchestration-events-david", apply=True)
    assert report["action"] == "skip"


# ── listener_active marker migration ───────────────────────────────────


def _seed_marker(tmp_path: Path, name: str, topic: str, ai_id: str = "empirica") -> Path:
    (tmp_path / ".empirica").mkdir(exist_ok=True)
    p = tmp_path / ".empirica" / f"listener_active_{name}.json"
    p.write_text(json.dumps({
        "monitor_task_id": None,
        "armed_at": 1780000000.0,
        "ai_id": ai_id,
        "name": f"{ai_id}-inbox",
        "topic": topic,
        "mode": "standalone",
    }, indent=2), encoding="utf-8")
    return p


def test_marker_migration_rewrites_pre_tenant_form(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _seed_marker(tmp_path, "empirica_empirica-inbox",
                 "ntfy:empirica-orchestration-events?tags=empirica")
    reports = _migrate_listener_active_markers(
        "empirica-orchestration-events-david", apply=True,
    )
    assert len(reports) == 1
    assert reports[0]["action"] == "rewrite"
    assert reports[0]["applied"] is True
    new = json.loads((tmp_path / ".empirica" / "listener_active_empirica_empirica-inbox.json").read_text())
    assert new["topic"] == "ntfy:empirica-orchestration-events-david?tags=empirica"


def test_marker_migration_preserves_tag_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _seed_marker(tmp_path, "cortex_cortex-inbox",
                 "ntfy:orchestration-events?tags=cortex", ai_id="cortex")
    _migrate_listener_active_markers(
        "empirica-orchestration-events-david", apply=True,
    )
    new = json.loads(
        (tmp_path / ".empirica" / "listener_active_cortex_cortex-inbox.json").read_text(),
    )
    # Tag preserved, base swapped.
    assert new["topic"] == "ntfy:empirica-orchestration-events-david?tags=cortex"


def test_marker_migration_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    marker = _seed_marker(tmp_path, "empirica_empirica-inbox",
                          "ntfy:orchestration-events?tags=empirica")
    reports = _migrate_listener_active_markers(
        "empirica-orchestration-events-david", apply=False,
    )
    assert reports[0]["action"] == "rewrite"
    assert reports[0]["applied"] is False
    # Disk unchanged
    on_disk = json.loads(marker.read_text())
    assert on_disk["topic"] == "ntfy:orchestration-events?tags=empirica"


def test_marker_migration_keeps_canonical(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _seed_marker(tmp_path, "empirica_empirica-inbox",
                 "ntfy:empirica-orchestration-events-david?tags=empirica")
    reports = _migrate_listener_active_markers(
        "empirica-orchestration-events-david", apply=True,
    )
    assert reports[0]["action"] == "keep"


def test_marker_migration_handles_per_practice_topic(tmp_path, monkeypatch):
    """A topic like `ffp-archive` (no -orchestration-events- segment) is
    per-practice + retired per the canonical model."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _seed_marker(tmp_path, "ffp_ffp-inbox",
                 "ntfy:ffp-archive?tags=ffp-archive", ai_id="ffp-archive")
    reports = _migrate_listener_active_markers(
        "empirica-orchestration-events-david", apply=True,
    )
    assert reports[0]["action"] == "rewrite"
    new = json.loads(
        (tmp_path / ".empirica" / "listener_active_ffp_ffp-inbox.json").read_text(),
    )
    assert new["topic"] == "ntfy:empirica-orchestration-events-david?tags=ffp-archive"


def test_marker_migration_returns_empty_when_no_empirica_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # No tmp_path/.empirica directory.
    reports = _migrate_listener_active_markers(
        "empirica-orchestration-events-david", apply=True,
    )
    assert reports == []


# ── end-to-end handler ─────────────────────────────────────────────────


def _args(**overrides):
    defaults = {"apply": False, "output": "json"}
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def test_handler_returns_2_when_canonical_unresolvable(tmp_path, monkeypatch, capsys):
    """If cortex returns no orchestration-events channel, the verb must
    refuse to rewrite (rather than silently picking a wrong topic)."""
    _patch_creds(tmp_path, monkeypatch, "ntfy:\n  topic: orchestration-events\n")
    with patch(
        "empirica.core.cockpit.notification_channels.fetch_notification_channels",
        return_value=None,
    ):
        rc = handle_mesh_migrate_topics_command(_args())
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "Cortex" in out["error"]


def test_handler_full_dry_run_reports_pending_rewrites(tmp_path, monkeypatch, capsys):
    _patch_creds(tmp_path, monkeypatch,
        "ntfy:\n"
        "  url: https://ntfy.example\n"
        "  topic: orchestration-events\n"
        "  token: tk_test_x\n",
    )
    _seed_marker(tmp_path, "empirica_empirica-inbox",
                 "ntfy:orchestration-events?tags=empirica")
    with patch(
        "empirica.core.cockpit.notification_channels.fetch_notification_channels",
        return_value={
            "channels": [
                {"category": "orchestration_events",
                 "topic": "empirica-orchestration-events-david"},
            ],
        },
    ):
        rc = handle_mesh_migrate_topics_command(_args(apply=False))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["canonical_base"] == "empirica-orchestration-events-david"
    assert out["credentials"]["action"] == "rewrite"
    assert out["credentials"]["applied"] is False
    assert out["rewrites_pending"] == 2
    assert out["rewrites_applied"] == 0
    # Nothing actually written.
    assert "topic: orchestration-events" in (tmp_path / "credentials.yaml").read_text()


def test_handler_apply_writes_creds_and_markers(tmp_path, monkeypatch, capsys):
    _patch_creds(tmp_path, monkeypatch,
        "ntfy:\n"
        "  url: https://ntfy.example\n"
        "  topic: orchestration-events\n"
        "  token: tk_test_x\n",
    )
    _seed_marker(tmp_path, "empirica_empirica-inbox",
                 "ntfy:orchestration-events?tags=empirica")
    with patch(
        "empirica.core.cockpit.notification_channels.fetch_notification_channels",
        return_value={
            "channels": [
                {"category": "orchestration_events",
                 "topic": "empirica-orchestration-events-david"},
            ],
        },
    ):
        rc = handle_mesh_migrate_topics_command(_args(apply=True))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is False
    assert out["rewrites_applied"] == 2
    assert out["rewrites_pending"] == 0
    # Disk written.
    assert "empirica-orchestration-events-david" in (tmp_path / "credentials.yaml").read_text()
    marker_data = json.loads(
        (tmp_path / ".empirica" / "listener_active_empirica_empirica-inbox.json").read_text(),
    )
    assert marker_data["topic"] == "ntfy:empirica-orchestration-events-david?tags=empirica"
