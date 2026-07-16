# Empirica System Architecture

**Version:** 1.11.10 | **Updated:** 2026-06-08 | **Status:** Production

---

## What is Empirica?

Empirica is a **measurement-first epistemic framework** for AI agents — a CLI + plugin layer that wraps any AI workflow with a measured transaction loop (PREFLIGHT → CHECK → POSTFLIGHT), enforces a noetic / praxic firewall before actions, and grounds the agent's self-assessment against deterministic evidence (tests, lint, git, artifact logs).

It's provider-agnostic (Claude Code, Gemini CLI, Cursor, Copilot, Codex, Roo Code, etc.) and ships with hooks for whichever platform's available.

---

## How to read this folder

41 architecture docs accumulated over a year of design iteration. Reading them all is a waste of context. The tiering below tells you which ones load-bearing surfaces actually depend on right now, and which are deep-dives you should pull on demand.

If you're an AI agent (or just exploring), use **`empirica docs-explain --topic "<concept>"`** before reading directly — it does keyword retrieval across all docs and surfaces the relevant passage.

| You're | Read |
|---|---|
| An AI loading context at session start | The 6 **Tier 1** docs below. Anything else only when you hit a specific surface. |
| A developer integrating Empirica into a new harness | Tier 1 + the **Tier 2 → Identity & mesh** group. |
| Debugging a specific subsystem | `docs-explain --topic "<that subsystem>"`, then drill into Tier 2. |
| Curious about a deprecated/exploratory direction | **Tier 3** at the bottom — these don't reflect current shipping state. |

---

## Tier 1 — Canonical (load first)

The mental model. Everything else hangs off these.

| Doc | What it covers |
|---|---|
| [`AI_ID_AS_ANCHOR.md`](AI_ID_AS_ANCHOR.md) | The practice model — `ai_id` (NOT cwd) is THE anchor for cross-machine identity. Practitioner vs practice distinction. Resolution chain. |
| [`NOETIC_PRAXIC_FRAMEWORK.md`](NOETIC_PRAXIC_FRAMEWORK.md) | The thinking-phases mental model. Noetic (investigate) → CHECK (gate) → Praxic (act). Why CHECK gates the transition but does NOT end the transaction. |
| [`SENTINEL_ARCHITECTURE.md`](SENTINEL_ARCHITECTURE.md) | The gate controller. PreToolUse hook that blocks praxic actions until CHECK passes. Per-domain criticality. Read this before assuming you know how a tool call gets blocked. |
| [`EPP_ARCHITECTURE.md`](EPP_ARCHITECTURE.md) | Epistemic Persistence Protocol — how state survives compaction boundaries. PREFLIGHT/POSTFLIGHT as measurement windows. |
| [`STORAGE_ARCHITECTURE_COMPLETE.md`](STORAGE_ARCHITECTURE_COMPLETE.md) | Four-layer storage: HOT (in-session) / WARM (SQLite) / SEARCH (Qdrant) / COLD (git notes + YAML). Where every artifact lives and why. |
| [`separation-of-concerns.md`](separation-of-concerns.md) | The overall layering — CLI / Core / Data / Plugins / Hooks. Read before adding to the wrong layer. |

---

## Tier 2 — Domain deep-dives (consult on demand)

Read these when you're actually working in the area. Grouped by concern.

### Identity & mesh

| Doc | Surface |
|---|---|
| [`EVENT_LISTENER.md`](EVENT_LISTENER.md) | Push-primary wake bridge — `empirica loop listen`, ntfy stream, supervisor pattern. |
| [`MULTI_PROJECT_STORAGE.md`](MULTI_PROJECT_STORAGE.md) | Workspace + per-project SQLite. `global_projects` + `entity_registry` mirrors. |
| [`DISPATCH_BUS.md`](DISPATCH_BUS.md) | Typed cross-instance protocol (terminal / desktop / cortex coordination). |

### Governance & calibration

| Doc | Surface |
|---|---|
| [`SUBAGENT_EPISTEMIC_ASSESSMENT.md`](SUBAGENT_EPISTEMIC_ASSESSMENT.md) | Subagent governance — bounded autonomy, delegated tool counting, parent transaction inheritance. |
| [`PHASE_AWARE_CALIBRATION.md`](PHASE_AWARE_CALIBRATION.md) | Phase-aware evidence collection — work_type-weighted vector categories. |
| [`COMPLETION_TRACKING.md`](COMPLETION_TRACKING.md) | How "done" is decided per phase (noetic vs praxic). |
| [`ASSESSMENT_AND_SIGNALING.md`](ASSESSMENT_AND_SIGNALING.md) | Sentinel signal generation — how vector deltas turn into behavioral guidance. |
| [`UNIVERSAL_GOVERNANCE_PATTERN.md`](UNIVERSAL_GOVERNANCE_PATTERN.md) | The reusable governance template — same gate logic at every meta-layer. |
| [`DISCIPLINE_IS_SPEED.md`](DISCIPLINE_IS_SPEED.md) | The philosophy: why epistemic workflow makes AI work faster, not slower. |

