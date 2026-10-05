# Canonical Storage - The Foundation Layer

**Modules:** `empirica.core.canonical`, `empirica.core.canonical.empirica_git`, `empirica.core.qdrant`

This is the module-level reference for how epistemic state reaches disk. Empirica persists it in four places plus a Claude Code bridge: **SQLite** (the working store), **git notes** (the replicated mirror), **JSON logs** (checkpoint audit files) and **Qdrant** (semantic search, optional). For the data-flow picture, the layer comparison and the verification tables, read [STORAGE_ARCHITECTURE_COMPLETE.md](./STORAGE_ARCHITECTURE_COMPLETE.md). For the sync model, read [SYNC_ARCHITECTURE.md](./SYNC_ARCHITECTURE.md).

**Related docs:**
- [STORAGE_ARCHITECTURE_COMPLETE.md](./STORAGE_ARCHITECTURE_COMPLETE.md) - layers, schemas, data flow
- [SYNC_ARCHITECTURE.md](./SYNC_ARCHITECTURE.md) - notes replication and rebuild
- [Qdrant API Reference](../reference/api/qdrant.md) - embeddings and semantic search API
- [QDRANT_EPISTEMIC_INTEGRATION.md](./QDRANT_EPISTEMIC_INTEGRATION.md) - Qdrant collections and use

---

## Philosophy

SQLite is what the CLI reads and writes. Git notes are the mirror that travels with the repository, so a fresh machine or a corrupted database can be rebuilt from them (`empirica rebuild`, `empirica sync-pull --rebuild`, `empirica profile-sync`). Qdrant is derived: it can be re-embedded from SQLite at any time (`empirica rebuild --qdrant-only`) and the system works without it.

Two things are deliberately not mirrored. The calibration tables `grounded_beliefs` and `grounded_verifications` are SQLite-only, so a rebuild from notes cannot restore them. CRM rows (organizations, contacts, engagements) are not stored here at all; they are canonical in crm-mcp, and core's `entity_registry` rows for them are stale join ids.

## Architecture

```
  CLI verbs / hooks                      artifact stores (one per type)
        │                                  GitFindingStore  GitUnknownStore  GitDeadEndStore
        │ checkpoints                      GitMistakeStore   GitDecisionStore GitAssumptionStore
        ▼                                  GitGoalStore      GitSourceStore   GitMessageStore
  GitEnhancedReflexLogger                           │
   ├─ GitStateCapture  (HEAD, commits, dirty)       │ refs/notes/empirica/<type>/<id>
   ├─ GitNotesStorage ──► refs/notes/empirica/session/<session_id>/<PHASE>/<round>
   └─ CheckpointStorage                             │
        ├─ save_to_sqlite ──► reflexes (pointer to the note)
        └─ save_to_json   ──► .empirica_reflex_logs/checkpoints/<session_id>/
                                                    │
   SQLite  <project>/.empirica/sessions/sessions.db ◄┘  (working store; ProfileImporter rebuilds it from notes)
        │
        └─ derived: empirica.core.qdrant
             ├─ collections.py   project_<id>_{memory,docs,eidetic,episodic,goals,...}, global_learnings
             ├─ memory.py        upsert_memory, upsert_docs, search
             ├─ embeddings.py    EmbeddingsProvider (get_embedding, get_embedding_provider)
             └─ rebuild.py       rebuild_qdrant_from_db

  note_lifecycle.py: gardening reaches notes (archive on delete, stamp on resolve)
  SessionSync / sync-push: replicate refs/notes/empirica/* between machines
```

The unifying interface is not one class. Checkpoints (PREFLIGHT, CHECK, POSTFLIGHT vectors) go through `GitEnhancedReflexLogger`; each artifact type has its own store in `empirica_git/`, called by the `*-log` verbs and `log-artifacts`.

---

## Core Classes

### GitEnhancedReflexLogger

`empirica/core/canonical/git_enhanced_reflex_logger.py`. Orchestrates one checkpoint write across the layers.

```python
logger = GitEnhancedReflexLogger(session_id="abc123", enable_git_notes=True)

logger.add_checkpoint(
    phase="CHECK", round_num=1,
    vectors={"know": 0.7, "uncertainty": 0.3},
    metadata={"task": "..."},
)
last = logger.get_last_checkpoint(max_age_hours=24, phase="CHECK")
rows = logger.list_checkpoints(...)
diff = logger.get_vector_diff(since_checkpoint=last, current_vectors={"know": 0.8})
```

