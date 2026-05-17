# Event Listener — push-primary wake bridge for canonical loops

**Updated:** 2026-05-16
**Status:** shipped (T8–T12, 1.9.5 → 1.9.6)
**Related:** [COCKPIT.md](COCKPIT.md), [DISPATCH_BUS.md](DISPATCH_BUS.md), [NOTIFY.md](NOTIFY.md)

---

## What it is

The event listener bridges **Cortex orchestration events** into a running
Claude Code session as wake signals — without a periodic poll, without
keeping Cortex's auth in the AI's context, and without crossing the
**ECO-gated autonomy** boundary.

**Core property:** each event the AI acts on traces back to an explicit
ECO actor decision. The wake mechanism (ntfy, listener, Monitor) is
replaceable and hijackable at the OS layer; the **authorization model is
constrained at the content layer** by Cortex's status filter. Compromising
the wake channel cannot widen the AI's authority — at most it can wake
the AI to no-op (forged events fail status re-verification against Cortex).

---

## The pipeline

```
ECO actor decision (phone / extension)
        │
        ▼
┌──────────────────────────────────────────────┐
│ Cortex                                       │
│   Status transition (eco_review → accepted) │
│   /v1/orchestration/inbox?status=accepted…   │
│                                              │
│   ──► publish ntfy message (opaque ping)    │
└─────────────────────┬────────────────────────┘
                      │
                      │  (held HTTP stream — push, not poll)
                      ▼
┌──────────────────────────────────────────────┐
│ Listener — `empirica loop listen`            │
│   curl -sN → ntfy /<topic>/json              │
│                                              │
│   On each "message" event:                   │
│     poll_and_diff(inbox + outbox)            │
│       └─► filter by EMISSION_STATUSES        │
│       └─► diff against state file            │
│       └─► emit JSON lines to stdout          │
│                                              │
│   On reconnect / startup:                    │
│     same poll_and_diff (no missed events)    │
└─────────────────────┬────────────────────────┘
                      │
                      │  (one JSON line per real wake event)
                      ▼
┌──────────────────────────────────────────────┐
│ Claude Code session                          │
│   SessionStart hook armed a persistent       │
│   Monitor on the listener's stdout           │
│                                              │
│   Each line → <task-notification> wake       │
│   AI invokes /cortex-mailbox-poll skill      │
│   Skill re-verifies proposal_id with Cortex  │
│   Acts iff status ∈ ECO-decided set          │
└──────────────────────────────────────────────┘
```

---

## Components

### Cortex publisher (external)

- **Repo:** `empirica-cortex`
- **Module:** `src/cortex/orchestration/emitter.py`
- **Topic:** `orchestration-events` (configurable per-deployment)
- Publishes one ntfy message per status transition on a tracked proposal.
  Message body is opaque from the listener's perspective — only arrival
  matters as a wake signal.

### ntfy (wake-pinger only)

- **Role:** wake-ping transport, not content source.
- **Why:** defense in depth + ECO-gated autonomy. Even a fully compromised
  ntfy topic can wake the AI but cannot direct it to act on forged content —
  the listener always re-verifies against Cortex's actual proposal state.
- **Auth:** Bearer-token (preferred) or basic-auth, resolved by
  `credentials_loader.get_ntfy_config()` with this precedence:
  1. `ORCHESTRATION_NTFY_*` env vars (`_URL` / `_TOPIC` / `_USER` /
     `_PASS` / `_TOKEN`)
  2. `~/.empirica/credentials.yaml` `ntfy:` block
  3. `~/.empirica/notify.yaml` `backends.ntfy` block (the outbound
     dispatcher's config; the loader follows `auth_env` indirection so
     env-var-stored tokens are picked up automatically)
  4. Defaults: prod ntfy server + the AI-wake topic
