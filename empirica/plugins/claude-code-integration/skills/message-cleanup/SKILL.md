---
name: message-cleanup
description: >
  Daily housekeeping body for the canonical `message-cleanup` loop. Prunes
  expired git-notes mesh messages so the inbox stays focused on un-read
  ones. Loaded by the loop scheduler when the cron entry fires (default
  03:17 daily) — never invoked directly by a user. Triggers:
  `<task-notification>` from the message-cleanup loop, "message
  housekeeping", "expired messages", "prune mesh".
---

# Message Cleanup — daily housekeeping body

This is a **scheduled-loop body skill**, not a user-facing workflow.
The `message-cleanup` canonical loop fires once a day (cron `17 3 * * *`)
and wakes a session to run this skill. The runtime is short: one CLI
verb + a receipt log.

## When this fires

The TUI cockpit registers this loop alongside `cortex-mailbox-poll`
when the user toggles `L` on an instance for the first time, or by
explicit `empirica loop register --name message-cleanup`. On fire,
the AI sees a `<task-notification>` and loads this skill.

## What to do

One command, then close out:

```bash
empirica message-cleanup --output json
```

The verb walks `refs/notes/empirica/messages/` for any message whose
`expiry_at` is in the past and removes them. JSON output shape:

```json
{
  "ok": true,
  "dry_run": false,
  "removed_count": 12,
  "removed": [
    {"message_id": "...", "channel": "...", "subject": "..."},
    ...
  ]
}
```

If `removed_count > 0`, log a brief finding so the cleanup is visible
in the project's audit trail:

```bash
empirica finding-log \
  --finding "message-cleanup: pruned <N> expired mesh messages" \
  --impact 0.2 --epistemic-source intuition
```

If `removed_count == 0`, no artifact needed — silent success is fine.

Then signal heartbeat and you're done:

```bash
empirica loop heartbeat message-cleanup --status ok --result \
  $([ "$removed_count" -gt 0 ] && echo found || echo empty)
```

## What NOT to do

- Don't open an empirica transaction for this — it's a pure CLI
  cleanup, no praxic decisions to gate.
- Don't run with `--dry-run` unless you're debugging. The loop's
  scheduled fire IS the action.
- Don't escalate to user attention unless the verb errors. A scheduled
  cleanup is supposed to be invisible.

## Source

- Verb: `empirica message-cleanup` — handler at
  `empirica/cli/command_handlers/message_commands.py::handle_message_cleanup_command`
- Underlying logic: `empirica/core/canonical/empirica_git/message_store.py::cleanup_expired`
- Catalog entry: `empirica/core/cockpit/canonical_loops.py` (this loop)
