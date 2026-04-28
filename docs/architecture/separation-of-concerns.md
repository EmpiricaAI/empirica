# Empirica Architecture: Separation of Concerns

**Version:** 1.0.0
**Status:** AUTHORITATIVE
**Last Updated:** 2026-01-02

---

## Overview

Empirica uses multiple context injection layers, each with a specific purpose. This document defines what belongs where to prevent duplication, conflation, and drift.

---

## The Two Axes: Workflow vs Thinking

**CRITICAL DISTINCTION:**

### Workflow Phases (Mandatory, Structural)
```
PREFLIGHT ────────► CHECK ────────► POSTFLIGHT ────────► POST-TEST
   │                  │                  │                    │
   │                  │                  │                    │
 Baseline         Sentinel            Learning           Grounded
 Assessment        Gate               Delta            Verification
```

The workflow is **mandatory**. Every significant task must have:
- **PREFLIGHT**: Baseline epistemic assessment before work
- **CHECK**: Gate controlled by Sentinel (proceed/investigate)
- **POSTFLIGHT**: Measure learning delta after work

### Thinking Phases (AI-Chosen, Within Workflow)
```
┌─────────────────────────────────────────────┐
│             THINKING PHASES                 │
│  (AI chooses what's needed within workflow) │
├─────────────────────────────────────────────┤
│                                             │
│   NOETIC                    PRAXIC          │
│   (High entropy)            (Low entropy)   │
│   ─────────────            ──────────────   │
│   - Investigate            - Execute        │
│   - Explore                - Write          │
│   - Hypothesize            - Commit         │
│   - Search                 - Deploy         │
│   - Read                   - Test           │
│   - Question               - Implement      │
│                                             │
└─────────────────────────────────────────────┘
```

The AI **chooses** when to use noetic vs praxic thinking. CHECK gates the transition from uncertain (noetic-heavy) to confident (praxic-ready).

### How They Interact
```
PREFLIGHT → [noetic/praxic as needed] → CHECK → [noetic/praxic as needed] → POSTFLIGHT → POST-TEST
    │                                      │                                                │
    │                                      │                                                │
    └── AI may use noetic heavily          └── If "investigate": more noetic         Grounded
        if uncertain                           If "proceed": can go praxic         Verification
```

---

## Context Injection Layers

### Layer 1: System Prompt (CLAUDE.md)
**Always loaded. Identity and principles.**