- **Token auth (`tk_` prefix):** access tokens use `Authorization: Bearer
  tk_...`. Preferred because they're revocable + don't expose the account
  password. Extension obtains the token on registration and sets it on
  Cortex; copy that value into `credentials.yaml`'s `ntfy.token` field.
- **Topic split** (cortex T12 design): two topics so phone notifications
  and AI-wake events don't cross-pollute.
  - `orchestration-proposals` — phone-targeted (ECO decisions waiting on
    a human Accept/Decline)
  - `orchestration-events` — AI-wake topic (this listener subscribes here;
    both auto-accepted + ECO-accepted proposals emit here)
- **Tag-based filtering (cortex `ae92166` + listener `c9981f35e`):** Cortex
  publishes each event with `X-Tags: zap,orchestration_event,<source>,
  <targets…>`. Listeners subscribe with `?tags=<their_ai_id>` so ntfy
  server-side filters out events not touching this instance. Per-event
  wake traffic scales `O(involved_instances)`, not `O(N_instances)`.
  Default on; override with `EMPIRICA_NTFY_TAG_FILTER=false` for
  unfiltered (audit dashboards, debugging).

### Listener — `empirica loop listen`

- **File:** `empirica/core/loop_scheduler/listener.py` → `run_listener()`
- **CLI:** `empirica loop listen --instance <id> [--loop-name cortex-mailbox-poll]`
- **Held connection:** `curl -sN --no-buffer --keepalive-time 30` to
  `<ntfy>/<topic>/json[?tags=<ai_id>]` — one JSON line per ntfy message.
- **Per-message reaction:** call `_emit_catchup_events()` →
  `content_poll.poll_and_diff()` → emit any new-or-changed proposal events
  to stdout. The ntfy payload itself is discarded; Cortex is the source of
  truth.
- **Failure handling:**
  - Connection drop → exponential backoff (1s → 60s cap), reconnect,
    catch-up poll on reconnect (no missed events).
  - Auth failure (no `connected_ok` flag set on a stream cycle) → 5min
    backoff (auth issues rarely self-fix in seconds).
  - SIGTERM/SIGINT → clean exit code 0 (systemd / Monitor lifecycle
    knows the listener stopped intentionally).

### Cortex emission points (shipped 2026-05-16)

Cortex publishes a ntfy event with proper tags on each of these
proposal-lifecycle moments:

| Trigger | Status | Publishes ntfy? | Commit |
|---|---|---|---|
| Create + ECO-accept | accepted | ✅ honest flag in response | `f4c85bf` |
| Create + auto-accept (Homer mode) | accepted | ✅ honest flag | `f4c85bf` |
| Status transition (changed / declined) | matching status | ✅ inherited from publish helper | (pre-existing) |
| Target AI calls `cortex_complete_proposal` | completed | ✅ source AI wakes on outbox | `91cbd2f` |

All publishes carry `X-Tags: zap,orchestration_event,<source>,<targets…>`
(`ae92166`) so the listener-side `?tags=<ai_id>` filter scopes delivery.

### Companion MCP tool — `cortex_get_proposal` (`e8d3b1a`)

When a wake event arrives the AI has a `proposal_id`. Rather than guessing
`target_claudes` to call `cortex_inbox_poll`, the AI calls
`cortex_get_proposal(api_key, proposal_id)` to fetch the full envelope
directly. Cross-tenant safe (unknown ids return 404-shape). This makes
wake events self-sufficient — the listener-to-action path needs no
ai_id-guessing.

### Content poll — `poll_and_diff`

- **File:** `empirica/core/loop_scheduler/content_poll.py`
- **Direction split:**
  - **Inbox** (`target_claudes ∋ instance_id`): proposals targeting this
    AI. ECO must have decided →
    `EMISSION_STATUSES_INBOX = (accepted, changed, declined)`.
    `eco_review` is explicitly excluded — the security boundary.
  - **Outbox** (`source_claude == instance_id`): proposals THIS AI emitted.
    ECO already decided at emission time; downstream transitions are
    informational acks →
    `EMISSION_STATUSES_OUTBOX = (changed, declined, completed)`.
    `accepted` on outbox is informational only (noise filter).
