# Dispatch Bus

**Typed action requests between Claude instances, carried over git notes.**

A dispatch is a message whose body is a JSON envelope (`action`, `payload`,
`correlation_id`, `priority`, optional `deadline`, optional required
capabilities). The bus adds three things to the Cortex-free `message-*` layer:
a registry of instances and their capabilities, capability-based target
selection, and request/response correlation.

Read [`MESSAGING_LAYERS.md`](MESSAGING_LAYERS.md) first. The dispatch bus is a
typed overlay on the `message-*` layer, so it shares that layer's properties: no
server, no account, delivery on git push, no gating, no authentication. It is
**not** the Cortex layer (`cortex_propose`, `mailbox *`), and it is not the
in-process [`EpistemicBus`](EPISTEMIC_BUS.md) despite the name.

| Layer | Carries | Use for |
|---|---|---|
| `message-*` | free text | peers sharing a repo |
| **dispatch bus** (this doc) | typed `action` + JSON payload, correlated reply | machine-to-machine requests between instances you run |
| `mailbox *` / `cortex_propose` | proposals gated by an ECO decision | asking another practice to do work |
| `cortex_bus_*` MCP tools | server-queued actions (below) | Cortex-hosted dispatch for desktop or scheduled instances |

---

## Shape

```
DispatchBus(instance_id)                     empirica/core/dispatch_bus.py
  ├─ InstanceRegistry   ~/.empirica/bus-instances.yaml   (per user, per machine)
  └─ GitMessageStore    refs/notes/empirica/messages/<channel>/<message-id>
```

`DispatchBus` does not use `EpistemicBus` or `bus_persistence`. Those are a
separate mechanism.

### Instance

Registered in `~/.empirica/bus-instances.yaml`:

- `instance_id`: free string, e.g. `terminal-claude-1`. The bus does not
  authenticate it; any writer can send as any `from_instance`.
- `type`: free string (`claude-code-cli`, `cowork-web`, `desktop-app`,
  `cortex-server` appear in the help text).
- `capabilities`: tags such as `git`, `gmail`, `browser`.
- `subscribes`: channel names. Recorded in the registry only; `bus-subscribe`
  takes its own `--channel` and does not read this field.

PyYAML must be importable; without it the registry loads empty and saves
nothing.

### Dispatch

A `DispatchMessage` is sent with `GitMessageStore.send_message`:
channel `dispatch`, subject `dispatch:<action>`, type `request`, body the JSON
envelope, default TTL 86400 seconds (`--ttl`). `correlation_id` is
`<instance_id>-<unix ms>-<action>`.

- `to_instance` can be an instance id, or `*`. With `*` **and**
  `required_capabilities`, the bus picks the **first** registry instance that
  has all of them and addresses the dispatch to it; with none matching it logs a
  warning and returns no correlation id. With `*` and no capabilities the
  message is addressed to `*`, which the store treats as a broadcast.
- `deadline` is a Unix timestamp computed from `--deadline` seconds. It is
  enforced on the **receiving** side only: `poll_inbox` skips expired
  dispatches. Nothing sends an `expired` result to the sender;
  `DispatchStatus.EXPIRED` exists but no code produces it, so a sender waiting
  past the deadline sees a timeout, not a status.
- `callback_channel` is stored in the envelope. Replies are sent on the channel
  of the original message, not on `callback_channel`.

### Result

The receiver answers with `send_result`, which calls `GitMessageStore.reply`
(type `response`, threaded to the request). A `DispatchResult` carries
`correlation_id`, `status`, `payload`, `error`, `duration_ms`. `handle_dispatch`
runs a handler `(dispatch) -> (status, payload, error)` and turns an exception
into `failed`. Statuses defined: `pending`, `acknowledged`, `in_progress`,
`completed`, `failed`, `expired`, `rejected`. In practice a handler returns
`completed`, `failed` or `rejected`.

