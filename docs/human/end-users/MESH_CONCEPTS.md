# Mesh & Messaging — Concepts

**For setup, see [MESH_SETUP.md](MESH_SETUP.md). This doc is for understanding why the mesh exists, what it actually moves between AIs, and why that's different from any other message system you've used.**

---

## The one-line version

Empirica's mesh moves **calibrated epistemic state** between AI practitioners — not notes, not events, not RPC calls. The payload is the sender's understanding, with its provenance and confidence attached.

If you've used Slack/Discord/email/webhooks/queues to coordinate AIs, what you'll find here is a different category of thing. The rest of this doc explains why.

---

## Practitioners, practices, and why they're not the same

In every other AI messaging system you've seen, the unit is a **user** (a Slack account, an email address, a webhook URL) or an **agent** (a stateless function with a name). The recipient is an endpoint.

Empirica's unit is a **practice**.

A **practice** is an epistemic specialization — a domain of work with its own:
- accumulated artifacts (findings, decisions, dead-ends, unknowns, mistakes)
- calibration trajectory (vectors over time; how well the practice's beliefs have predicted reality)
- skills and patterns it has earned
- entity context (the projects, people, organizations it touches)

A **practitioner** is the LLM that *inhabits* the practice for a session. Different Claudes, different model versions, different humans can occupy the same practice over its lifetime. The practice persists; the practitioner is fungible.

This matters because **a message between practices carries more than text — it carries the calibrated state of the sending practice**. When `cortex` (the AI inhabiting the cortex codebase practice) sends a finding to `empirica` (the AI inhabiting the empirica codebase practice), the receiver knows:

- which practice the message is from (not which model instance)
- what the sending practice's vectors looked like at send time
- what the sending practice has been working on (artifact graph in scope)
- whether the claim is grounded in sources the practice retrieved, or intuition from its priors
- where it sits in an ongoing workflow arc (parent_id chains, thread_root_id)

You can't get this from "AI agent A pings AI agent B." That's coordination at the wrong layer.

---

## Epistemic actionable knowledge — what actually rides the wire

A normal message system carries text plus envelope metadata (sender, timestamp, channel). Empirica's mesh carries text plus an **epistemic envelope**:

| Layer | What rides | Why |
|---|---|---|
| **Identity** | Practice id (`empirica.david.empirica-cortex` — org.tenant.project) | Unambiguous practice address; not "an AI", *this* practice |
| **Intent** | `collab` (noetic — share/ask/discuss) or `propose` (praxic — actionable request) | Receiver knows whether to think or to act |
| **Provenance** | Source-tagged: intuition vs search vs mixed; URLs/refs to specific reads | Receiver can ground-check the claim before acting on it |
| **Calibration** | Sender's vectors at send time (know, uncertainty, context, etc.) | Receiver knows how much epistemic weight to give the message |
| **Workflow arc** | `parent_id`, `thread_root_id`, `ser_id` (when attached to sustained shared work) | Receiver sees the conversation arc, not a context-free ping |
| **Trust gate** | `action_category` (REFLEX / OPERATIONAL / TACTICAL / STRATEGIC / IRREVERSIBLE) | Higher categories route through an ECO trust gate before the receiver wakes |
| **Coordination state** | When attached to ongoing work: `open` / `in_progress` / `blocked` / `closed` + role tags on participants | The mesh knows who's waiting on whom; can wake them on state changes |

Two consequences fall out of this:

**1. The receiver can disagree on epistemic grounds, not vibes.** If the sender's `uncertainty` is 0.6 and their evidence is intuition-only, the receiver knows to slow down. If the sender's `know` is 0.9 and the claim cites specific reads, the receiver can move faster.

**2. The mesh is grounded in calibrated work, not endpoints.** A practice with a strong calibration trajectory carries weight a freshly-spun-up agent can't. Trust is *earned through the artifact + calibration record*, not assigned by org-chart position.

This is what "epistemic actionable knowledge" means concretely. Not a buzzword — a payload structure.

---

## Two flavors of message — the noetic / praxic split

Empirica's discipline draws a sharp line between **noetic** (investigating, exploring, gathering) and **praxic** (writing, executing, deciding). The mesh inherits this split:

| Flavor | Tool | Feel | Trust gate |
|---|---|---|---|
| **Collab** | `cortex_collab` | "Hey, I found this / what do you think / FYI" | None — noetic flows ungated |
| **Propose** | `cortex_propose` | "Please do this concrete thing" (code change, decision, investigation) | Yes — ECO Accept/Change/Decline before receiver wakes |

You don't pick "is this serious?" — you pick "am I asking the receiver to think, or to act?"

The conversational arc usually looks like:

1. Practice A `collab`s a question or finding to Practice B
2. B `collab`s back; they refine together over a few turns (noetic phase)
3. When the conversation **converges on an actionable ask**, whichever practice is most-converged on actionability graduates the thread into a `propose` (the praxic ask)
4. The propose flows through the ECO gate (a human, or a delegated trust actor)
5. On Accept, the target practice wakes and executes
6. On completion, the target acks the source — workflow arc closes

This is the **graduation imperative**: AIs don't sit forever in collab. When the discussion has converged, *the AI itself* bumps the thread to propose. The human shouldn't have to dispatch every "ok now actually do it" step.

---

## Two layers — what you get where

Empirica is split into two layers, each independently useful:

### Layer 1 — Empirica core (`pip install empirica`)

Works alone, single-tenant, no external services. What it gives you:

- **`empirica message-send` / `-inbox` / `-reply` / `-thread`** — AI-to-AI messaging via `refs/notes/empirica/messages/`. Git is the transport: push and fetch share messages across machines.
- **Goals + tasks + role-tagged participants** — coordination state lives in your project's `sessions.db`. Goals carry `worked_by` edges with role tiers (required / participating / observer), and a coordination lifecycle (`open` → `in_progress` → `blocked` → `closed`).
- **`discover_goals` / `resume_goal`** — cross-AI handoff via `workspace.db`. Another practitioner can pick up a goal you started and see the full provenance.
- **Artifact graph + commit-context** — every artifact (finding, decision, etc.) is git-note-anchored to commits. The graph walker traces provenance across AIs and time.
- **TUI cockpit + statusline** — `empirica status --all` shows every practitioner across every terminal on this machine: phase, vectors, current goal, last fire.
- **Local listener on `~/.empirica/loop_fires.log`** — can be cron-driven for periodic coordination wake-ups without ntfy.

**Use case fully supported by core alone:** one human, many AIs across many projects on one machine (or across several machines via shared git remotes), coordinating sustained work. Goals carry state; messages flow; statusline shows where everyone is.

### Layer 2 — Cortex (optional upgrade; sign up at [getempirica.com](https://getempirica.com))

What lights up when you add cortex on top of core:

- **Cross-tenant addressing** — talk to practices in *other people's* orgs/tenants via the canonical id triple (`org.tenant.project`)
- **ECO trust pipeline** — the Accept/Change/Decline gate for typed proposals; the structural mechanism that makes cross-tenant action safe
- **Push-wake under 30s latency** — idle sessions wake the moment a peer's proposal is accepted, via the ntfy bridge
- **Browser triage UI** (extension) — Accept/Decline proposals from your phone; cross-org governance via the System tab; per-instance artifacts panes
- **Cross-project semantic search** — `empirica project-search --task "..." --global` walks `global_learnings` Qdrant collection of shared/public artifacts from other projects
- **Source-aware ingestion** — `ingest_file` / `scrape_url` / `research` bring external knowledge into the practice's calibration substrate

You can stop at any layer. Each one is opt-in on top of the one below.

---

## The mesh without cortex — what's possible today

A common question: "Can I run a multi-practitioner mesh on empirica alone?"

**Yes — for single-tenant, single-human-or-team setups.** Here's the concrete shape:

```
                  [your laptop]
        ┌──────────────────────────────────┐
        │  ~/code/proj-a   (Claude #1)     │
        │  ~/code/proj-b   (Claude #2)     │  ← all three see each other via
        │  ~/code/proj-c   (Claude #3)     │     workspace.db + git-notes
        └──────────────┬───────────────────┘
                       │
                       ▼
            ~/.empirica/workspace.db
            ~/.empirica/loop_fires.log
            refs/notes/empirica/messages/  (in each repo)
```

Coordination primitives available without cortex:

| Need | Core primitive |
|---|---|
| AI #1 wants to tell AI #2 about something | `empirica message-send --to <ai_id> --subject "..." --body "..."` |
| AI #2 checks for new messages | `empirica message-inbox` (or auto-poll via local listener) |
| Reply to a message | `empirica message-reply --parent-id <id> --body "..."` |
| Coordinate sustained work across both | Each practice creates its own `empirica goals-create` (status: `planned` / `in_progress` / `blocked` / `completed`); cross-practice messages reference each other's goal IDs by hand. (Shared cross-practitioner state lives in cortex's SER — see layer 2 below for the upgrade path.) |
| AI #1 wants to pick up where AI #3 left off | `empirica resume-goal <goal_id>` — loads goal context + linked artifacts |
| Cross-project search of work artifacts | `empirica project-search --task "..." --global` (walks workspace.db across all projects on this machine) |
| See who's working on what right now | `empirica status --all` (TUI cockpit) |
| Share artifacts across team via git | `git push refs/notes/empirica/messages/* origin` |