- **State file:** `~/.empirica/loop_state/<inst>_<loop>.json` — atomic
  write (temp + rename). Tracks `{proposal_id: {status, direction, seen_at}}`
  for the diff.
- **Bootstrap:** on first run (state file absent), record everything as
  seen *without* emitting. Prevents flooding the AI with historical state
  the first time a loop is enabled.
- **Failure mode:** if both inbox and outbox fetch fail (Cortex
  unreachable), do not mutate state. Next poll retries from the same
  point.

### SessionStart Monitor arm

- **File:** `empirica/plugins/claude-code-integration/hooks/session-monitor-arm.py`
- **Trigger:** Claude Code SessionStart hook (`startup` and `resume`
  matchers).
- **Logic:**
  1. Resolve `instance_id` via `InstanceResolver.instance_id()`.
  2. List active systemd-user timer units matching the instance.
  3. If any → emit `hookSpecificOutput.additionalContext` with an arming
     instruction and a per-loop reaction table.
  4. If none → emit empty output (no false instructions).
- **Output shape:** the AI gets a markdown block telling it to call
  `Monitor(command="empirica loop listen --instance <id>", persistent=True)`.
  Monitor with an identical command is idempotent — the second call is a
  no-op.

### ProposalEvent wire shape

One JSON line per stdout write — the unit consumed by Monitor and the
TUI's events column:

```json
{
  "ts": "2026-05-16T11:00:00+00:00",
  "instance_id": "tmux_5",
  "loop": "cortex-mailbox-poll",
  "event_type": "proposal_event",
  "direction": "inbox",
  "proposal_id": "prop_xyz",
  "proposal_title": "Refactor auth handler",
  "status": "accepted",
  "action_category": "code",
  "eco_actor": "david@phone",
  "change_kind": "status_changed",
  "commit_sha": null
}
```

`commit_sha` is populated only for outbox `completed` events — the
AI-to-AI ack primitive carries the SHA in `audit_log.details.commit_sha`
so the source AI knows which commit landed its work.

### Reaction protocol

Each AI session loads the **`cortex-mailbox-poll`** skill (the receive
side) for the per-direction per-status reaction logic, and the
**`cortex-mailbox-send`** skill (the send side) for the corresponding
"how do I emit / reply / ack" guidance:

| direction | status | reaction |
|---|---|---|
| inbox | accepted | act on the proposal per `action_category` |
| inbox | changed | refine per ECO comments |
| inbox | declined | update mental model — no action |
| outbox | changed | refine per ECO comments and re-emit |
| outbox | declined | update mental model — proposal dropped |
| outbox | completed | acknowledge AI-to-AI handoff (commit_sha included) |

**Mid-transaction wake:** the EWM pattern says don't drop in-flight work
for a new event. The skill logs a goal
`"Process <direction>/<status>: <proposal_id>"` and picks it up at the
next natural break.

---

## Naming convention — `ai_id` ↔ project root

AIs are addressed in cortex orchestration (`target_claudes`,
`source_claude`, inbox routing) by an `ai_id` derived from their
project root's basename, with the `empirica-` prefix stripped where
present.

| Project root | `ai_id` |
|---|---|
| `~/empirical-ai/empirica` | `empirica` |
| `~/empirical-ai/empirica-cortex` | `cortex` |
| `~/empirical-ai/empirica-outreach` | `outreach` |
| `~/empirical-ai/empirica-extension` | `extension` |
| `~/code/myproject` | `myproject` |

**Where it's written:** `setup-claude-code` (via the `project_init`
handler) derives this value and persists it as `ai_id` in
`.empirica/project.yaml`. The mechanical implementation is
`basename.removeprefix('empirica-')` — see
`empirica.cli.command_handlers.project_init._derive_ai_id`.

