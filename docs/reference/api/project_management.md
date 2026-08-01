# Project Management API

**Module:** `empirica.data.repositories.projects` and related modules
**Category:** Project & Workspace Management
**Stability:** Prose is hand-written; the Verified API surface section is generated from source


> ### Verified against the code
>
> The entries that documented functions which do not exist have been removed
> (43 across four files, 2026-08-01), and a **Verified API surface** section
> generated from source is appended below. `tests/test_api_docs_symbols_exist.py`
> fails CI if a function that does not exist is documented here again.

---

## Overview

The Project Management API provides comprehensive tools for managing multi-session projects, including:

- Project lifecycle management
- AI-to-AI handoff reports
- Cross-session knowledge tracking
- Project-level findings and unknowns
- Reference documentation management

Each project maps to a git repository and maintains its own epistemic state across sessions.

---

## Project Repository

### `class ProjectRepository`

Main repository for project management operations.

#### `__init__(self, db_path: Optional[str] = None)`

Initialize the project repository.

**Parameters:**
- `db_path: Optional[str]` - Path to database file, defaults to standard location

**Example:**
```python
from empirica.data.repositories.projects import ProjectRepository

project_repo = ProjectRepository()
```

### `create_project(self, name: str, description: str, repos: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None) -> str`

Create a new project.

**Parameters:**
- `name: str` - Project name
- `description: str` - Project description
- `repos: Optional[List[str]]` - List of repository URLs associated with project
- `metadata: Optional[Dict[str, Any]]` - Optional project metadata

**Returns:** `str` - Project ID (UUID string)

**Example:**
```python
project_id = project_repo.create_project(
    name="User Authentication System",
    description="Secure user authentication with OAuth2 and JWT tokens",
    repos=["https://github.com/company/auth-service.git"],
    metadata={
        "domain": "security",
        "complexity": "high",
        "team_size": 3
    }
)
```

### `get_project(self, project_id: str) -> Optional[Dict]`

Get project details by ID.

**Parameters:**
- `project_id: str` - Project identifier

**Returns:** `Optional[Dict]` - Project dictionary or None if not found

**Example:**
```python
project = project_repo.get_project(project_id="proj-123")
if project:
    print(f"Project: {project['name']}")
    print(f"Status: {project['status']}")
```

## Project Handoff Management

## Project Knowledge Management

### `log_finding(self, project_id: str, session_id: str, finding: str, goal_id: Optional[str] = None, task_id: Optional[str] = None) -> str`

Log a project finding (discovery or insight).

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: str` - Session where finding was made
- `finding: str` - Description of the finding
- `goal_id: Optional[str]` - Optional associated goal
- `task_id: Optional[str]` - Optional associated task

**Returns:** `str` - Finding ID

**Example:**
```python
finding_id = project_repo.log_finding(
    project_id="proj-123",
    session_id="sess-456",
    finding="Discovered that bcrypt is 3x slower than argon2 for password hashing",
    goal_id="goal-789"
)
```

### `get_project_findings(self, project_id: str, limit: Optional[int] = None, since_timestamp: Optional[float] = None) -> List[Dict]`

Get all findings for a project.

**Parameters:**
- `project_id: str` - Project identifier
- `limit: Optional[int]` - Optional limit on results
- `since_timestamp: Optional[float]` - Optional timestamp filter

**Returns:** `List[Dict]` - List of finding dictionaries

**Example:**
```python
findings = project_repo.get_project_findings(project_id="proj-123", limit=20)
for finding in findings:
    print(f"Finding: {finding['finding']}")
```

### `log_unknown(self, project_id: str, session_id: str, unknown: str, goal_id: Optional[str] = None, task_id: Optional[str] = None) -> str`

Log an unknown or unresolved question for the project.

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: str` - Session where unknown was identified
- `unknown: str` - Description of the unknown
- `goal_id: Optional[str]` - Optional associated goal
- `task_id: Optional[str]` - Optional associated task

**Returns:** `str` - Unknown ID

**Example:**
```python
unknown_id = project_repo.log_unknown(
    project_id="proj-123",
    session_id="sess-456",
    unknown="What are the performance requirements for auth system?",
    goal_id="goal-789"
)
```

### `get_project_unknowns(self, project_id: str, resolved: Optional[bool] = None) -> List[Dict]`

Get unknowns for a project, optionally filtered by resolution status.

**Parameters:**
- `project_id: str` - Project identifier
- `resolved: Optional[bool]` - Filter by resolution status (None=all, True=resolved, False=unresolved)

**Returns:** `List[Dict]` - List of unknown dictionaries

