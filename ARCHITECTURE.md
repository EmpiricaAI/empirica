# Empirica — Architecture

> **Scope.** A top-level orientation: what the pieces are, why they are separate,
> and where the real detail lives. It deliberately reaches across boundaries that
> `docs/architecture/` covers one at a time — that folder has 46 documents, each
> good on its subject and none of them a map. This is the map.
>
> Numbers here are measured against this repo, not estimated. They will drift;
> the shapes they illustrate are the durable part.

---

## The one idea

An AI working on a codebase has beliefs about its own competence — how well it
understands the domain, how clear the path is, what it does not know. Those
beliefs are normally invisible, unrecorded, and unchecked.

Empirica makes them **first-class, measured, and gradable against evidence.**

Work happens inside an **epistemic transaction**:

```
PREFLIGHT  →  [noetic: investigate]  →  CHECK  →  [praxic: change things]  →  POSTFLIGHT
   │                                      │                                      │
 declare beliefs                    gate the transition                   re-declare, and be
 before starting                    from reading to writing               graded against evidence
```

Two properties make this more than journalling:

1. **The gate is enforced outside the model.** CHECK is not a prompt asking the
   AI to be careful — it is a `PreToolUse` hook that refuses to run `Edit`,
   `Write`, or a mutating shell command until CHECK has passed. An AI cannot
   talk its way past it. See [The harness boundary](#the-harness-boundary).

2. **Self-assessment is scored against things the AI does not control** — test
   results, commit activity, artifact ratios, goal completion. The gap between
   *claimed* and *observed* is the calibration signal. High numbers are not the
   goal; **accurate** numbers are.

`docs/architecture/ASSESSMENT_AND_SIGNALING.md` and
`docs/architecture/DISCIPLINE_IS_SPEED.md` carry the reasoning.

---

## Noetic and praxic

The distinction the whole system turns on:

| | **Noetic** | **Praxic** |
|---|---|---|
| Does | reads, searches, retrieval | writes, executes, mutates |
| Can change state? | provably no | yes, or maybe |
| Gated? | never | always |

The discriminator is **effect, not name**. `sed 's/x/y/'` writes to stdout and
flows free; `sed -i` edits files and gates — same binary, opposite sides of the
line. So the classifier inspects flags, not just command names, and a tool
trusted by name is still rejected by flag.

This cuts both ways, and the second direction is easy to miss: **over-gating is
a defect too.** A practitioner forced through CHECK in order to *read* learns
that CHECK is a formality — so a false deny on an inert command corrupts the
gate more thoroughly than the friction it causes.

---

## Where state lives

Three layers, deliberately not one. Each answers a different question, and each
survives a different kind of loss.

### 1. SQLite — the queryable record

`.empirica/sessions/sessions.db` · 83 tables · `empirica/data/`

Sessions, transactions, vectors, goals and every artifact type. This is what
`goals-list` reads and what calibration scores against. Currently in this repo:

| | |
|---|---|
| findings | 4,287 |
| goals | 1,468 |
| dead-ends | 755 |
| decisions | 514 |
| unknowns | 486 |
| mistakes | 178 |
| assumptions | 62 |
| **edges between them** | **862** |

That last row is the one that matters. Artifacts are a **graph**, not a list —
an artifact connected to nothing cannot be swept, re-evaluated, or invalidated
along with its premises.

### 2. Git notes — the durable, travelling record

`refs/notes/empirica/*` · `empirica/core/canonical/`

Artifacts and breadcrumbs written as git notes, anchored to the commits they
describe. They travel with the repo, survive a clone, sync on push, and need no
server — so the epistemic history is as durable as the code it is about. This is
also the transport for the Cortex-free messaging layer.

### 3. Qdrant — the retrieval layer

`empirica/core/qdrant/`

Vector search over artifacts, docs and code, so a session can ask *"has anyone
already learned this?"* and get a semantic answer. **Optional**: without Qdrant,
retrieval degrades to keyword search rather than failing — which is exactly the
kind of silent degradation that has to announce itself, and does.

> **Why three.** SQLite answers *"what is true now?"* fast. Git notes answer
> *"what was believed when this commit was made?"* durably. Qdrant answers
> *"what is relevant to this?"* semantically. One store would do one of those
> well and the others badly.

---

## The harness boundary

Empirica is a CLI and a library — but enforcement lives in the **harness**, via
hooks the AI cannot bypass:

`empirica/plugins/claude-code-integration/hooks/`

| Hook | Does |
|---|---|
| `sentinel-gate` | Classifies every tool call noetic/praxic; blocks praxic before CHECK |
| `pre-compact` / `post-compact` | Persists epistemic state across a context compaction, and restores it after |
| `session-init` / `session-end-postflight` | Bootstraps context at start; closes the measurement loop at end |
| `transaction-enforcer` / `tool-router` | Keeps work inside a transaction; routes tool calls to the right handler |
| `session-monitor-arm` | Arms the mesh listener when peer messaging is configured |
| `subagent-start` / `subagent-stop` | Tracks delegated work as its own measured unit |

**Which harness is a runtime fact**, carried by `EMPIRICA_HARNESS` and read by
every hook. Claude Code is the harness with a full integration today; `empirica
setup` refuses by name rather than configuring a surface another harness never
loads.

Compaction deserves its own note: it is **routine and lossless by design**, not
an emergency. State is externalised continuously — goals, artifacts, git notes,
breadcrumbs — so a compaction swaps active conversation for durable state that
`project-bootstrap` re-grounds on the next turn.

---

## The mesh (optional)

Multiple practitioners — different projects, possibly different people —
coordinating. Two layers with confusingly similar names, which is why
[`docs/architecture/MESSAGING_LAYERS.md`](docs/architecture/MESSAGING_LAYERS.md)
exists to keep them apart:

- **`message-*`** — git notes. No server, works offline, ships in core.
- **`mailbox *`** — Cortex proposals, gated by a human or auto Accept/Decline
  before the target acts.

Rule of thumb: **`message-*` carries words; `mailbox *` carries authority.**
Everything mesh-related is optional — core is fully functional alone.

---

## The tree

| Path | Files | What |
|---|---|---|
| `empirica/core/` | 247 | Transactions, sentinel, calibration, canonical storage, retrieval, cockpit, scanner, loops |
| `empirica/cli/` | 158 | Parsers and command handlers |
| `empirica/data/` | 43 | Repositories and the SQLite schema |
| `empirica/plugins/` | 28 | Harness integration — hooks, skills, agents |
| `empirica/api/` | 13 | HTTP surface (daemon) |
| `empirica/config/` | 13 | Path resolution, settings |
| `tests/` | 410 | ~5,600 tests |

~168k lines of Python.

Largest `core/` subsystems: `cockpit` (26), `canonical` (22), `qdrant` (20),
`post_test` (19), `chat` (10), `bootstrap` (10).

---

## Known tensions

An architecture document that lists no problems is marketing.

- **The CLI surface is too large.** 280 subcommands including aliases. The
  default answer to "add a verb" is *no* — prefer a flag on an existing one.
- **Three storage layers cost consistency.** They are written at different
  moments and can disagree; reconciliation is explicit work, not automatic.
- **Retrieval quality is the ceiling.** Everything downstream of "surface the
  relevant prior artifact" is only as good as that surfacing, and a large
  unpruned graph actively mis-steers it. Hence `/epistemic-gardening`.
- **Calibration is only as honest as the reporting.** The evidence sources are
  ungameable, but the beliefs are self-reported. Divergence between them is a
  signal to work on discipline — never to adjust the numbers.

---

## Where to go next

| You want | Read |
|---|---|
| The full operating model | `/empirica-constitution` (skill) |
| Every architecture topic in depth | [`docs/architecture/`](docs/architecture/) — 46 documents |
| When a practice wakes, and why | [`docs/architecture/TRIGGER_MODEL.md`](docs/architecture/TRIGGER_MODEL.md) |
| Messaging: which layer, and why both | [`docs/architecture/MESSAGING_LAYERS.md`](docs/architecture/MESSAGING_LAYERS.md) |
| Storage internals | [`docs/architecture/CANONICAL_STORAGE.md`](docs/architecture/CANONICAL_STORAGE.md) |
| Using it day to day | [`docs/human/`](docs/human/) |
| Getting started | [`README.md`](README.md) |