**Where it's read:** AI sessions read `ai_id` from project.yaml at
session start. CLI commands accept `--ai-id` explicitly. Cortex's
listener uses the value as the `instance_id` argument to
`empirica loop listen --instance <id>` and as the `ai_id` filter
when querying `/v1/orchestration/inbox?ai_id=<id>`.

**Why this convention:** AIs are bound to projects (not models or
workstreams), so the project's identity is the natural addressing
layer. A user with a single project has a single AI. A user with
multiple projects gets multiple addressable AIs out-of-the-box,
keyed by their project layout. Cross-project orchestration falls
out naturally because peer AIs already know each other's project
names.

**Migration:** Pre-2026-05-16 projects may have `ai_id` unset in
`project.yaml` (the field is new). Falling back to `claude-code` is
the legacy default. Re-running `setup-claude-code --force` on those
projects refreshes the field. Project-internal callers should:

```python
ai_id = (project_yaml.get('ai_id')
         or basename.removeprefix('empirica-')
         or 'claude-code')
```

---

## State files

| Path | Owner | Purpose |
|---|---|---|
| `~/.empirica/credentials.yaml` (`ntfy:` block) | user | listener auth |
| `~/.empirica/loop_state/<inst>_<loop>.json` | content_poll | diff state |
| `~/.empirica/loop_fires.log` | (legacy — content events now stream via listener stdout) | historical fires log |
| `~/.config/systemd/user/empirica-loop-<inst>-<loop>.{timer,service}` | systemd | scheduler |

The listener itself holds no on-disk state — it's a stream processor.
Restart loses zero events (catch-up on reconnect).

---

## TUI surfacing

The cockpit TUI (`empirica tui`) renders a unified **events column**
(post-T9):

- **Header:** `⊕<count>` chip showing recent events across all instances.
- **Per-row glyph:** liveness summary derived from active loops + recent
  fires log tail.
- **Detail pane:** latest 5 events with `direction`, `status`,
  `proposal_id`, `eco_actor`, `title`.
- **`a` keybinding (T11):** toggle auto-accept on/off for the current
  instance. Auto-accept lets the AI act on inbox `accepted` events without
  a pause — surfacing only `changed` / `declined` for human awareness.

The TUI does not own the wake mechanism — it observes the same state files
the listener writes / the AI reads. Closing the TUI does not stop events.

---

## ECO-gated autonomy — the security boundary

> "Mechanism is replaceable + replicable + hijackable; the authorization
> model is constrained at the content layer. This is a structural
> property, not a runtime check." — design decision, 2026-05-15

The wake pipeline (ntfy → listener → Monitor → AI) is a transport. **None
of those layers authorize action.** Authorization comes from:

1. **Status filter at content_poll:** only `accepted | changed | declined`
   inbox proposals emit a wake event. `eco_review` is structurally
   excluded — there is no flag, no env var, no debug toggle to include
   it. Code change required.
2. **Re-verification at action time:** when the AI receives a wake
   event, it re-fetches the proposal by ID before acting. If Cortex
   returns a different status, the AI defers to the live state.
3. **Cortex-side ECO actor identity:** every proposal carries
   `eco_decision.actor` — the human or automation that made the call.
   The AI surfaces this in its action commit messages and audit logs.

A timer that fires every microsecond, or a fully forged ntfy stream,
or a `loop_fires.log` rewritten by an adversary cannot widen the AI's
authority — at most they waste polling round-trips.

---

## Failure modes — and how the listener handles them

