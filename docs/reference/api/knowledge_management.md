# Knowledge Management API

**Module:** `empirica.data.repositories.breadcrumbs` (core implementation)
**Category:** Knowledge & Learning Management
**Stability:** Prose is hand-written; the Verified API surface section is generated from source


> ### Verified against the code
>
> The entries that documented functions which do not exist have been removed
> (43 across four files, 2026-08-01), and a **Verified API surface** section
> generated from source is appended below. `tests/test_api_docs_symbols_exist.py`
> fails CI if a function that does not exist is documented here again.

---

## Overview

The Knowledge Management API provides tools for capturing, organizing, and retrieving knowledge artifacts during AI workflows. This includes:

- Finding capture and retrieval
- Unknown tracking and resolution
- Dead end logging to prevent repeated failures
- Reference document management
- Epistemic source attribution

---

## Breadcrumb Repository

### `class BreadcrumbRepository`

Central repository for tracking knowledge artifacts (findings, unknowns, dead ends).

#### `__init__(self, db_path: Optional[str] = None)`

Initialize the breadcrumb repository.

**Parameters:**
- `db_path: Optional[str]` - Database path, defaults to standard location

**Example:**
```python
from empirica.data.repositories.breadcrumbs import BreadcrumbRepository

breadcrumb_repo = BreadcrumbRepository()
```

### `log_finding(self, project_id: str, session_id: str, finding: str, goal_id: Optional[str] = None, task_id: Optional[str] = None, subject: Optional[str] = None, tags: Optional[List[str]] = None) -> str`

Log a new finding discovered during work.

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: str` - Session where finding was made
- `finding: str` - Description of the finding
- `goal_id: Optional[str]` - Optional associated goal
- `task_id: Optional[str]` - Optional associated task
- `subject: Optional[str]` - Optional subject area
- `tags: Optional[List[str]]` - Optional tags for categorization

**Returns:** `str` - Finding ID

**Example:**
```python
finding_id = breadcrumb_repo.log_finding(
    project_id="proj-123",
    session_id="sess-456",
    finding="Discovered that bcrypt is 3x slower than argon2 for password hashing",
    goal_id="goal-789",
    tags=["performance", "security", "authentication"]
)
```

### `log_unknown(self, project_id: str, session_id: str, unknown: str, goal_id: Optional[str] = None, task_id: Optional[str] = None, subject: Optional[str] = None, tags: Optional[List[str]] = None) -> str`

Log an unknown or unresolved question.

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: str` - Session where unknown was identified
- `unknown: str` - Description of the unknown
- `goal_id: Optional[str]` - Optional associated goal
- `task_id: Optional[str]` - Optional associated task
- `subject: Optional[str]` - Optional subject area
- `tags: Optional[List[str]]` - Optional tags for categorization

**Returns:** `str` - Unknown ID

**Example:**
```python
unknown_id = breadcrumb_repo.log_unknown(
    project_id="proj-123",
    session_id="sess-456",
    unknown="What are the performance requirements for auth system?",
    goal_id="goal-789",
    tags=["requirements", "performance"]
)
```

### `resolve_unknown(self, unknown_id: str, resolution: str, resolved_by: str, resolution_method: Optional[str] = None) -> bool`

Mark an unknown as resolved.

**Parameters:**
- `unknown_id: str` - Unknown identifier
- `resolution: str` - Resolution description
- `resolved_by: str` - Identifier of resolver
- `resolution_method: Optional[str]` - Method used for resolution

**Returns:** `bool` - True if resolution successful

**Example:**
```python
success = breadcrumb_repo.resolve_unknown(
    unknown_id="unk-123",
    resolution="Performance requirements are 1000 req/sec with <100ms latency",
    resolved_by="claude-sonnet-4",
    resolution_method="stakeholder_interview"
)
```

### `log_dead_end(self, project_id: str, session_id: str, approach: str, why_failed: str, goal_id: Optional[str] = None, task_id: Optional[str] = None, subject: Optional[str] = None, tags: Optional[List[str]] = None) -> str`

