# Epistemic Agent Architecture - Turtle Stack

**Version:** 1.0
**Date:** 2026-01-02
**Status:** Active
**Goal ID:** 137b6c60-76ef-46f8-a6cc-043753162b78

---

## The Turtle Principle

> "Same epistemic rules apply at every meta-layer."

The Epistemic Agent architecture follows a recursive self-similar pattern. Each layer has its own epistemic transaction workflow (PREFLIGHT → CHECK → POSTFLIGHT), maintains its own epistemic vectors, and can spawn agents at lower layers.

```
┌──────────────────────────────────────────────────────────────────────┐
│                      LAYER 4: Meta-Orchestrator                       │
│  (Future: Cross-org federation, reputation networks)                 │
│  Epistemic State: Federation-level trust vectors                     │
└──────────────────────────────────────────────────────────────────────┘
                                   ↓
┌──────────────────────────────────────────────────────────────────────┐
│                      LAYER 3: Sentinel                               │
│  (Current: Aggregate, arbitrate, merge branches)                     │
│  Epistemic State: Session-level vectors, merge scoring               │
│  Commands: agent-aggregate, assess-state                             │
└──────────────────────────────────────────────────────────────────────┘
                                   ↓
┌──────────────────────────────────────────────────────────────────────┐
│                      LAYER 2: Epistemic Agent                        │
│  (Current: Spawn, investigate, report)                               │
│  Epistemic State: Branch-level preflight/postflight                  │
│  Commands: agent-spawn, agent-report, investigate-multi              │
└──────────────────────────────────────────────────────────────────────┘
                                   ↓
┌──────────────────────────────────────────────────────────────────────┐
│                LAYER 1: Epistemic Transaction Workflow                │
│  (Foundation: PREFLIGHT → CHECK → POSTFLIGHT)                        │
│  Epistemic State: 13 vectors (know, uncertainty, context, etc.)      │
│  See: ../human/end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md           │
│  Commands: preflight-submit, check-submit, postflight-submit         │
└──────────────────────────────────────────────────────────────────────┘
                                   ↓
┌──────────────────────────────────────────────────────────────────────┐
│                      LAYER 0: Breadcrumb Trail                       │
│  (Base: Findings, unknowns, dead ends)                               │
│  Epistemic State: Individual observations                            │
│  Commands: finding-log, unknown-log, deadend-log                     │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Agent Command Reference

### agent-spawn

Spawns an epistemic agent with tracked investigation branch.

```bash
empirica agent-spawn \
  --session-id <session-id> \
  --task "Investigate security implications of OAuth flow" \
  --persona security \
  --context "Focus on token storage patterns" \
  --output json
```

**Returns:**
- `branch_id`: Unique ID for tracking this investigation
- `preflight_vectors`: Agent's initial epistemic state
- `agent_prompt`: Full prompt with embedded turtle awareness

**What happens:**
1. Creates investigation branch in DB
2. Applies persona-specific epistemic priors
3. Generates prompt with epistemic transaction instructions
4. Returns branch_id for later reporting

### agent-report

Reports agent's postflight results after investigation completes.

```bash
empirica agent-report \
  --branch-id <branch-id> \
  --postflight '{"findings": [...], "postflight_vectors": {...}, "summary": "..."}' \
  --output json
```

**Merge Score Calculation:**
```
merge_score = (learning_delta × quality × confidence) / cost_penalty

where:
  learning_delta = avg(|postflight[v] - preflight[v]|) for all vectors
  quality = avg(finding impacts)
  confidence = 1 - postflight.uncertainty
  cost_penalty = 1 + (action_count × 0.1)
```

**Side effects:**
- Embeds findings to Qdrant for semantic search
- Calculates learning delta (epistemic growth)
- Updates branch status to 'completed'

### agent-aggregate

Aggregates results from multiple agents (Sentinel role).

```bash
empirica agent-aggregate \
  --session-id <session-id> \
  --round 1 \
  --output json