**Example:**
```python
unresolved = project_repo.get_project_unknowns(project_id="proj-123", resolved=False)
print(f"Project has {len(unresolved)} unresolved unknowns")
```

### `resolve_unknown(self, unknown_id: str, resolution: str, resolved_by: str) -> bool`

Mark an unknown as resolved.

**Parameters:**
- `unknown_id: str` - Unknown identifier
- `resolution: str` - Resolution description
- `resolved_by: str` - Identifier of resolver

**Returns:** `bool` - True if resolution successful

**Example:**
```python
success = project_repo.resolve_unknown(
    unknown_id="unk-123",
    resolution="Performance requirements are 1000 req/sec with <100ms latency",
    resolved_by="claude-sonnet-4"
)
```

### `log_dead_end(self, project_id: str, session_id: str, approach: str, why_failed: str, goal_id: Optional[str] = None, task_id: Optional[str] = None) -> str`

Log a failed approach or dead end.

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: str` - Session where failure occurred
- `approach: str` - Description of the failed approach
- `why_failed: str` - Explanation of why it failed
- `goal_id: Optional[str]` - Optional associated goal
- `task_id: Optional[str]` - Optional associated task

**Returns:** `str` - Dead end ID

**Example:**
```python
dead_end_id = project_repo.log_dead_end(
    project_id="proj-123",
    session_id="sess-456",
    approach="Using JWT tokens without refresh mechanism",
    why_failed="Caused frequent re-authentication for users"
)
```

### `get_project_dead_ends(self, project_id: str, limit: Optional[int] = None) -> List[Dict]`

Get all dead ends for a project.

**Parameters:**
- `project_id: str` - Project identifier
- `limit: Optional[int]` - Optional limit on results

**Returns:** `List[Dict]` - List of dead end dictionaries

**Example:**
```python
dead_ends = project_repo.get_project_dead_ends(project_id="proj-123")
for dead_end in dead_ends:
    print(f"Avoid: {dead_end['approach']} - {dead_end['why_failed']}")
```

---

## Reference Documentation Management

### `get_project_reference_docs(self, project_id: str, doc_type: Optional[str] = None, tags: Optional[List[str]] = None) -> List[Dict]`

Get reference documents for a project, with optional filters.

**Parameters:**
- `project_id: str` - Project identifier
- `doc_type: Optional[str]` - Optional document type filter
- `tags: Optional[List[str]]` - Optional tags to match (documents must have ALL tags)

**Returns:** `List[Dict]` - List of document dictionaries

**Example:**
```python
security_docs = project_repo.get_project_reference_docs(
    project_id="proj-123",
    tags=["security", "authentication"]
)
```

## Epistemic Source Tracking

### `add_epistemic_source(self, project_id: str, source_type: str, title: str, session_id: Optional[str] = None, source_url: Optional[str] = None, description: Optional[str] = None, confidence: float = 0.5, epistemic_layer: Optional[str] = None, supports_vectors: Optional[Dict[str, float]] = None, related_findings: Optional[List[str]] = None, discovered_by_ai: Optional[str] = None, source_metadata: Optional[Dict] = None) -> str`

Add an epistemic source to ground project knowledge.

**Parameters:**
- `project_id: str` - Project identifier
- `source_type: str` - Type of source ('url', 'doc', 'code_ref', 'paper', 'api_doc', 'git_commit', 'chat_transcript', 'epistemic_snapshot')
- `title: str` - Source title
- `session_id: Optional[str]` - Optional session that discovered this source
- `source_url: Optional[str]` - Optional URL or path
- `description: Optional[str]` - Optional description
- `confidence: float` - Confidence in this source (0.0-1.0), default 0.5
- `epistemic_layer: Optional[str]` - Optional layer ('noetic', 'epistemic', 'action')
- `supports_vectors: Optional[Dict[str, float]]` - Optional dict of epistemic vectors this source supports
- `related_findings: Optional[List[str]]` - Optional list of finding IDs
- `discovered_by_ai: Optional[str]` - Optional AI identifier
- `source_metadata: Optional[Dict]` - Optional metadata dict

**Returns:** `str` - Source ID

**Example:**
```python
source_id = project_repo.add_epistemic_source(
    project_id="proj-123",
    source_type="spec",
    title="RFC 6749 - OAuth 2.0 Authorization Framework",
    source_url="https://datatracker.ietf.org/doc/html/rfc6749",
    description="Official OAuth 2.0 specification",
    confidence=0.95,
    supports_vectors={"know": 0.9, "context": 0.85},
    discovered_by_ai="claude-sonnet-4"
)
```

### `get_epistemic_sources(self, project_id: str, session_id: Optional[str] = None, source_type: Optional[str] = None, min_confidence: float = 0.0, limit: Optional[int] = None) -> List[Dict]`

Get epistemic sources for a project with optional filters.

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: Optional[str]` - Optional session filter
- `source_type: Optional[str]` - Optional source type filter
- `min_confidence: float` - Minimum confidence threshold, default 0.0
- `limit: Optional[int]` - Optional limit on results