Log a failed approach or dead end.

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: str` - Session where failure occurred
- `approach: str` - Description of the failed approach
- `why_failed: str` - Explanation of why it failed
- `goal_id: Optional[str]` - Optional associated goal
- `task_id: Optional[str]` - Optional associated task
- `subject: Optional[str]` - Optional subject area
- `tags: Optional[List[str]]` - Optional tags for categorization

**Returns:** `str` - Dead end ID

**Example:**
```python
dead_end_id = breadcrumb_repo.log_dead_end(
    project_id="proj-123",
    session_id="sess-456",
    approach="Using JWT tokens without refresh mechanism",
    why_failed="Caused frequent re-authentication for users",
    tags=["authentication", "usability", "security"]
)
```

## Reference Document Management

> **Note:** The `ReferenceDocumentManager` class is planned but not yet implemented as a separate class.
> Reference document functionality is currently available via `BreadcrumbRepository.add_reference_doc()`.

### `class ReferenceDocumentManager` (Planned)

Will manage reference documents for projects.

#### `__init__(self, db_path: Optional[str] = None)`

Initialize the reference document manager.

**Parameters:**
- `db_path: Optional[str]` - Database path, defaults to standard location

**Current Alternative:**
```python
from empirica.data.repositories.breadcrumbs import BreadcrumbRepository

repo = BreadcrumbRepository()
repo.add_reference_doc(session_id, title, path, doc_type, content_hash)
```

## Epistemic Source Tracking

> **Note:** The `EpistemicSourceTracker` class is planned but not yet implemented as a separate class.
> Source tracking functionality is available via the `epistemic_sources` table in the session database.

### `class EpistemicSourceTracker` (Planned)

Will track sources of epistemic knowledge and their reliability.

#### `__init__(self, db_path: Optional[str] = None)`

Initialize the epistemic source tracker.

**Parameters:**
- `db_path: Optional[str]` - Database path, defaults to standard location

**Current Alternative:**
```python
# Source tracking is done via project_management API
from empirica.data.repositories.projects import ProjectRepository

repo = ProjectRepository()
repo.add_epistemic_source(project_id, source_type, title, ...)
```

## Knowledge Analytics (Planned)

> **Note:** These analytics functions are planned but not yet implemented.

## Knowledge Utilities

## Claude Code Bridge

Breadcrumb data (findings, unknowns, dead-ends, goals, mistakes) feeds into Claude Code's
`MEMORY.md` hot cache at session end via the epistemic summarizer. This means breadcrumbs
logged via this API are automatically surfaced in the next Claude Code session, ranked by
`impact × type_confidence × recency_decay`.

**Source:** `plugins/claude-code-integration/hooks/session-end-postflight.py` → `update_memory_hot_cache()`
**See also:** [claude-code-symbiosis.md](../../architecture/claude-code-symbiosis.md)

---

## Best Practices

1. **Log findings promptly** - Capture discoveries immediately to preserve context.

2. **Tag consistently** - Use consistent tags to enable effective filtering and search.

3. **Track unknowns systematically** - Log all uncertainties to enable systematic resolution.

4. **Document dead ends** - Prevent repeated failures by logging unsuccessful approaches.

5. **Rate source confidence** - Accurately assess source reliability for proper weighting.

6. **Maintain knowledge graphs** - Use relationships between artifacts for deeper insights.

7. **Regular cleanup** - Remove outdated information to maintain relevance.

8. **Export periodically** - Create backups and documentation snapshots.

---

## CLI Commands

For command-line usage, see [CLI Commands Reference](../../human/developers/CLI_COMMANDS_UNIFIED.md).

### Breadcrumb Logging
```bash
empirica finding-log --session-id <ID> --finding "..." --impact 0.7
empirica unknown-log --session-id <ID> --unknown "..."
empirica unknown-resolve --unknown-id <UUID> --resolved-by "..."
empirica deadend-log --session-id <ID> --approach "..." --why-failed "..."
empirica mistake-log --session-id <ID> --mistake "..." --recovery "..."
```

### Querying
```bash
empirica query findings --session-id <ID> --output json
empirica query unknowns --session-id <ID> --output json
empirica query dead-ends --session-id <ID> --output json
```

---

## Error Handling

Methods typically raise:
- `ValueError` for invalid parameters
- `sqlite3.Error` for database issues
- `KeyError` when referenced entities don't exist
- `RuntimeError` for state-related issues

---

## Batch Artifact Verbs (Graph API)

Three CLI verbs for connected-artifact operations. Each accepts JSON on
stdin (or from a file via positional arg) and supports `--schema` to
print the input shape without touching the DB.

### `empirica log-artifacts -`

Batch-create connected artifacts in one call. Nodes are typed artifacts
(`finding`, `unknown`, `dead_end`, `mistake`, `assumption`, `decision`,
`source`); edges are typed relationships (`evidence`, `raised_by`,
`grounded_by`, `resolves`, `invalidates`, `sourced_from`, `caused_by`,
`prevents`, `attached_to`).

```bash
empirica log-artifacts --schema     # print full input shape + valid types

