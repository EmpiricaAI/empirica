# Skills — what ships, when each fires, which are load-bearing

Empirica ships **14 skills** with the Claude Code plugin. They are *lazy*: a
skill does nothing until it is loaded, so knowing when each one fires matters
more than knowing what it contains.

Until this document existed you discovered them by finding a `/slash-command`
in a prompt, or not at all — and the README did not mention skills exist.

> **Load-bearing vs situational.** One skill carries most graph upkeep and the
> rest fire on specific triggers. The deep protocol skills (transactions,
> constitution, mesh mailbox) ship with the Cortex bundle, not this plugin —
> the system prompt's §TRANSACTION DISCIPLINE carries the always-loaded core.

---

## The load-bearing skill

| Skill | Fires when | Why it is load-bearing |
|---|---|---|
| **`/epistemic-gardening`** | Pre-release, periodically, or when retrieval starts surfacing stale artifacts | Resolve / archive / prune so the graph reflects what is live. An epistemic graph that only grows is an archive, and stale artifacts actively mis-steer retrieval |

---

## Governance and transaction discipline

Core discipline — shipped in this plugin, not Cortex-gated.

| Skill | Fires when | Why it is load-bearing |
|---|---|---|
| **`/empirica-constitution`** | First PREFLIGHT of a session; routing a decision you have not met before; the user asks what Empirica can do | Deep governance — phase-aware completion, the cognitive immune system, the practice model. The *why* underneath the operational routing |
| **`/epistemic-transaction`** | Work spans 3+ files, 2+ goals, or several noetic→praxic cycles | The transaction discipline itself — PREFLIGHT vectors, goal decomposition, task evidence, wire formats for every submission. Largest skill in the set at ~5,800 words, and the depth is the point |
| **`/pre-action-grounding`** | A task arrives without its *why* or a checkable done-condition — especially unstructured requests from non-devs | Investigate first, ask last (0–2 questions), bank the ungrounded residue as assumptions, and emit a goal whose criteria are typed where an evaluator reaches and honestly prose where none can. Exists because 79% of goals said "done when done" |

---

## Mesh (require Cortex)

These need the Cortex mesh configured. Core is fully functional without them.
The mesh protocol skills — `cortex-mailbox-poll`, `cortex-mailbox-send` — are
distributed with the Cortex bundle, not this plugin: installing Cortex provides
them alongside its MCP tools. (Written without the `/` here deliberately: the
index guard treats backticked slash-commands as claims that this plugin ships
them.) The governance and transaction-discipline skills below are **core** and
ship in this plugin — they are not Cortex-gated.

| Skill | Fires when |
|---|---|
| **`/inbox-listener`** | Arming an event listener — "arm this listener", "subscribe to ntfy topic", "wake me when X arrives" |

---

## Quality and audit

| Skill | Fires when |
|---|---|
| **`/eat-the-broccoli`** | Pre-release, after a refactor, or when something smells off. Deterministic tooling **plus** a pattern hunt for failure classes that pass every test and still ship broken. Vendored from an upstream repo — edit there, not here |
| **`/code-audit`** | "audit this code", "find duplication", "find dead code", "technical debt" |
| **`/code-docs-align`** | "check if docs match code", "verify docstrings", "find stale comments", "audit TODOs" — purely noetic, finds mismatches and fixes nothing |
| **`/architecture-review`** | Stress-testing a proposed or existing architecture — bottlenecks, single points of failure, security and cost gaps |
| **`/services-auditor`** | `empirica scan --explain`, or auditing running AI services from the scanner snapshot |

---

## Interaction and setup

| Skill | Fires when |
|---|---|
| **`/epistemic-persistence-protocol`** | The user pushes back on a position. Load it **before** responding, to classify the pushback type. Its five-way vocabulary is contracted on by the `UserPromptSubmit` hook — it is not decorative |
| **`/ewm-interview`** | "set up my workflow", "create workflow protocol". Guided multi-choice interview producing `workflow-protocol.yaml`, and it provisions the practices the answers imply rather than describing them |
| **`/dispatch-agent`** | Spawning subagents that would benefit from inherited findings and dead-ends |
| **`/render`** | "render this", "generate SVG", markdown with diagrams |

---

## Scheduled bodies — not invoked by you

These are loop bodies. The scheduler loads them when a cron entry fires; you
do not call them.

| Skill | Fires when |
|---|---|
| **`/loop-cron`** | Registering periodic background work — the template other cron loops build on |
| **`/message-cleanup`** | Daily (default 03:17) — prunes expired git-notes mesh messages |
| **`/services-audit-cron`** | Biweekly — schedules the services-auditor body |

> **Cron is opt-in and never installed by default.** A scheduled loop exists
> only because someone registered it. Wake-on-event is preferred wherever the
> event exists.

---

## Reading this page as an AI

- **A skill you do not load does nothing.** The triggers above are the whole
  interface; a skill's content cannot help you if the trigger never fires.
- **Load before acting, not after.** The common failure is recognising a
  trigger, acting from memory, and loading the skill afterwards to check. Skill
  content evolves; memory of it does not.
- **Descriptions are trigger surface.** The phrases in each skill's
  `description:` are what cause it to load. They are not a table of contents,
  and trimming them makes the skill load less often.

## Adding a skill

`empirica/plugins/claude-code-integration/skills/<name>/SKILL.md`, with
front-matter carrying `name` and `description`. The description is the load
trigger — write it as the situations that should summon the skill, not as a
summary of its contents. Then add a row here: a skill absent from this page is
one nobody will find.
