# Empirica System Architecture

**Version:** 1.9.6
**Updated:** 2026-04-30

---

## What is Empirica?

Empirica is a **measurement-first epistemic framework** for AI agents. It provides:

- **13 epistemic vectors** measuring cognitive state (know, uncertainty, engagement, etc.)
- **CASCADE workflow** — structured investigation before action
- **Grounded calibration** — comparing beliefs against deterministic service observations
- **Sentinel gate** — blocks action until sufficient understanding
- **Multi-layer memory** — eidetic (facts), episodic (narratives), prosodic (voice)
- **Instance isolation** — multiple AI instances work without cross-talk
- **Provider agnostic** — works with Claude, Gemini, Qwen, Copilot, Rovo

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  AI AGENT (Claude Code, Gemini CLI, Cursor, etc.)       │
│  Uses Empirica via: MCP Server │ CLI │ Python API       │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────────┐
│  CASCADE WORKFLOW                                        │
│  PREFLIGHT → NOETIC → CHECK → PRAXIC → POSTFLIGHT      │
│       │                  │                    │          │
│   Baseline          Sentinel Gate        Learning       │
│   Assessment        (proceed/investigate) Delta          │
│                                                          │
│  13 Vectors: know, do, context, clarity, coherence,     │
│  signal, density, state, change, completion, impact,    │
│  engagement, uncertainty                                 │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────────┐
│  CORE SYSTEMS                                            │
│                                                          │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────┐  │
│  │ Sentinel     │ │ Calibration  │ │ Goal           │  │
│  │ Orchestrator │ │ Engine       │ │ Orchestration  │  │
│  │ (gate+nudge) │ │ (dual-track) │ │ (tasks)     │  │
│  └──────────────┘ └──────────────┘ └────────────────┘  │
│                                                          │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────┐  │
│  │ Instance     │ │ Subagent     │ │ Persona        │  │
│  │ Isolation    │ │ Governance   │ │ Registry       │  │
│  │ (multi-pane) │ │ (delegation) │ │ (13D vectors)  │  │
│  └──────────────┘ └──────────────┘ └────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────┴──────────────────────────────────┐
│  STORAGE (5 layers)                                      │
│                                                          │
│  HOT:    In-memory (lesson graph, active state)          │
│  WARM:   SQLite (sessions.db — structured data)          │
│  SEARCH: Qdrant (14+ collection types, semantic search)  │
│  COLD:   Git notes (compressed, distributed, immutable)  │
│  LOGS:   JSON (human-readable audit trail)               │
│                                                          │
│  Memory types: eidetic (facts), episodic (narratives),   │
│  calibration (Brier scores), intent (assumptions,        │
│  decisions, edges), workspace (entity graph)              │
└─────────────────────────────────────────────────────────┘
```

---

## Architecture Documents

### Core Workflow

| Document | Description |
|----------|-------------|
| [NOETIC_PRAXIC_FRAMEWORK.md](NOETIC_PRAXIC_FRAMEWORK.md) | Investigation vs action phases, CHECK gate |
| [NOETIC_BATCH_SPEC.md](NOETIC_BATCH_SPEC.md) | Bundled noetic operations (reads + greps + globs + investigate) |
| [COMPLETION_TRACKING.md](COMPLETION_TRACKING.md) | Phase-aware completion semantics |
| [ASSESSMENT_AND_SIGNALING.md](ASSESSMENT_AND_SIGNALING.md) | Vector assessment and signaling |
| [AI_WORKFLOW_AUTOMATION.md](AI_WORKFLOW_AUTOMATION.md) | Automated workflow patterns |

### Sentinel & Calibration

| Document | Description |
|----------|-------------|
| [SENTINEL_ARCHITECTURE.md](SENTINEL_ARCHITECTURE.md) | Gate controller, decision logic, earned autonomy |
| [SENTINEL_CONSTITUTION.md](SENTINEL_CONSTITUTION.md) | Governance principles for measurement |
| [EPP_ARCHITECTURE.md](EPP_ARCHITECTURE.md) | Epistemic Persistence Protocol — calibrated backbone under user pushback |
| [PHASE_AWARE_CALIBRATION.md](PHASE_AWARE_CALIBRATION.md) | Dual-track calibration, evidence sources, Brier scores |
| [SELF_MONITORING.md](SELF_MONITORING.md) | Self-monitoring patterns |

### Storage & Memory

| Document | Description |
|----------|-------------|
| [STORAGE_ARCHITECTURE_COMPLETE.md](STORAGE_ARCHITECTURE_COMPLETE.md) | 5-layer storage system |
| [CANONICAL_STORAGE.md](CANONICAL_STORAGE.md) | Canonical storage patterns |
| [QDRANT_EPISTEMIC_INTEGRATION.md](QDRANT_EPISTEMIC_INTEGRATION.md) | 14+ Qdrant collection types |
| [MULTI_PROJECT_STORAGE.md](MULTI_PROJECT_STORAGE.md) | Cross-project data management |
| [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) | Hot/warm/cold memory layers, eidetic + episodic + prosodic |
| [EPISTEMIC_STATE_COMPLETE_CAPTURE.md](EPISTEMIC_STATE_COMPLETE_CAPTURE.md) | Full state capture |
| [noetic-rag-architecture.md](noetic-rag-architecture.md) | RAG with epistemic awareness |

### Multi-Agent & Orchestration

| Document | Description |
|----------|-------------|
| [EPISTEMIC_AGENT_ARCHITECTURE.md](EPISTEMIC_AGENT_ARCHITECTURE.md) | Multi-agent coordination |
| [SUBAGENT_EPISTEMIC_ASSESSMENT.md](SUBAGENT_EPISTEMIC_ASSESSMENT.md) | Subagent persona decomposition and Brier scoring |
| [HANDOFF_SYSTEM.md](HANDOFF_SYSTEM.md) | Agent-to-agent knowledge transfer |
| [EPISTEMIC_BUS.md](EPISTEMIC_BUS.md) | Event bus for epistemic state |

### Instance Isolation

| Document | Description |
|----------|-------------|
| [instance_isolation/](instance_isolation/) | Full instance isolation architecture |
| [instance_isolation/ARCHITECTURE.md](instance_isolation/ARCHITECTURE.md) | File taxonomy, resolution priority |
| [instance_isolation/CLAUDE_CODE.md](instance_isolation/CLAUDE_CODE.md) | Claude Code specific patterns |
| [instance_isolation/MCP_AND_CLI.md](instance_isolation/MCP_AND_CLI.md) | MCP and CLI integration |
| [instance_isolation/KNOWN_ISSUES.md](instance_isolation/KNOWN_ISSUES.md) | Bug history (11.1-11.30+) |

### Integration

| Document | Description |
|----------|-------------|
| [claude-code-symbiosis.md](claude-code-symbiosis.md) | Claude Code hook integration |
| [COCKPIT.md](COCKPIT.md) | Cockpit UI — sentinel status, loops, listeners, instances, notify dispatcher |
| [DISPATCH_BUS.md](DISPATCH_BUS.md) | Typed cross-instance protocol (terminal/cowork/desktop/cortex) |
| [NOTIFY.md](NOTIFY.md) | Notification dispatcher — pluggable backends (stdout, log, ntfy) |
| [SYNC_ARCHITECTURE.md](SYNC_ARCHITECTURE.md) | Sync architecture |
| [SUPPORTING_COMPONENTS.md](SUPPORTING_COMPONENTS.md) | Supporting subsystems |
| [separation-of-concerns.md](separation-of-concerns.md) | What goes where |

---

## Key Concepts

### CASCADE Workflow
```
PREFLIGHT → [NOETIC: investigate] → CHECK → [PRAXIC: act] → POSTFLIGHT
```
- PREFLIGHT opens a measurement window (transaction)
- CHECK gates the noetic→praxic transition
- POSTFLIGHT closes the window and captures learning delta
- POST-TEST automatically collects grounded evidence

### 13 Epistemic Vectors

| Category | Vectors |
|----------|---------|
| Foundation | know, do, context |
| Comprehension | clarity, coherence, signal, density |
| Execution | state, change, completion, impact |
| Meta | engagement, uncertainty |

### Dual-Track Calibration
- **Track 1 (self-referential):** PREFLIGHT→POSTFLIGHT delta = learning measurement
- **Track 2 (grounded):** POSTFLIGHT beliefs vs service observations = belief calibration
- 8 evidence sources: pytest, git, code quality, goals, artifacts, issues, sentinel, codebase model

### Instance Isolation
Multiple AI instances in tmux panes, X11 windows, or macOS terminals work
without cross-talk. Instance-specific files track project→session→transaction bindings.

---

## Related Documentation

- [docs/reference/](../reference/) — API reference, configuration, environment variables
- [docs/guides/](../guides/) — Workflow guides (tmux, project switching)
- [docs/human/](../human/) — End-user and developer documentation
