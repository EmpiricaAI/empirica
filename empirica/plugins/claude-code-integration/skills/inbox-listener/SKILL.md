---
name: inbox-listener
description: "Use when arming an event listener for the canonical mesh — when the user says 'arm this listener', 'subscribe to ntfy topic', 'wake me when X arrives', or when responding to a system-reminder from listener-install-pickup. The new canonical flow is `empirica listener on/arm/off` — three single-purpose tool calls that auto-resolve defaults, short-circuit when a persistent OS service is already subscribed, and emit structured next_step JSON the AI can mechanically chain. The older curl-based pattern lives as the 'legacy / custom topics' fallback at the bottom."
version: 2.0.0
---

# Inbox-Listener Wiring

Empirica's loop-cron skill wires periodic background work into the
registry. This skill does the same for **event-driven** background work:
held HTTP stream (ntfy), Monitor wake on event, listener stays armed.

**Canonical flow:**
three single-purpose verbs (`on/arm/off`) that emit structured
`next_step` JSON. The AI chains 3 mechanical tool calls per direction;
the CLI handles defaults, persistent-service detection, and state-file
management.

---

## When to Use

- User asks to "arm the listener" or "subscribe to ntfy"
- A system-reminder from `listener-install-pickup` lands in your conversation
- SessionStart hook (`session-monitor-arm.py`) emits Monitor-arming
  instructions in `additionalContext` (it now delegates to
  `empirica listener on`, so the output is consistent with what
  this skill teaches)

If you're scheduling **periodic** work (cron-mode loop), use `/loop-cron`
instead. This skill is for "wake when something arrives."

---

## Canonical 3-step arm flow

Three tool calls. CLI handles the rest.

### 1. `empirica listener on --output json`

Auto-resolves: `--ai-id` from `.empirica/project.yaml` (or pass explicitly),
`--name` defaults to `<ai_id>-inbox`, topic is queried per-tenant from
cortex's `GET /v1/users/me/notification-channels` registry (returns
`<org>-orchestration-events-<tenant>` for the calling tenant — post-T16/T17
isolation). The bare `orchestration-events` topic is RETIRED — cortex
collapsed publish to per-tenant only and no longer fans out the bare
fallback. Any listener still pinned to the bare topic receives zero
traffic and must restart to pick up the per-tenant resolution from the
channels endpoint.

Two possible response shapes:

**(a) Persistent OS service already running** — arm a LOG TAIL, not a second listener:
```json
{
  "ok": true,
  "status": "persistent_service_tail_session",
  "next_step": {
    "tool": "Monitor",
    "args": {
      "description": "Cortex orchestration log tail for <id> (persistent-service mode)",
      "command": "tail -F -n 0 ~/.empirica/loop_fires.log 2>/dev/null | grep -E --line-buffered '\"instance_id\": \"<id>\"'",
      "timeout_ms": 1800000
    },
    "after_arm": "empirica listener arm <monitor_task_id> --name <id>-inbox"
  }
}
```
The systemd-user / launchd service holds the ntfy stream and writes every event
to `~/.empirica/loop_fires.log`, but it cannot push into your session — the tail
is what delivers. Without it the events reach disk and you never see them.

**(b) No persistent service** — arm in-session:
```json
{
  "ok": true,
  "ai_id": "<id>",
  "name": "<id>-inbox",
  "status": "awaiting_arm",
  "next_step": {
    "tool": "Monitor",
    "args": {
      "description": "Cortex orchestration push listener for <id>",
      "command": "empirica loop listen --instance <id>",
      "timeout_ms": 1800000
    },
    "after_arm": "empirica listener arm <monitor_task_id> --name <id>-inbox"
  }
}
```

### Monitors expire — re-arming is the contract

Claude Code 2.1.271 replaced the no-timeout `persistent` option with a deadline:
every Monitor watch ends after at most 30 minutes (10 in `-p` runs) and you are
notified. **Re-arm it** — same Monitor call, then `empirica listener arm` again
with the new task id. Passing `persistent: true` is accepted and silently ignored
on current builds, and omitting it is accepted on older ones, so the call above
is valid everywhere. With the OS service running nothing is lost in the gap;
without it, events wait in `loop_fires.log` until the next arm or `mailbox poll`.

### 2. Arm Monitor with the emitted args

```python
result = Monitor(
    description="Cortex orchestration push listener for <id>",
    command="empirica loop listen --instance <id>",
    timeout_ms=1800000,
)
# capture result.task_id for step 3
```

### 3. `empirica listener arm <monitor_task_id>`

Replaces the `monitor_task_id: null` placeholder in
`listener_active_<instance>_<name>.json` with the real id. Now `off`
knows what to TaskStop later.

---

## Canonical 3-step off flow

### 1. `empirica listener off --output json`

Reads the state file, emits:
```json
{
  "ok": true,
  "monitor_task_id": "tk_xxx",
  "next_step": {
    "tool": "TaskStop",
    "args": {"task_id": "tk_xxx"},
    "after_stop": "empirica listener unregister <name>"
  }
}
```