echo '{
  "nodes": [
    {"ref": "f1", "type": "finding",
     "data": {"finding": "X is Y", "impact": 0.7}},
    {"ref": "d1", "type": "decision",
     "data": {"choice": "use Y", "rationale": "because X"}}
  ],
  "edges": [
    {"from": "f1", "to": "d1", "relation": "evidence"}
  ]
}' | empirica log-artifacts -
```

**Forgiving aliases** (since v1.8.14): `id` and `node_id` are accepted as
aliases for `ref` on nodes; `type` and `kind` are accepted as aliases for
`relation` on edges. Aliases are normalized before validation; canonical
names are surfaced in `alias_warnings` on success.

**Validation errors** include a `hint` field pointing at `--schema` and
naming the common pitfalls (nodes need `ref` not `id`, edges need
`relation` not `type`).

### `empirica resolve-artifacts -`

Batch-resolve open artifacts (unknowns, assumptions, goals).

```bash
empirica resolve-artifacts --schema

echo '{
  "resolutions": [
    {"type": "unknown", "id": "abc-123",
     "resolution": "answered: see finding f1"},
    {"type": "assumption", "id": "def-456",
     "verified": true, "resolution": "confirmed by experiment"}
  ]
}' | empirica resolve-artifacts -
```

### `empirica delete-artifacts -`

Batch-delete stale artifacts. Supports `--dry-run` to preview.

```bash
empirica delete-artifacts --schema

