"""Cockpit→Claude listener install requests.

Symmetric to `loop_install_request` but for event listeners. The
empirica CLI can register a listener in any instance's
`listeners_{instance_id}.json` directly, but it can't arm the listener
— that requires Claude Code tool calls inside the target session
(background Bash for curl, Monitor for wake delivery, both returning
task IDs only available inside CC). The empirica CLI knows how to ask;
the owning Claude knows how to arm.

The bridge: write a "pending install request" file. A UserPromptSubmit
hook on the target instance surfaces the pending request as a
`<system-reminder>` (via `hookSpecificOutput.additionalContext`) on
the next prompt. The target Claude reads the system-reminder and runs
the inbox-listener skill which arms the listener (background curl +
Monitor) and writes the listener_active_*.json runtime metadata.

Pending file path:
  ~/.empirica/listener_install_pending_{instance_id}_{name}.json

Each file contains:
  {
    "instance_id": "tmux_3",
    "name": "outreach-inbox",
    "topic": "ntfy:outreach-claude-inbox",
    "description": "Cortex orchestration inbox",
    "on_wake_template": "Process new orchestration message",
    "requested_at": "2026-04-30T20:30:00Z",
    "requested_by": "tmux_7",
    "prompt_template": "<full inbox-listener skill prompt with name/topic substituted>"
  }

The hook reads pending files for the running instance, surfaces them,
then deletes them. Idempotent — re-requesting just rewrites the file.
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
    """Pending request file path. Same sanitization rule as the
    loop install-request so writers and readers agree on the filename."""
    safe_inst = _safe_suffix(instance_id)
    safe_name = _safe_suffix(name)
    return EMPIRICA_DIR / f"listener_install_pending_{safe_inst}_{safe_name}.json"


def list_pending(instance_id: str) -> list[Path]:
    """All pending install request files for the given instance."""
    safe_inst = _safe_suffix(instance_id)
    return sorted(EMPIRICA_DIR.glob(f"listener_install_pending_{safe_inst}_*.json"))


def render_inbox_listener_prompt(
    name: str,
    topic: str,
    description: str = "",
    on_wake_template: str = "",
) -> str:
    """Render the inbox-listener skill template with placeholders substituted.

    The owning Claude invokes this prompt to arm the listener: launches
    a background curl held against the topic, arms a Monitor against
    that curl with persistent: true, writes the listener_active_*.json
    runtime metadata containing the Monitor task id + curl PID. On each
    wake, runs the on_wake_template, then stays armed.
    """
    desc = description or f"{name} event listener"
    wake_body = on_wake_template or "(no on-wake handler — record wake and continue)"
    return f"""\
Run /inbox-listener with the following parameters:

  name:        {name}
  topic:       {topic}
  description: {desc}
  on_wake:
    {wake_body}

Arming sequence (the skill will walk you through it):

  1. Launch background bash with curl -N held against the topic
     (e.g. for ntfy: `curl -N -u "$NTFY_USER:$NTFY_PASSWORD" \\
       https://$NTFY_SERVER/{{TOPIC_REST}}/json`). Capture the
     run_in_background task id.
  2. Arm Monitor with persistent: true watching the curl task's
     stdout for new JSON lines. Capture the Monitor task id.
  3. Write ~/.empirica/listener_active_{{INSTANCE}}_{name}.json:
       {{"monitor_task_id": "<id>", "curl_pid": <pid>,
         "armed_at": "<iso>"}}
  4. On each Monitor wake:
       - Check pause flag at ~/.empirica/listener_paused_{{INSTANCE}}_{name}.
         If present, exit the wake-handler; the body pause-check is
         the backstop until `empirica listener pause` queues the
         uninstall request and the loop-uninstall-pickup hook
         delivers it.
       - Run the on_wake handler:
            {wake_body}
       - empirica listener record-wake {name} \\
           --message "<short summary>"
       - Loop back to step 4 — Monitor stays armed.

The listener should idle at zero token cost between wakes (held
connection) and process events with sub-second latency from publish.
"""


@dataclass
class ListenerInstallRequest:
    """A pending request the cockpit makes to a target Claude instance.

    `requested_by` is the cockpit's own instance_id (so the receiver can
    show 'requested by tmux_7'); None when the request was made via CLI
    outside any tracked instance.
    """

    instance_id: str
    name: str
    topic: str
    description: str = ""
    on_wake_template: str = ""
    requested_at: str = ""
    requested_by: str | None = None
    prompt_template: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "topic": self.topic,
            "description": self.description,
            "on_wake_template": self.on_wake_template,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "prompt_template": self.prompt_template,
        }

    @classmethod
    def from_path(cls, path: Path) -> ListenerInstallRequest | None:
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
            topic=str(data.get("topic", "")),
            description=str(data.get("description", "") or ""),
            on_wake_template=str(data.get("on_wake_template", "") or ""),
            requested_at=str(data.get("requested_at", "")),
            requested_by=data.get("requested_by"),
            prompt_template=str(data.get("prompt_template", "") or ""),
        )


def write_pending(
    instance_id: str,
    name: str,
    topic: str,
    description: str = "",
    on_wake_template: str = "",
    requested_by: str | None = None,
) -> Path:
    """Write a pending install request. Idempotent — overwrites existing
    file with the same instance_id+name."""
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = pending_path(instance_id, name)
    request = ListenerInstallRequest(
        instance_id=instance_id,
        name=name,
        topic=topic,
        description=description,
        on_wake_template=on_wake_template,
        requested_at=datetime.now(tz=timezone.utc).isoformat(),
        requested_by=requested_by,
        prompt_template=render_inbox_listener_prompt(
            name=name,
            topic=topic,
            description=description,
            on_wake_template=on_wake_template,
        ),
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(request.to_dict(), f, indent=2)
    return path


def consume_pending(instance_id: str) -> list[ListenerInstallRequest]:
    """Read + delete all pending install requests for this instance.

    Used by the UserPromptSubmit hook: after surfacing as additionalContext,
    the file is removed so the request only fires once.
    """
    out: list[ListenerInstallRequest] = []
    for path in list_pending(instance_id):
        try:
            request = ListenerInstallRequest.from_path(path)
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
    "ListenerInstallRequest",
    "consume_pending",
    "list_pending",
    "pending_path",
    "render_inbox_listener_prompt",
    "write_pending",
]