`add_checkpoint` builds a compressed checkpoint, writes a git note first (signed when a `SigningPersona` is supplied) so the SQLite row can point at it, then writes the SQLite row and the JSON file. Without git it degrades to SQLite and JSON only. `round_num` auto-increments from the `reflexes` table when omitted.

### GitNotesStorage

`git_notes_storage.py`. Reads and writes checkpoint notes, one ref per checkpoint, attached to `HEAD`:

```
refs/notes/empirica/session/<session_id>/<PHASE>/<round>
```

`add_note`, `add_signed_note`, `get_latest_note(phase=None)`, `list_checkpoints(session_id, limit, phase)` (discovers refs with `git for-each-ref`). Non-git projects and repositories with no commits are skipped silently.

### GitStateCapture

`git_state_capture.py`. `capture_state(get_last_checkpoint_fn=None)` returns `head_commit`, `commits_since_last_checkpoint` and `uncommitted_changes`, or `{}` outside a usable repository. It lets a checkpoint be correlated with code changes.

### CheckpointStorage

`checkpoint_storage.py`. The SQLite and JSON half of a checkpoint, constructed as `CheckpointStorage(session_id, base_log_dir)`:

- `save_to_sqlite(checkpoint, git_commit_sha=None, git_notes_ref=None)` writes a pointer row to `reflexes`. Git is the authoritative copy and SQLite is the queryable index.
- `load_from_sqlite(phase=None, max_age_hours=24)`
- `save_to_json(checkpoint)` writes `checkpoints/<session_id>/checkpoint_<PHASE>_<round>_<timestamp>.json` under `base_log_dir`, which defaults to `.empirica_reflex_logs`.

### Artifact stores (`empirica/core/canonical/empirica_git/`)

Each store writes one note per artifact under `refs/notes/empirica/<type>/<id>`.

| Store | Ref prefix | Main methods |
|---|---|---|
| `GitFindingStore` | `findings/` | `store_finding`, `load_finding`, `resolve_finding`, `discover_findings`, `count_findings` |
| `GitUnknownStore` | `unknowns/` | `store_unknown`, `load_unknown`, `resolve_unknown`, `discover_unknowns` |
| `GitDeadEndStore` | `dead_ends/` | `store_dead_end`, `load_dead_end`, `discover_dead_ends`, `search_similar` |
| `GitMistakeStore` | `mistakes/` | `store_mistake`, `load_mistake`, `discover_mistakes`, `get_by_root_cause` |
| `GitDecisionStore` | `decisions/` | `store_decision`, `load_decision`, `discover_decisions` |
| `GitAssumptionStore` | `assumptions/` | `store_assumption`, `load_assumption`, `discover_assumptions` |
| `GitSourceStore` | `sources/` | `store_source` |
| `GitGoalStore` | `goals/` | `store_goal`, `load_goal`, `discover_goals`, `add_lineage` |
| `GitMessageStore` | `messages/<channel>/` | `send_message`, `get_inbox`, `mark_read`, `reply`, `get_thread`, `cleanup_expired` |

`CheckpointManager` and `auto_checkpoint` write to `refs/notes/empirica/checkpoints`. A live repository also holds `handoff/` and `tasks/` refs under `refs/notes/empirica/`; I did not trace their writers for this page. Session checkpoint refs are the only ones with the deeper `<session_id>/<PHASE>/<round>` path.

### Note lifecycle (`note_lifecycle.py`, `note_reconcile.py`)

Notes are the log that `rebuild` imports back into SQLite, so a note that disagrees with SQLite is a pending revert. `note_lifecycle` keeps gardening honest about that:

```
refs/notes/empirica/<type>/<id>            active, mirrors SQLite
refs/notes/empirica-archive/<type>/<id>    the journey of what gardening removed
```

Deleting an artifact archives its note (`archive_note`) instead of destroying the ref; resolving stamps the active note (`stamp_resolution`). `note_reconcile` plans and applies the repair for history that diverged before this existed (`plan`, `apply`), and `doctor` reports the divergence. Archive refs live outside `refs/notes/empirica/*`, so `sync-push` does not replicate them. One known gap: invalidation of dead-ends and mistakes in `resolve-artifacts` filter mode is SQLite-only, so a rebuild would show them as valid again.

### ProfileImporter

