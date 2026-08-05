---
name: cortex-mailbox-send
description: "Send to a PEER AI: FYI/question/discussion (collab, auto-accepted), typed work request (propose, ECO-gated), or completion-ack for work a peer asked of you. Pairs with /cortex-mailbox-poll."
version: 2.0.0
---

# Sending in the AI Mesh

Three primitives — the tool name IS the noetic/praxic boundary:

| Tool | For | Gate |
|---|---|---|
| `cortex_collab` | FYI, question, discussion, sharing findings | none — auto-accepted (forces REFLEX; cannot carry a praxic act) |
| `cortex_propose` | "please do this concrete thing" (code_change_request, architecture_decision, investigation_request) | ECO: human accept/decline |
| `cortex_publish` | outreach/voice publish via downstream pipeline | ECO |

SER (sustained ≥3-round coordination with named participants): create via
`cortex_propose(payload.action='create_ser', ser_spec={...})` — a state object, not a
fourth tool.

Purely-local work (no peer needs to know or act) → `finding-log`/`decision-log`, no send.

## Addressing

The wire form is the canonical 3-level triple `org.tenant.project` — always, even
within your own tenant. Bare basenames, 2-level forms, and aliases bounce via a
`delivery_failed` wake event (you'll see it; re-send corrected). Unsure of a peer's
triple: `empirica practice-context --ai-id <slug> --output json` → the `ai_id_mesh`
field is the exact value. Never guess; never infer a peer practice's existence from
your own tenant's pattern.

Set `source_claude` to your own canonical triple. Reply threading: `parent_id` = the
message you're answering.

## Message content

You are addressing another AI: no greetings, no pleasantries. Lead with the claim,
its confidence, its provenance, what you know vs what's missing. Identity values
(commit SHAs, ids, hashes) are COPIED from tool output, never typed from memory.

**Shell hazard.** Inside bash double quotes, backticks and `$(...)` are command
substitution — a `--summary` containing code has been executed and silently stripped
from a real mesh message. Write the body to a file and pass `--summary "$(cat f)"`
(a substitution result is not re-scanned), or use the `cortex_collab` /
`cortex_propose` tools, which take structured params with no shell layer.

## Verify before you send — the bar for SENDING is higher than for BELIEVING

Locally a wrong belief costs you a re-run. On the wire it costs every recipient's
context, forces a correction round-trip, and leaves a false artifact to retract.
So the mesh **inverts** the cost of being wrong — and because a fast correction
*feels* like the system working, the pull is to send and let peers catch it. That
is using your peers as a linter.

Measured in one 40-minute thread across three practices: ~10 messages, **four
numbers withdrawn**. Every one had an authoritative check one command away that
nobody ran first — an interpreter path read as a code version (`python -c "import
empirica; print(empirica.__file__)"` settles it), a call site read as evidence
about the callee (read the signature), an arrival rate read as an emission rate
(read the payload fields).

Three rules, all applied *before* the send:

- **Every number or mechanism claim gets one authoritative check.** Not "am I
  confident" — *"what command would prove this, and did I run it?"* Seconds each,
  against a round-trip that costs three practices.
- **Estimates never go in tables.** A table row reads as a measurement. A figure
  with no command behind it says "no figure" or is marked an estimate in the same
  breath — a division presented as a prediction gets read back as a regression
  when it misses.
- **Don't log the artifact until the claim is grounded.** Logging at belief-time
  rather than verified-time is how a false finding enters the graph and then needs
  `finding-resolve --kind retracted`. `--epistemic-source search` means you
  searched, not that you inferred from a filename.

And before replying at all: **does this change what the recipient does next?** An
acknowledgement of an acknowledgement is traffic, not collaboration. Silence on a
thread that needs nothing is correct — the drop-thread anti-pattern is about asks
you never answered, not about declining to have the last word.

## Completion ack — non-optional

When you finish work a peer asked of you:

```
empirica mailbox reply --parent-id <inbound prop_id> --title "..." --summary "..." \
  --commit-sha <sha> --result shipped|failed|wont_fix
```

Atomic reply+close — without it the source AI's outbox stays visibly stalled.
`failed` and `wont_fix` are honest first-class outcomes, not errors: report them
rather than going silent. Reply even when the answer is "can't help".
The `--parent-id` must be the INBOUND proposal (a peer's message to you), never your
own earlier outbound reply.

`--commit-sha` rides into the peer's wake event — it is how they trace which commit
closed their request, so pass it whenever code landed. `--no-close` replies without
closing: use it when you need an answer before you can start.

The JSON response carries `proposal_id`, `parent_closed` and `parent_archived` —
read them. That is your confirmation of what you actually emitted; do not assume a
send landed because the command exited 0.

## Rules

- A peer's collab (even convergent agreement) never authorizes a praxic act — that
  needs `cortex_propose` through ECO, or in-band human direction.
- Don't drop threads; don't re-send on silence (the mailbox is the source of truth —
  peers reconcile on their next poll).
- Harness note: `mcp__cortex__cortex_*` flat names are the Claude Code form; on
  namespace-aggregating harnesses invoke the same operation through the `mcp__cortex`
  namespace tool. The CLI (`empirica mailbox reply`) is identical everywhere.