| Failure | Detection | Recovery |
|---|---|---|
| Network blip | curl stream EOF | reconnect with exponential backoff; catch-up poll on reconnect |
| ntfy server restart | stream EOF mid-message | same as network blip |
| ntfy auth credential rotation | stream never gets a message (`connected_ok=False`) | 5min backoff, retry indefinitely |
| Cortex unreachable during catch-up | both inbox + outbox fetch raise | state file untouched, retry on next push or reconnect |
| Listener process killed | systemd / Monitor sees process exit | systemd `Restart=on-failure` (Type=oneshot — re-armed via Monitor on next AI session start) |
| AI session restarts | SessionStart hook re-fires | Monitor re-armed; listener subprocess re-spawned |
| Compact rotation | hook fires on `compact` matcher | same as session restart |
| Per-instance pause toggle (TUI) | systemd-user timer stopped (`systemctl --user stop ...timer`) | zero token cost while paused; no wake events delivered |

---

## Testing

### End-to-end via real ECO decision

1. From cortex side, ensure a test proposal targets the instance under
   test (`target_claudes ∋ <instance_id>`).
2. ECO-accept the proposal (David's phone or web extension).
3. Watch the receiving instance's TUI events column (or its Monitor
   stream in the Claude Code session) — a `proposal_event` line with
   `status=accepted` should arrive within ~100ms of the ECO decision.
4. The receiving Claude invokes `/cortex-mailbox-poll`, re-fetches the
   proposal, acts per `action_category`.

### Manual wake (skipping the ECO step in test)

The `empirica listener fire` command (see `empirica listener --help`)
triggers a synthetic wake on a registered listener — useful for
verifying the Monitor-to-skill plumbing without driving content from
Cortex.

For testing the **catch-up path** in isolation: stop the listener,
ECO-decide a proposal at Cortex, restart the listener — the initial
catch-up poll on startup will emit the event (proves the
reconnect-triggers-catch-up invariant).

### Loop-level sanity

```bash
empirica loop systemd-status cortex-mailbox-poll --instance <id>
systemctl --user list-timers 'empirica-loop-*'
tail -F ~/.empirica/loop_state/<inst>_cortex-mailbox-poll.json
```

The state file mtime updates on every poll — if it's stale, the timer
is either disabled or failing.

---

## Configuration

### Required credentials

```yaml
# ~/.empirica/credentials.yaml
ntfy:
  url: https://ntfy.example.org              # defaults to prod ntfy server
  topic: orchestration-events                 # AI-wake topic; default ok
  token: tk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx     # PREFERRED — ntfy access token
  # — or, legacy basic auth:
  # user: <basic-auth-user>
  # password: <basic-auth-pass>

cortex:
  url: https://cortex.example.org
  api_key: <bearer-token>
```

**One of `token` or `user+password` is required.** Without ntfy creds the
listener exits with code 2 and a clear message on stderr — systemd
surfaces the failure rather than silently sleeping. Tokens are preferred
(revocable, no password exposure); the empirica extension obtains a
token on registration and stores the same value on Cortex — copy that
into `credentials.yaml`.

**Fallback resolution:** `get_ntfy_config()` will also read from
`~/.empirica/notify.yaml` `backends.ntfy` block and follow `auth_env`
indirection (env var named there → value used as token if `tk_`-prefixed,
otherwise password). Single source of truth across inbound listener
and outbound notify dispatcher.

---

## Fresh install — getting wake working on a new machine

Tonight's debugging surfaced the install-time gaps. This section captures
the minimum new-user path so the same headache doesn't repeat.

**Pre-reqs:**
- empirica installed (`pip install empirica` or editable dev install)
- A Claude Code session running in the project root
- For Linux: systemd-user available (`systemctl --user is-system-running`).
  Optional — listener works without systemd (push-only mode); systemd is
  the catch-up safety net.

**Step 1 — Run the canonical project init.** From your project root:

```bash
empirica project-init
```

This writes `.empirica/project.yaml` including a derived `ai_id` (the
basename of your project, with `empirica-` prefix stripped where present).
That `ai_id` is what Cortex addresses you with in orchestration routing.

