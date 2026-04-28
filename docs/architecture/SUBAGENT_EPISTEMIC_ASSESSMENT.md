# Subagent Epistemic Assessment

**Status:** Core Architecture
**Version:** 0.2.0
**Date:** 2026-03-16 (updated 2026-04-08)

---

## Overview

When an AI spawns subagents, those agents operate without epistemic
accountability. They return results with no confidence calibration,
no scope tracking, and no way to measure reliability over time.

This spec adds an epistemic lens to Claude Code's native subagent
system via the existing hook infrastructure. Two modes: **passive**
(observe and calibrate) and **active** (imprint and constrain).

---

## Storage Architecture (v1.8.14+)

Subagent sessions live in a **dedicated `subagent_sessions` table**,
isolated from the main `sessions` table. This separation was added in
migration 034 (KNOWN_ISSUES 11.24) to fix two related problems:

1. **Diagnostic pollution.** Pre-v1.8.14, every Task spawn created a
   row in the main `sessions` table. Subagent children were always
   newer than their parents, so any "recent sessions" diagnostic
   surfaced only subagent rows — masking the actual parent in
   post-compact failures and statusline lookups.

2. **Recent-sessions queries returning unhelpful results.** Code that
   listed sessions for dashboards or "what was I working on" features
   had to filter `WHERE parent_session_id IS NULL` to skip subagents.
   Many call sites forgot, leading to subtle bugs.

**Architecture:**

```
sessions table                    subagent_sessions table
─────────────                    ──────────────────────
parent (claude-code) ◄──────┐    child (Explore)
                            │    child (general-purpose)
                            └────child (superpowers:code-reviewer)
                                 (linked via parent_session_id)
```

The parent session lives in `sessions`. Each Task spawn creates a row
in `subagent_sessions` with `parent_session_id` pointing back. Lineage
is preserved without polluting the main table.

**API surface** (in `SessionDatabase`):

| Method | Purpose |
|--------|---------|
| `create_subagent_session(agent_name, parent_session_id, ...)` | Called by `subagent-start` hook |
| `end_subagent_session(session_id, rollup_summary)` | Called by `subagent-stop` hook |
| `get_subagent_session(session_id)` | Single subagent lookup |
| `list_subagents_for_parent(parent_session_id, status=None)` | Enumerate children when you actually want them |

Rollup at `subagent-stop` still logs findings to the **parent** session
in the main `sessions` table — the child's purpose is lifecycle
tracking and lineage, not artifact storage.

**Note on Claude Code session_id sharing:** All subagents inherit the
parent's Claude Code process-level `session_id` (kernel limitation —
they're spawned in-process). The Empirica `child_session_id` we create
is a separate UUID used for our own lineage tracking and budget
allocation. Hooks that gate on Claude Code's session_id therefore
cannot distinguish parent from child traffic; gating must happen at
the agent_name layer instead.

