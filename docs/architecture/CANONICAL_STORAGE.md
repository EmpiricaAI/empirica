# Canonical Storage - The Foundation Layer

**Module:** `empirica.core.canonical`

The Canonical storage layer provides the foundational persistence mechanisms for Empirica. All epistemic state ultimately flows through these classes to reach SQLite, Git notes, or JSON logs.

## Philosophy

This document is the **class-level API reference** for the canonical storage
layer. Empirica persists epistemic state across a four-layer model — **SQLite**
(HOT, structured) · **Git Notes** (WARM, distributed) · **JSON Logs** (AUDIT,
human-readable) · **Qdrant** (SEARCH, semantic) — plus a **MEMORY.md** Claude
Code hot-cache bridge. Every epistemic operation writes to the appropriate
layers based on data type.

For the full data-flow picture — layer comparison, token-compression levels,
verification-table schemas, crypto signing, and the complete storage
walkthrough — see
[STORAGE_ARCHITECTURE_COMPLETE.md](./STORAGE_ARCHITECTURE_COMPLETE.md).

**Related docs:**
- [STORAGE_ARCHITECTURE_COMPLETE.md](./STORAGE_ARCHITECTURE_COMPLETE.md) - Visual guide with diagrams and data flow
- [Qdrant API Reference](../reference/api/qdrant.md) - Embedding providers and semantic search API
- [QDRANT_EPISTEMIC_INTEGRATION.md](./QDRANT_EPISTEMIC_INTEGRATION.md) - Qdrant architecture and collections

---

## Architecture

```
                   ┌───────────────────────────────────┐
                   │     GitEnhancedReflexLogger       │
                   │     (Unified Storage Interface)    │
                   └───────────────────────────────────┘
                              │
       ┌──────────────────────┼──────────────────────┐
       │           │          │          │           │
       ▼           ▼          ▼          ▼           │
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  SQLite  │ │Git Notes │ │   JSON   │ │  Qdrant  │  │
│   (HOT)  │ │  (WARM)  │ │ (AUDIT)  │ │ (SEARCH) │  │
└──────────┘ └──────────┘ └──────────┘ └──────────┘  │
       │           │          │          │           │
       ▼           ▼          ▼          ▼           │
  sessions.db  refs/notes/ .empirica/  localhost:    │
               empirica/   logs/       6333          │
                                                     │
                   ┌─────────────────────────────────┘
                   │  QdrantMemory (Semantic Layer)
                   ▼
            ┌──────────────────────────────────────┐
            │  EmbeddingProvider                   │
            │  (Jina, Voyage, Ollama, OpenAI)      │
            └──────────────────────────────────────┘
```

---

## Core Classes

### GitNotesStorage

Low-level git notes storage for epistemic checkpoints.

```python
storage = GitNotesStorage(
    session_id="abc123",
    git_repo_path=Path("."),
    signing_persona=signing_persona  # Optional cryptographic signing
)

# Add checkpoint to git notes
note_sha = storage.add_note({
    "phase": "CHECK",
    "round": 1,
    "vectors": {"know": 0.7, "uncertainty": 0.3},
    "timestamp": time.time()
})
# Creates: refs/notes/empirica/session/{session_id}/CHECK/1

# Retrieve checkpoints
checkpoints = storage.get_checkpoints(
    session_id="abc123",
    phase_filter="CHECK",
    limit=10
)
```

**Namespace hierarchy:**
```
refs/notes/empirica/
├── session/{session_id}/
│   ├── PREFLIGHT/1
│   ├── CHECK/1
│   ├── POSTFLIGHT/1
│   └── ...
├── handoff/{session_id}
├── tasks/{goal_id}
└── ...
```

### GitStateCapture

Captures current git repository state for checkpoint context.

```python
capture = GitStateCapture(repo_path=Path("."))

state = capture.capture_state()
# Returns: {
#     "commit_hash": "abc1234",
#     "branch": "main",
#     "dirty": False,
#     "staged_files": [],
#     "unstaged_files": []
# }
```