**Returns:** `List[Dict]` - List of source dictionaries

**Example:**
```python
high_confidence_sources = project_repo.get_epistemic_sources(
    project_id="proj-123",
    min_confidence=0.8,
    source_type="spec"
)
```

---

## Project Analytics

### `get_project_sessions(self, project_id: str) -> List[Dict]`

Get all sessions associated with a project.

**Parameters:**
- `project_id: str` - Project identifier

**Returns:** `List[Dict]` - List of session dictionaries

**Example:**
```python
sessions = project_repo.get_project_sessions(project_id="proj-123")
for session in sessions:
    print(f"Session {session['session_id']}: {session['ai_id']} - {session['start_time']}")
```

---

## Session-Project Linking

### `link_session_to_project(self, session_id: str, project_id: str) -> bool`

Link a session to a project.

**Parameters:**
- `session_id: str` - Session identifier
- `project_id: str` - Project identifier

**Returns:** `bool` - True if linking successful

**Example:**
```python
success = project_repo.link_session_to_project(
    session_id="sess-456",
    project_id="proj-123"
)
```

## Project Utilities

## CLI Commands

For command-line usage, see [CLI Commands Reference](../../human/developers/CLI_COMMANDS_UNIFIED.md).

### Project Management
```bash
empirica project-init --name "Project Name"
empirica project-create --name "Project Name" --output json
empirica project-list --output json
empirica project-bootstrap --session-id <ID> --output json
```

### Handoffs
```bash
empirica handoff-create --session-id <ID> --task-summary "..." --key-findings '[...]'
empirica handoff-query --output json
```

### Semantic Search (requires Qdrant)
```bash
empirica project-search --project-id <ID> --task "query" --output json
empirica project-embed --project-id <ID> --output json
```

---

## Best Practices

1. **Create comprehensive handoff reports** - Include sufficient context for seamless AI-to-AI transitions.

2. **Log findings and unknowns consistently** - Maintain project knowledge base across sessions.

3. **Track epistemic sources** - Document where knowledge comes from to enable verification.

4. **Use appropriate confidence ratings** - Rate source confidence accurately to enable proper weighting.

5. **Link sessions to projects** - Maintain clear project-session relationships for analytics.

6. **Monitor project health** - Regularly check health metrics to catch issues early.

7. **Archive completed projects** - Set appropriate status for completed work to maintain focus.

---

## Error Handling

Methods typically raise:
- `ValueError` for invalid parameters
- `sqlite3.Error` for database issues
- `KeyError` when referenced entities don't exist
- `RuntimeError` for state-related issues

---

**Module Location:** `empirica/data/repositories/projects.py`
**API Stability:** Beta (analytics methods planned)
**Last Updated:** 2026-02-08

---

## Verified API surface

Generated from source. Every entry below exists; the signatures are the real ones.


### `empirica/data/repositories/projects.py`


#### class `ProjectRepository`

- `create_project(self, name: str, description: str | None=None, repos: list[str] | None=None, project_type: str | None=None, project_tags: list[str] | None=None, parent_project_id: str | None=None, project_id: str | None=None) -> str` — Create a new project for multi-repo/multi-session tracking.
- `get_project(self, project_id: str) -> dict | None` — Get project data
- `get_project_by_name(self, name: str) -> dict | None` — Get project data by name (case-insensitive)
- `resolve_project_id(self, project_id_or_name: str) -> str | None` — Resolve project identifier to UUID.
- `link_session_to_project(self, session_id: str, project_id: str)` — Link a session to a project
- `get_project_sessions(self, project_id: str) -> list[dict]` — Get all sessions for a project
- `aggregate_project_learning_deltas(self, project_id: str) -> dict[str, float]` — Compute total epistemic learning across all project sessions.
- `create_project_handoff(self, project_id: str, project_summary: str, key_decisions: list[str] | None=None, patterns_discovered: list[str] | None=None, remaining_work: list[str] | None=None) -> str` — Create project-level handoff report by aggregating session handoffs.
- `get_latest_project_handoff(self, project_id: str) -> dict | None` — Get the most recent project handoff
- `get_ai_epistemic_handoff(self, project_id: str, ai_id: str) -> dict | None` — Get latest epistemic handoff (POSTFLIGHT checkpoint) for a specific AI in this project.

