"""Tests for `empirica listener gc` (goal d75f2b7c).

Closes extension's prop_xeqwwgmktr ask: there is no GC verb today to
prune stale ~/.empirica/listener_active_*.json files by liveness.

Three prune criteria, OR'd:
  - legacy_topic: bare orchestration-events or per-org pre-T16/T17 form
  - no_service_or_health: no matching empirica-listener-<ai_id>.service
    AND no recent listener_health_<ai_id>.json marker
  - stale: armed_at older than --age-days AND no recent wake activity

Dry-run by default; --apply actually removes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace


def _seed_active(
    home: Path, ai_id: str, *, topic: str, armed_at: float | None = None, last_wake_at: float | None = None
) -> Path:
    """Write a listener_active_<ai_id>-inbox.json file under tmp_home."""
    empirica_dir = home / ".empirica"
    empirica_dir.mkdir(parents=True, exist_ok=True)
    fname = f"listener_active_{ai_id}_{ai_id}-inbox.json"
    p = empirica_dir / fname
    data = {
        "monitor_task_id": "task-fake",
        "curl_pid": None,
        "armed_at": armed_at if armed_at is not None else time.time(),
        "ai_id": ai_id,
        "name": f"{ai_id}-inbox",
        "topic": topic,
        "mode": "tail",
    }
    if last_wake_at is not None:
        data["last_wake_at"] = last_wake_at
    p.write_text(json.dumps(data, indent=2))
    return p


def _seed_service_unit(home: Path, ai_id: str) -> Path:
    """Write a fake systemd unit so the no_service check passes."""
    unit_dir = home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    p = unit_dir / f"empirica-listener-{ai_id}.service"
    p.write_text("[Unit]\nDescription=fake\n")
    return p


def _seed_health_marker(home: Path, ai_id: str, *, age_seconds: float = 60) -> Path:
    """Write a fresh listener_health_<ai_id>.json marker."""
    p = home / ".empirica" / f"listener_health_{ai_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "instance_id": ai_id,
                "loop": "cortex-mailbox-poll",
                "status": "ok",
                "ts": "now",
            }
        )
    )
    target_mtime = time.time() - age_seconds
    import os

    os.utime(p, (target_mtime, target_mtime))
    return p


def _run_gc(tmp_path: Path, monkeypatch, *, apply: bool = False, age_days: int = 7) -> tuple[int, dict, str]:
    """Invoke the gc handler with HOME pinned to tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    import io
    import sys

    from empirica.cli.command_handlers.cockpit_commands import (
        handle_listener_gc_command,
    )

    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    args = SimpleNamespace(apply=apply, age_days=age_days, output="json")
    rc = handle_listener_gc_command(args)
    out = captured.getvalue().strip()
    payload = json.loads(out) if out else {}
    return rc or 0, payload, out


# ── Empty / safe defaults ──────────────────────────────────────────────


def test_gc_returns_zero_when_no_empirica_dir(tmp_path, monkeypatch):
    """No ~/.empirica/ — nothing to prune; clean no-op."""
    rc, payload, _ = _run_gc(tmp_path, monkeypatch)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["pruned"] == []
    assert payload["kept"] == []


def test_gc_dry_run_by_default(tmp_path, monkeypatch):
    """No --apply → files are NOT removed even if flagged."""
    _seed_active(tmp_path, "empirica-cortex", topic="ntfy:orchestration-events?tags=cortex")
    rc, payload, _ = _run_gc(tmp_path, monkeypatch, apply=False)
    assert rc == 0
    assert payload["dry_run"] is True
    assert payload["pruned_count"] == 1
    # File still on disk
    surviving = list((tmp_path / ".empirica").glob("listener_active_*.json"))
    assert len(surviving) == 1


# ── Criterion 1: legacy_topic ─────────────────────────────────────────


def test_gc_flags_bare_orchestration_events_as_legacy(tmp_path, monkeypatch):
    """Bare `orchestration-events` topic is retired post-T16/T17."""
    _seed_active(tmp_path, "empirica-cortex", topic="ntfy:orchestration-events?tags=empirica.david.empirica-cortex")
    _seed_service_unit(tmp_path, "empirica-cortex")  # has service
    _seed_health_marker(tmp_path, "empirica-cortex", age_seconds=60)  # fresh health
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch)
    assert payload["pruned_count"] == 1
    reasons = payload["pruned"][0]["reasons"]
    assert any("legacy_topic" in r for r in reasons)
    assert any("bare orchestration-events" in r for r in reasons)


