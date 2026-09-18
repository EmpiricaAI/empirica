# Noetic RAG Architecture

## Overview

Noetic RAG (Retrieval-Augmented Generation for epistemic systems) defines the formal contracts for what gets retrieved versus stored at each CASCADE phase. This creates a closed-loop epistemic system where AI learning is persistent and measurable.

## Core Principle

```
RETRIEVAL PHASES          │          STORAGE PHASE
(inject context)          │          (extract from conversation)
                          │
PREFLIGHT ─────► CHECK ───┼─────────► POSTFLIGHT
                          │
```

**PREFLIGHT and CHECK** are *read* operations — they inject context to inform the AI before and during work.

**POSTFLIGHT** is a *write* operation — it extracts learnings from the conversation and persists them for future retrieval.

---

## Memory Types

| Type | What It Stores | Confidence Model | Decay Model |
|------|----------------|------------------|-------------|
| **Eidetic Facts** | Stable facts ("API uses OAuth2", "Config in /etc") | Confidence score (0.0-1.0), grows with confirmation | None (persists until contradicted) |
| **Episodic Narratives** | Session arcs, decisions, investigations | N/A | Time-based (recency_weight decays) |
| **Patterns/Lessons** | Procedural knowledge ("How to X") | source_confidence | Immune decay (anti-confirmation) |
| **Dead-ends** | Failed approaches ("Tried X, failed because Y") | N/A | Never decay |
| **Capability Beliefs** | Self-assessment accuracy (grounded calibration) | Bayesian posterior | Continuous update |
| **Learning Trajectory** | Session deltas (PREFLIGHT→POSTFLIGHT) | N/A | Historical record only |

---

## Phase Contracts

### PREFLIGHT (Retrieval)

**Purpose:** Establish baseline epistemic state, inject relevant context for the upcoming work.

**Retrieves:**
1. **Eidetic Facts** — High-confidence facts relevant to task context
   - Source: `search_eidetic(query=task_context, min_confidence=0.7)`
   - Injected as: Background knowledge the AI can rely on

2. **Episodic Narratives** — Recent session arcs for continuity
   - Source: `search_episodic(query=task_context, apply_recency_decay=True)`
   - Injected as: "Last time we worked on X, we discovered Y"

3. **Patterns/Lessons** — Procedural knowledge for this type of work
   - Source: `retrieve_task_patterns(task_context).lessons`
   - Injected as: "For this type of task, remember to Z"

4. **Dead-ends** — Approaches NOT to try
   - Source: `retrieve_task_patterns(task_context).dead_ends`
   - Injected as: "Don't try X because it failed due to Y"

5. **Calibration Warnings** — Past calibration gaps for similar tasks
   - Source: `retrieve_task_patterns(task_context).calibration_warnings`
   - Injected as: "For similar tasks, you overestimated know by 0.3"

**Temporal Awareness:**
- Large gap since last session → deeper episodic retrieval (re-orientation needed)
- Small gap → lighter retrieval (continuation)
- Context gap detected from: `current_time - last_session.timestamp`

**Stores:** Nothing (pure retrieval phase)

---

### CHECK (Retrieval + Validation)

**Purpose:** Validate current approach against known patterns, gate readiness.

**Retrieves:**
1. **Dead-end Warnings** — Does current approach match known failures?
   - Source: `check_against_patterns(approach).dead_end_matches`
   - If match found: Block proceed, suggest alternative

2. **Mistake Patterns** — Vector patterns that historically led to mistakes
   - Source: `check_against_patterns(approach, vectors).mistake_risk`
   - High uncertainty + low know = historical mistake pattern

3. **Calibration Bias** — Systematic bias for this type of work
   - Source: `check_against_patterns(approach).calibration_bias`
   - "For similar tasks, you consistently overestimate completion"

**Computes:**
- Readiness gate: `corrected_know >= 0.70 AND corrected_uncertainty <= 0.35`
- Thresholds from: `_check_load_dynamic_thresholds()` in `_workflow_check.py` — Brier-score calibration moves the defaults (NOT learning trajectory!)
- Returns: `proceed` or `investigate`

**Stores:** Nothing (validation phase, no persistence)

---

### POSTFLIGHT (Storage)

**Purpose:** Extract learnings from the conversation, persist for future retrieval.

**Stores:**