`profile_import.py`. The reverse path: reads notes (findings, unknowns, dead-ends, mistakes, decisions, assumptions, goals) and imports them into SQLite idempotently, preserving UUIDs. Its docstring names `empirica profile-sync` as the caller; the `rebuild --from-notes` path is described in [SYNC_ARCHITECTURE.md](./SYNC_ARCHITECTURE.md).

### SessionSync

`session_sync.py`. `pull_latest(notes_only=True)` fetches `refs/notes/empirica/*` from `origin`; `push_checkpoint(notes_only=True)` pushes it. `auto_sync_before_resume` and `auto_sync_after_checkpoint(auto_push=False)` wrap them. The user-facing replication path is `empirica sync-push`, `sync-pull`, `sync-status` and `profile-sync`; the namespaces `sync-push` sends are the `_PUSH_REFSPECS` tuple in `sync_commands.py` (`empirica/*`, `breadcrumbs`, `empirica-precompact`).

### VectorState

`reflex_frame.py`. One dimension of an assessment, not the whole vector set:

```python
@dataclass
class VectorState:
    score: float            # 0.0-1.0, validated
    rationale: str
    evidence: str | None = None
    warrants_investigation: bool = False
    investigation_priority: str | None = None   # low | medium | high | critical
    investigation_reason: str | None = None
```

The module also holds `Action` (`PROCEED`, `INVESTIGATE`, `CLARIFY`, `RESET`, `STOP`) and `CANONICAL_WEIGHTS` (foundation 0.35, comprehension 0.25, execution 0.25, engagement 0.15). The full 13-vector assessment is `EpistemicAssessmentSchema` in `empirica.core.schemas.epistemic_assessment`.

---

## Sentinel hooks

`empirica_git/sentinel_hooks.py` is an optional in-process hook layer, not the CHECK gate. `SentinelHooks` (class-level state) lets evaluators register against checkpoints and, with turtle mode, checks the Sentinel's own grounding before it observes.

- `SentinelDecision`: `PROCEED`, `INVESTIGATE`, `BRANCH`, `REVISE`, `HALT`, `HANDOFF`, `ESCALATE`, `BLOCK`.
- `TurtleStatus`: `CRYSTALLINE`, `SOLID`, `EMERGENT`, `FORMING`, `DARK`.
- `SentinelState`: `evaluator_health`, `decision_consistency`, `response_latency`, `evaluation_count`, `last_decision`, `confidence`, `last_turtle_check`, with `get_grounding_score()` and `get_turtle_status()`.
- `SentinelHooks`: `register_evaluator`, `evaluate_checkpoint`, `post_checkpoint_hook`, `turtle_check`, `enable_turtle_mode`, `enable_looping`, `is_enabled`.

There is no git pre-commit installer in this module.

---

## Storage Paths

| Storage | Path | Purpose |
|---|---|---|
| SQLite | `<project>/.empirica/sessions/sessions.db` (resolved by `get_session_db_path`) | Working store |
| Git notes | `refs/notes/empirica/<type>/<id>`, `refs/notes/empirica/session/<session_id>/<PHASE>/<round>` | Replicated mirror |
| Archived notes | `refs/notes/empirica-archive/<type>/<id>` | Gardening history, not pushed |
| Checkpoint JSON | `.empirica_reflex_logs/checkpoints/<session_id>/` | Per-checkpoint audit files |
| Lessons | `.empirica/lessons/` (YAML cold storage) | Lesson files |
| Qdrant | `EMPIRICA_QDRANT_URL`, else `localhost:6333`, unless a URL resolver is installed | Semantic search |
| MEMORY.md | `~/.claude/projects/{key}/memory/MEMORY.md` | Claude Code hot cache |

MEMORY.md key derivation: `{key}` is the absolute project path with `/` replaced by `-`; `/home/user/code/myapp` becomes `-home-user-code-myapp`.

---

## Qdrant (`empirica/core/qdrant`)

Optional. Core works without it; set `EMPIRICA_ENABLE_EMBEDDINGS=true` to enable semantic features. The package `__init__` exports only `PersonaRegistry`; import from the submodules.

### Collections (`collections.py`)

Per project, named `project_<project_id>_<kind>`:

