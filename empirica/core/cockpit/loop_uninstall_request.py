"""Cockpit→Claude loop uninstall requests.

Symmetric inverse of `loop_install_request`. When a loop is paused, the
empirica CLI can clear `next_scheduled_job_id` from the registry but it
*can't* call CronDelete itself — that's a Claude Code tool, not a shell
command. Result, pre-fix: the OS cron table still has the recurring job
queued, so it keeps firing every interval, spawning fresh CC sessions
that run the body just long enough to see the pause flag and exit.
Pause was advisory.

The bridge: write a "pending uninstall request" file. A UserPromptSubmit
hook on the owning instance surfaces the pending request as a
`<system-reminder>` (via `hookSpecificOutput.additionalContext`) on the
next prompt. The owning Claude reads the system-reminder and runs
`CronDelete(<job_id>)` from inside that CC session. The cron is gone,
the loop is genuinely off.

Pending file path:
  ~/.empirica/loop_uninstall_pending_{instance_id}_{name}.json

Each file contains:
  {
    "instance_id": "tmux_3",
    "name": "metrics-watch",
    "job_id": "cron-create-abc123",
    "scheduler_kind": "cron-create",
    "requested_at": "2026-04-30T20:30:00Z",
    "requested_by": "tmux_7",   # the instance that issued pause
    "reason": "manual pause"     # short label for the reminder
  }

The hook reads pending files for the running instance, surfaces them,
then deletes them. Idempotent — re-pausing just rewrites the file.
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

DEFAULT_SCHEDULER_KIND = "cron-create"


def _safe_suffix(text: str) -> str:
    return text.replace("/", "-").replace("%", "")


def pending_path(instance_id: str, name: str) -> Path:
    """Pending uninstall file path. Same sanitization rule as install
    requests so writers and readers agree on the filename."""
    safe_inst = _safe_suffix(instance_id)
    safe_name = _safe_suffix(name)
    return EMPIRICA_DIR / f"loop_uninstall_pending_{safe_inst}_{safe_name}.json"


def list_pending(instance_id: str) -> list[Path]:
    """All pending uninstall request files for the given instance."""
    safe_inst = _safe_suffix(instance_id)
    return sorted(EMPIRICA_DIR.glob(f"loop_uninstall_pending_{safe_inst}_*.json"))


@dataclass
class LoopUninstallRequest:
    """A pending request to cancel a scheduled cron job inside the owning
    Claude instance. The pause CLI can't call CronDelete directly — this
    file is the bridge."""

    instance_id: str
    name: str
    job_id: str
    scheduler_kind: str = DEFAULT_SCHEDULER_KIND
    requested_at: str = ""
    requested_by: str | None = None
    reason: str = "pause"

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "job_id": self.job_id,
            "scheduler_kind": self.scheduler_kind,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "reason": self.reason,
        }

    @classmethod
    def from_path(cls, path: Path) -> LoopUninstallRequest | None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return cls(
            instance_id=str(data.get("instance_id", "")),
            name=str(data.get("name", "")),
            job_id=str(data.get("job_id", "")),
            scheduler_kind=str(data.get("scheduler_kind") or DEFAULT_SCHEDULER_KIND),
            requested_at=str(data.get("requested_at", "")),
            requested_by=data.get("requested_by"),
            reason=str(data.get("reason", "") or "pause"),
        )


def write_pending(
    instance_id: str,
    name: str,
    job_id: str,
    scheduler_kind: str = DEFAULT_SCHEDULER_KIND,
    requested_by: str | None = None,
    reason: str = "pause",
) -> Path:
    """Write a pending uninstall request. Idempotent — overwrites existing
    file with the same instance_id+name."""
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = pending_path(instance_id, name)
    request = LoopUninstallRequest(
        instance_id=instance_id,
        name=name,
        job_id=job_id,
        scheduler_kind=scheduler_kind,
        requested_at=datetime.now(tz=timezone.utc).isoformat(),
        requested_by=requested_by,
        reason=reason,
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(request.to_dict(), f, indent=2)
    return path


def consume_pending(instance_id: str) -> list[LoopUninstallRequest]:
    """Read + delete all pending uninstall requests for this instance.

    Used by the UserPromptSubmit hook: after surfacing as additionalContext,
    the file is removed so the request only fires once. If Claude doesn't
    call CronDelete in time, the body's pause check at the next fire is
    the backstop — it sees the pause flag and exits without scheduling
    the next fire, so the loop dies cleanly after at most one more
    silent fire.
    """
    out: list[LoopUninstallRequest] = []
    for path in list_pending(instance_id):
        try:
            request = LoopUninstallRequest.from_path(path)
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
    "DEFAULT_SCHEDULER_KIND",
    "EMPIRICA_DIR",
    "LoopUninstallRequest",
    "consume_pending",
    "list_pending",
    "pending_path",
    "write_pending",
]
