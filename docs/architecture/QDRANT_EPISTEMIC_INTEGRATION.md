# Qdrant × Epistemic Learning Integration

**Status:** ✅ Production-Ready
**Last Updated:** 2025-12-19

**Related docs:**
- [Qdrant API Reference](../reference/api/qdrant.md) - EmbeddingsProvider, QdrantMemory classes and methods
- [CANONICAL_STORAGE.md](./CANONICAL_STORAGE.md) - Four-layer storage architecture overview
- [STORAGE_ARCHITECTURE_COMPLETE.md](./STORAGE_ARCHITECTURE_COMPLETE.md) - Visual data flow diagrams

---

## Architecture Overview

Empirica uses a **dual-database architecture** for optimal AI learning:

```
┌─────────────────────────────────────────────────────────┐
│  TRANSACTIONAL DATABASE (SQLite/PostgreSQL)              │
│  - Sessions, Goals, CASCADE states                       │
│  - Structured queries, relational integrity             │
│  - ACID transactions                                     │
└─────────────────────────────────────────────────────────┘
                            │
                            │ Epistemic artifacts
                            ▼
┌─────────────────────────────────────────────────────────┐
│  VECTOR DATABASE (Qdrant)                                │
│  - Semantic search of learning experiences               │
│  - Findings, unknowns, mistakes, epistemic trajectories │
│  - Cosine similarity matching                            │
└─────────────────────────────────────────────────────────┘
```

**Key insight:** Transactional DB stores *what happened*, Qdrant enables *learning from what happened*.

---

## Qdrant Collections

### Per-Project Collections (3 per project):

### 1. **`project_{id}_docs`** - Documentation Embeddings
```python
{
  "id": "doc_uuid",
  "vector": [1536-dim embedding],
  "payload": {
    "doc_path": "docs/architecture/CASCADE.md",
    "tags": ["workflow", "epistemic"],
    "concepts": ["PREFLIGHT", "POSTFLIGHT"],
    "questions": ["How to track learning?"],
    "use_cases": ["Multi-agent coordination"]
  }
}
```

**Purpose:** Semantic search across project documentation

### 2. **`project_{id}_memory`** - Learning Artifacts
```python
{
  "id": "artifact_uuid",
  "vector": [1536-dim embedding],
  "payload": {
    "type": "finding" | "unknown" | "mistake" | "dead_end"
  }
}
```

**Purpose:** Semantic search for:
- **Findings:** What was learned/discovered
- **Unknowns:** What's still unclear (resolved tracking)
- **Mistakes:** What went wrong (with root cause + prevention)
- **Dead ends:** What didn't work (prevents repeat attempts)

### 3. **`project_{id}_epistemics`** - Epistemic Trajectories
```python
{
  "id": "session_uuid",
  "vector": [1536-dim embedding of combined reasoning],
  "payload": {
    "session_id": "uuid",
    "ai_id": "empirica",
    "timestamp": "2025-12-19T10:30:00",
    "task_description": "Implement OAuth2 authentication",

    // Flattened vectors (for filtering)
    "preflight": {"engagement": 0.85, "know": 0.6, "do": 0.7, ...},
    "postflight": {"engagement": 0.9, "know": 0.85, "do": 0.8, ...},

    // Learning deltas (POSTFLIGHT - PREFLIGHT)
    "deltas": {
      "engagement": +0.05,
      "know": +0.25,        // 🎯 Key learning metric
      "do": +0.10,
      "uncertainty": -0.30  // 🎯 Uncertainty reduction
    },

    // Calibration metadata
    "calibration_accuracy": "good" | "fair" | "poor",
    "investigation_phase": true,  // Had CHECK gates
    "mistakes_count": 2,

    // Outcomes
    "completion": 0.95,
    "impact": 0.90
  }
}
```

**Purpose:** Pattern recognition across learning experiences

### Global Collections (shared across projects):

### 4. **`global_learnings`** - Cross-Project Knowledge
```python
{
  "id": "item_uuid",
  "vector": [1536-dim embedding],
  "payload": {
    "type": "finding" | "unknown_resolved" | "dead_end",
    "text": "Original learning text",
    "project_id": "source project",
    "session_id": "source session",
    "impact": 0.85,  # High-impact items only (≥0.7)
    "tags": ["oauth", "security"]
  }
}
```

**Purpose:** Cross-project semantic search for high-impact learnings
**Population:** Via `sync_high_impact_to_global()` or auto-sync on high-impact findings