| Belongs Here | Does NOT Belong Here |
|--------------|---------------------|
| AI identity (ai-id, model) | How-to tutorials |
| Bias corrections (+0.10 uncertainty, -0.05 know) | Full command reference |
| Readiness gate formula (know ≥0.70, uncertainty ≤0.35) | Detailed examples |
| Workflow diagram (PRE→CHECK→POST→POST-TEST) | Deep-dive explanations |
| Thinking phase definitions (noetic/praxic) | Anti-pattern catalogs |
| Self-improvement protocol | Calibration pattern examples |
| Documentation policy | Version history |
| Dynamic context pointers (what's injected where) | |
| Storage architecture (where data goes) | |

**Token budget:** ~1KB (lean and essential)

**Update frequency:** Rarely (when core architecture changes)

---

### Layer 2: Skill (SKILL.md + references/)
**Loaded on trigger. Self-sufficient how-to reference.**

| Belongs Here | Does NOT Belong Here |
|--------------|---------------------|
| Complete command reference with examples | AI identity |
| Decision frameworks (when to use what) | Bias correction formulas |
| Anti-patterns with explanations | Self-improvement protocol |
| Calibration patterns and examples | What gets injected dynamically |
| Workflow patterns (Quick Task, Investigation, Complex) | |
| Goals and subtask management | |
| Epistemic subagent spawning | |
| Handoff types (Investigation, Complete, Planning) | |
| Semantic search triggers | |
| Hook integration (PreToolCall sentinel gate, SessionStart/End) | |

**Token budget:** ~2KB SKILL.md + ~5KB per reference file

**Update frequency:** When features change (requires rebuild for bridge extraction)

**Progressive disclosure:**
1. Metadata only (always available for matching)
2. SKILL.md body (on trigger phrase match)
3. references/ files (on demand)

---

### Layer 3: Project Bootstrap
**Loaded at session start. Project-specific context.**

| Belongs Here | Does NOT Belong Here |
|--------------|---------------------|
| Active goals and subtasks | How to use Empirica |
| Recent findings (high-impact learnings) | Bias corrections |
| Open unknowns (unresolved questions) | Workflow patterns |
| Dead ends (approaches that failed) | Command reference |
| Project configuration | |
| Git state | |
| Prior session handoffs | |

**Token budget:** ~800 tokens (fast), ~2000 tokens (full)

**Update frequency:** Every session (dynamic, project-specific)

---

### Layer 4: MCO (Meta-Agent Configuration Objects)
**Loaded by Sentinel. Threshold and behavior configuration.**

| Belongs Here | Does NOT Belong Here |
|--------------|---------------------|
| CASCADE style profiles (default, exploratory, rigorous) | Workflow structure |
| Persona definitions (researcher, implementer, reviewer) | Command syntax |
| Threshold configurations | Examples |
| Model-specific bias profiles | Tutorials |
| Confidence weight formulas | |
| Drift detection thresholds | |
| Ask-before-investigate rules | |
| Goal scoping rules | |

**Token budget:** ~200-400 tokens (targeted load)

**Update frequency:** When calibration data suggests changes

---

### Layer 5: Extracted Skill Config (meta-agent-config.yaml)
**Loaded via epistemic bootstrap. Compressed decision frameworks.**

| Belongs Here | Does NOT Belong Here |
|--------------|---------------------|
| Decision frameworks (extracted) | Full explanations |
| Anti-patterns (id + description + reason) | Code examples |
| Cost models | Tutorials |
| Key commands (compressed) | Detailed how-to |
| Doc references (pointers only) | |

**Token budget:** 88% reduction from skills (~0.5KB per domain)

**Update frequency:** When skills are updated (run extractor)

---

### Layer 6: Semantic Search (Qdrant Vector Store)
**Queried on demand. Distributed memory across sessions.**

```
┌────────────────────────────────────────────────────────────┐
│                    QDRANT COLLECTIONS                      │
├────────────────────────────────────────────────────────────┤
│  project_{id}_docs      │ Documentation embeddings         │
│  project_{id}_memory    │ Findings, unknowns, dead ends    │
│  project_{id}_epistemics│ PREFLIGHT→POSTFLIGHT deltas      │
│  global_learnings       │ Cross-project high-impact items  │
│  personas               │ Epistemic agent profiles         │
└────────────────────────────────────────────────────────────┘
```

| Belongs Here | Does NOT Belong Here |
|--------------|---------------------|
| Findings (auto-embedded on log) | Static config |
| Unknowns (auto-embedded on log) | Workflow rules |
| Dead ends with why-failed | Identity info |
| Resolved unknowns with resolution | Command syntax |
| High-impact learnings (impact ≥0.7) | |
| Epistemic trajectories | |

**Feedback Loop:**
1. **Write path:** `finding-log`, `unknown-log` → auto-embed to `project_{id}_memory`
2. **Read path:** `project-search --task "query"` → semantic search → relevant context
3. **Bootstrap path:** `project-bootstrap --global` → `search_global()` → cross-project learnings

**Token budget:** Variable (search returns top-k results, typically 5-10)

**Update frequency:** Real-time (every breadcrumb logged)

**Current Status:**
- ✅ `project_{id}_memory` - Working (findings/unknowns auto-embedded)
- ✅ `project_{id}_docs` - Working (via project-embed)
- ✅ `project_{id}_epistemics` - Working (POSTFLIGHT deltas)
- ✅ `global_learnings` - Collection initialized (2026-01-02)
- ✅ `personas` - Collection initialized, 8 pre-defined personas embedded (2026-01-02)

**Note:** Current embeddings use local hash-based provider (limited semantic matching).
For full semantic capability: `export EMPIRICA_EMBEDDINGS_PROVIDER=openai`

**Persona Flow (Planned but not wired):**
```
investigation_branches (SQLite)
         │
         │ Sentinel picks winners (is_winner=TRUE)
         ▼
extract_persona_from_loop_tracker()     ← emerged_personas.py
         │
         ├─► .empirica/personas/*.yaml  ← EmergedPersonaStore (file-based)
         │
         └─► Qdrant personas collection ← persona_registry.py (NOT WIRED)
                    │
                    │ Future task similarity search
                    ▼
           Sentinel suggests priors based on similar past successes
```

---

## Epistemic Subagent Spawning

When spawning epistemic subagents for parallel investigation:

```bash
# Spawn investigation agent
empirica agent-spawn --session-id <ID> \
  --task "Investigate authentication patterns" \
  --persona researcher \
  --cascade-style exploratory

# Agent reports back
empirica agent-report --agent-id <AGENT_ID> \
  --findings '["JWT used", "No refresh token"]' \
  --unknowns '["Token storage location"]'

# Aggregate results
empirica agent-aggregate --session-id <ID>
```

**Where this belongs:**
- Skill: Full command reference and examples
- System prompt: Brief mention that subagents exist
- MCO: Persona and cascade style definitions

---

## Sentinel as Gatekeeper

Sentinel controls the CHECK gate:

```
        PREFLIGHT
            │
            ▼
    ┌───────────────┐
    │   AI Works    │◄─────────────────────┐
    │  (noetic or   │                      │
    │   praxic)     │                      │
    └───────┬───────┘                      │
            │                              │
            ▼                              │
    ┌───────────────┐                      │
    │   SENTINEL    │                      │
    │   (CHECK)     │                      │
    │               │                      │
    │  Evaluates:   │                      │
    │  - Vectors    │     "investigate"    │
    │  - Findings   │──────────────────────┘
    │  - Unknowns   │
    │  - MCO rules  │
    │               │
    └───────┬───────┘
            │ "proceed"
            ▼
    ┌───────────────┐
    │   AI Works    │
    │  (praxic)     │
    └───────┬───────┘
            │
            ▼
        POSTFLIGHT
```

**Sentinel uses:**
- MCO cascade styles (thresholds)
- MCO model profiles (bias corrections)
- Current epistemic vectors
- Findings and unknowns from session

**Enforcement layers (as of 2026-01-08):**
- **PreToolCall hooks**: `sentinel-gate.py` blocks Edit/Write/Bash without valid CHECK
- **SessionStart hooks**: `session-init.py` auto-creates session + bootstrap for new conversations
- **SessionEnd hooks**: `session-end-postflight.py` auto-captures POSTFLIGHT
- **MCP**: `EMPIRICA_EPISTEMIC_MODE` — **Removed in 1.8.14.** MCP epistemic middleware was replaced by Sentinel hooks

---

## Decision Tree: Where Does It Go?

```
Is it about AI identity or core principles?
├── YES → System Prompt (CLAUDE.md)
└── NO
    │
    Is it a how-to or reference for using Empirica?
    ├── YES → Skill (SKILL.md or references/)
    └── NO
        │
        Is it project-specific context (goals, findings)?
        ├── YES → Project Bootstrap
        └── NO
            │
            Is it a threshold, persona, or calibration config?
            ├── YES → MCO
            └── NO
                │
                Is it compressed skill knowledge for bootstrapping?
                ├── YES → meta-agent-config.yaml
                └── NO
                    │
                    Is it a learning that should persist across sessions?
                    ├── YES → Qdrant (auto-embedded via finding-log/unknown-log)
                    └── NO → Consider if it needs to be stored at all
```

---

## Migration Guide

### Moving from Current State

1. **CLAUDE.md cleanup:**
   - Remove detailed command examples → Skill
   - Remove calibration pattern examples → Skill
   - Keep: identity, bias corrections, readiness gate, workflow diagram

2. **SKILL.md enrichment:**
   - Add bias corrections (yes, duplicate for self-sufficiency)
   - Add noetic/praxic definitions with examples
   - Add epistemic subagent section
   - Add handoff types
   - Add semantic search triggers

3. **Bridge extraction:**
   - Run `empirica skill-extract` after skill updates
   - Verify 80-90% reduction maintained

---

## Validation Checklist

Before committing changes, verify:

- [ ] System prompt ≤1KB
- [ ] SKILL.md ≤2KB, references/ ≤5KB each
- [ ] No how-to in system prompt
- [ ] No identity in skill
- [ ] Workflow (PRE→CHECK→POST→POST-TEST) mentioned in both (system = authority, skill = reference)
- [ ] Thinking phases (noetic/praxic) explained in skill
- [ ] Bias corrections in skill (for self-sufficiency)
- [ ] Subagent spawning in skill
- [ ] meta-agent-config.yaml regenerated if skill changed

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.1.0 | 2026-01-02 | Added Layer 6: Semantic Search (Qdrant), updated decision tree |
| 1.0.0 | 2026-01-02 | Initial separation of concerns spec |