def test_gc_flags_per_org_topic_as_legacy(tmp_path, monkeypatch):
    """Pre-T16/T17 per-org `empirica-orchestration-events` is retired."""
    _seed_active(
        tmp_path, "empirica-cortex", topic="ntfy:empirica-orchestration-events?tags=empirica.david.empirica-cortex"
    )
    _seed_service_unit(tmp_path, "empirica-cortex")
    _seed_health_marker(tmp_path, "empirica-cortex", age_seconds=60)
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch)
    assert payload["pruned_count"] == 1
    reasons = payload["pruned"][0]["reasons"]
    assert any("legacy_topic" in r for r in reasons)
    assert any("per-org" in r for r in reasons)


def test_gc_keeps_per_tenant_topic(tmp_path, monkeypatch):
    """Current per-tenant `empirica-orchestration-events-david` is canonical."""
    _seed_active(
        tmp_path,
        "empirica-cortex",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-cortex",
    )
    _seed_service_unit(tmp_path, "empirica-cortex")
    _seed_health_marker(tmp_path, "empirica-cortex", age_seconds=60)
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch)
    assert payload["pruned_count"] == 0
    assert payload["kept_count"] == 1


# ── Criterion 2: no_service_or_health ─────────────────────────────────


def test_gc_flags_no_service_and_no_recent_health(tmp_path, monkeypatch):
    """No empirica-listener-<ai>.service unit AND no fresh health marker
    → file is an orphan."""
    _seed_active(
        tmp_path,
        "empirica-mesh-support",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-mesh-support",
    )
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch)
    assert payload["pruned_count"] == 1
    reasons = payload["pruned"][0]["reasons"]
    assert any("no_service_or_health" in r for r in reasons)


def test_gc_keeps_when_service_exists(tmp_path, monkeypatch):
    """Service unit present → not flagged for no_service even without health."""
    _seed_active(
        tmp_path,
        "empirica-cortex",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-cortex",
    )
    _seed_service_unit(tmp_path, "empirica-cortex")
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch)
    assert payload["pruned_count"] == 0
    assert payload["kept_count"] == 1


def test_gc_keeps_when_health_marker_fresh_even_without_service(tmp_path, monkeypatch):
    """Fresh health marker proves the listener is alive; service-on-disk
    isn't required for the no_service check to pass."""
    _seed_active(
        tmp_path,
        "empirica-extension",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-extension",
    )
    _seed_health_marker(tmp_path, "empirica-extension", age_seconds=60)
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch)
    assert payload["pruned_count"] == 0
    assert payload["kept_count"] == 1


def test_gc_flags_when_health_marker_stale_and_no_service(tmp_path, monkeypatch):
    """Stale health marker (older than 5 min) doesn't shield — flagged."""
    _seed_active(
        tmp_path,
        "empirica-autonomy",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-autonomy",
    )
    _seed_health_marker(tmp_path, "empirica-autonomy", age_seconds=3600)  # 1hr stale
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch)
    assert payload["pruned_count"] == 1
    reasons = payload["pruned"][0]["reasons"]
    assert any("no_service_or_health" in r for r in reasons)


# ── Criterion 3: stale ────────────────────────────────────────────────


def test_gc_flags_dead_listener_old_and_quiet(tmp_path, monkeypatch):
    """A DEAD listener (no service unit, no fresh health) that is old with no
    recent wake is pruned, and 'stale' is among the reasons. Stale fires only
    for not-live listeners now — a live one is kept regardless of a quiet inbox
    (see the two regression tests below)."""
    old_armed = time.time() - (10 * 24 * 60 * 60)  # 10 days
    _seed_active(
        tmp_path,
        "empirica-cortex",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-cortex",
        armed_at=old_armed,
    )
    # No service unit, no health marker → not live.
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch, age_days=7)
    assert payload["pruned_count"] == 1
    reasons = payload["pruned"][0]["reasons"]
    assert any("stale" in r for r in reasons)


