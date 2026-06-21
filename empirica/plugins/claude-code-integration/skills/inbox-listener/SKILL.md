---
name: inbox-listener
description: "Use when arming an event listener for the canonical mesh — when the user says 'arm this listener', 'subscribe to ntfy topic', 'wake me when X arrives', or when responding to a system-reminder from listener-install-pickup. The new canonical flow is `empirica listener on/arm/off` — three single-purpose tool calls that auto-resolve defaults, short-circuit when a persistent OS service is already subscribed, and emit structured next_step JSON the AI can mechanically chain. The older curl-based pattern lives as the 'legacy / custom topics' fallback at the bottom."
version: 2.0.0
---

# Inbox-Listener Wiring

Empirica's loop-cron skill wires periodic background work into the
registry. This skill does the same for **event-driven** background work:
held HTTP stream (ntfy), Monitor wake on event, listener stays armed.

**Canonical flow (Phase 1 of prop_oxrhoehv4 shipped 2026-05-21):**
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

**(a) Persistent OS service already running** — short-circuit:
```json
{
  "ok": true,
  "status": "persistent_service_active",
  "next_step": null,
  "message": "No in-session Monitor needed — wake events arrive via the system service."
}
```
You're done. The systemd-user / launchd service already holds the
ntfy stream; wake events arrive through the normal channel.

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
      "persistent": true,
      "timeout_ms": 3600000
    },
    "after_arm": "empirica listener arm <monitor_task_id> --name <id>-inbox"
  }
}
```

### 2. Arm Monitor with the emitted args

```python
result = Monitor(
    description="Cortex orchestration push listener for <id>",
    command="empirica loop listen --instance <id>",
    persistent=True,
    timeout_ms=3600000,
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
   │       held via Monitor with persistent: true      │
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

## Delivery model — authoritative (David-ratified 2026-06-21)

The listener is a **liveness** layer, not a delivery guarantee:

- **Mailbox is the source of truth.** A message lands in the recipient
  practice's cortex mailbox and stays there durably. The practitioner
  picks it up on `session_init`, on monitor-arm, or on an explicit inbox
  poll. If the practice is empty (no live practitioner), the message
  simply waits — pull is the truth, so nothing is lost and no nag is needed.
- **Push is one wake per genuinely-new actionable item.** That is the
  whole job of this listener. It is NOT a retry / acknowledgement engine.
- **No cortex babysitting.** The historical reminder/escalation chain
  (cortex commit `38caedf`: T+60s reminder re-emit, T+120s `ai_unreachable`
  escalation) is **retired** — it was responsibility-creep (delivery is the
  sender's job), stream-noise on an already-crowded mesh, and itself
  unreliable ("server restart eats pending tasks"). Do not build behavior
  that depends on a cortex reminder or escalation ping.
- **Human visibility = the extension + ntfy stream**, which already surface
  routed collabs / proposals / SERs ambiently. That *is* the "did my AI
  pick this up?" surface — cortex does not re-ping to tell a human what
  they can already see.
- **Autonomy is the systemic crack-net.** The canonical autonomy
  watch-layer sweeps for anything that genuinely slipped (failed
  completions, items that landed but were never picked up, sends that
  bounced and weren't refired) — one quiet sweep with judgment replacing
  N per-message timers. **System messages must stay rare and high-signal;
  they must never become stream noise.**

---

## SessionStart hook integration

The plugin's `session-monitor-arm.py` hook delegates to
`empirica listener on --output json` and renders the JSON response as
markdown in `additionalContext`. So:

- Fresh SessionStart with persistent service running → hook emits the
  "persistent service running, no Monitor needed" block. You read it,
  nothing to arm.
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