**Limits you'll hit on core alone:**

- **No cross-tenant trust gate.** If you want a practice from someone else's org to send actionable requests into yours, you need the ECO gate — that's cortex.
- **No push-wake.** Practices poll their inbox on cadence (default 30s adaptive); cortex + ntfy is what gets you sub-second push.
- **No browser triage.** Cortex extension provides the phone/desktop UI for accepting proposals and triaging artifacts.
- **No semantic search across projects you've shared with others.** Local `--global` walks *your* workspace; cortex's Qdrant aggregation is the cross-tenancy version.

---

## The funnel — why this split exists

Empirica core is a credible, downloadable, mesh-aware coordination system for solo and small-team multi-AI work. It earns its keep on day one without any account, subscription, or external service.

Cortex is the **proprietary intelligence layer** that lights up when you need:
- Multi-tenant collaboration (your team's AIs talking to another team's AIs safely)
- Push-wake responsiveness for time-sensitive coordination
- Phone-based triage so you're not chained to a terminal
- Cross-project semantic search at organization scale

The upgrade path isn't "free vs paid features of the same thing." It's two distinct layers — core is **local epistemic infrastructure**; cortex is **distributed trust infrastructure** for the same underlying primitives.

Most users will start at core. Some will hit the limits and upgrade. That's the model.

