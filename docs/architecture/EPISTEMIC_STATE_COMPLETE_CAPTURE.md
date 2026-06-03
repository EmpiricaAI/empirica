# Complete Epistemic State Capture Design

**Purpose:** Define ALL data that should be captured in epistemic state snapshots
**Date:** 2025-12-26

**Related docs:**
- [05_EPISTEMIC_VECTORS_EXPLAINED.md](../human/end-users/05_EPISTEMIC_VECTORS_EXPLAINED.md) - End-user guide to vectors
- [STORAGE_ARCHITECTURE_COMPLETE.md](./STORAGE_ARCHITECTURE_COMPLETE.md) - How state is stored
- [QDRANT_EPISTEMIC_INTEGRATION.md](./QDRANT_EPISTEMIC_INTEGRATION.md) - Semantic search of epistemic artifacts
- [CLI_COMMANDS_UNIFIED.md](../human/developers/CLI_COMMANDS_UNIFIED.md) - Commands: finding-log, unknown-log, deadend-log

---

## Current Vectors (13) ✅

### Cognitive Vectors
1. **engagement** - Alignment with task/goal
2. **know** - What is understood
3. **do** - Capability to act
4. **context** - Situational awareness
5. **clarity** - Mental model sharpness
6. **coherence** - Internal consistency
7. **signal** - Information quality
8. **density** - Complexity/depth
9. **state** - Current working memory load
10. **change** - Delta from baseline
11. **completion** - How much work is done
12. **impact** - Importance/value of work
13. **uncertainty** - What is unknown

---

## Additional Capture Dimensions

### 1. Git State (Critical for Drift Detection)

**Why:** Memory compact happens in git repo - git state shows work continuity

**Data to Capture:**
```json
{
  "git_state": {
    "head_commit": "abc123...",
    "branch": "main",
    "ahead_behind": {"ahead": 0, "behind": 0},
    "uncommitted_changes": {
      "files_modified": 5,
      "files_added": 2,
      "files_deleted": 0,
      "insertions": 342,
      "deletions": 127
    },
    "commits_since_last_checkpoint": [
      {"hash": "def456", "message": "Fix hooks", "timestamp": "..."}
    ],
    "last_command": "git status",  // If trackable
    "dirty": true
  }
}
```

**Source:** Git commands via subprocess
**Impact:** Detect if work state lost during compact (files reset, commits disappeared)

---

### 2. Reasoning Trail (Cognitive Continuity)

**Why:** Reasoning is the "why" behind vectors - loss indicates metacognitive drift

**Data to Capture:**
```json
{
  "reasoning": {
    "latest": "Debugging hooks - found CheckpointManager mismatch",
    "trajectory": [
      {"timestamp": "...", "reasoning": "Starting investigation"},
      {"timestamp": "...", "reasoning": "Found root cause"},
      {"timestamp": "...", "reasoning": "Implemented fix"}
    ],
    "decision_history": [
      {"checkpoint": "PREFLIGHT", "decision": null},
      {"checkpoint": "CHECK-1", "decision": "investigate", "confidence": 0.65},
      {"checkpoint": "CHECK-2", "decision": "proceed", "confidence": 0.82}
    ],
    "epistemic_tags": ["debugging", "hook_integration", "storage_layer"]
  }
}
```

**Source:**
- Latest: Most recent checkpoint reasoning
- Trajectory: All checkpoints in session
- Decisions: CHECK gates only