### CheckpointStorage

Stores and retrieves epistemic checkpoints with optional signing.

```python
storage = CheckpointStorage(
    db_path=".empirica/sessions/sessions.db",
    signing_enabled=True
)

# Store checkpoint
checkpoint_id = storage.store_checkpoint(
    session_id="abc123",
    phase="CHECK",
    vectors={"know": 0.7, "uncertainty": 0.3},
    reasoning="Gate check for praxic transition",
    metadata={"goal_id": "xyz789"}
)

# Retrieve checkpoint
checkpoint = storage.get_checkpoint(checkpoint_id)

# List session checkpoints
checkpoints = storage.list_checkpoints(
    session_id="abc123",
    phase="CHECK"
)
```

### GitGoalStore

Git-backed storage for goals with cryptographic integrity.

```python
store = GitGoalStore(repo_path=".")

# Store goal in git notes
store.store_goal(
    goal_id="xyz789",
    objective="Implement OAuth2",
    success_criteria=["Tests pass", "Code reviewed"],
    session_id="abc123"
)

# Retrieve goal
goal = store.get_goal("xyz789")

# List session goals
goals = store.list_session_goals("abc123")
```

### VectorState

Data class representing epistemic vector state at a point in time.

```python
@dataclass
class VectorState:
    know: float
    uncertainty: float
    context: float
    clarity: float
    coherence: float
    signal: float
    density: float
    engagement: float
    state: float
    change: float
    completion: float
    impact: float
    timestamp: float
```

### SessionSync

Synchronizes session state across storage layers.

```python
sync = SessionSync(session_id="abc123")

# Sync session to all storage layers
result = sync.sync_all()
# Returns: {'sqlite': True, 'git_notes': True, 'json_logs': True}

# Check sync status
status = sync.check_sync_status()
# Returns: {'in_sync': True, 'last_sync': 1704628800}
```

---

## Sentinel Integration

### SentinelState

Represents Sentinel gate state at CHECK time.

```python
@dataclass
class SentinelState:
    decision: SentinelDecision  # PROCEED, INVESTIGATE, HALT
    vectors: Dict[str, float]
    corrected_vectors: Dict[str, float]
    gate_passed: bool
    reasoning: str
```

### SentinelDecision (Enum)

```python
class SentinelDecision(Enum):
    PROCEED = "proceed"
    INVESTIGATE = "investigate"
    HALT = "halt"
    ESCALATE = "escalate"
```

### TurtleStatus (Enum)

Investigation branch status (multi-agent investigation).

```python
class TurtleStatus(Enum):
    ACTIVE = "active"
    MERGED = "merged"
    ABANDONED = "abandoned"
    BLOCKED = "blocked"
```

### SentinelHooks

Git hooks integration for Sentinel enforcement.

```python
hooks = SentinelHooks(repo_path=".")

# Install pre-commit hook
hooks.install_pre_commit()

# Check if commit should be allowed
allowed = hooks.check_commit(
    commit_message="feat: Add OAuth2 flow",
    vectors={"know": 0.7, "uncertainty": 0.3}
)
```

---

## Storage Paths

| Storage | Path | Purpose |
|---------|------|---------|
| SQLite | `.empirica/sessions/sessions.db` | Primary structured data |
| Git Notes | `refs/notes/empirica/...` | Distributed, version-controlled |
| JSON Logs | `.empirica/logs/*.jsonl` | Human-readable audit trail |
| Lessons | `.empirica/lessons/*.yaml` | Cold storage for lessons |
| Qdrant | `localhost:6333` | Semantic vector search |
| MEMORY.md | `~/.claude/projects/{key}/memory/MEMORY.md` | Claude Code hot cache (bridge) |

**MEMORY.md key derivation:** The `{key}` is the absolute project path with `/` replaced by `-`.
For example, `/home/user/code/myapp` → `-home-user-code-myapp`.

---

## Qdrant Integration

