# Dispatch Bus

**Typed cross-instance dispatch protocol for Empirica.**

The Dispatch Bus lets multiple Claude instances (terminal CLI, web Cowork,
desktop app, Cortex MCP server) coordinate via typed action dispatch with
correlation tracking, capability-based routing, and deadline enforcement.

It's a thin protocol layer on top of the existing `GitMessageStore` —
mature, persistent, audit-trail-friendly transport — so storage, TTL,
threading, broadcast, and read tracking come "for free."

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      DispatchBus (typed protocol)            │
│  - DispatchMessage (action, payload, correlation_id, ...)    │
│  - DispatchResult  (status, payload, error, duration)        │
│  - InstanceRegistry (~/.empirica/bus-instances.yaml)         │
│  - Capability matching (route by capability requirements)    │
│  - Deadline enforcement (dispatches expire)                  │
└──────────────┬──────────────────────────────────┬───────────┘
               │                                  │
    ┌──────────▼──────────┐            ┌─────────▼──────────┐
    │  GitMessageStore    │            │   EpistemicBus      │
    │  (transport)        │            │   (in-process)      │
    │                     │            │                     │
    │  - Git notes        │            │  - Pub/sub          │
    │  - Channels         │            │  - Event types      │
    │  - TTL + cleanup    │            │  - Persistence      │
    │  - Threading        │            │    (SQLite/Qdrant)  │
    │  - Broadcast (*)    │            │                     │
    └─────────────────────┘            └────────────────────┘
               │                                  │
               └──────────────┬───────────────────┘
                              │
                ┌─────────────▼───────────────┐
                │       MCP Bridge             │
                │  bus_register, bus_dispatch, │
                │  bus_instances, bus_status,  │
                │  bus_poll                    │
                └──────────────────────────────┘
```

---

## Concepts

### Instance

A **Claude instance** is any process that participates in the bus —
terminal CLI, web Cowork, desktop app, Cortex MCP server, or custom
agents. Each instance has:

- **`instance_id`** — unique identifier (e.g., `terminal-claude-1`, `cowork-web-1`)
- **`instance_type`** — what kind of instance (`claude-code-cli`, `cowork-web`, etc.)
- **`capabilities`** — declared abilities (`codebase`, `git`, `gmail`, `browser`, ...)
- **`subscribes`** — channels this instance polls

Registered in `~/.empirica/bus-instances.yaml`. The registry is shared
across all instances on the same machine.

### Dispatch

A **dispatch** is a typed action request sent from one instance to another.
It encodes:

- `action` — action name (`schedule_cron`, `send_email`, `research_topic`, ...)
- `from_instance` / `to_instance` — sender and target IDs
- `payload` — action-specific JSON data
- `correlation_id` — auto-generated unique ID for request/response matching
- `priority` — `low` / `normal` / `high` / `urgent`
- `deadline` — optional Unix timestamp after which the dispatch expires
- `required_capabilities` — for capability-routed dispatches (target = `*`)
- `callback_channel` — where to send the result

### Capability Routing

If you don't know which instance can handle an action, dispatch to `"*"`
with `required_capabilities`:

```python
bus.dispatch(
    to_instance="*",
    action="send_email",
    required_capabilities=["gmail"],  # Bus picks an instance with gmail capability
    payload={"to": "user@example.com", "subject": "..."},
)
```

### Result

The target instance handles the dispatch and sends back a `DispatchResult`:

- `correlation_id` — matches the original dispatch
- `status` — `completed` / `failed` / `expired` / `rejected`
- `payload` — handler return value
- `error` — error message if failed
- `duration_ms` — handler execution time

Results travel back via `GitMessageStore.reply()` (which preserves
threading via `thread_id`), so request/response pairs are linked
automatically.

---

## Usage

### CLI

```bash
# Register this instance
empirica bus-register \
    --instance-id terminal-claude-1 \
    --type claude-code-cli \
    --capabilities codebase,git,shell \
    --subscribes dispatch.terminal,dispatch

# Dispatch a typed action to a specific instance
empirica bus-dispatch \
    --from terminal-claude-1 \
    --to cowork-web-1 \
    --action schedule_cron \
    --payload '{"schedule": "0 9 * * *", "command": "run X"}' \
    --deadline 3600

# Dispatch + wait for result (blocks until target completes or times out)
empirica bus-dispatch \
    --from terminal-claude-1 \
    --to cowork-web-1 \
    --action send_email \
    --payload '{"to": "user@example.com"}' \
    --wait \
    --wait-timeout 60

# Capability-routed dispatch (any instance with 'gmail')
empirica bus-dispatch \
    --from terminal-claude-1 \
    --to '*' \
    --action send_email \
    --required-capabilities gmail \
    --payload '{"to": "..."}'

# List all registered instances
empirica bus-instances
empirica bus-instances --capability gmail  # Filter by capability

# Check this instance's bus state
empirica bus-status --instance-id terminal-claude-1

# Subscribe to incoming dispatches (blocking, prints to stdout)
empirica bus-subscribe --instance-id terminal-claude-1 --channel dispatch
```

### Python API

```python
from empirica.core.dispatch_bus import (
    DispatchBus, DispatchPriority, DispatchStatus,
)

# Initialize bus for this instance
bus = DispatchBus(instance_id="terminal-claude-1")

# Register self
bus.register_self(
    instance_type="claude-code-cli",
    capabilities=["codebase", "git", "shell"],
    subscribes=["dispatch.terminal"],
)

# Dispatch
correlation_id = bus.dispatch(
    to_instance="cowork-web-1",
    action="schedule_cron",
    payload={"schedule": "0 9 * * *", "command": "run X"},
    priority=DispatchPriority.HIGH,
    deadline_seconds=3600,
)

