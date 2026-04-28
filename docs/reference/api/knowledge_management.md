# Knowledge Management API

**Module:** `empirica.data.repositories.breadcrumbs` (core implementation)
**Category:** Knowledge & Learning Management
**Stability:** Beta (BreadcrumbRepository stable; ReferenceDocumentManager and EpistemicSourceTracker are planned)

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

### `log_finding(self, project_id: str, session_id: str, finding: str, goal_id: Optional[str] = None, subtask_id: Optional[str] = None, subject: Optional[str] = None, tags: Optional[List[str]] = None) -> str`

Log a new finding discovered during work.

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: str` - Session where finding was made
- `finding: str` - Description of the finding
- `goal_id: Optional[str]` - Optional associated goal
- `subtask_id: Optional[str]` - Optional associated subtask
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

### `get_findings(self, project_id: str, limit: Optional[int] = None, since_timestamp: Optional[float] = None, tags: Optional[List[str]] = None, subject: Optional[str] = None) -> List[Dict]`

Get findings for a project with optional filters.

**Parameters:**
- `project_id: str` - Project identifier
- `limit: Optional[int]` - Optional limit on results
- `since_timestamp: Optional[float]` - Optional timestamp filter
- `tags: Optional[List[str]]` - Optional tags to match (finding must have ALL tags)
- `subject: Optional[str]` - Optional subject filter

**Returns:** `List[Dict]` - List of finding dictionaries

**Example:**
```python
recent_findings = breadcrumb_repo.get_findings(
    project_id="proj-123",
    limit=10,
    tags=["security", "performance"],
    since_timestamp=time.time() - (7 * 24 * 3600)  # Last week
)

for finding in recent_findings:
    print(f"Finding: {finding['finding'][:50]}...")
```

### `log_unknown(self, project_id: str, session_id: str, unknown: str, goal_id: Optional[str] = None, subtask_id: Optional[str] = None, subject: Optional[str] = None, tags: Optional[List[str]] = None) -> str`

Log an unknown or unresolved question.

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: str` - Session where unknown was identified
- `unknown: str` - Description of the unknown
- `goal_id: Optional[str]` - Optional associated goal
- `subtask_id: Optional[str]` - Optional associated subtask
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

### `get_unknowns(self, project_id: str, resolved: Optional[bool] = None, limit: Optional[int] = None, tags: Optional[List[str]] = None, subject: Optional[str] = None) -> List[Dict]`

Get unknowns for a project with optional filters.

**Parameters:**
- `project_id: str` - Project identifier
- `resolved: Optional[bool]` - Filter by resolution status (None=all, True=resolved, False=unresolved)
- `limit: Optional[int]` - Optional limit on results
- `tags: Optional[List[str]]` - Optional tags to match
- `subject: Optional[str]` - Optional subject filter

**Returns:** `List[Dict]` - List of unknown dictionaries

**Example:**
```python
unresolved = breadcrumb_repo.get_unknowns(
    project_id="proj-123",
    resolved=False,
    tags=["requirements"]
)

print(f"Found {len(unresolved)} unresolved requirements unknowns")
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

### `log_dead_end(self, project_id: str, session_id: str, approach: str, why_failed: str, goal_id: Optional[str] = None, subtask_id: Optional[str] = None, subject: Optional[str] = None, tags: Optional[List[str]] = None) -> str`

Log a failed approach or dead end.

**Parameters:**
- `project_id: str` - Project identifier
- `session_id: str` - Session where failure occurred
- `approach: str` - Description of the failed approach
- `why_failed: str` - Explanation of why it failed
- `goal_id: Optional[str]` - Optional associated goal
- `subtask_id: Optional[str]` - Optional associated subtask
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

### `get_dead_ends(self, project_id: str, limit: Optional[int] = None, tags: Optional[List[str]] = None, subject: Optional[str] = None) -> List[Dict]`

Get dead ends for a project with optional filters.

**Parameters:**
- `project_id: str` - Project identifier
- `limit: Optional[int]` - Optional limit on results
- `tags: Optional[List[str]]` - Optional tags to match
- `subject: Optional[str]` - Optional subject filter

**Returns:** `List[Dict]` - List of dead end dictionaries

**Example:**
```python
security_dead_ends = breadcrumb_repo.get_dead_ends(
    project_id="proj-123",
    tags=["security"]
)

for dead_end in security_dead_ends:
    print(f"Avoid: {dead_end['approach']} - {dead_end['why_failed']}")