### QdrantMemory

Semantic storage and retrieval for epistemic data.

```python
from empirica.core.canonical import QdrantMemory

memory = QdrantMemory(
    url="http://localhost:6333",
    collection_prefix="empirica"
)

# Store finding with embedding
memory.store_finding(
    finding_id="abc123",
    text="JWT tokens not validated on every request",
    metadata={"impact": 0.8, "session_id": "xyz"}
)

# Semantic search
results = memory.search_findings(
    query="authentication vulnerabilities",
    limit=5,
    threshold=0.7
)
```

### EmbeddingProvider

Multi-provider embedding generation.

```python
from empirica.core.canonical import EmbeddingProvider

# Configure via environment
# EMPIRICA_EMBEDDING_PROVIDER=jina
# EMPIRICA_EMBEDDING_MODEL=jina-embeddings-v3
# JINA_API_KEY=...

provider = EmbeddingProvider.from_env()

# Generate embeddings
embedding = provider.embed("JWT validation security pattern")
# Returns: [0.123, 0.456, ...] (1024-dim vector)

# Batch embeddings
embeddings = provider.embed_batch([
    "Authentication patterns",
    "Authorization flows",
    "Token validation"
])
```

### Supported Providers

The provider → model → dimensions matrix (Jina, Voyage, Ollama, OpenAI, and the
local hash-based fallback) is in
[STORAGE_ARCHITECTURE_COMPLETE.md § Layer 4](./STORAGE_ARCHITECTURE_COMPLETE.md#layer-4-qdrant-vector-database-semantic-search)
and the [Qdrant API Reference](../reference/api/qdrant.md). Configure via
`EMPIRICA_EMBEDDING_PROVIDER` / `EMPIRICA_EMBEDDING_MODEL` plus the provider's
API-key env var (see the `EmbeddingProvider.from_env()` example above).

### Collections

| Collection | Content | Auto-populated by |
|------------|---------|-------------------|
| `empirica_findings` | Learnings | `finding-log` |
| `empirica_unknowns` | Questions | `unknown-log` |
| `empirica_dead_ends` | Failed approaches | `deadend-log` |
| `empirica_lessons` | Procedural knowledge | `lesson-create` |

---

## Claude Code Bridge (MEMORY.md Hot Cache)

At session end, the `session-end-postflight` hook curates the top epistemic
artifacts (max 12, project-scoped, ranked by
`impact × type_confidence × recency_decay`) into Claude Code's
`~/.claude/projects/{key}/memory/MEMORY.md`, preserving manual content between
`<!-- empirica-auto-start -->` / `<!-- empirica-auto-end -->` delimiters.
Multiple Claude instances on one project share this file (swarm learning).

The full data-flow diagram and the ranking-formula breakdown live in
[STORAGE_ARCHITECTURE_COMPLETE.md § Layer 5](./STORAGE_ARCHITECTURE_COMPLETE.md#layer-5-claude-code-bridge-memorymd-hot-cache).

**Source:** `plugins/claude-code-integration/hooks/session-end-postflight.py`

---

## Git Isomorphism

Every epistemic state maps to a git state:
- Session checkpoints → git notes
- Goals → git notes + database
- Findings → git notes + database + Qdrant

This enables:
- Versioned confidence tracking
- Repo-portable epistemic history
- Audit trail for compliance

---

## Source Files

- `empirica/core/canonical/git_notes_storage.py` - GitNotesStorage
- `empirica/core/canonical/git_state_capture.py` - GitStateCapture
- `empirica/core/canonical/checkpoint_storage.py` - CheckpointStorage
- `empirica/core/canonical/git_enhanced_reflex_logger.py` - Unified logger
- `empirica/core/canonical/empirica_git/goal_store.py` - GitGoalStore
- `empirica/core/canonical/empirica_git/sentinel_hooks.py` - SentinelHooks, SentinelState
- `empirica/core/canonical/empirica_git/session_sync.py` - SessionSync