### 5. **`empirica_lessons`** - Procedural Knowledge (Epistemic Lesson Graphs)
```python
{
  "id": "md5_hash_of_lesson_id",
  "vector": [384-dim embedding],  # Hash-based placeholder, or model embedding
  "payload": {
    "lesson_id": "8f89dc21e5160e5a",
    "name": "NotebookLM: Navigate to Studio Tab",
    "description": "CRITICAL: Navigate from Chat to Studio tab...",
    "domain": "notebooklm",
    "tags": ["notebooklm", "studio", "navigation", "atomic"],
    "source_confidence": 0.95,
    "teaching_quality": 0.90
  }
}
```

**Purpose:** Semantic search for procedural knowledge (how-to lessons)

**Architecture:** 4-layer storage for optimal speed:
- **HOT (ns):** In-memory graph - relationships, prerequisites, deltas
- **WARM (μs):** SQLite `lessons` table - metadata, queryable
- **SEARCH (ms):** Qdrant `empirica_lessons` - semantic similarity
- **COLD (10ms):** YAML files in `.empirica/lessons/` - full content

**Knowledge Graph:** Lessons connected via `knowledge_graph` table with edges:
- `requires` - Must complete prerequisite first
- `enables` - Opens up dependent lessons
- `related_to` - Semantic similarity

**API Note (Qdrant 1.7+):** Use `query_points()` instead of deprecated `search()`:
```python
response = client.query_points(
    collection_name="empirica_lessons",
    query=vector,  # Not query_vector
    limit=10
)
for point in response.points:  # Not hits
    print(point.payload, point.score)
```

### 6. **`personas`** - Epistemic Agent Profiles
```python
{
  "id": "persona_uuid",
  "vector": [13-dim epistemic state],  # Or 1536-dim task embedding
  "payload": {
    "persona_id": "security-expert-001",
    "name": "Security Expert",
    "agent_type": "epistemic_agent" | "predefined",
    "focus_domains": ["security", "oauth"],
    "reputation_score": 0.75,
    "initial_vectors": {...},  # Starting epistemic state
    "delta_pattern": {...},    # How vectors evolved (for emerged)
    "provenance": {
      "source_session_id": "...",
      "source_branch_id": "...",
      "is_emerged": true | false
    }
  }
}
```

**Purpose:** Semantic matching of personas to tasks
**Population:**
- Pre-defined: From `.empirica/personas/*.json` via `embed_predefined_personas()`
- Emerged: From winning branches via `extract_persona_from_loop_tracker()`

**Status:** ⚠️ Collection not initialized. See [separation-of-concerns.md](./separation-of-concerns.md) for planned flow.

---

## Data Flow: SQLite → Qdrant

### 1. Findings/Unknowns/Mistakes (Logged in Real-Time)

```bash
# CLI usage (AI-first JSON mode)
cat > /tmp/finding.json << EOF
{
  "project_id": "project-uuid",
  "session_id": "session-uuid",
  "finding": "PKCE flow requires state parameter for security"
}
EOF

empirica finding-log /tmp/finding.json
```

**What happens:**
1. **SQLite:** `INSERT INTO project_findings` (transactional record)
2. **Qdrant:** `upsert_memory()` → `project_{id}_memory` (semantic index)

**Result:** Finding is now:
- Queryable by SQL (structured)
- Searchable by similarity (semantic)

### 2. Epistemic Trajectories (After POSTFLIGHT)

```python
# Triggered after POSTFLIGHT submission
from empirica.core.epistemic_trajectory import store_trajectory

store_trajectory(
    project_id="project-uuid",
    session_id="session-uuid",
    db=SessionDatabase()
)
```

**What happens:**
1. **Extract from SQLite:**
   - PREFLIGHT vectors from `reflexes` table
   - POSTFLIGHT vectors from `reflexes` table
   - Compute deltas (POSTFLIGHT - PREFLIGHT)
   - Get mistakes count, CHECK count

2. **Store to Qdrant:**
   - Embedding = combined reasoning (PREFLIGHT + POSTFLIGHT)
   - Payload = complete trajectory metadata

**Result:** Session learning is now semantically searchable

---

## Semantic Search Queries

### Example 1: Find Similar Learning Experiences

```bash
empirica project-search \
  --project-id <UUID> \
  --query "OAuth2 PKCE authentication flow learning" \
  --min-learning 0.2 \
  --limit 5 \
  --output json
```

**Returns:**
```json
{
  "ok": true,
  "results": [
    {
      "score": 0.92,  // Cosine similarity
      "session_id": "abc123...",
      "task_description": "Implement OAuth2 with PKCE",
      "deltas": {
        "know": +0.35,
        "uncertainty": -0.40
      },
      "calibration_accuracy": "good",
      "mistakes_count": 1
    }
  ]
}
```

**Use case:** "Show me sessions where we learned about OAuth2 with high knowledge gain"