```

**Aggregation strategies:**
- `epistemic-score`: Weight by merge_score (default)
- `consensus`: Majority findings
- `all`: Include everything

### agent-export

Exports an epistemic agent as a shareable JSON package.

```bash
empirica agent-export \
  --branch-id <branch-id> \
  --register \
  --output json
```

**Package structure:**
```json
{
  "format_version": "1.0",
  "agent_id": "branch-id",
  "epistemic_profile": {
    "preflight_vectors": {...},
    "postflight_vectors": {...},
    "learning_delta": {...}
  },
  "provenance": {
    "investigation_path": "security-audit",
    "task": "...",
    "findings_count": 5
  },
  "reputation_seed": 0.5
}
```

**--register flag:** Registers to Qdrant-based sharing network.

### agent-import

Imports an epistemic agent, inheriting its learned state.

```bash
empirica agent-import \
  --session-id <session-id> \
  --input-file agent-package.json \
  --output json
```

**Key behavior:** Uses imported agent's postflight vectors as preflight for new branch (inherits learning).

### agent-discover

Searches the sharing network for epistemic agents.

```bash
empirica agent-discover \
  --domain "security" \
  --min-reputation 0.7 \
  --limit 10 \
  --output json
```

### investigate-multi

Multi-persona parallel investigation with epistemic auto-merge.

```bash
empirica investigate-multi \
  --session-id <session-id> \
  --task "Analyze authentication options" \
  --personas "security,performance,ux" \
  --aggregate-strategy epistemic-score \
  --output json
```

**What happens:**
1. Spawns N agents (one per persona)
2. Each gets persona-specific epistemic priors
3. All investigate in parallel
4. Results aggregated by chosen strategy

---

## Persona Epistemic Priors

Different personas start with different vector biases:

| Persona | know | uncertainty | context | Key Trait |
|---------|------|-------------|---------|-----------|
| security | 0.55 | 0.60 | 0.50 | High suspicion, thorough |
| performance | 0.60 | 0.45 | 0.55 | Metric-focused |
| ux | 0.50 | 0.50 | 0.45 | User-empathy |
| architect | 0.65 | 0.40 | 0.60 | System-wide view |
| general | 0.50 | 0.50 | 0.50 | Balanced |

---

## Sharing Network Architecture

The sharing network enables cross-project and cross-org epistemic agent exchange.

```
┌────────────────────────────────────────────────────────────────┐
│                    QDRANT PERSONA REGISTRY                      │
│  Collection: "personas"                                         │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Point Structure:                                               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ id: agent_id (UUID)                                      │   │
│  │ vector: [13-dim epistemic state]                         │   │
│  │ payload: {                                               │   │
│  │   persona_id: "security-agent-001",                      │   │
│  │   agent_type: "epistemic_agent",                         │   │
│  │   focus_domains: ["security", "oauth"],                  │   │
│  │   reputation_score: 0.75,                                │   │
│  │   learning_delta: {...},                                 │   │
│  │   findings_preview: [...],                               │   │
│  │   provenance: {...}                                      │   │
│  │ }                                                        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  Search Modes:                                                  │
│  • By domain: find_agents_by_domain("security")                 │
│  • By reputation: find_agents_by_reputation(min=0.7)            │
│  • By vector similarity: semantic epistemic matching            │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

### Reputation Evolution

Agent reputation evolves based on:
- **Import success rate:** Do imported agents help?
- **Learning delta magnitude:** Did they learn significantly?
- **Community ratings:** (Future) Explicit feedback

```
reputation = reputation_seed + (Σ import_successes / Σ imports) × 0.5
```

---

## Evolution Roadmap

### Tier 3: In Progress (2026-01-02)