# Wait for result (blocking)
result = bus.wait_for_result(correlation_id, timeout_seconds=60)
if result and result.status == DispatchStatus.COMPLETED:
    print(f"Result: {result.payload}")

# Or poll for incoming dispatches
dispatches = bus.poll_inbox()
for d in dispatches:
    # Handle and respond
    bus.handle_dispatch(d, lambda dispatch: (
        DispatchStatus.COMPLETED,
        {"result": "ok"},
        None,
    ))
```

### MCP

Cowork web Claude or desktop Claude can use the bus via MCP tools:

- `bus_register` — register instance
- `bus_dispatch` — send typed dispatch
- `bus_instances` — list registered instances
- `bus_status` — check instance state
- `bus_poll` — poll inbox for incoming dispatches

---

## Suggested Channels

Conventional channels (you can use anything):

| Channel | Purpose |
|---------|---------|
| `dispatch` | Default channel for typed dispatches |
| `dispatch.terminal` | Direct to terminal CLI instances |
| `dispatch.cowork` | Direct to web Cowork instances |
| `dispatch.research` | Direct to Cortex/research instances |
| `findings.global` | Broadcast new findings cross-instance |
| `alerts` | High-priority cross-instance alerts |

---

## Use Cases

### 1. Schedule a cron job from terminal

```python
bus.dispatch(
    to_instance="*",
    action="schedule_cron",
    required_capabilities=["schedule"],
    payload={
        "name": "brand-monitor",
        "schedule": "0 9 * * *",
        "command": "python3 /path/to/brand-monitor.py",
    },
)
```

### 2. Trigger drafter after scout finds opportunity

```python
# In scout flow
bus.dispatch(
    to_instance="*",
    action="generate_draft",
    required_capabilities=["drafter"],
    payload={
        "opportunity_id": opp.id,
        "channel_id": channel.id,
        "variations": 2,
    },
    deadline_seconds=300,
)
```

### 3. Cortex research request

```python
correlation_id = bus.dispatch(
    to_instance="cortex-server-1",
    action="research",
    payload={
        "query": "best practices for B2B AI tool LinkedIn cadence",
        "max_sources": 5,
    },
    deadline_seconds=600,
)
result = bus.wait_for_result(correlation_id, timeout_seconds=600)
findings = result.payload.get("findings", [])
```

### 4. Cross-instance handoff

```python
# Terminal hits a problem requiring browser interaction
bus.dispatch(
    to_instance="*",
    action="reproduce_user_issue",
    required_capabilities=["browser"],
    payload={
        "url": "https://app.example.com/foo",
        "steps": ["click login", "enter creds", "click submit"],
        "context": "user reports 500 error",
    },
)
```

---

## Implementation Notes

### Why on top of GitMessageStore?

GitMessageStore was mature, tested, and handled persistence via git notes
(durable, version-controlled, syncable across machines via push/pull).
Building dispatch on top means:

- Storage, TTL, cleanup, threading, broadcast — all free
- Messages persist across instance restarts
- Cross-machine sync via git push/pull (when repos are shared)
- Audit trail in git history

### Why typed dispatch separately from raw messages?

Raw `message-send` is freeform — subject and body are arbitrary strings.
Typed dispatch encodes the payload as JSON in the body with a known schema
(`DispatchMessage`), so callers can rely on structured fields like
`action`, `payload`, `correlation_id`, `deadline`, etc.

### What about subscribe semantics?

`GitMessageStore.subscribe()` (added in T2) does pull-based polling — runs
in a thread, polls `get_inbox_since()` at a configurable interval, invokes
a callback per message. Real push/streaming requires either inotify on the
git refs directory or a separate event channel (out of scope for v1).

For most cross-instance dispatch use cases, 2-second polling latency is
acceptable. If lower latency is needed, the EpistemicBus (in-process pub/sub
with persistence to SQLite) provides faster reactions for events that don't
need cross-instance routing.

### Bus persistence

`wire_persistent_observers(session_id)` is called from
`workflow_commands.py` (PREFLIGHT/CHECK/POSTFLIGHT) to persist EpistemicBus
events to SQLite (and Qdrant if available). This is orthogonal to dispatch
bus messaging — events go through the in-process bus, dispatches go through
git notes.

---

## Security Considerations

The dispatch bus inherits GitMessageStore's security model:

- Messages are stored as plain JSON in git notes (no encryption)
- Anyone with read access to the repo can see all messages
- Anyone with write access can send messages on behalf of any `from_instance`
- No authentication of `from_instance` claims

For sensitive dispatches:

- Don't include secrets in payloads
- Use short TTLs (`--ttl 300` for sensitive content)
- Run `empirica message-cleanup` regularly
- Use a dedicated repo for bus messages if needed

For cross-machine dispatch, secure the git push/pull transport (SSH keys,
HTTPS auth, private repos).

---

## Files

| Component | Path |
|-----------|------|
| Core protocol | `empirica/core/dispatch_bus.py` |
| Transport | `empirica/core/canonical/empirica_git/message_store.py` |
| In-process bus | `empirica/core/epistemic_bus.py` |
| Bus persistence | `empirica/core/bus_persistence.py` |
| CLI commands | `empirica/cli/command_handlers/bus_commands.py` |
| CLI parsers | `empirica/cli/parsers/bus_parsers.py` |
| MCP tools | `empirica-mcp/empirica_mcp/server.py` |
| Tests | `tests/core/test_dispatch_bus.py`, `tests/integration/test_dispatch_bus_e2e.py` |

---

## Status

Implemented in v1.8.14+. Closes the protocol gap that David and Philipp
identified for orchestrating work across terminal, web Cowork, desktop, and
Cortex Claude instances.