### Example 2: Query Mistakes/Dead Ends

```python
from empirica.core.qdrant.vector_store import search

results = search(
    project_id="project-uuid",
    query_text="authentication token refresh errors",
    kind="memory",  # Search findings/unknowns/mistakes/dead_ends
    limit=5
)
```

**Returns:**
```python
{
  "memory": [
    {
      "score": 0.88,
      "type": "mistake",
      # Original mistake text embedded
    },
    {
      "score": 0.76,
      "type": "dead_end",
      # What didn't work
    }
  ]
}
```

**Use case:** "Before trying refresh tokens, show me what failed before"

### Example 3: Project-Wide Learning Stats

```bash
empirica calibration-report --project-id <UUID> --output json
```

**Returns:**
```json
{
  "ok": true,
  "stats": {
    "total_sessions": 47,
    "avg_know_delta": +0.18,
    "avg_uncertainty_delta": -0.22,
    "high_learning_sessions": 23,  // know Δ ≥0.2
    "calibration_breakdown": {
      "good": 38,
      "fair": 7,
      "poor": 2
    },
    "investigation_rate": 0.68  // 68% had CHECK gates
  }
}
```

**Use case:** "Is the team learning efficiently? Are calibrations good?"

---

## Database Schema (SQLite Side)

### `project_findings` Table
```sql
CREATE TABLE project_findings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    goal_id TEXT,
    task_id TEXT,
    finding TEXT NOT NULL,
    created_timestamp REAL NOT NULL,
    finding_data TEXT NOT NULL,  -- JSON metadata
    subject TEXT,  -- Auto-detected from directory

    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (goal_id) REFERENCES goals(id)
);
```

### `project_unknowns` Table
```sql
CREATE TABLE project_unknowns (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    goal_id TEXT,
    task_id TEXT,
    unknown TEXT NOT NULL,
    is_resolved BOOLEAN DEFAULT FALSE,
    resolved_by TEXT,
    created_timestamp REAL NOT NULL,
    resolved_timestamp REAL,
    unknown_data TEXT NOT NULL,
    subject TEXT,

    FOREIGN KEY (project_id) REFERENCES projects(id)
);
```

### `mistakes_made` Table
```sql
CREATE TABLE mistakes_made (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    goal_id TEXT,
    mistake TEXT NOT NULL,
    why_wrong TEXT NOT NULL,
    cost_estimate TEXT,
    root_cause_vector TEXT,  -- Epistemic vector at mistake time
    prevention TEXT,
    created_timestamp REAL NOT NULL,
    mistake_data TEXT NOT NULL,

    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (goal_id) REFERENCES goals(id)
);
```

**Note:** Dead ends are stored as a type of finding with `is_dead_end=true` flag.

---

## Epistemic Handoff Integration

### What Gets Handed Off?

When AI A → AI B handoff occurs:

**From SQLite:**
- Session metadata (ai_id, timestamp, CASCADE phase)
- Goals tree (objectives, tasks, completion status)
- PREFLIGHT/CHECK/POSTFLIGHT trajectory
- Mistakes made during session

**From Qdrant (Semantic Context):**
- Top 5 similar learning experiences
- Related findings for current task
- Relevant mistakes/dead ends to avoid

**Combined handoff payload:**
```json
{
  "session_id": "uuid",
  "from_ai": "empirica",
  "to_ai": "sonnet",
  "epistemic_state": {
    "last_checkpoint": "POSTFLIGHT",
    "know": 0.85,
    "uncertainty": 0.20
  },
  "context": {
    "goals": [...],
    "findings": [...],
    "unknowns": [...]
  },
  "semantic_context": {
    "similar_sessions": [
      // Qdrant results for "task similar to current work"
    ],
    "relevant_mistakes": [
      // Qdrant results for "mistakes related to task"
    ]
  }
}
```

---

## Epistemic Learning Loop

```
1. AI starts session → PREFLIGHT (know=0.6, uncertainty=0.7)
2. AI works on task
3. AI logs findings/unknowns in real-time → SQLite + Qdrant
4. AI hits uncertainty → CHECK (confidence too low)
5. AI investigates (Qdrant semantic search for similar experiences)
6. AI completes → POSTFLIGHT (know=0.85, uncertainty=0.3)
7. System extracts trajectory → stores to Qdrant epistemics
8. Next AI queries Qdrant → learns from this session's experience
```

**Key insight:** Qdrant enables *semantic memory* - AIs don't just log what they learned, they can **search for relevant past learning**.

---

## Configuration

### Local Qdrant (Default)

```bash
# Stores in ./.qdrant_data directory
export EMPIRICA_QDRANT_PATH="./.qdrant_data"
```

