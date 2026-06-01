---
name: cortex-mailbox-poll
description: "Use when wiring the canonical cortex inbox+outbox polling loop into Claude Code's /loop. This is the orchestration spine — every empirica claude polls Cortex on a fast adaptive cadence (30s base, 5m max) for proposals addressed to itself + status changes on its own outgoing proposals. Self-throttles when an empirica transaction is open (the AI is already busy; no need to interrupt). The canonical loop catalog (empirica/core/cockpit/canonical_loops.py) auto-installs this when the TUI cockpit toggles L on an instance that has no loops registered. This skill is the body the AI runs each fire."
version: 1.0.0
---

# Cortex mailbox-poll cron loop wiring

The Phase 1 canonical orchestration loop. Every empirica claude polls
Cortex inbox+outbox on a fast adaptive cadence so peer proposals and
status changes route in seconds, not on the next user prompt.

This skill is a thin wrapper over `/loop-cron` — same self-scheduling
template, with `cortex_inbox_poll` + `cortex_outbox_poll` MCP calls
plugged in as the body.

---

## When to Use

Register the canonical mailbox-poll cron when:

- You're setting up a new empirica claude instance and want it to join
  the orchestration mesh (react to proposals routed via `cortex_propose`)
- The TUI cockpit auto-installed `cortex-mailbox-poll` via the canonical
  catalog (see `empirica/core/cockpit/canonical_loops.py`) and surfaced
  a pending install request — this skill is the body
- You want fast collaboration cadence (30s base) without the overhead
  of polling when you're already busy (self-throttle)

If your project has a custom inbox poll already (e.g. `outreach-inbox-poll`,
`eco-inbox-poll` in `.empirica/project.yaml`), use that instead — the
project-specific config takes precedence over this canonical default.

---

## AI_ID convention

Your `ai_id` is the basename of your project's root directory (with
`empirica-` prefix stripped where present). The full mapping table
lives in `~/.claude/empirica-system-prompt.md` and the wire-level
detail in `docs/architecture/EVENT_LISTENER.md`. Quick reference:

| Project root | `ai_id` |
|---|---|
| `~/empirical-ai/empirica` | `empirica` |
| `~/empirical-ai/empirica-cortex` | `cortex` |
| `~/empirical-ai/empirica-outreach` | `outreach` |

Read your canonical id from `.empirica/project.yaml`'s `ai_id` field
(set by `setup-claude-code` at project init). Use it in
`session-create --ai-id <id>`, `cortex_propose source_claude=<id>`,
and `target_claudes=[<peer-ids>]` calls.

---

## Reaction Protocol — Phase 1c+ / T8 content events (push-primary)

The SessionStart `session-monitor-arm.py` hook arms a Monitor on
`empirica loop listen --instance <your-id>` — the push-primary
listener that holds an ntfy stream to Cortex and emits one stdout
line per ECO-decided proposal event. Each line is a `<task-notification>`
wake event into this session.

The listener's stdout shape matches what the older
`tail -F ~/.empirica/loop_fires.log` path emitted (pre-T8 fallback,
still used by some legacy installations) — so the reaction protocol
below is identical regardless of which transport delivered the line.

### Content event (the wake signal you should usually act on)

```json
{"event_type": "proposal_event",
 "proposal_id": "prop_abc",
 "proposal_title": "Surface project block on daemon HTTP",
 "status": "accepted",
 "action_category": "TACTICAL",
 "eco_actor": "eco-phone",
 "change_kind": "new",
 "instance_id": "<you>",
 "loop": "cortex-mailbox-poll",
 "ts": "..."}
```

The `empirica loop tick` body polled Cortex and diffed against last-seen
state. **This event IS the content** — you don't need to poll inbox/outbox
yourself. Each event represents one ECO-decided proposal (`accepted` /
`changed` / `declined`).

**ECO-gated autonomy property:** every action you take ultimately traces
back to `eco_actor`'s decision. Even if the timer or fires log were
compromised, your re-verification via `cortex_inbox_poll(status="accepted")`
against the proposal_id is the auth boundary — Cortex only returns
ECO-decided state. Hijacking the wake signal cannot widen your authority.

### Step 0 — Recipient gate (always check this first)