1. **Findings → Eidetic Facts**
   - Source: Explicit `finding-log` calls during session
   - Destination: `embed_eidetic(content=finding, confidence=0.5+)`
   - Confidence grows if finding confirms existing fact

2. **Session Arc → Episodic Narrative**
   - Source: Auto-generated from session vectors + goal completion
   - Destination: `embed_episodic(narrative=arc, episode_type="session_arc")`
   - Includes: learning_delta, outcome, key_moments

3. **Capability Belief → Grounded Calibration**
   - Source: `run_grounded_verification(postflight_vectors)`
   - Compares: Belief vectors vs deterministic service observations
   - Updates: `grounded_beliefs` table, exports to `.breadcrumbs.yaml`

4. **Learning Trajectory → Analytics**
   - Source: PREFLIGHT vectors vs POSTFLIGHT vectors
   - Destination: `learning_trajectory` section in `.breadcrumbs.yaml`
   - Purpose: Track learning over time (NOT used for bias correction)
   - Note: This is informational only — does NOT feed the Sentinel

5. **Unknowns → Open Questions**
   - Source: Explicit `unknown-log` calls during session
   - Destination: Memory collection, retrievable in future PREFLIGHT

6. **Dead-ends → Approach Warnings**
   - Source: Explicit `deadend-log` calls when approach fails
   - Destination: Memory collection, retrieved in CHECK to block repeat attempts

**Triggers immune decay:** If finding contradicts existing lesson, lesson confidence decays.

---

## Two Bayesian Tracks (Critical Distinction)

### 1. Learning Trajectory (PREFLIGHT → POSTFLIGHT)

```
PREFLIGHT vectors ──────────────────► POSTFLIGHT vectors
     │                                       │
     │              "What did I learn?"      │
     └───────────── session delta ───────────┘
```

**Measures:** Learning within an epistemic transaction
**Used for:** Analytics, dashboards, learning velocity metrics
**NOT used for:** Sentinel bias correction (this was the bug!)

### 2. Capability Belief (Self-assessment → Evidence)

```
POSTFLIGHT self-assessment ──────────► Objective evidence
          │                                    │
          │    "How accurate was I?"           │
          └────── calibration gap ─────────────┘
```

**Measures:** Belief calibration (divergence from service observations)
**Used for:** CHECK's dynamic readiness thresholds and PREFLIGHT's `calibration_adjustments` (`BayesianBeliefManager.get_calibration_adjustments`)
**Sources:** Test results, git metrics, artifact counts, goal completion

---

## Adaptive Retrieval Depth

```python
context_gap = current_time - last_session.timestamp

if context_gap > 4_HOURS:
    # Human was AFK, AI needs re-orientation
    retrieval_depth = "deep"
    # More episodic narratives, full context recovery
elif context_gap > 30_MINUTES:
    retrieval_depth = "moderate"
    # Standard retrieval
else:
    retrieval_depth = "light"
    # Continuation, minimal retrieval needed
```

---

## Storage Layers

| Layer | Purpose | Location |
|-------|---------|----------|
| **HOT** | Active session state | Memory |
| **WARM** | Persistent structured data | `.empirica/sessions/sessions.db` (SQLite) |
| **SEARCH** | Semantic retrieval | Qdrant (4 collections per project) |
| **COLD** | Archival + versioned | Git notes, `.empirica/lessons/*.yaml` |

### Qdrant Collections (per project)

1. `project_{id}_docs` — Documentation embeddings
2. `project_{id}_memory` — Findings, unknowns, dead-ends, mistakes
3. `project_{id}_eidetic` — Stable facts with confidence
4. `project_{id}_episodic` — Session narratives with temporal decay
5. `project_{id}_calibration` — Grounded verification records

---

## Summary

| Phase | Role | What Flows |
|-------|------|------------|
| **PREFLIGHT** | Retrieval | Facts, narratives, patterns, dead-ends → AI context |
| **CHECK** | Validation | Warnings, mistakes, calibration → gate decision |
| **POSTFLIGHT** | Storage | Findings, arcs, beliefs, trajectory → persistence |

**The key insight:** PREFLIGHT/CHECK inject context to help the AI work better. POSTFLIGHT extracts value from the conversation to improve future PREFLIGHT/CHECK. This creates a closed-loop system where AI learning compounds over time.