### Remote Qdrant (Production)

```bash
# Connect to Qdrant Cloud or self-hosted
export EMPIRICA_QDRANT_URL="https://your-qdrant-instance:6333"
```

### Code Usage

```python
from empirica.core.qdrant.vector_store import (
    init_collections,
    upsert_memory,
    search,
    upsert_epistemics,
    search_epistemics
)

# Initialize collections for project
init_collections(project_id="project-uuid")

# Store learning artifact
upsert_memory(
    project_id="project-uuid",
    items=[{
        "id": "finding-123",
        "text": "PKCE requires state parameter",
        "type": "finding"
    }]
)

# Semantic search
results = search(
    project_id="project-uuid",
    query_text="OAuth2 security best practices",
    kind="memory",
    limit=5
)
```

---

## Database Abstraction Layer Compatibility

**Critical:** The new PostgreSQL abstraction layer (feat: add database abstraction layer) affects **ONLY transactional data**:

✅ **Transactional (SQLite/PostgreSQL):**
- Sessions, goals, CASCADE states
- Structured queries, ACID transactions
- Affected by `db_adapter.py`

🔒 **Vector Store (Qdrant) - UNCHANGED:**
- Findings, unknowns, mistakes, epistemic trajectories
- Semantic search via embeddings
- Independent from transactional DB choice

**Why this matters for enterprise:**
- PostgreSQL: Better concurrency for multi-agent sessions
- Qdrant: Already handles concurrent semantic search perfectly
- Both scale independently

---

## Performance Characteristics

| Operation | Transactional DB | Qdrant |
|-----------|------------------|--------|
| **Insert finding** | <1ms (SQLite) | ~5-10ms (embedding + upsert) |
| **Semantic search** | N/A | ~20-50ms (5-10 results) |
| **Trajectory storage** | ~2ms (vectors) | ~10ms (embedding) |
| **Concurrent writes** | Blocking (SQLite), MVCC (PostgreSQL) | Lock-free (Qdrant) |

**Bottleneck:** Embedding generation (OpenAI API call) ~100-200ms

**Optimization:** Batch embeddings when possible

---

## CLI Reference

```bash
# Log learning artifacts
empirica finding-log --project-id <UUID> --finding "..."
empirica unknown-log --project-id <UUID> --unknown "..."
empirica deadend-log --project-id <UUID> --deadend "..."
empirica mistake-log --session-id <UUID> --mistake "..." --why-wrong "..."

# Semantic search
empirica project-search \
  --project-id <UUID> \
  --query "OAuth2 learning" \
  --min-learning 0.2 \
  --limit 10

# Project stats
empirica calibration-report --project-id <UUID>

# Embed project docs
empirica project-embed --project-id <UUID>
```

---

## Implementation Files

**Core:**
- `empirica/core/qdrant/vector_store.py` - Qdrant client wrapper
- `empirica/core/epistemic_trajectory.py` - Trajectory extraction + storage
- `empirica/core/qdrant/embeddings.py` - OpenAI embedding generation

**CLI:**
- `empirica/cli/command_handlers/epistemics_commands.py` - Search/stats
- `empirica/cli/command_handlers/project_commands.py` - Finding/unknown/deadend logging

**Database:**
- `empirica/data/session_database.py` - SQLite methods for findings/unknowns/mistakes

---

## Future Enhancements

**Planned:**
- [ ] Automatic epistemic trajectory storage on POSTFLIGHT (currently manual)
- [ ] Filter epistemics by calibration quality in search
- [ ] Cross-project semantic search (multi-project learning)
- [ ] Epistemic pattern detection (recurring high-learning tasks)
- [ ] Unknown resolution tracking (link resolved unknown to finding)

**Under consideration:**
- [ ] Vector similarity for mistake prevention (alert if similar mistake was made before)
- [ ] Epistemic skill gap detection (find patterns of low-learning areas)
- [ ] Multi-modal embeddings (code + text together)

---

## Summary

**Qdrant integration enables:**
1. ✅ Semantic search of past learning experiences
2. ✅ Pattern recognition across sessions (what leads to high learning?)
3. ✅ Mistake prevention (search before trying similar approaches)
4. ✅ Epistemic handoffs with semantic context
5. ✅ Project-wide learning analytics

**Relationship to transactional DB:**
- Transactional DB (SQLite/PostgreSQL) = Single source of truth
- Qdrant = Semantic index for learning artifacts
- Dual-database architecture optimizes for both ACID + similarity search

**Enterprise impact:**
- PostgreSQL handles multi-agent concurrency (transactional)
- Qdrant handles semantic learning (already concurrent)
- Both scale independently as team grows