| Kind | Content |
|---|---|
| `memory` | findings, unknowns, mistakes, dead-ends (`upsert_memory`) |
| `docs` | project documents (`upsert_docs`) |
| `eidetic` | stable facts with confidence |
| `episodic` | session narratives with temporal decay |
| `goals` | goals and tasks |
| `epistemics` | learning trajectories (PREFLIGHT to POSTFLIGHT deltas) |
| `calibration` | grounded calibration summaries |
| `assumptions`, `decisions`, `intents` | epistemic intent layer |

Global: `global_learnings`, `global_eidetic`, `workspace_index`. `init_collections(project_id)` creates them; `get_collection_info()` lists them.

### Memory and search (`memory.py`)

```python
from empirica.core.qdrant.memory import search, upsert_memory

upsert_memory("my-project", [
    {"id": "abc123", "type": "finding",
     "text": "JWT tokens not validated on every request", "session_id": "xyz"},
])
results = search("my-project", "authentication vulnerabilities", kind="focused", limit=5)
```

`search` takes `kind` of `"focused"` (docs, memory, eidetic, episodic; the default), `"all"` (alias of focused), `"intelligence"` (memory, eidetic, episodic, assumptions, decisions, goals), or one collection name. It returns empty results when Qdrant is unavailable and hides resolved artifacts unless `include_resolved=True`. Both functions accept `qdrant_url` for per-request routing.

### Embeddings (`embeddings.py`)

```python
from empirica.core.qdrant.embeddings import get_embedding, get_embedding_provider

vec = get_embedding("JWT validation security pattern")        # list[float]
provider = get_embedding_provider()                           # EmbeddingsProvider
vecs = provider.batch_embed(["Authentication patterns", "Token validation"])
```

`EmbeddingsProvider` resolves its provider and model from, in order, the environment (`EMPIRICA_EMBEDDINGS_PROVIDER`, `EMPIRICA_EMBEDDINGS_MODEL`), then `~/.empirica/embeddings.conf`, then code defaults; `auto` picks a provider and falls back to a local one. The provider and model matrix is in [STORAGE_ARCHITECTURE_COMPLETE.md § Layer 4](./STORAGE_ARCHITECTURE_COMPLETE.md#layer-4-qdrant-vector-database-semantic-search) and the [Qdrant API Reference](../reference/api/qdrant.md). Per-project URL routing is a hook (`set_url_resolver`); with none installed the environment variable and then localhost apply.

Other modules: `eidetic.py`, `episodic.py`, `global_sync.py` (cross-project sync and search), `resolution_sync.py` (marks resolved artifacts), `rebuild.py` (`rebuild_qdrant_from_db`). CLI: `empirica qdrant-status`, `empirica rebuild --qdrant` or `--qdrant-only`.

---

## Claude Code Bridge (MEMORY.md Hot Cache)

At session end, the `session-end-postflight` hook curates the top epistemic artifacts (max 12, project-scoped, ranked by `impact × type_confidence × recency_decay`) into Claude Code's `~/.claude/projects/{key}/memory/MEMORY.md`, preserving manual content between `<!-- empirica-auto-start -->` and `<!-- empirica-auto-end -->`. Several Claude instances on one project share the file.

The data-flow diagram and the ranking breakdown are in [STORAGE_ARCHITECTURE_COMPLETE.md § Layer 5](./STORAGE_ARCHITECTURE_COMPLETE.md#layer-5-claude-code-bridge-memorymd-hot-cache).

**Source:** `empirica/plugins/claude-code-integration/hooks/session-end-postflight.py`

---

## Source Files

- `empirica/core/canonical/git_enhanced_reflex_logger.py` - GitEnhancedReflexLogger
- `empirica/core/canonical/git_notes_storage.py` - GitNotesStorage
- `empirica/core/canonical/git_state_capture.py` - GitStateCapture
- `empirica/core/canonical/checkpoint_storage.py` - CheckpointStorage
- `empirica/core/canonical/reflex_frame.py` - VectorState, Action, CANONICAL_WEIGHTS
- `empirica/core/canonical/empirica_git/*_store.py` - per-type note stores
- `empirica/core/canonical/empirica_git/note_lifecycle.py`, `note_reconcile.py` - archive, stamp, repair
- `empirica/core/canonical/empirica_git/profile_import.py` - notes to SQLite
- `empirica/core/canonical/empirica_git/session_sync.py`, `checkpoint_manager.py`, `sentinel_hooks.py`
- `empirica/core/qdrant/` - collections, memory, embeddings, rebuild
