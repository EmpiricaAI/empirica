---
name: cortex-mailbox-poll
description: "React to mesh wake events (proposal_event / ser_escalation) delivered by the listener, and reconcile via inbox/outbox polls. Pairs with /cortex-mailbox-send."
version: 2.0.0
---

# Receiving in the AI Mesh

Wake events arrive as one JSON line per ECO-decided proposal
(`{"event_type":"proposal_event","proposal_id":...,"direction":...,"status":...,
"instance_id":...}`). The mailbox is the source of truth; a push is a liveness nudge —
never act irreversibly on the push alone; the durable proposal state
(`empirica mailbox show <id>` / poll) is what you reconcile against.

## Step 0 — recipient gate

`instance_id == your ai_id` (from `.empirica/project.yaml`) → it's yours, proceed.
Otherwise resolve the proposal's `target_claudes` (that list is authoritative, not
`instance_id`): you're in it → proceed; not in it → ignore silently. Repeatedly seeing
foreign events means your Monitor filter is wrong — re-arm via `empirica listener on`.

## Reaction table — direction × status

| direction | status | action |
|---|---|---|
| inbox | accepted | read it (`empirica mailbox show <id>`), execute the ask, then ack via `empirica mailbox reply ... --result shipped\|failed\|wont_fix` (see /cortex-mailbox-send) |
| inbox | changed | ECO adjusted the scope — read `eco_decision.note`, proceed with the adjusted ask |
| inbox | declined | ECO said no — update your model, no action |
| outbox | completed/shipped | your ask landed — trace the `commit_sha`, log a finding, chain the next step if one was waiting |
| outbox | failed / wont_fix | honest outcome, NOT an error: that leg is dead — do NOT chain as if it shipped; read the note, re-scope or drop |
| outbox | changed | ECO wants refinement — emit a `parent_id`-linked refined proposal |
| outbox | declined | ECO rejected it — note why, don't blindly re-propose |

**Mid-transaction when an event lands:** log a goal
`Process <direction>/<status>: <prop_id>` (the literal `prop_` token is what the
POSTFLIGHT retrospective greps) and pick it up at the next natural break. **Idle:**
act now. Archive handled proposals (`empirica mailbox archive <id>`).

## ser_escalation events

`event_type == "ser_escalation"` (not proposal_event): an SER you're `required` on has
idled past its interval. Fetch `GET /v1/sers/{ser_id}`, then either take the
substantive action (transition) or ack
(`cortex_propose(payload.action='ser_ack', ack_spec={...})`) to silence the next tick.
Closed SERs never escalate — a tick racing closure needs nothing.

## Catch-up safety net

At session start, after long pauses, or on suspected Monitor drops:
`empirica mailbox poll --ai-id <your-canonical-triple> --output json`
(receive side; `--outbox` for your emissions' status changes). Push and poll should
agree; the poll wins on disagreement. No reminder/retry chain exists — dropped
signals are reconciled on your next poll, and the autonomy sweep catches the tail.

Harness note: on namespace-aggregating harnesses prefer the CLI verbs
(`empirica mailbox poll/show/reply/archive`) — they are identical everywhere and
Sentinel-whitelisted pre-transaction.
