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
    - If type=collab_brief: read the payload, decide if you can act now
      or want to surface to the user; either way log a finding-log so
      the trail is durable
    - If type=spec_updated: ack with cortex_archive_proposal once you've
      consumed the change
    - If type=architecture_decision / code_change_request: surface to
      the user (these need human ack before action)
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

| type | Default action |
|---|---|
| `collab_brief` | Read payload, log finding-log with summary + impact, surface to user if requires their input |
| `spec_updated` | Read the spec at `payload.path`, log finding-log "consumed spec X", archive the proposal via `cortex_archive_proposal` |
| `architecture_decision` | Surface to user — these need explicit ack before action |
| `code_change_request` | Surface to user if non-trivial; if a tiny mechanical change, can act and follow up |
| `investigation_request` | Run the investigation via `cortex_research` or local tools, post results via `cortex_collab_post` |
| `publish` | Compose draft per spec, follow voice profile, send back via `cortex_propose` parent-id linked |
| `trust_escalation_request` | Always surface to user — never auto-act on trust changes |

For ANY proposal that requires acting (not just acknowledging), the AI
should open an empirica transaction (PREFLIGHT) to record the work. The
mailbox-poll itself is meant to be lightweight — its job is to detect
and route, not to do the work inline.

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