| Item | Status | Implementation |
|------|--------|----------------|
| Persona storage in Qdrant | ⚠️ Partial | PersonaRegistry exists, but collection not initialized. Pre-defined personas in `.empirica/personas/*.json` not embedded. |
| Bayesian beliefs logging | ✅ Done | BayesianBeliefManager.update_belief() - CHECK/POSTFLIGHT |
| CHECK phase hooks | ✅ Done | Python hook system |
| Sentinel calibration | ✅ Done | Confidence tracking via calibration |
| Multi-persona orchestration | ✅ Done | investigate-multi |
| Winner→Extract→Embed flow | ❌ Not wired | `emerged_personas.py` exists but not connected to agent workflow |

### Tier 4: Sentinel Autonomy (Implemented v1.6.6)

| Item | Status | Implementation |
|------|--------|----------------|
| Autonomy calibration loop | ✅ Done | Sentinel tracks `tool_call_count` per transaction, PREFLIGHT calculates `avg_turns` from history, nudges at 1x/1.5x/2x thresholds. See [SENTINEL_ARCHITECTURE.md](./SENTINEL_ARCHITECTURE.md#autonomy-calibration-loop). |
| Subagent transaction exemption | ✅ Done | Subagents (no `active_session` file) bypass Sentinel gates. Parent CHECK authorizes spawn. See [SENTINEL_ARCHITECTURE.md](./SENTINEL_ARCHITECTURE.md#subagent-cascade-exemption). |
| Subagent governance | ✅ Done | Delegated work counting (SubagentStop transcript parsing), pre-spawn budget check (SubagentStart), `maxTurns: 25` default ceiling on all agents. |
| Stale transaction detection | ✅ Done | Status-only purge (`status != "open"`). No time-based eviction — overnight sessions survive. |
| Automatic branch pruning | ❌ Not started | Sentinel prunes low-merge-score branches |
| Dynamic persona selection | ❌ Not started | Sentinel chooses personas based on task |
| Cross-session learning | ⚠️ Partial | Calibration data persists across sessions via `.breadcrumbs.yaml` and Qdrant. Full sentinel strategy learning not implemented. |
| Escalation protocols | ⚠️ Partial | CHECK "investigate" loops back to noetic phase. Human checkpoint hooks exist. Full auto-escalation not wired. |

### Tier 5: Cognitive Vault (Future)

| Item | Description |
|------|-------------|
| Encrypted epistemic commits | Sign and encrypt learning data |
| Merkle tree verification | Prove epistemic history integrity |
| Cross-org federation | Share agents between organizations |
| Privacy-preserving search | Search without exposing raw data |

### Tier 6: Emergent Intelligence (Vision)

| Item | Description |
|------|-------------|
| SLM trained on deltas | Small model learns from epistemic patterns |
| Self-improving Sentinel | Sentinel evolves its own merge strategies |
| Collective intelligence | Agent swarms with emergent problem-solving |

---

## Key Insight: Why Turtle Works

The recursive structure enables:

1. **Consistent abstractions**: Same 13 vectors at every layer
2. **Composable oversight**: Sentinels can spawn sub-Sentinels
3. **Measurable learning**: Delta = postflight - preflight works at every scale
4. **Traceable provenance**: Every agent has a branch_id linking to parent

```
Session → spawns → Agent → spawns → Sub-Agent
   ↓                   ↓                  ↓
PREFLIGHT           PREFLIGHT         PREFLIGHT
   ↓                   ↓                  ↓
 CHECK               CHECK              CHECK
   ↓                   ↓                  ↓
POSTFLIGHT         POSTFLIGHT        POSTFLIGHT
   ↓                   ↓                  ↓
 Delta               Delta             Delta
   ↓                   ↓                  ↓
   └───────────────────┴──────────────────┘
              Aggregate upward
```

**It's turtles all the way down - and all the way up.**

---

## References

- [Epistemic Transaction Workflow](./NOETIC_PRAXIC_FRAMEWORK.md)
- [Storage Architecture](./STORAGE_ARCHITECTURE_COMPLETE.md)
- [Qdrant Integration](./QDRANT_EPISTEMIC_INTEGRATION.md)
- [Main Architecture Overview](./README.md)
