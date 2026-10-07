"""Sentinel pause-file wrapper.

Wraps the existing ~/.empirica/sentinel_paused_{instance_id} mechanism that
sentinel-gate.py reads. The hook is the source of truth — these helpers just
write/remove/inspect the same files, so CLI-driven pause/resume is trivially
consistent with hook-driven gating.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EMPIRICA_DIR = Path.home() / ".empirica"
GLOBAL_PAUSE_FILE = EMPIRICA_DIR / "sentinel_paused"


def _safe_instance_suffix(instance_id: str) -> str:
    """Sanitize instance_id for use as filename suffix.

    Mirrors the same rule sentinel-gate.py:get_pause_file_path uses, so
    instance pause files line up exactly between writer (CLI) and reader
    (hook).
    """
    return instance_id.replace("/", "-").replace("%", "")


def pause_file_path(instance_id: str | None) -> Path:
    """Return the pause file path for an instance, or the global file."""
    if instance_id:
        return EMPIRICA_DIR / f"sentinel_paused_{_safe_instance_suffix(instance_id)}"
    return GLOBAL_PAUSE_FILE


@dataclass
class SentinelPauseStatus:
    """Resolved Sentinel pause state for an instance."""

    instance_id: str | None
    paused: bool
    since: str | None  # ISO-8601 UTC, or None when not paused
    reason: str | None  # First non-empty line of pause file content, if any
    scope: str  # "instance" | "global" | "none"


def _read_pause_file(path: Path) -> tuple[str | None, str | None]:
    """Return (since_iso, reason) from a pause file's mtime and contents.

    File presence is the authoritative pause signal — contents are
    informational. We tolerate empty files and unreadable files.
    """
    try:
        mtime = path.stat().st_mtime
        since = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except OSError:
        since = None

    reason: str | None = None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            reason = text.splitlines()[0]
    except OSError:
        pass

    return since, reason


def sentinel_status(instance_id: str | None) -> SentinelPauseStatus:
    """Inspect Sentinel pause state for an instance.

    Resolution order matches sentinel-gate.py:is_empirica_paused:
    1. instance-specific file → "instance" scope
    2. global file → "global" scope
    3. neither → "none" scope (paused=False)
    """
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    if instance_id:
        instance_path = pause_file_path(instance_id)
        if instance_path.exists():
            since, reason = _read_pause_file(instance_path)
            return SentinelPauseStatus(
                instance_id=instance_id,
                paused=True,
                since=since,
                reason=reason,
                scope="instance",
            )

    if GLOBAL_PAUSE_FILE.exists():
        since, reason = _read_pause_file(GLOBAL_PAUSE_FILE)
        return SentinelPauseStatus(
            instance_id=instance_id,
            paused=True,
            since=since,
            reason=reason,
            scope="global",
        )

    return SentinelPauseStatus(
        instance_id=instance_id,
        paused=False,
        since=None,
        reason=None,
        scope="none",
    )


def pause_sentinel(
    instance_id: str | None, reason: str | None = None, *, global_scope: bool = False
) -> SentinelPauseStatus:
    """Pause the Sentinel for one instance, or for EVERY instance when `global_scope` is set.

    A falsy `instance_id` used to fall through to the global pause file, so a pause run from a terminal that resolved to no
    instance silently disarmed gating fleet-wide. It now raises unless the caller asks for the global scope explicitly
    (David's ruling 2026-10-06; the `--global` flag does that for the CLI).

    Idempotent — re-pausing rewrites the file (refreshing mtime and reason).
    """
    if not instance_id and not global_scope:
        raise ValueError(
            "pause_sentinel needs an instance_id: pass global_scope=True to pause the Sentinel for EVERY instance"
        )
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = pause_file_path(None if global_scope and not instance_id else instance_id)
    content = (reason or "").strip()
    path.write_text(content, encoding="utf-8")
    return sentinel_status(instance_id)


def resume_sentinel(instance_id: str | None) -> SentinelPauseStatus:
    """Remove the pause file for the given instance (or global if no instance_id).

    Idempotent — resuming an unpaused instance is a no-op success.
    """
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = pause_file_path(instance_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return sentinel_status(instance_id)
