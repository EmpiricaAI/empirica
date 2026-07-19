"""System-level canonical loop catalog.

When the TUI cockpit user toggles L (loops) on an instance that has no
loops registered AND no `.empirica/project.yaml` `cockpit.loops` block,
fall back to this catalog — empirica ships a sane default that wires
each empirica claude into the orchestration mesh out of the box.

## Design

Entries are dicts matching the same shape `project_loops` returns
(see `project_cockpit_config.py`):

    {
      "name": str,           # required, unique per instance
      "kind": "interval" | "cron" | "monitor",
      "interval": str,        # for kind=interval (base when backoff is set)
      "cron": str,            # for kind=cron
      "description": str,
      "base_interval": str,   # optional backoff floor
      "max_interval": str,    # optional backoff ceiling
    }

The body (what the AI does when the loop fires) is NOT stored here —
it lives in a companion skill the AI loads on fire. The `description`
field is the one-line summary surfaced in the TUI; the skill name is
implied by the loop name where applicable.

## Adaptive cadence model

Loops are adaptive — every entry has `base_interval` (fast) and
`max_interval` (idle). The runtime adjusts:

- **Self-throttle**: if the AI has an open empirica transaction, the
  loop body returns immediately. Caller is already engaged — no need
  to interrupt for an inbox poll.
- **Backoff on no-op**: if a poll returns nothing new, the next
  interval grows toward `max_interval` (exponential by convention).
- **Reset on activity**: any non-empty poll resets to `base_interval`.

## Adding entries

This is a curated catalog. Each entry should be:
- General-purpose enough that any empirica claude benefits
- Self-contained (the body in the companion skill should not assume
  project-specific config beyond `~/.empirica/credentials.yaml`)
- Cheap when idle (backoff to max_interval should be tolerable)

For project-specific loops, use `.empirica/project.yaml` cockpit.loops
instead — project config takes precedence over this catalog.
"""

from __future__ import annotations

from typing import Any

CANONICAL_LOOPS: list[dict[str, Any]] = [
    {
        # The orchestration spine — every empirica claude polls Cortex
        # inbox + outbox to react to proposals and emit follow-ups.
        # 30s base when active, 5m max when idle. Self-throttles when
        # the local AI has an open empirica transaction.
        "name": "cortex-mailbox-poll",
        "kind": "interval",
        "interval": "30s",
        "base_interval": "30s",
        "max_interval": "5m",
        "description": (
            "Poll Cortex inbox + outbox via MCP for orchestration "
            "messages. Body: cortex_inbox_poll(ai_id=<self>) + "
            "cortex_outbox_poll(ai_id=<self>, status=changed). "
            "Self-throttles when an empirica transaction is open. "
            "Backoff: 30s base → 5m max on consecutive empty polls; "
            "resets to 30s on any non-empty result. "
            "Body skill: /cortex-mailbox-poll."
        ),
        # By convention, loop name == body-skill name. When the loop-install
        # pickup hook surfaces the pending file, the AI calls /loop with
        # the matching skill template.
        "body_skill": "cortex-mailbox-poll",
        # Phase 1c (goal f718156c): mark this canonical loop for the systemd
        # scheduler path. TUI install + toggle route through systemctl
        # instead of CronCreate. The wake bridge into the running session is
        # the Monitor armed at SessionStart (session-monitor-arm.py).
        # Must match VALID_SCHEDULER_KIND in loop_registry.py.
        "scheduler_kind": "systemd-user",
        # OPT-IN ONLY. Wake-on-event (the persistent listener) is the canonical
        # mesh trigger; this 30s poll is redundant on any wake-on-event seat and
        # is only wanted on harnesses that CANNOT do wake-on-event (cron-only VMs,
        # isolated cloud). So the loop-install pickup hook NEVER auto-queues it —
        # the user opts in with `empirica loop register` where they actually need
        # it. (David 2026-07-19: "wake on event should be the only mesh trigger
        # mechanism unless the user opts in and needs it for other harnesses that
        # cannot do wake on event".) Genuine housekeeping crons (message-cleanup)
        # do NOT set this — they auto-install regardless.
        "opt_in_only": True,
    },
    {
        # Housekeeping: prune expired git-notes mesh messages once a day.
        # The `empirica message-cleanup` verb already exists — this entry
        # just gives it a schedule so AIs aren't doing it by hand (and the
        # mesh inbox stays focused on un-read messages).
        # Cron form: 03:17 daily — odd minute to avoid lining up with
        # other workspace cron entries that prefer round numbers.
        "name": "message-cleanup",
        "kind": "cron",
        "cron": "17 3 * * *",
        "description": (
            "Daily cleanup of expired git-notes mesh messages. Body: "
            "`empirica message-cleanup --output json` — removes messages "
            "past their expiry_at timestamp and prints a receipt. "
            "Pure CLI verb (no MCP, no AI judgment needed); the loop "
            "body skill just invokes it and reports the receipt count. "
            "Body skill: /message-cleanup."
        ),
        "body_skill": "message-cleanup",
        "scheduler_kind": "systemd-user",
    },
]


def canonical_loop_names() -> list[str]:
    """Return just the names — useful for UX hints / completion."""
    return [entry["name"] for entry in CANONICAL_LOOPS]


def canonical_loop_by_name(name: str) -> dict[str, Any] | None:
    """Look up a single canonical entry by name. Returns None if absent."""
    for entry in CANONICAL_LOOPS:
        if entry.get("name") == name:
            return entry
    return None


__all__ = [
    "CANONICAL_LOOPS",
    "canonical_loop_by_name",
    "canonical_loop_names",
]
