"""Cockpit→Claude listener uninstall requests.

Symmetric inverse of `listener_install_request`. When `empirica
listener pause` runs, it can clear the runtime registry and write the
pause sidecar, but it can't TaskStop the Monitor or kill the
backgrounded curl from outside CC. The pause handler reads the runtime
metadata from `listener_active_<instance>_<name>.json` (Monitor task
id, curl pid) and writes a pending uninstall file. The owning
instance's UserPromptSubmit hook surfaces it as a system-reminder
asking Claude to TaskStop / kill PID, then delete the active file.

Pending file path:
  ~/.empirica/listener_uninstall_pending_{instance_id}_{name}.json

Each file contains:
  {
    "instance_id": "tmux_3",
    "name": "outreach-inbox",
    "monitor_task_id": "task_abc123",
    "curl_pid": 12345,
    "requested_at": "2026-04-30T20:30:00Z",
    "requested_by": "tmux_7",
    "reason": "manual pause"
  }

The hook reads pending files for the running instance, surfaces them,
then deletes them. Idempotent — re-pausing rewrites the file.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EMPIRICA_DIR = Path.home() / ".empirica"


def _safe_suffix(text: str) -> str:
    return text.replace("/", "-").replace("%", "")


def pending_path(instance_id: str, name: str) -> Path:
    """Pending uninstall file path. Same sanitization rule as the install
    request so writers and readers agree on the filename."""
    safe_inst = _safe_suffix(instance_id)
    safe_name = _safe_suffix(name)
    return EMPIRICA_DIR / f"listener_uninstall_pending_{safe_inst}_{safe_name}.json"


def list_pending(instance_id: str) -> list[Path]:
    """All pending uninstall request files for the given instance."""
    safe_inst = _safe_suffix(instance_id)
    return sorted(EMPIRICA_DIR.glob(f"listener_uninstall_pending_{safe_inst}_*.json"))


@dataclass
class ListenerUninstallRequest:
    """A pending request to cancel a held listener inside the owning
    Claude instance. The pause CLI can't call TaskStop or kill the
    background curl from another process — this file is the bridge."""

    instance_id: str
    name: str
    monitor_task_id: str
    curl_pid: int | None = None
    requested_at: str = ""
    requested_by: str | None = None
    reason: str = "pause"

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "monitor_task_id": self.monitor_task_id,
            "curl_pid": self.curl_pid,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "reason": self.reason,
        }

    @classmethod
    def from_path(cls, path: Path) -> ListenerUninstallRequest | None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        curl_pid = data.get("curl_pid")
        try:
            curl_pid_int = int(curl_pid) if curl_pid is not None else None
        except (TypeError, ValueError):
            curl_pid_int = None
        return cls(
            instance_id=str(data.get("instance_id", "")),
            name=str(data.get("name", "")),
            monitor_task_id=str(data.get("monitor_task_id", "")),
            curl_pid=curl_pid_int,
            requested_at=str(data.get("requested_at", "")),
            requested_by=data.get("requested_by"),
            reason=str(data.get("reason", "") or "pause"),
        )


def write_pending(
    instance_id: str,
    name: str,
    monitor_task_id: str,
    curl_pid: int | None = None,
    requested_by: str | None = None,
    reason: str = "pause",
) -> Path:
    """Write a pending uninstall request. Idempotent — overwrites existing
    file with the same instance_id+name."""
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = pending_path(instance_id, name)
    request = ListenerUninstallRequest(
        instance_id=instance_id,
        name=name,
        monitor_task_id=monitor_task_id,
        curl_pid=curl_pid,
        requested_at=datetime.now(tz=timezone.utc).isoformat(),
        requested_by=requested_by,
        reason=reason,
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(request.to_dict(), f, indent=2)
    return path


def consume_pending(instance_id: str) -> list[ListenerUninstallRequest]:
    """Read + delete all pending uninstall requests for this instance.

    Used by the UserPromptSubmit hook: after surfacing as additionalContext,
    the file is removed so the request only fires once. If Claude doesn't
    run TaskStop/kill in time, the listener body's pause check at next
    wake is the backstop.
    """
    out: list[ListenerUninstallRequest] = []
    for path in list_pending(instance_id):
        try:
            request = ListenerUninstallRequest.from_path(path)
            if request is not None:
                out.append(request)
        except Exception as exc:  # one unreadable file must not lose the others, but it must not vanish unsaid either
            logger.warning(
                "%s: skipping unreadable pending request %s (%s: %s)", __name__, path.name, type(exc).__name__, exc
            )
        try:
            path.unlink()
        except OSError:
            pass
    return out


__all__ = [
    "EMPIRICA_DIR",
    "ListenerUninstallRequest",
    "consume_pending",
    "list_pending",
    "pending_path",
    "write_pending",
]