**Before any of the below, verify the event is addressed to YOU.** The
shared `loop_fires.log` carries events for every AI on the mesh; the
Monitor that bridges those fires into your session is supposed to
`grep '"instance_id": "<your-ai-id>"'`, but a session armed before that
filter existed — or one set up wide for debugging — will see the whole
stream. Defense in depth: check before you act.

**`target_claudes` is the authoritative recipient list, not
`instance_id`.** `instance_id` identifies which AI's loop emitted the
event into the shared log; `target_claudes` (on the underlying proposal)
identifies who the proposal is FOR. These often agree but they are not
the same field — confusing them is the most common way real messages get
silently dropped.

**Branching logic, in order:**

1. **Read your own `ai_id`** from `.empirica/project.yaml` `ai_id:`
   field, or derive `basename.removeprefix('empirica-')`.

2. **If `event["instance_id"] == your ai_id`** — fast path. The event
   came through your own loop, so it's already targeted to you. Proceed
   to the direction-specific handler below. No proposal fetch needed
   just for the gate.

3. **If `event["instance_id"] != your ai_id`** — DO NOT silently ignore.
   Your Monitor is in broadcast mode (visible in its `description`:
   phrases like "all events", "not filtered by instance_id", "corrected"
   are tell-tales). Fall through to step 4 — `target_claudes` decides,
   not `instance_id`.

4. **Resolve `target_claudes` for this event.** Cheap path: if you've
   recently polled your inbox you already know the proposal_id and can
   look it up locally. Otherwise fetch with `cortex_get_proposal(
   proposal_id=event["proposal_id"])`. Then:
   - **`your ai_id IN target_claudes`** → proceed to the
     direction-specific handler below. This is a real message for you
     that came in via a broadcast or peer-emitted Monitor.
   - **`your ai_id NOT IN target_claudes`** → silently ignore. The event
     is observable noise from other AIs' loops, no action.

5. **If you keep hitting step 4 repeatedly** (more than ~3 events per
   session), your Monitor is set up wrong. Re-arm with the
   correctly-filtered command via `empirica setup-claude-code --force`
   or `empirica listener on --output json` (whose `next_step.args.command`
   pins the `grep` filter for your `ai_id`). Fixing the Monitor at the
   source is cheaper than per-event `cortex_get_proposal` calls.

**Catch-up safety net:** at session start, after long pauses, or any
time you suspect Monitor drops, run `cortex_inbox_poll(ai_id=<you>,
status="accepted,changed")` directly. The Monitor is the *push* path
for liveness; the inbox poll is the *pull* path for correctness. Both
should agree; the poll wins on disagreement.

**What to do — depends on `direction`:**

### `direction: "inbox"` — proposal is FOR you (ECO-gated)

The proposal targets this AI. ECO has decided. Authorization to act is
verified by the status field (`accepted`/`changed`/`declined` — never
`eco_review`).

1. **If mid-transaction:** log a goal using the **exact convention** so the
   POSTFLIGHT deferred-proposals nudge can surface it later:
   ```bash
   empirica goals-create \
     --objective "Process proposal <proposal_id>: <title>" \
     --description "From <source_claude>. Direction=<direction>, status=<status>. \
                    Original ask: <summary>. \
                    Complete via cortex_complete_proposal + goals-complete."
   ```
   The literal token `prop_` (in objective OR description) is what the
   POSTFLIGHT retrospective greps for. Pick up at the next natural break
   (EWM pattern). Do not interrupt the in-flight work.

   **Before POSTFLIGHT of the in-flight transaction**, scan for open
   proposal-derived goals (`empirica goals-list` + grep `prop_`) and either
   action them now or explicitly roll forward as planned for the next
   transaction. The retrospective will surface them in the POSTFLIGHT
   response — don't let them slip past after you close the window.
2. **If idle:**
   - status `accepted` → fetch full proposal via
     `cortex_inbox_poll(ai_id=<you>, status="accepted")` and find this
     proposal_id, then execute the payload (code change, follow-up emit).
     **When done, mark it `completed` via the cortex completion primitive
     so the source AI gets the ack** — this is the AI-to-AI handshake.
   - status `changed` → ECO requested refinement of a proposal SOMEONE ELSE
     sent that targets you. Read `eco_decision.note` and proceed with the
     adjusted scope.
   - status `declined` → ECO said no to something pointed at you. Update
     mental model; no action needed.