### 2. `TaskStop(task_id)`

Disarms the Monitor.

### 3. `empirica listener unregister <name>`

Clears the registry entry + sidecar state files. Listener is fully off.

---

## Reaction protocol — what happens when events arrive

Once armed (or with persistent service running), each ECO-decided
proposal event arrives as one `<task-notification>` JSON line into
your chat. The reaction protocol is owned by `/cortex-mailbox-poll`
(load that skill before your first transaction if you're a session
that receives mesh events — `session-monitor-arm.py` reminds you in
its `additionalContext`).

---

## Architecture (concise)

```
external publisher (Cortex)
        │ HTTP POST with X-Tags including the target ai_id
        ▼
   ntfy server (per-tenant topic, e.g.
   <org>-orchestration-events-<tenant>;
   resolved from /v1/users/me/notification-channels)
        │ held HTTP stream
        ▼
   ┌──────────────────────────────────────────────────┐
   │ EITHER:                                          │
   │   (a) persistent OS service holds the stream     │
   │       (empirica-listener-<ai_id>.service /       │
   │        com.empirica.listener.<ai_id>.plist)      │
   │   OR:                                            │
   │   (b) in-session `empirica loop listen` subprocess│
   │       held via Monitor, re-armed every 30 min     │
   └──────────────────────────────────────────────────┘
        │ emits one JSON line per ECO-decided proposal event
        ▼
   <task-notification> wakes the running Claude
        │
        ▼
   /cortex-mailbox-poll reaction protocol acts on the event
```

**Idle cost:** zero. The held stream consumes no Claude tokens until a
real event arrives.

**Wake latency:** sub-second from publish to wake handler (network
round-trip + Monitor poll cycle).

---

## Delivery model

**The mailbox is the source of truth; push is liveness only.** This listener
delivers one wake per genuinely-new actionable item. It is not a retry engine, not
an acknowledgement engine, and nothing here guarantees delivery — a dropped wake is
reconciled on the next poll, and a message to an empty practice simply waits.

The consequence that matters when arming this: **never act irreversibly on a push
alone.** Reconcile against the durable proposal state.

Full model — send-side refire duty, why the reminder/escalation chain was retired,
and the autonomy sweep that catches the tail: `/cortex-mailbox-poll`, which owns it.
This section carried a 250-word restatement of that page; it now carries the steer
you need *before* you would know to load it.

## SessionStart hook integration

The plugin's `session-monitor-arm.py` hook delegates to
`empirica listener on --output json` and renders the JSON response as
markdown in `additionalContext`. So:

- Fresh SessionStart with persistent service running → hook emits the
  LOG-TAIL Monitor block. Arm it + run `empirica listener arm <task_id>`.
- Fresh SessionStart without persistent service → hook emits the
  Monitor-arming block. You arm the Monitor + run
  `empirica listener arm <task_id>`.

Either way, the source of truth for *what to do* is
`empirica listener on`. The hook is just the automation surface.

---

## Legacy: per-listener custom topics (curl-based)

For listeners on **non-canonical topics** (e.g. a custom ntfy channel
for an external publisher unrelated to the Cortex mesh), the older
curl-based pattern remains supported via the 9 power-user verbs
(`register / pause / resume / record-wake / fire / install-request /
list / status / unregister`). The Cockpit→Claude `install-request`
flow still queues a pending install via UserPromptSubmit; this skill
historically taught the manual curl + Monitor + record-wake protocol
for that case.

If you receive a system-reminder from `listener-install-pickup` with
a custom topic, follow the embedded prompt template — it carries the
specifics (NAME, TOPIC, ON_WAKE, INSTANCE) needed to construct the
curl + Monitor pair manually. For the canonical mesh topic, prefer
`empirica listener on` instead.

---

## State files

| Path | Purpose | Owner |
|---|---|---|
| `~/.empirica/listeners_<instance>.json` | Declarative registry (name/topic/description per listener) | `ListenerRegistry` |
| `~/.empirica/listener_paused_<instance>_<name>` | Pause sidecar (empty file when paused) | `set_listener_paused` |
| `~/.empirica/listener_active_<instance>_<name>.json` | Runtime metadata (Monitor task id, curl pid, armed_at) | `listener on/arm` writes; `listener off` reads |

`on` writes the active file with a placeholder `monitor_task_id: null`;
`arm <task_id>` replaces the placeholder; `off` reads it to emit the
TaskStop next_step; `unregister` deletes it.

---

## Related

- `/cortex-mailbox-poll` — the receive-side reaction protocol for
  events arriving via the listener.
- `/cortex-mailbox-send` — the send-side primitive for emitting your
  own proposals.
- `empirica/core/loop_scheduler/persistent_listener.py` — the
  systemd-user / launchd persistent service shipped in 7eac3c838.
- `empirica/cli/command_handlers/cockpit_commands.py` —
  `handle_listener_on/arm/off_command` (lines ~1530-1735), the
  canonical CLI handlers this skill teaches.