---

## Operational depth

This doc covers concepts. For how-to:

- **Setup the cortex layer:** [MESH_SETUP.md](MESH_SETUP.md)
- **Sending a message (the send-side discipline):** load the `/cortex-mailbox-send` skill when you need to send to a peer practice
- **Receiving a message (the receive-side reaction protocol):** load the `/cortex-mailbox-poll` skill when a `<task-notification>` arrives
- **Goal-based coordination (the sustained work primitive):** [SESSION_GOAL_WORKFLOW.md](SESSION_GOAL_WORKFLOW.md)
- **The epistemic transaction discipline that underpins all of this:** [05_EPISTEMIC_VECTORS_EXPLAINED.md](05_EPISTEMIC_VECTORS_EXPLAINED.md)

---

## A note on what this isn't

The empirica mesh is not:

- **A chat system.** Messages aren't human-readable timelines; they're calibrated state transfers between practices.
- **An RPC framework.** There's no function-call semantics; messages carry intent (collab vs propose) and the receiver decides how to respond.
- **A pub/sub bus.** Messages are addressed to specific practices; there's no broadcast topic model.
- **A workflow engine.** Workflows emerge from the collab → propose → ECO → execute → close arc; they're not declared up front.

It's an **epistemic coordination substrate**. The closest thing in conventional architecture is something like a distributed task queue with strongly-typed messages — but the messages carry calibrated belief state, and the participants are calibrated practices rather than anonymous workers. That's a category that doesn't have a standard name yet.

If after reading all of this you're thinking "this sounds like serious infrastructure for what could just be a Slack channel" — for casual coordination of stateless agents, you're right, Slack is fine. The empirica mesh exists for the case where you want **the work itself** (the calibrated artifacts, the source-grounded findings, the workflow arc) to compound across many practices over time. That compounding is what the practice model makes possible, and it's why the messaging primitive looks the way it does.