See [`docs/reference/api/core_session_management.md`](../reference/api/core_session_management.md#subagent-sessions-v1712) for full method signatures and the `subagent_sessions` schema.

---

## How It Works

```
Parent AI spawns subagent
        │
        ▼
┌─────────────────────────────┐
│  subagent-start hook fires  │
│                             │
│  1. Decompose prompt → vectors (NLP)
│  2. Match/create persona archetype
│  3. Record subagent preflight
│  4. (Active) Inject scope preamble
└──────────────┬──────────────┘
               │
        Agent executes
               │
               ▼
┌─────────────────────────────┐
│  subagent-stop hook fires   │
│                             │
│  1. Capture result summary
│  2. Record subagent postflight
│  3. Wait for parent assessment
│     (next finding-log or CHECK)
│  4. Compute calibration delta
│  5. Update Brier score for archetype
└─────────────────────────────┘
```

---

## Mode 1: Passive (Observe and Calibrate)

Zero changes to how the parent AI works. The hooks observe silently.

### Prompt Decomposition

The `subagent-start` hook receives the agent's prompt and description.
It decomposes this into estimated epistemic vectors via pattern matching:

| Prompt Signal | Vector Mapping |
|---------------|----------------|
| "Research...", "Find...", "Search..." | high uncertainty, investigation type |
| "Implement...", "Write...", "Build..." | higher do, praxic type |
| "Check...", "Verify...", "Test..." | moderate know, audit type |
| "Explore...", "Analyze..." | moderate breadth, noetic type |
| Domain keywords (MCP, chemistry, etc.) | domain tags |
| "thorough", "quick", "comprehensive" | depth/density signal |

```python
def decompose_prompt(prompt: str, description: str) -> SubagentPersona:
    """Derive epistemic vectors from subagent prompt."""
    return SubagentPersona(
        archetype=classify_archetype(prompt),  # research|code|audit|explore
        vectors=estimate_vectors(prompt),
        domain_tags=extract_domains(prompt),
        scope=estimate_scope(prompt, description),
        prompt_hash=hash(prompt),  # for dedup
    )
```

### Persona Archetype Matching

Archetypes are broad categories, not unique personas per spawn:

| Archetype | Typical Vectors | Example Prompts |
|-----------|-----------------|-----------------|
| `researcher` | know:0.3, uncertainty:0.7, depth:0.8 | "Research the MCP spec..." |
| `explorer` | know:0.4, uncertainty:0.5, breadth:0.7 | "Explore the codebase for..." |
| `implementer` | know:0.6, uncertainty:0.3, do:0.8 | "Write a function that..." |
| `auditor` | know:0.5, uncertainty:0.4, clarity:0.8 | "Check if the tests cover..." |
| `analyst` | know:0.5, uncertainty:0.5, density:0.8 | "Analyze the performance of..." |

Stored in the existing persona registry (Qdrant `personas` collection).

### Calibration Tracking

When the parent AI processes the subagent's result, the next epistemic
action (finding-log, CHECK, or explicit assessment) becomes the grounded
truth for that subagent's output.

```python
# Subagent returned with research findings
# Parent logs a finding → this IS the assessment

calibration_point = {
    "subagent_id": "a4c63bdeeec6103a7",
    "archetype": "researcher",
    "domain": ["mcp", "protocol-spec"],
    "estimated_vectors": {  # from prompt decomposition
        "know": 0.30,
        "uncertainty": 0.70,
        "depth": 0.80,
    },
    "assessed_vectors": {   # from parent's subsequent actions
        "know": 0.85,       # parent found the results highly informative
        "uncertainty": 0.15, # most unknowns were resolved
        "depth": 0.90,      # thorough — found specific technical details
    },
    "delta": {
        "know": +0.55,      # agent exceeded expectations
        "uncertainty": -0.55,
    },
    "outcome": "used",       # findings were incorporated into CHECK
    "finding_ids": ["..."],  # linked findings from this agent's output
}
```

Over time, this builds a Brier score per archetype:

```
researcher archetype (n=47):
  know overestimate: -0.05 (slightly conservative — good)
  depth overestimate: +0.12 (claims thorough but misses ~12%)
  hit rate: 78% of findings actually used by parent
  avg resolution: 2.1 unknowns resolved per spawn

explorer archetype (n=23):
  know overestimate: +0.15 (overestimates — discount by 15%)
  breadth underestimate: -0.08 (finds more than expected)
  hit rate: 62% of findings used
  avg resolution: 1.4 unknowns resolved per spawn
```

---

## Mode 2: Active (Imprint and Constrain)

The parent defines explicit scope parameters that the `subagent-start`
hook injects into the agent's prompt as a scoping preamble.

### Imprint Schema

```python
@dataclass
class SubagentImprint:
    """Epistemic contract for a subagent."""

    # Scope shape (0.0-1.0)
    breadth: float = 0.5       # How wide to search
    depth: float = 0.5         # How deep to investigate
    confidence_floor: float = 0.5  # Minimum confidence to report

    # Boundaries
    domain_tags: list[str] = field(default_factory=list)
    scope_type: str = "research"  # research|code|audit|explore|analyze
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)

    # Resource limits
    max_tool_calls: int | None = None
    max_sources: int | None = None

    # Calibration adjustment (from historical Brier score)
    confidence_discount: float = 0.0  # Applied to agent's claimed confidence
```

### Prompt Injection

The hook translates the imprint into natural language prepended to the
agent's system context:

```python
def imprint_to_preamble(imprint: SubagentImprint) -> str:
    parts = []

    if imprint.breadth < 0.3:
        parts.append("Focus narrowly on the specific question asked.")
    elif imprint.breadth > 0.7:
        parts.append("Cast a wide net — explore adjacent topics and connections.")

    if imprint.depth > 0.7:
        parts.append("Be thorough — include technical details, specific numbers, and sources.")
    elif imprint.depth < 0.3:
        parts.append("Surface-level scan only — headlines and key facts.")

    if imprint.confidence_floor > 0.7:
        parts.append("Only report findings you're confident about. Flag anything uncertain.")

    if imprint.out_of_scope:
        parts.append(f"Out of scope: {', '.join(imprint.out_of_scope)}")

    if imprint.domain_tags:
        parts.append(f"Domain focus: {', '.join(imprint.domain_tags)}")

    return " ".join(parts)
```

### Example: Three Agents from This Session

How the three investigation agents spawned earlier would look with imprints:

```python
# Agent 1: MCP Resources
SubagentImprint(
    breadth=0.3,          # narrow — just resources, not full MCP
    depth=0.9,            # thorough — need limits, push/pull, templates
    confidence_floor=0.7,
    domain_tags=["mcp", "protocol-spec"],
    scope_type="research",
    in_scope=["resource limits", "push notifications", "templates"],
    out_of_scope=["MCP tools", "MCP prompts", "MCP sampling"],
)

# Agent 2: Competitive Landscape
SubagentImprint(
    breadth=0.8,          # wide — survey the field
    depth=0.5,            # moderate — key findings not exhaustive
    confidence_floor=0.5, # include emerging/uncertain work
    domain_tags=["caching", "RAG", "prediction"],
    scope_type="research",
    in_scope=["predictive caching", "knowledge graph caching", "eviction strategies"],
    out_of_scope=["pricing", "marketing"],
)

# Agent 3: Codebase Exploration
SubagentImprint(
    breadth=0.6,          # moderate — Qdrant + MCP areas
    depth=0.8,            # thorough — need file paths and code snippets
    confidence_floor=0.8, # only report what you actually find in code
    domain_tags=["empirica", "qdrant", "mcp-server"],
    scope_type="explore",
    in_scope=["MCP resources", "caching", "knowledge graph", "embeddings pipeline"],
    out_of_scope=["CLI commands", "test files", "documentation"],
)
```

---

## Grounded Confidence Control (Brier Score)

The Brier score measures calibration: when an agent says "I'm 80% confident",
are they right 80% of the time?

### Computation

```python
def brier_score(predictions: list[CalibrationPoint]) -> float:
    """
    Lower is better. 0.0 = perfect calibration.

    For each prediction, compare claimed confidence to binary outcome
    (was the finding actually used/confirmed by parent?).
    """
    n = len(predictions)
    if n == 0:
        return 0.5  # no data, assume poorly calibrated

    total = sum(
        (p.claimed_confidence - p.actual_outcome) ** 2
        for p in predictions
    )
    return total / n
```

### Per-Vector Brier Scores

More useful than a single score — track calibration per vector:

```
researcher archetype:
  know:        Brier 0.08 (well calibrated)
  uncertainty: Brier 0.12 (slightly overconfident)
  depth:       Brier 0.18 (claims thorough, misses things)
  breadth:     Brier 0.05 (very well calibrated)
```

### Applying Calibration

When a subagent returns results, the parent can adjust trust:

```python
def calibrated_confidence(raw_confidence: float,
                          archetype: str,
                          vector: str) -> float:
    """Adjust agent's claimed confidence by historical calibration."""
    brier = get_brier_score(archetype, vector)
    historical_bias = get_historical_bias(archetype, vector)

    # If agent typically overestimates know by 0.15, discount
    adjusted = raw_confidence - historical_bias

    return max(0.0, min(1.0, adjusted))
```

### Earned Autonomy for Subagents

As calibration data accumulates, well-calibrated archetypes earn more trust:

| Brier Score | Trust Level | Behavior |
|------------|-------------|----------|
| < 0.10 | **High** | Findings accepted at face value |
| 0.10-0.25 | **Moderate** | Findings accepted with calibration adjustment |
| 0.25-0.40 | **Low** | Findings flagged for parent verification |
| > 0.40 | **Untrusted** | Agent gets tighter imprint or different archetype |

This mirrors the Sentinel's earned autonomy for the parent AI, applied
one layer down to subagents. Same principle, same vectors, same calibration
infrastructure — turtles all the way down.

---

## Integration with Existing Infrastructure

| Component | How It's Used |
|-----------|---------------|
| `subagent-start.py` hook | Prompt decomposition + imprint injection |
| `subagent-stop.py` hook | Result capture + postflight recording |
| Persona registry (Qdrant) | Store archetype profiles + calibration |
| Calibration collection | Per-archetype Brier scores |
| Finding-log | Parent assessment = reference belief state |
| Sentinel | Can apply earned autonomy thresholds |

### New Storage

Extends persona registry with subagent-specific fields:

```python
# In personas collection
{
    "persona_id": "archetype:researcher",
    "name": "Research Agent",
    "type": "subagent_archetype",      # new
    "vector_profile": [0.3, 0.7, ...], # 13D epistemic signature
    "calibration": {                    # new
        "brier_overall": 0.12,
        "brier_per_vector": {"know": 0.08, "depth": 0.18, ...},
        "historical_bias": {"know": -0.05, "depth": +0.12, ...},
        "total_spawns": 47,
        "hit_rate": 0.78,
    },
    "focus_domains": ["research", "spec-analysis", "literature-review"],
}
```

---

## Implementation

### Phase 1: Passive Observation
- Enhance `subagent-start.py` to decompose prompts into vectors
- Enhance `subagent-stop.py` to capture results
- Link subagent outputs to parent's subsequent finding-logs
- Store calibration points in calibration collection

### Phase 2: Archetype Matching
- Define base archetypes (researcher, explorer, implementer, auditor, analyst)
- Store in persona registry
- Match spawned agents to nearest archetype
- Start accumulating per-archetype Brier scores

### Phase 3: Active Imprinting
- Add imprint schema to `agent-spawn` / subagent prompt
- Build prompt injection in `subagent-start.py`
- Allow parent to specify scope constraints
- Track whether tighter imprints improve calibration

### Phase 4: Earned Autonomy
- Apply calibration-based trust levels
- Well-calibrated archetypes get wider scope
- Poorly calibrated archetypes get constrained
- Surface calibration data to parent AI during CHECK

---

## Relationship to Hot Cache

Subagent assessment is core infrastructure. The hot cache product builds
on it: if subagents feed the cache (e.g., research agents that pre-load
context), their Brier scores directly affect cache entry confidence.
A finding from a well-calibrated agent gets higher LER priority than
one from a poorly calibrated agent.

```
subagent Brier score → confidence weight → LER score → cache priority
```

This is why subagent assessment is core and the hot cache is product.
The core makes the product trustworthy.