`wait_for_result` polls the sender's inbox every two seconds for a `response`
with the matching `correlation_id`.

---

## CLI

`empirica <verb> --help` is the flag reference.

```bash
empirica bus-register --instance-id terminal-claude-1 --type claude-code-cli \
    --capabilities codebase,git,shell --subscribes dispatch

empirica bus-dispatch --from terminal-claude-1 --to cowork-web-1 \
    --action schedule_cron --payload '{"schedule": "0 9 * * *"}' --deadline 3600

empirica bus-dispatch --from terminal-claude-1 --to '*' --action send_email \
    --required-capabilities gmail --payload '{"to": "..."}' --wait --wait-timeout 60

empirica bus-instances [--capability gmail]
empirica bus-status --instance-id terminal-claude-1     # registry row, pending counts
empirica bus-subscribe --instance-id terminal-claude-1  # blocking poll, prints JSON lines
```

- `--from` defaults to `claude-code`.
- A malformed `--payload` is logged and replaced by `{}`; the dispatch still
  goes out.
- `bus-subscribe` only prints. It never replies, so a `--wait` sender needs
  something else (a handler using the Python API) to call `handle_dispatch`.

### Python

```python
from empirica.core.dispatch_bus import DispatchBus, DispatchStatus

bus = DispatchBus(instance_id="terminal-claude-1")
bus.register_self(instance_type="claude-code-cli", capabilities=["git"])

cid = bus.dispatch(to_instance="cowork-web-1", action="schedule_cron",
                   payload={"schedule": "0 9 * * *"}, deadline_seconds=3600)
result = bus.wait_for_result(cid, timeout_seconds=60)   # None on timeout

for d in bus.poll_inbox():                              # receiver side
    bus.handle_dispatch(d, lambda d: (DispatchStatus.COMPLETED, {"ok": True}, None))
```

### MCP (empirica-mcp)

`bus_register`, `bus_dispatch`, `bus_instances`, `bus_status` map one-to-one to
the CLI verbs. `bus_poll` maps to **`message-inbox`**, not to a bus verb: pass
`ai_id` and `channel="dispatch"`; it returns raw messages, not parsed dispatches.

### Cortex-side bus (different system)

Cortex exposes `cortex_bus_register`, `cortex_bus_dispatch`, `cortex_bus_poll`
and `cortex_bus_complete`. These queue actions on the Cortex server for a
registered instance (aimed at desktop and scheduled-task instances) and use a
Cortex API key. They share a name and a vocabulary with this bus and nothing
else: no git notes, no `bus-instances.yaml`, and a result is reported with
`completed` or `failed` plus a text summary. A dispatch sent with `bus-dispatch`
never appears in `cortex_bus_poll`, and the reverse.

---

## Channels

Free strings. The ones the code uses are `dispatch` (default) and whatever you
pass to `--channel` / `callback_channel`. The channel is part of the ref path, so
it should be a plain token.

---

## Security

Inherited from `GitMessageStore`: bodies are plain JSON in git notes, readable by
anyone who can read the repo, and `from_instance` is a claim, not an identity.
Do not put secrets in payloads, use a short `--ttl` for anything sensitive, run
`empirica message-cleanup` to drop expired messages, and treat a handler as
executing requests from every writer to the repo. For cross-machine use, the
transport is whatever protects your git remote.

---

## Files and tests

| Component | Path |
|---|---|
| Protocol, registry | `empirica/core/dispatch_bus.py` |
| Transport | `empirica/core/canonical/empirica_git/message_store.py` |
| CLI | `empirica/cli/parsers/bus_parsers.py`, `empirica/cli/command_handlers/bus_commands.py` |
| MCP mapping | `empirica-mcp/empirica_mcp/server.py` (`bus_*` entries) |
| Tests | `tests/core/test_dispatch_bus.py`, `tests/integration/test_dispatch_bus_e2e.py` |