echo '{
  "deletions": [
    {"type": "finding", "id": "abc-123"}
  ],
  "reason": "Stale test data"
}' | empirica delete-artifacts -
```

Deletions are logged as a `decision` artifact for audit trail, and
removed from both SQLite and Qdrant.

---

**Module Location:** `empirica/data/repositories/breadcrumbs.py`
**Batch verbs:** `empirica/cli/command_handlers/graph_commands.py`
**API Stability:** Beta (BreadcrumbRepository stable; other classes planned)
**Last Updated:** 2026-04-27

---

## Verified API surface

Generated from source. Every entry below exists; the signatures are the real ones.


### `empirica/data/repositories/breadcrumbs.py`


#### class `BreadcrumbRepository`

- `log_finding(self, project_id: str, session_id: str, finding: str, goal_id: str | None=None, subtask_id: str | None=None, subject: str | None=None, impact: float | None=None, transaction_id: str | None=None, entity_type: str | None=None, entity_id: str | None=None, source_ids: list[str] | None=None, visibility: str | None=None, epistemic_source: str | None=None, description: str | None=None) -> str` — Log a project finding (what was learned/discovered)
- `create_source(self, project_id: str, session_id: str | None, title: str, url: str | None=None, source_type: str='reference', description: str | None=None, confidence: float=0.7, visibility: str | None=None, transaction_id: str | None=None) -> str` — Create a minimal epistemic source row and return its id — the creation
- `backfill_goal_attachment(self, goal_id: str, session_id: str, transaction_id: str | None) -> int` — Backward counterpart to `_attach_to_goal`: when a goal is created MID-
- `log_unknown(self, project_id: str, session_id: str, unknown: str, goal_id: str | None=None, subtask_id: str | None=None, subject: str | None=None, impact: float | None=None, transaction_id: str | None=None, entity_type: str | None=None, entity_id: str | None=None, visibility: str | None=None, epistemic_source: str | None=None, description: str | None=None, source_ids: list[str] | None=None) -> str` — Log a project unknown (what's still unclear)
- `resolve_unknown(self, unknown_id: str, resolved_by: str, resolution_finding_id: str | None=None) -> bool` — Mark an unknown as resolved. Returns True only if a row actually changed.
- `resolve_finding(self, finding_id: str, resolution: str, superseded_by: str | None=None, resolution_kind: str | None=None) -> bool` — Mark a finding as resolved/superseded — kept for history, dropped from
- `log_dead_end(self, project_id: str, session_id: str, approach: str, why_failed: str, goal_id: str | None=None, subtask_id: str | None=None, subject: str | None=None, impact: float=0.5, transaction_id: str | None=None, entity_type: str | None=None, entity_id: str | None=None, visibility: str | None=None, epistemic_source: str | None=None, description: str | None=None, source_ids: list[str] | None=None) -> str` — Log a project dead end (what didn't work)
- `log_session_finding(self, session_id, finding, goal_id=None, subtask_id=None, subject=None, impact=None)` — Deprecated: redirects to log_finding. Session-scoped tables merged into project_*.
- `log_session_unknown(self, session_id, unknown, goal_id=None, subtask_id=None, subject=None, impact=None)` — Deprecated: redirects to log_unknown. Session-scoped tables merged into project_*.
- `log_session_dead_end(self, session_id, approach, why_failed, goal_id=None, subtask_id=None, subject=None, impact=0.5)` — Deprecated: redirects to log_dead_end. Session-scoped tables merged into project_*.
- `log_session_mistake(self, session_id, mistake, why_wrong, cost_estimate=None, root_cause_vector=None, prevention=None, goal_id=None)` — Deprecated: redirects to log_mistake. Session-scoped tables merged into project_*.
- `add_reference_doc(self, project_id: str, doc_path: str, doc_type: str | None=None, description: str | None=None) -> str` — Add a reference document to project.
- `get_project_findings(self, project_id: str, limit: int | None=None, subject: str | None=None, depth: str='moderate', uncertainty: float | None=None) -> list[dict]` — Get findings for a project with deprecation filtering.
- `get_project_unknowns(self, project_id: str, resolved: bool | None=None, subject: str | None=None, limit: int | None=None) -> list[dict]` — Get unknowns for a project (project-scoped).
- `get_project_dead_ends(self, project_id: str, limit: int | None=None, subject: str | None=None) -> list[dict]` — Get all dead ends for a project (project-scoped).
- `get_project_reference_docs(self, project_id: str) -> list[dict]` — Get all reference docs for a project.
- `log_mistake(self, session_id: str, mistake: str, why_wrong: str, cost_estimate: str | None=None, root_cause_vector: str | None=None, prevention: str | None=None, goal_id: str | None=None, project_id: str | None=None, transaction_id: str | None=None, entity_type: str | None=None, entity_id: str | None=None, visibility: str | None=None, epistemic_source: str | None=None, description: str | None=None, source_ids: list[str] | None=None) -> str` — Log a mistake for learning and future prevention.
- `get_mistakes(self, session_id: str | None=None, goal_id: str | None=None, limit: int=10) -> list[dict]` — Retrieve logged mistakes.
- `get_project_mistakes(self, project_id: str, limit: int | None=None) -> list[dict]` — Get mistakes for a project (uses direct project_id column)
- `log_assumption(self, project_id: str, session_id: str, assumption: str, confidence: float=0.5, domain: str | None=None, goal_id: str | None=None, transaction_id: str | None=None, entity_type: str | None=None, entity_id: str | None=None, visibility: str | None=None, epistemic_source: str | None=None, description: str | None=None, source_ids: list[str] | None=None) -> str` — Log an unverified belief to the assumptions table.
- `log_decision(self, project_id: str, session_id: str, choice: str, rationale: str, alternatives: str | None=None, confidence: float=0.7, reversibility: str='exploratory', goal_id: str | None=None, transaction_id: str | None=None, entity_type: str | None=None, entity_id: str | None=None, evidence_refs: list[str] | None=None, visibility: str | None=None, epistemic_source: str | None=None, description: str | None=None, source_ids: list[str] | None=None) -> str` — Log a decision choice point to the decisions table.
- `log_bead(self, project_id: str, session_id: str, coordination_state: str='open', updated_at: float | None=None, last_transition_actor: str | None=None, beads_issue_id: str | None=None, scope: str | None=None, goal_id: str | None=None, transaction_id: str | None=None, entity_type: str | None=None, entity_id: str | None=None, visibility: str | None=None, epistemic_source: str | None=None, description: str | None=None) -> str` — Log a bead (v0 coordination-record) to the legacy `beads` table.