```

---

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

### `add_reference_document(self, project_id: str, doc_path: str, doc_type: str, description: str, title: Optional[str] = None, tags: Optional[List[str]] = None, confidence: float = 0.5, related_findings: Optional[List[str]] = None, discovered_by_ai: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str`

Add a reference document to the project.

**Parameters:**
- `project_id: str` - Project identifier
- `doc_path: str` - Path to document (relative to project or URL)
- `doc_type: str` - Type of document ('spec', 'api', 'tutorial', 'paper', 'code', 'git_commit', 'chat_transcript')
- `description: str` - Description of the document
- `title: Optional[str]` - Optional document title
- `tags: Optional[List[str]]` - Optional tags for categorization
- `confidence: float` - Confidence in document accuracy (0.0-1.0), default 0.5
- `related_findings: Optional[List[str]]` - Optional related finding IDs
- `discovered_by_ai: Optional[str]` - Optional AI identifier
- `metadata: Optional[Dict[str, Any]]` - Optional metadata dictionary

**Returns:** `str` - Document ID

**Example:**
```python
doc_id = ref_doc_manager.add_reference_document(
    project_id="proj-123",
    doc_path="https://datatracker.ietf.org/doc/html/rfc6749",
    doc_type="spec",
    title="RFC 6749 - OAuth 2.0 Authorization Framework",
    description="Official OAuth 2.0 specification",
    confidence=0.95,
    tags=["oauth2", "security", "authorization"]
)
```

### `get_reference_documents(self, project_id: str, doc_type: Optional[str] = None, tags: Optional[List[str]] = None, min_confidence: float = 0.0, limit: Optional[int] = None) -> List[Dict]`

Get reference documents for a project with optional filters.

**Parameters:**
- `project_id: str` - Project identifier
- `doc_type: Optional[str]` - Optional document type filter
- `tags: Optional[List[str]]` - Optional tags to match (document must have ALL tags)
- `min_confidence: float` - Minimum confidence threshold, default 0.0
- `limit: Optional[int]` - Optional limit on results

**Returns:** `List[Dict]` - List of document dictionaries

**Example:**
```python
security_docs = ref_doc_manager.get_reference_documents(
    project_id="proj-123",
    tags=["security", "authentication"],
    min_confidence=0.8
)

for doc in security_docs:
    print(f"{doc['title']}: {doc['doc_path']}")
```

### `search_reference_documents(self, project_id: str, query: str, max_results: int = 10) -> List[Dict]`

Search reference documents by content.

**Parameters:**
- `project_id: str` - Project identifier
- `query: str` - Search query
- `max_results: int` - Maximum results to return, default 10

**Returns:** `List[Dict]` - List of matching document dictionaries

**Example:**
```python
oauth_docs = ref_doc_manager.search_reference_documents(
    project_id="proj-123",
    query="oauth2 implementation best practices",
    max_results=5
)

for doc in oauth_docs:
    print(f"Match: {doc['title']} - {doc['doc_path']}")
```

---

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

### `add_source(self, project_id: str, source_type: str, title: str, session_id: Optional[str] = None, source_url: Optional[str] = None, description: Optional[str] = None, confidence: float = 0.5, epistemic_layer: Optional[str] = None, supports_vectors: Optional[Dict[str, float]] = None, related_findings: Optional[List[str]] = None, discovered_by_ai: Optional[str] = None, source_metadata: Optional[Dict] = None) -> str`

Add an epistemic source to track knowledge provenance.

**Parameters:**
- `project_id: str` - Project identifier
- `source_type: str` - Type of source ('url', 'doc', 'code_ref', 'paper', 'api_doc', 'git_commit', 'chat_transcript', 'epistemic_snapshot')
- `title: str` - Source title
- `session_id: Optional[str]` - Optional session that discovered this source
- `source_url: Optional[str]` - Optional URL or path to source
- `description: Optional[str]` - Optional description
- `confidence: float` - Confidence in this source (0.0-1.0), default 0.5
- `epistemic_layer: Optional[str]` - Optional epistemic layer ('noetic', 'epistemic', 'action')
- `supports_vectors: Optional[Dict[str, float]]` - Optional dict of epistemic vectors this source supports
- `related_findings: Optional[List[str]]` - Optional list of related finding IDs
- `discovered_by_ai: Optional[str]` - Optional discovering AI identifier
- `source_metadata: Optional[Dict]` - Optional additional metadata

**Returns:** `str` - Source ID

**Example:**
```python
source_id = source_tracker.add_source(
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

### `get_sources(self, project_id: str, source_type: Optional[str] = None, min_confidence: float = 0.0, epistemic_layer: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]`

Get epistemic sources for a project with optional filters.

**Parameters:**
- `project_id: str` - Project identifier
- `source_type: Optional[str]` - Optional source type filter
- `min_confidence: float` - Minimum confidence threshold, default 0.0
- `epistemic_layer: Optional[str]` - Optional epistemic layer filter
- `limit: Optional[int]` - Optional limit on results

**Returns:** `List[Dict]` - List of source dictionaries

**Example:**
```python
reliable_sources = source_tracker.get_sources(
    project_id="proj-123",
    min_confidence=0.8,
    source_type="spec"
)

for source in reliable_sources:
    print(f"Source: {source['title']} (confidence: {source['confidence']})")
```

### `update_source_confidence(self, source_id: str, new_confidence: float, reason: Optional[str] = None) -> bool`

Update confidence in an epistemic source based on new evidence.

**Parameters:**
- `source_id: str` - Source identifier
- `new_confidence: float` - New confidence value (0.0-1.0)
- `reason: Optional[str]` - Optional reason for confidence update

**Returns:** `bool` - True if update successful

**Example:**
```python
# Update confidence after verification
success = source_tracker.update_source_confidence(
    source_id="src-456",
    new_confidence=0.98,
    reason="Successfully validated implementation approach from this source"
)
```

---

## Knowledge Analytics (Planned)

> **Note:** These analytics functions are planned but not yet implemented.

### `get_knowledge_graph(self, project_id: str, include_unknowns: bool = True, include_dead_ends: bool = True, include_sources: bool = True) -> Dict[str, Any]` (Planned)

Will generate knowledge graph for a project showing relationships between findings, unknowns, dead ends, and sources.

**Parameters:**
- `project_id: str` - Project identifier
- `include_unknowns: bool` - Include unknowns in graph, default True
- `include_dead_ends: bool` - Include dead ends in graph, default True
- `include_sources: bool` - Include sources in graph, default True

**Returns:** `Dict[str, Any]` - Knowledge graph with nodes and edges

**Example:**
```python
graph = knowledge_repo.get_knowledge_graph(project_id="proj-123")
print(f"Nodes: {len(graph['nodes'])}")
print(f"Edges: {len(graph['edges'])}")

# Use graph for visualization or analysis
```

### `get_knowledge_delta(self, project_id: str, session_ids: Optional[List[str]] = None) -> Dict[str, float]`

Get knowledge delta for a project (what was learned).

**Parameters:**
- `project_id: str` - Project identifier
- `session_ids: Optional[List[str]]` - Optional list of session IDs to include (all if None)

**Returns:** `Dict[str, float]` - Knowledge deltas for each epistemic vector

**Example:**
```python
delta = knowledge_repo.get_knowledge_delta(project_id="proj-123")
print(f"Knowledge gain: {delta['know']}")
print(f"Uncertainty reduction: {delta['uncertainty']}")
print(f"Capability improvement: {delta['do']}")
```

### `identify_knowledge_gaps(self, project_id: str, vector_threshold: float = 0.3) -> Dict[str, List[str]]`

Identify knowledge gaps in a project based on low vector scores.

**Parameters:**
- `project_id: str` - Project identifier
- `vector_threshold: float` - Threshold below which vectors indicate gaps, default 0.3

**Returns:** `Dict[str, List[str]]` - Gaps organized by vector type

**Example:**
```python
gaps = knowledge_repo.identify_knowledge_gaps(project_id="proj-123", vector_threshold=0.4)
for vector, gap_items in gaps.items():
    print(f"Low {vector} gaps: {len(gap_items)} items")
```

---

## Knowledge Utilities

### `export_knowledge_base(self, project_id: str, export_format: str = 'json') -> Dict[str, Any]`

Export project knowledge base in specified format.

**Parameters:**
- `project_id: str` - Project identifier
- `export_format: str` - Export format ('json', 'markdown', 'csv'), default 'json'

**Returns:** `Dict[str, Any]` - Exported knowledge base

**Example:**
```python
export_data = knowledge_repo.export_knowledge_base(
    project_id="proj-123",
    export_format="markdown"
)

# Save to file for documentation
with open("project_knowledge.md", "w") as f:
    f.write(export_data['content'])
```

### `import_knowledge_base(self, project_id: str, knowledge_data: Dict[str, Any], import_format: str = 'json') -> bool`

Import knowledge base data into project.

**Parameters:**
- `project_id: str` - Project identifier
- `knowledge_data: Dict[str, Any]` - Knowledge data to import
- `import_format: str` - Import format ('json', 'markdown'), default 'json'

**Returns:** `bool` - True if import successful

**Example:**
```python
with open("external_knowledge.json", "r") as f:
    knowledge_data = json.load(f)

success = knowledge_repo.import_knowledge_base(
    project_id="proj-123",
    knowledge_data=knowledge_data
)
```

### `cleanup_stale_references(self, project_id: str, days_old: int = 90) -> int`

Clean up stale references that are no longer relevant.

**Parameters:**
- `project_id: str` - Project identifier
- `days_old: int` - Age threshold in days, default 90

**Returns:** `int` - Number of references cleaned up

**Example:**
```python
cleaned = knowledge_repo.cleanup_stale_references(
    project_id="proj-123",
    days_old=180
)
print(f"Cleaned up {cleaned} stale references")
```

---

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