**Impact:** Detect reasoning continuity break (forgot why we're doing this)

---

### 3. Database Artifacts (Epistemic Evidence)

**Why:** Findings/unknowns/dead_ends are ground truth - must not lose count/context

**Data to Capture:**
```json
{
  "breadcrumbs": {
    "findings": {
      "total": 125,
      "session": 8,
      "recent": [
        {"id": "...", "finding": "CheckpointManager mismatch", "timestamp": "..."}
      ]
    },
    "unknowns": {
      "total": 48,
      "unresolved": 32,
      "resolved": 16,
      "session": 3,
      "recent": [
        {"id": "...", "unknown": "SessionStart mapping unclear", "is_resolved": false}
      ]
    },
    "dead_ends": {
      "total": 12,
      "session": 1,
      "recent": [
        {"approach": "Using CheckpointManager", "why_failed": "Wrong storage layer"}
      ]
    },
    "mistakes": {
      "total": 8,
      "session": 0,
      "recent": []
    }
  },
  "goals": {
    "total": 3,
    "active": 2,
    "completed": 1,
    "completion_rate": 0.33,
    "current": {
      "id": "...",
      "objective": "Debug action hooks",
      "progress": "2/4 tasks complete"
    }
  },
  "handoffs": {
    "count": 5,
    "most_recent": {
      "timestamp": "...",
      "from_ai": "empirica-verbose-fix",
      "summary": "Completed hook debugging"
    }
  }
}
```

**Source:** SessionDatabase methods
- `get_project_findings(project_id, limit=5)`
- `get_project_unknowns(project_id, limit=5)`
- `get_project_dead_ends(project_id, limit=5)`
- `get_project_mistakes(project_id, limit=5)`
- `get_goals_for_session(session_id)`
- `get_latest_handoff(project_id)`

**Impact:** Detect evidence loss (findings count dropped, goals forgotten)

---

### 4. Session Metadata (Workflow State)

**Why:** CASCADE workflow position matters - losing phase means losing structure

**Data to Capture:**
```json
{
  "session": {
    "session_id": "abc123",
    "ai_id": "empirica-hook-debug",
    "project_id": "def456",
    "start_time": "2025-12-26T00:03:00",
    "duration_minutes": 67,
    "cascade_phase": "CHECK",  // PREFLIGHT, CHECK, POSTFLIGHT, or null
    "round": 2,  // Which CHECK round
    "cycle": 3,  // Investigation depth iteration
    "scope_depth": 0.75,
    "checkpoints_count": 4,
    "last_checkpoint": {
      "phase": "CHECK",
      "timestamp": "2025-12-26T01:05:00",
      "vectors": {...}
    }
  }
}
```

**Source:**
- SessionDatabase.get_session(session_id)
- GitEnhancedReflexLogger.list_checkpoints(limit=1)

**Impact:** Detect workflow state loss (forgot we're in CHECK, forgot investigation cycle)

---

### 5. Project Context (Multi-Session Awareness)

**Why:** Work happens across sessions - project state shows broader context

**Data to Capture:**
```json
{
  "project": {
    "project_id": "def456",
    "name": "empirica",
    "description": "Empirica CLI and framework",
    "repos": ["https://github.com/Nubaeon/empirica.git"],
    "active_sessions": [
      {"session_id": "abc123", "ai_id": "empirica-hook-debug"},
      {"session_id": "xyz789", "ai_id": "qwen-testing"}
    ],
    "ai_collaborators": ["empirica-hook-debug", "qwen-testing", "empirica-verbose-fix"],
    "total_sessions": 42,
    "learning_deltas": {
      "know": 0.15,  // Average learning across sessions
      "uncertainty": -0.10
    }
  }
}
```

**Source:**
- SessionDatabase.get_project(project_id)
- SessionDatabase.get_active_sessions(project_id)

**Impact:** Detect multi-session context loss (forgot other AIs working, forgot project scope)

---

### 6. Investigation Context (Depth Tracking)

**Why:** Cycle/round/scope already captured, but need breadcrumb chains

**Data to Capture:**
```json
{
  "investigation": {
    "cycle": 3,
    "round": 2,
    "scope_depth": 0.75,
    "breadcrumb_chains": [
      {
        "finding": "CheckpointManager mismatch",
        "led_to_unknown": "Why does checkpoint-list work?",
        "led_to_finding": "GitEnhancedReflexLogger used instead",
        "led_to_fix": "Changed monitor_commands.py:409"
      }
    ],
    "exploration_paths": [
      "monitor_commands.py → CheckpointManager → GitEnhancedReflexLogger → Fixed"
    ]
  }
}
```

**Source:**
- Cycle/round/scope from args
- Breadcrumb chains: Could link findings → unknowns by timestamp proximity
- Exploration paths: Git history + file reads

**Impact:** Detect investigation continuity break (lost the thread of inquiry)

---

### 7. Reference Docs (Knowledge Anchors)

**Why:** Docs loaded = context available. Must not lose what was consulted.

**Data to Capture:**
```json
{
  "reference_docs": {
    "loaded_count": 13,
    "recent": [
      {
        "path": "docs/architecture/INVESTIGATION_CYCLE_TRACKING.md",
        "type": "architecture",
        "accessed": "2025-12-26T00:45:00"
      }
    ],
    "pre_summaries": {
      "count": 5,
      "most_recent": {
        "timestamp": "2025-12-26T01:10:06",
        "impact": 0.75,
        "completion": 0.60
      }
    }
  }
}
```

**Source:**
- SessionDatabase.get_project_reference_docs(project_id)
- Filter for pre_summary_snapshot type

**Impact:** Detect knowledge anchor loss (forgot what docs were consulted)

---

## Optional: Semantic Search Context (Qdrant)

**User's question:** "Might be a good place to get AI to do semantic search for quick retrieval?"

### Use Case
**Scenario:** Post-compact, AI needs to quickly recall:
- "What did I learn about X?"
- "What mistakes did I make with Y?"
- "What findings relate to Z?"

### Proposed Design (Optional)

```python
def enrich_state_with_semantic_context(state: Dict, query: str = None) -> Dict:
    """
    Optionally enrich epistemic state with semantic search results.

    Args:
        state: Base epistemic state dict
        query: Optional semantic query (if None, auto-generate from state)

    Returns:
        Enriched state with semantic_context field
    """
    if not qdrant_available():
        return state  # Skip if Qdrant not configured

    # Auto-generate query from state if not provided
    if not query:
        query = f"""
        Session: {state['session']['session_id']}
        Working on: {state['goals']['current']['objective']}
        Recent findings: {[f['finding'] for f in state['breadcrumbs']['findings']['recent']]}
        Unknowns: {[u['unknown'] for u in state['breadcrumbs']['unknowns']['recent']]}
        """

    # Search Qdrant
    results = qdrant_search(query, limit=5)

    state['semantic_context'] = {
        "query": query,
        "results": results,
        "relevance_scores": [r['score'] for r in results]
    }

    return state
```

### When to Use Semantic Search

**YES (High Value):**
- Post-compact: "What was I working on before compact?"
- Long sessions: "Remind me what I discovered about X 2 hours ago"
- Multi-session: "What did other AIs find about Y?"
- Handoff resume: "What context do I need from previous session?"

**NO (Over-Engineering):**
- Pre-compact: Already have fresh state, don't need search
- Short sessions: Recent findings already in memory
- Fresh session start: project-bootstrap is sufficient

### Recommendation: **Make it optional**

```bash
# Without semantic search (default, fast)
empirica assess-state --output json

# With semantic search (optional, slower, more context)
empirica assess-state --semantic-search --output json

# With custom query
empirica assess-state --semantic-query "Findings about hook integration" --output json
```

---

## Complete Capture Schema

**Full epistemic state snapshot:**

```json
{
  "type": "epistemic_state_snapshot",
  "timestamp": "2025-12-26T01:30:00",
  "trigger": "pre_compact" | "post_compact" | "manual",

  // Core vectors (13)
  "vectors": {
    "engagement": 0.85,
    "know": 0.70,
    "uncertainty": 0.30,
    "do": 0.75,
    "context": 0.80,
    "clarity": 0.85,
    "coherence": 0.80,
    "signal": 0.75,
    "density": 0.65,
    "state": 0.80,
    "change": 0.15,
    "completion": 0.60,
    "impact": 0.75
  },

  // Git state
  "git_state": { /* ... */ },

  // Reasoning trail
  "reasoning": { /* ... */ },

  // Database artifacts
  "breadcrumbs": { /* ... */ },
  "goals": { /* ... */ },
  "handoffs": { /* ... */ },

  // Session metadata
  "session": { /* ... */ },

  // Project context
  "project": { /* ... */ },

  // Investigation context
  "investigation": { /* ... */ },

  // Reference docs
  "reference_docs": { /* ... */ },

  // Optional: Semantic search
  "semantic_context": { /* ... */ }  // Only if --semantic-search used
}
```

---

## Implementation Priorities

### Phase 1: Core Data (Required)
1. Vectors (13) ✅ Already captured
2. Git state (NEW)
3. Breadcrumbs (findings, unknowns, dead_ends, mistakes) (NEW)
4. Session metadata (NEW)
5. Reasoning trail (PARTIAL - only latest, need trajectory)

### Phase 2: Context Enrichment (High Value)
6. Goals (NEW)
7. Project context (NEW)
8. Reference docs (PARTIAL - need access tracking)
9. Investigation context (PARTIAL - have cycle/round, need chains)

### Phase 3: Advanced Features (Optional)
10. Semantic search (NEW, optional)
11. Handoffs (NEW)
12. Learning deltas (NEW)

---

## Post-Compact Hook Requirements

**User's requirement:** "Post-compact hook needs to load project-bootstrap for dynamic context loading"

**Implementation:**

```python
# post-compact.py (revised)

def main():
    # 1. Load pre-compact snapshot
    pre_snapshot = load_latest_pre_summary_snapshot()

    # 2. Load project-bootstrap (dynamic context)
    bootstrap = subprocess.run(
        ['empirica', 'project-bootstrap', '--session-id', session_id, '--output', 'json'],
        capture_output=True,
        text=True
    )
    bootstrap_data = json.loads(bootstrap.stdout)

    # 3. Capture fresh post-compact state
    post_state = subprocess.run(
        ['empirica', 'assess-state',
         '--session-id', session_id,
         '--prompt', f"Bootstrap: {bootstrap_data}. Pre-compact: {pre_snapshot}. Assess current.",
         '--output', 'json'],
        capture_output=True,
        text=True
    )
    post_data = json.loads(post_state.stdout)

    # 4. Compare ALL dimensions
    drift_analysis = {
        "vectors": compare_vectors(pre_snapshot['vectors'], post_data['vectors']),
        "git_state": compare_git_state(pre_snapshot['git_state'], post_data['git_state']),
        "breadcrumbs": compare_breadcrumbs(pre_snapshot['breadcrumbs'], post_data['breadcrumbs']),
        "reasoning": compare_reasoning(pre_snapshot['reasoning'], post_data['reasoning']),
        "goals": compare_goals(pre_snapshot['goals'], post_data['goals'])
    }

    # 5. Present comprehensive drift report
    print_drift_report(drift_analysis)

    # 6. Inject context for AI
    inject_context_to_ai(bootstrap_data, pre_snapshot, post_data, drift_analysis)
```

---

## Key Design Decisions

### ✅ Capture Everything Listed Above
- Git state: Critical for detecting code loss
- Breadcrumbs: Ground truth evidence
- Reasoning trail: Metacognitive continuity
- Goals/session/project: Workflow context

### ✅ Make Semantic Search Optional
- High value for long/multi-session work
- Over-engineering for simple sessions
- Use `--semantic-search` flag

### ✅ Comprehensive Drift Comparison
- Not just vectors (current flaw)
- Compare: git, breadcrumbs, reasoning, goals
- Report: "You lost 3 findings, git reset 2 files, forgot goal progress"

### ✅ Dynamic Bootstrap Loading
- Post-compact must call project-bootstrap
- Ensures latest project state available
- Includes importance-weighted snapshots

---

## Questions for User

1. **Git state tracking:** Should we also track git command history (bash history grep for git commands)?
2. **Breadcrumb chains:** Auto-link findings → unknowns, or require manual linking?
3. **Semantic search default:** Off by default (user opts in), or on if Qdrant configured?
4. **Snapshot size:** All this data = larger snapshots. Set size limits or trust curation?

---

## Next Steps

1. Implement `empirica assess-state` with full capture schema
2. Test pre-compact: Verify all data captured
3. Test post-compact: Verify bootstrap loaded + drift compared
4. Measure: Snapshot size, performance impact
5. Iterate: Add semantic search if needed