3. Archive via `cortex_archive_proposal` after handling.

### `direction: "outbox"` — proposal is FROM you (ack-style, no ECO gate)

A proposal you emitted earlier just transitioned state. ECO already decided
when it went out — these events are informational acks for the source AI.

- status `completed` (T7 — David's AI-to-AI ack primitive)
  → Target AI finished your work. Event carries `commit_sha` so you can
  trace the landing. Log a finding (`empirica finding-log`) noting the
  completion + commit. If there's a next-step you were waiting on, chain
  to it now.
- status `changed`
  → ECO sent your emission back for refinement. Read `eco_decision.note`
  and emit a `parent_id`-linked refined proposal via `cortex_propose`.
- status `declined`
  → ECO rejected your proposal. Update mental model. Optionally log a
  decision artifact noting why it didn't fly so you don't re-propose.

Outbox `accepted` is NEVER surfaced (informational — target will act on
the next tick of their inbox poll). Saves chat noise.

### Heartbeat event (legacy fallback)

```json
{"ts": "...", "instance_id": "<you>", "loop": "cortex-mailbox-poll"}
```

Older empirica versions, or non-cortex-mailbox-poll loops, emit
content-free heartbeats. If you receive one (no `event_type` field),
fall through to the Cron Prompt Template below — poll inbox/outbox
manually via MCP. This path costs more tokens; prefer the content-event
path when available.

---

## Adaptive cadence model

| Signal | Effect |
|---|---|
| Self has open empirica transaction | Skip poll (return `paused` result) — caller is busy, no interruption |
| Poll returns new items | Reset streak → next fire at base (30s) |
| Poll returns nothing | Advance streak → next fire grows toward max (5m) |
| Poll fails | Reset streak — retry at base, don't compound delay |

This is the same `--backoff exponential` pattern as `/loop-cron`, with
base 30s and max 5m. The faster floor reflects that orchestration is
interactive — 15m is too slow when a peer Claude is waiting.

---

## Resolving `ai_id` for the poll

Cortex inbox/outbox are scoped to a specific AI identity (e.g. `outreach`,
`cortex`, `extension`, `autonomy`, `empirica`). The body resolves `ai_id`
from project context, in this order:

1. **Project's `CLAUDE.md`** — look for an `AI_ID:` line or `**AI_ID:**`
   header. This is the canonical per-project convention.
2. **Project name fallback** — if the project is `empirica-outreach`,
   strip the `empirica-` prefix → `outreach`. If just `empirica`,
   use `empirica` as-is.
3. **`EMPIRICA_AI_ID` env var** — last-resort explicit override (for
   environments that don't have a project file).

If none resolves, the body emits a `fail` heartbeat with the
unresolved-ai_id error and lets the user fix the project's CLAUDE.md.

---

## Cron Prompt Template

When invoking `/loop` in cron mode, prepend these CLI lines to your
task prompt. Variables already filled — the canonical preset is
fixed by the catalog entry (30s base, 5m max).

```
At start (idempotent — safe to call every fire):
  empirica loop register --name cortex-mailbox-poll --kind interval \
    --interval 30s \
    --description "Poll Cortex inbox + outbox for orchestration messages (canonical)" \
    --backoff exponential --base-interval 30s --max-interval 5m

Check pause — exit silently AND don't schedule next fire if paused:
  PAUSED=$(empirica loop status cortex-mailbox-poll --output json | jq -r .paused)
  if [ "$PAUSED" = "true" ]; then
    empirica loop heartbeat cortex-mailbox-poll --status ok --result paused \
      --message "skipped, paused"
    exit 0
  fi

Self-throttle — if an empirica transaction is open, the caller is
already engaged. Don't interrupt; just freeze the streak.
  TX_OPEN=$(python3 -c "
from empirica.utils.session_resolver import InstanceResolver as R
tx = R.transaction_read()
print('true' if tx and tx.get('status') == 'open' else 'false')
")
  if [ "$TX_OPEN" = "true" ]; then
    empirica loop heartbeat cortex-mailbox-poll --status ok --result empty \
      --message "self-throttle: transaction open"
    NEXT_CRON=$(empirica loop schedule-next cortex-mailbox-poll --output json | jq -r .cron_one_shot)
    # CronCreate(cron=$NEXT_CRON, recurring=false, prompt='<this template again>')
    exit 0
  fi

Resolve self ai_id from project context:
  AI_ID=$(python3 -c "
import os, re
from pathlib import Path

# 1. Project's CLAUDE.md
for parent in [Path.cwd()] + list(Path.cwd().parents):
    claude_md = parent / 'CLAUDE.md'
    if claude_md.exists():
        text = claude_md.read_text()
        m = re.search(r'(?:^|\n)\*?\*?AI_ID:?\*?\*?\s*[\`\"]?([a-z0-9_-]+)[\`\"]?', text, re.IGNORECASE)
        if m:
            print(m.group(1)); raise SystemExit
        break

# 2. Project name fallback (strip 'empirica-' prefix)
project_path = os.getcwd()
name = Path(project_path).name
if name.startswith('empirica-'):
    print(name[len('empirica-'):]); raise SystemExit
if name == 'empirica':
    print('empirica'); raise SystemExit

# 3. Env var override
ai_id = os.environ.get('EMPIRICA_AI_ID')
if ai_id:
    print(ai_id); raise SystemExit

raise SystemExit(1)  # unresolved
")
  if [ -z "$AI_ID" ]; then
    empirica loop heartbeat cortex-mailbox-poll --status fail --result fail \
      --message "unresolved ai_id (no CLAUDE.md AI_ID line, no project name fallback, no EMPIRICA_AI_ID env)"
    NEXT_CRON=$(empirica loop schedule-next cortex-mailbox-poll --output json | jq -r .cron_one_shot)
    exit 0
  fi

Poll inbox via MCP — react to new proposals addressed to self.
The api_key for cortex_* MCP tools is read by the MCP server itself
from ~/.empirica/credentials.yaml; no need to pass it explicitly.
  Call mcp__cortex__cortex_inbox_poll(ai_id=$AI_ID)
  INBOX_NEW=<number of new items returned>

  For each new item:
    - If type=collab_brief: AUTO-REACT — read the payload, log a
      finding-log for durability, and post a reply via
      `empirica mailbox reply --parent-id <pid> --result shipped` (the
      atomic propose+complete verb closes the loop). Do NOT
      surface-and-wait. Collab is noetic/ungated — the human is the
      gate ONLY for ECO-gated typed proposals (see below) and for
      your own returning outbox state changes (status=changed/declined).
      If a collab asks you a question, answer it directly; the
      AI-to-AI substrate exists so the human doesn't have to dispatch.
    - If type=spec_updated: ack with cortex_archive_proposal once you've
      consumed the change
    - If type=architecture_decision / code_change_request / publish /
      trust_escalation_request: these are ECO-gated typed proposals.
      Surface to the user — they need explicit human Accept/Decline
      before action. Do not auto-execute the underlying work.
    - For any item with parent_id: link your follow-up via parent_id

Poll outbox via MCP — emit follow-ups for proposals that came back
as 'changed' (peer/user requested a refinement).
  Call mcp__cortex__cortex_outbox_poll(ai_id=$AI_ID, status=changed)
  OUTBOX_CHANGED=<number of changed proposals>

  For each changed proposal:
    - Read the refinement note
    - Compose an updated proposal with parent_id pointing to the original
    - Submit via cortex_propose (parent_id linking back closes the loop)

Determine result for backoff signaling:
  if [ "$INBOX_NEW" -gt 0 ] || [ "$OUTBOX_CHANGED" -gt 0 ]; then
    RESULT=found
    SUMMARY="ai_id=$AI_ID inbox=+$INBOX_NEW outbox-changed=+$OUTBOX_CHANGED"
  else
    RESULT=empty
    SUMMARY="ai_id=$AI_ID no activity"
  fi

At end — heartbeat with result, schedule + install the next fire:
  empirica loop heartbeat cortex-mailbox-poll --status ok --result $RESULT \
    --message "$SUMMARY"

  NEXT_CRON=$(empirica loop schedule-next cortex-mailbox-poll --output json | jq -r .cron_one_shot)
  # CronCreate(cron=$NEXT_CRON, recurring=false, prompt='<this whole template again>')

  # Heartbeat back the scheduler-returned job_id so pause can cancel:
  empirica loop heartbeat cortex-mailbox-poll --status ok --result $RESULT \
    --next-scheduled-job-id "$JOB_ID" --scheduler-kind cron-create

On MCP failure (network, auth, unexpected error):
  empirica loop heartbeat cortex-mailbox-poll --status fail --result fail \
    --message "{error message}"
  # Failure retries at base — schedule-next still returns base interval.
```

---

## Self-throttle rationale

The body's first real check (after pause) is whether `R.transaction_read()`
returns an open transaction. If yes, the AI is mid-PREFLIGHT-to-POSTFLIGHT
cycle — it's already focused on something. Polling the inbox right now
would surface a notification that competes for attention against the
in-flight work.

Self-throttle returns `empty` (not `paused`) so the streak grows naturally
toward `max_interval`. When the transaction eventually closes
(POSTFLIGHT), the next fire happens on whatever the current schedule
dictates — back to base 30s if a peer proposal arrived during the
transaction (because the inbox will have new items), or stretching
toward max if everything stays quiet.

---

## Handling received items

The body shape above lists the rough decision tree. Detailed handling
per proposal `type`:

| type | Gate | Default action |
|---|---|---|
| `collab_brief` | **None (noetic, ungated)** | **Auto-react**: read payload, log finding-log, post a reply via `empirica mailbox reply --parent-id <pid> --result shipped` (atomic propose+complete). If the collab asks a question, answer it; if it shares context, ack/integrate it; if it converges, graduate to `cortex_propose` per the send-side discipline. **Do NOT surface-and-wait** — the human-as-dispatcher pattern breaks the AFK/ambassador model. |
| `spec_updated` | None | Read spec at `payload.path`, log finding-log "consumed spec X", archive via `cortex_archive_proposal` |
| `architecture_decision` | **ECO-gated** | Surface to user — needs explicit human Accept/Decline before action |
| `code_change_request` | **ECO-gated** | Surface to user — same as above. Tiny mechanical changes can act-and-follow-up only if you're confident the user would auto-accept. |
| `investigation_request` | None (auto-reflex) | Run the investigation via `cortex_research` or local tools, post results via reply collab_brief |
| `publish` | **ECO-gated** | Surface to user — never auto-publish |
| `trust_escalation_request` | **ECO-gated** | Always surface to user — never auto-act on trust changes |

**The split is symmetric with the send side** (`/cortex-mailbox-send`): collab (noetic) flows ungated AI↔AI; ECO-gated typed proposals (`architecture_decision` / `code_change_request` / `publish` / `trust_escalation_request`) need the human gate. **Outbox state changes you receive** (`direction=outbox, status=changed/declined`) also surface to the user — those mean your own proposal needs human-aware refinement.

For ANY proposal that requires acting (not just acknowledging), the AI
should open an empirica transaction (PREFLIGHT) to record the work. The
mailbox-poll itself is meant to be lightweight — its job is to detect
and route, not to do the work inline.

> **Why the noetic auto-reply rule exists** (extension's `prop_urke62v`, 2026-06-01, David-directed): when AIs surface every collab question to the human, the human becomes a dispatcher between AI instances — defeats the AFK/ambassador model and burns the human's attention on routing. Collab is ungated *specifically so* AIs can converge without human intervention. Pairs with Contract 2 (fyi-drop): every collab wake is now actionable but actionable-by-AI, not actionable-by-human.

---

## Visibility

Once registered, the loop appears in:

```
empirica status              # current instance — cortex-mailbox-poll: 30s, last fire X ago
empirica status --all        # every Claude across every terminal
```

…showing the adaptive interval (current streak position), last fire
result, and pause state. From the TUI cockpit, press `L` on the instance
to toggle pause/resume globally.

---

## Related

- `/loop-cron` — the underlying registry-wiring template this skill wraps
- `empirica/core/cockpit/canonical_loops.py` — catalog entry that auto-installs this loop
- `docs/architecture/COCKPIT.md` — full state-file layout
- `cortex_propose`, `cortex_inbox_poll`, `cortex_outbox_poll`, `cortex_archive_proposal` —
  the MCP tools this body wires up
