# Handoff System - Session Continuity

**Module:** `empirica.core.handoff`

The Handoff system enables epistemic continuity across sessions, context switches, and AI-to-AI transfers. It captures what was learned, what remains unknown, and what the next session should focus on.

## Philosophy

Sessions end but knowledge persists:
- **Capture deltas**: What changed during this session?
- **Preserve context**: What does the next session need to know?
- **Dual storage**: Git notes (portable) + Database (queryable)
- **Compression**: Token-efficient format for context loading

---

## Architecture

```
Session Complete
      │
      ▼
┌─────────────────────────────────────┐
│  EpistemicHandoffReportGenerator    │
│  ─────────────────────────────────  │
│  • Collects session data            │
│  • Computes epistemic deltas        │
│  • Generates markdown + JSON        │
│  • Compresses for token efficiency  │
└─────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────┐
│       HybridHandoffStorage          │
│  ─────────────────────────────────  │
│  ┌──────────────┐ ┌──────────────┐  │
│  │GitHandoff    │ │DatabaseHandoff│ │
│  │Storage       │ │Storage       │  │
│  │              │ │              │  │
│  │refs/notes/   │ │handoff_      │  │
│  │empirica/     │ │reports       │  │
│  │handoff/      │ │table         │  │
│  └──────────────┘ └──────────────┘  │
└─────────────────────────────────────┘
```

---

## Classes

### EpistemicHandoffReportGenerator

Generates comprehensive handoff reports from session data.

```python
generator = EpistemicHandoffReportGenerator(session_id="abc123")

report = generator.generate_report(
    task_summary="Implemented authentication module",
    preflight_vectors={"know": 0.5, "uncertainty": 0.5},
    postflight_vectors={"know": 0.8, "uncertainty": 0.2},
    findings=["OAuth2 flow requires PKCE", "Token refresh every 15 min"],
    unknowns_resolved=["Which auth provider to use"],
    unknowns_remaining=["Rate limit handling unclear"],
    artifacts=["src/auth/oauth.py", "tests/test_auth.py"]
)
```

**Report contents:**
- `ai_id` - Which AI generated this
- `task_summary` - What was accomplished
- `epistemic_deltas` - Vector changes (PREFLIGHT → POSTFLIGHT)
- `key_findings` - Important learnings
- `knowledge_gaps_filled` - Resolved unknowns
- `remaining_unknowns` - Still open questions
- `recommended_next_steps` - What to do next
- `compressed_json` - Token-efficient format
- `markdown` - Human-readable report

### GitHandoffStorage

Store handoff reports in Git notes for distributed, version-controlled persistence.

```python
storage = GitHandoffStorage(repo_path="/path/to/repo")

# Store handoff
storage.store_handoff(session_id, report)
# Creates: refs/notes/empirica/handoff/{session_id}
# Creates: refs/notes/empirica/handoff/{session_id}/markdown

# Load handoff
handoff = storage.load_handoff(session_id, format='json')
markdown = storage.load_handoff(session_id, format='markdown')

# List all handoffs
session_ids = storage.list_handoffs()
```

**Benefits:**
- Travels with repo (clone, push, pull)
- Version controlled
- Survives database loss
- Human-readable with `git notes show`

### DatabaseHandoffStorage

Store handoff reports in SQLite for fast queries and indexing.

```python
storage = DatabaseHandoffStorage(db_path=".empirica/sessions/sessions.db")

# Store handoff
storage.store_handoff(session_id, report)

# Query by AI or date
recent = storage.query_handoffs(
    ai_id="empirica",
    since="2025-01-01",
    limit=10
)

# List all handoffs
session_ids = storage.list_handoffs()
```

**Benefits:**
- Fast indexed queries
- Filter by AI agent
- Filter by date range
- Relational integrity

### HybridHandoffStorage

Dual storage combining Git notes and Database for best of both worlds.

```python
storage = HybridHandoffStorage(
    repo_path="/path/to/repo",
    db_path=".empirica/sessions/sessions.db"
)

# Store in BOTH backends
result = storage.store_handoff(session_id, report)
# Returns: {'git_stored': True, 'db_stored': True, 'fully_synced': True}

# Load (prefers database for speed, falls back to git)
handoff = storage.load_handoff(session_id, prefer='database')

# Query with automatic merge from git notes
handoffs = storage.query_handoffs(
    ai_id="empirica",
    include_git=True  # Merge git notes not in database
)

# Check sync status
status = storage.check_sync_status(session_id)
# Returns: {'in_git': True, 'in_database': True, 'synced': True}
```

**Strategy:**
- Writes: Store in both backends
- Reads: Prefer database (faster), fallback to git
- Queries: Merge database + git notes for completeness

---

## Handoff Report Structure

```json
{
  "session_id": "abc123-...",
  "ai_id": "empirica",
  "timestamp": "2025-01-07T10:30:00Z",
  "task_summary": "Implemented OAuth2 authentication",
  "duration_seconds": 1800,

  "epistemic_deltas": {
    "know": 0.3,
    "uncertainty": -0.3,
    "clarity": 0.2
  },

  "key_findings": [
    "OAuth2 PKCE flow required for mobile",
    "Token refresh every 15 minutes"
  ],

  "knowledge_gaps_filled": [
    "Which auth provider to use → Auth0"
  ],

  "remaining_unknowns": [
    "Rate limit handling unclear",
    "Token storage security best practices"
  ],

  "recommended_next_steps": [
    "Implement token refresh logic",
    "Add rate limit handling",
    "Write integration tests"
  ],

  "artifacts_created": [
    "src/auth/oauth.py",
    "tests/test_auth.py"
  ],

  "calibration_status": "good",
  "overall_confidence_delta": 0.3,

  "compressed_json": "...",  // Token-efficient format
  "markdown": "..."          // Human-readable report
}
```

---

## CLI Integration

```bash
# Generate handoff at session end
empirica project-handoff --session-id <ID> --output json

# Load handoff for new session
empirica project-bootstrap --session-id <ID>

# List recent handoffs (use project-search for semantic queries)
empirica project-search --task "recent handoffs" --limit 5
```

---

## Source Files

- `empirica/core/handoff/report_generator.py` - Report generation
- `empirica/core/handoff/storage.py` - Dual storage backends
- `empirica/core/validation/handoff_validator.py` - Validation logic