### Memory & retrieval

| Doc | Surface |
|---|---|
| [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md) | Eidetic (facts) / episodic (narratives) / prosodic (voice). |
| [`QDRANT_EPISTEMIC_INTEGRATION.md`](QDRANT_EPISTEMIC_INTEGRATION.md) | Qdrant integration — collections, embedding pipeline. *(Dated 2025-12-19 — read with awareness.)* |
| [`GRAPH_TEMPORAL_LAYER.md`](GRAPH_TEMPORAL_LAYER.md) | Edge declaration + commit-context walker. |
| [`CANONICAL_STORAGE.md`](CANONICAL_STORAGE.md) | Foundation layer of the four-layer model. Reference detail; the complete picture is in `STORAGE_ARCHITECTURE_COMPLETE.md`. |
| [`SYNC_ARCHITECTURE.md`](SYNC_ARCHITECTURE.md) | Daemon sync pipeline — git-notes ↔ cortex. |
| [`EPISTEMIC_STATE_COMPLETE_CAPTURE.md`](EPISTEMIC_STATE_COMPLETE_CAPTURE.md) | What gets captured per transaction + how it survives compaction. |
| [`EPISTEMIC_BUS.md`](EPISTEMIC_BUS.md) | Event flow between subsystems. |

### Surfaces & integrations

| Doc | Surface |
|---|---|
| [`COCKPIT.md`](COCKPIT.md) | TUI cockpit — instance discovery, multi-Claude session tracking. |
| [`CHAT.md`](CHAT.md) | `empirica chat` surface. |
| [`NOTIFY.md`](NOTIFY.md) | Notify dispatcher — ntfy / log / stdout backends. |
| [`HANDOFF_SYSTEM.md`](HANDOFF_SYSTEM.md) | Cross-session handoff via empirica's handoff verbs. |
| [`AI_WORKFLOW_AUTOMATION.md`](AI_WORKFLOW_AUTOMATION.md) | How empirica wraps an AI loop — hooks, MCP, CLI entry points. |

### Operations & quality

| Doc | Surface |
|---|---|
| [`CI_CD.md`](CI_CD.md) | The release pipeline, channels, version sweep. |
| [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md) | Compliance + security-audit verb shape. |
| [`SELF_MONITORING.md`](SELF_MONITORING.md) | Drift detection + memory gap detection. |
| [`SERVICES_SCANNER.md`](SERVICES_SCANNER.md) | Phase 1 deterministic inventory of running AI services. |
| [`SUPPORTING_COMPONENTS.md`](SUPPORTING_COMPONENTS.md) | Index of secondary surfaces (helpers, utilities). |
| [`NOETIC_BATCH_SPEC.md`](NOETIC_BATCH_SPEC.md) | `noetic-batch` primitive — when to use it, when NOT to. |

---

## Tier 3 — Historical / proposal / aspirational

These predate current shipping state, propose things that didn't ship as written, or capture conceptual framing for blog/marketing rather than the actual implementation. Read for *why* the design choices were made; **do not use as a reference for current behavior**.

| Doc | Why it's here |
|---|---|
| [`claude-code-symbiosis.md`](claude-code-symbiosis.md) | Conceptual essay on the Empirica + Claude Code symbiosis. Predates the practice model. |
| [`noetic-rag-architecture.md`](noetic-rag-architecture.md) | Early Noetic-as-RAG framing. Superseded by the current `noetic-batch` + `docs-explain` separation. |
| [`SENTINEL_CONSTITUTION.md`](SENTINEL_CONSTITUTION.md) | Aspirational framing for the Sentinel — the shipped behavior is in `SENTINEL_ARCHITECTURE.md`. |

*(Candidates for `docs/architecture/_archive/` — pending a future cleanup pass.)*

---

## Finding a specific concept

Don't grep for it manually. Use:

```bash
# Topic lookup
empirica docs-explain --topic "epistemic vectors"
empirica docs-explain --topic "cortex-mailbox"

# Question answering
empirica docs-explain --question "How does CHECK gate praxic actions?"

# Target audience
empirica docs-explain --topic "<concept>" --audience ai
```

The retrieval reads across this folder, `docs/human/`, `docs/reference/`, and `docs/guides/`. Faster + more reliable than guessing which file the answer lives in.