def test_gc_keeps_live_listener_old_and_quiet(tmp_path, monkeypatch):
    """REGRESSION: a live listener (installed service + fresh health) is never
    pruned as 'stale' for a quiet inbox, however old the arm. Philipp
    mesh-support measured 6/7 live launchd listeners reaped as stale
    (2026-08-13) because stale wasn't liveness-aware and the criteria are ORed."""
    very_old_armed = time.time() - (60 * 24 * 60 * 60)  # 60 days, no wake
    _seed_active(
        tmp_path,
        "empirica-cortex",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-cortex",
        armed_at=very_old_armed,
    )
    _seed_service_unit(tmp_path, "empirica-cortex")
    _seed_health_marker(tmp_path, "empirica-cortex", age_seconds=60)
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch, age_days=7)
    assert payload["pruned_count"] == 0
    assert payload["kept_count"] == 1


def test_gc_keeps_live_listener_by_installed_service_alone(tmp_path, monkeypatch):
    """REGRESSION: liveness by the installed service ALONE (no health marker)
    still protects from stale — Philipp's launchd listeners are installed, and
    the fix must not rely on a health marker being present on every platform."""
    very_old_armed = time.time() - (60 * 24 * 60 * 60)
    _seed_active(
        tmp_path,
        "empirica-cortex",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-cortex",
        armed_at=very_old_armed,
    )
    _seed_service_unit(tmp_path, "empirica-cortex")  # installed only, no health
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch, age_days=7)
    assert payload["pruned_count"] == 0


def test_gc_keeps_old_armed_at_with_recent_wake(tmp_path, monkeypatch):
    """Recent last_wake_at saves a file even with old armed_at — it's
    actively in use."""
    old_armed = time.time() - (30 * 24 * 60 * 60)  # 30 days
    recent_wake = time.time() - 60  # 1 min ago
    _seed_active(
        tmp_path,
        "empirica-cortex",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-cortex",
        armed_at=old_armed,
        last_wake_at=recent_wake,
    )
    _seed_service_unit(tmp_path, "empirica-cortex")
    _seed_health_marker(tmp_path, "empirica-cortex", age_seconds=60)
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch, age_days=7)
    assert payload["pruned_count"] == 0
    assert payload["kept_count"] == 1


def test_gc_age_days_respected(tmp_path, monkeypatch):
    """--age-days N controls when the stale REASON fires, observed on a DEAD
    listener (which no_service prunes either way — so the age threshold's effect
    is visible in the reasons, not the prune outcome). A live listener could not
    be used here: the fix keeps it regardless of age."""
    armed_3d_ago = time.time() - (3 * 24 * 60 * 60)
    _seed_active(
        tmp_path,
        "empirica-cortex",
        topic="ntfy:empirica-orchestration-events-david?tags=empirica.david.empirica-cortex",
        armed_at=armed_3d_ago,
    )
    # Dead: no service unit, no health marker.

    # 7d threshold: 3d-old is not stale; still pruned by no_service, no stale reason
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch, age_days=7)
    assert payload["pruned_count"] == 1
    assert not any("stale" in r for r in payload["pruned"][0]["reasons"])

    # 1d threshold: 3d-old crosses the stale threshold → stale joins the reasons
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch, age_days=1)
    assert payload["pruned_count"] == 1
    assert any("stale" in r for r in payload["pruned"][0]["reasons"])


# ── --apply actually removes ──────────────────────────────────────────


def test_gc_apply_removes_file(tmp_path, monkeypatch):
    """With --apply the flagged file is actually unlinked."""
    p = _seed_active(tmp_path, "empirica-cortex", topic="ntfy:orchestration-events?tags=cortex")
    assert p.exists()
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch, apply=True)
    assert payload["pruned_count"] == 1
    assert payload["pruned"][0]["removed"] is True
    assert not p.exists()


# ── Corrupt file ──────────────────────────────────────────────────────


def test_gc_handles_corrupt_active_file(tmp_path, monkeypatch):
    """Unparseable JSON is its own prune reason — safe to clean."""
    (tmp_path / ".empirica").mkdir()
    bad = tmp_path / ".empirica" / "listener_active_corrupt_corrupt-inbox.json"
    bad.write_text("not json {{{")
    _rc, payload, _ = _run_gc(tmp_path, monkeypatch)
    assert payload["pruned_count"] == 1
    reasons = payload["pruned"][0]["reasons"]
    assert any("unreadable" in r for r in reasons)