**Step 2 — Register with Cortex via the extension.** The empirica
extension does this end-to-end:
1. You sign in to ntfy via the extension (creates a `tk_…` access token)
2. Extension registers your `ai_id` with Cortex, passing the token
3. Extension writes the token + cortex creds to `~/.empirica/credentials.yaml`

If you're not using the extension, populate `credentials.yaml` manually
per the structure above.

**Step 3 — Setup the Claude Code plugin.**

```bash
empirica setup-claude-code --force
```

This installs the hooks (including `session-monitor-arm.py` which arms
Monitor on session start) and configures the MCP server bridge.

**Step 4 — Enable the canonical loop from the cockpit TUI.** From any
terminal:

```bash
empirica tui
```

Press `e` (Events) on your instance row. This installs the
`empirica-loop-<ai_id>-cortex-mailbox-poll` systemd-user timer + writes
the loop into the registry. The TUI's active-wake (commit `063e8556a`)
also sends a Space+Enter into your pane so the AI processes any queued
state on its next turn.

**Step 5 — Restart your Claude session** so `SessionStart` fires the
updated hook:

```bash
/exit
claude --resume    # or fresh: just run `claude`
```

The hook detects your active timer, emits `additionalContext` instructing
the AI to call `Monitor(command="empirica loop listen --instance <ai_id>",
persistent=true)`. After your first turn post-restart, the AI arms the
Monitor and the listener subscribes to ntfy with tag filter.

**Step 6 — Verify.**

```bash
ps aux | grep "empirica loop listen"
# Should show 1 python listener + 1 curl-to-ntfy subprocess for your ai_id

systemctl --user list-timers 'empirica-loop-*'
# Should show your instance's timer firing every 30s
```

Send a test proposal from another AI session (or a manual
`cortex_propose` call) targeting your `ai_id`. Within ~5 seconds you
should see a `proposal_event` task-notification arrive in your chat.

**Common pitfalls:**

| Symptom | Likely cause | Fix |
|---|---|---|
| Listener exits code 2 | Missing `ntfy:` block in credentials.yaml | Add the block per Step 2 |
| `ntfy_emitted: false` in propose response | Pre-`f4c85bf` cortex | Update Cortex |
| Wake delivers but completion ack never wakes source | Pre-`91cbd2f` cortex | Update Cortex |
| Every listener wakes on every event (noisy) | Pre-`ae92166` cortex OR `EMPIRICA_NTFY_TAG_FILTER=false` | Update Cortex + use default-true flag |
| Hook fires but AI doesn't call Monitor | AI session not restarted since hook update | `/exit` + `claude --resume` |
| Stray `empirica-loop-<test_name>-…` timer installed by tests | Test that ran real `handle_loop_enable_command` without stubbing | `systemctl --user disable --now <unit>` + `rm ~/.config/systemd/user/<unit>*` |

### Per-instance enable

```bash
# Enable canonical loop on this instance (idempotent)
empirica loop enable cortex-mailbox-poll

# Check status
empirica loop systemd-status cortex-mailbox-poll

# Disable (mechanical pause — systemctl stop + remove timer)
empirica loop disable cortex-mailbox-poll
```

The TUI's `L` keybinding wraps these — single-keystroke enable/disable
per row.

---

## What this replaced

- **Pre-T8 (legacy):** `tail -F ~/.empirica/loop_fires.log | grep <instance>`
  armed at SessionStart. Required the systemd tick body to write events
  to a shared log, and the AI to grep its own events out. Worked but had
  cross-instance noise risk and didn't survive log rotation.
- **Pre-canonical-loops:** `/loop` skill with `CronCreate` — scheduler
  lived inside Claude Code session. Could not be paused without AI
  cooperation; AFK cost was N wakes/hour × instances.

The current shape — systemd timer for cadence + ntfy listener for
wake-from-idle — gives true synchronous pause (`systemctl --user stop`,
zero token cost while AFK) and push-primary delivery (≤100ms wake
latency vs polling N seconds).
