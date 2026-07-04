# System Utilities API

**Module:** Various utility modules in `empirica.utils.*`
**Category:** System & Infrastructure
**Stability:** Production Ready

---

## Overview

The System Utilities API provides essential infrastructure tools for:

- Branch mapping and Git integration
- Documentation-code integrity checking
- Migration tools

---

## Branch Mapping System

### `class BranchMapping`

Manages the mapping between Git branches and Empirica goals for AI agents working on multi-branch projects.

#### `__init__(self, repo_root: Optional[str] = None)`

Initialize the branch mapping system.

**Parameters:**
- `repo_root: Optional[str]` - Git repository root, defaults to searching from current directory

**Example:**
```python
from empirica.integrations.branch_mapping import BranchMapping

branch_mapper = BranchMapping()
# Or specify a specific repository
branch_mapper = BranchMapping(repo_root="/path/to/project")
```

### `add_mapping(self, branch_name: str, goal_id: str, beads_issue_id: Optional[str] = None, ai_id: Optional[str] = None, session_id: Optional[str] = None) -> bool`

Add a mapping between a Git branch and an Empirica goal.

**Parameters:**
- `branch_name: str` - Git branch name
- `goal_id: str` - Empirica goal UUID
- `beads_issue_id: Optional[str]` - Optional BEADS issue ID
- `ai_id: Optional[str]` - Optional AI identifier
- `session_id: Optional[str]` - Optional session UUID

**Returns:** `bool` - True if mapping added, False if branch already mapped

**Example:**
```python
success = branch_mapper.add_mapping(
    branch_name="feature/user-auth",
    goal_id="goal-123",
    ai_id="claude-sonnet-4",
    beads_issue_id="bd-auth-456"
)

if success:
    print("Branch mapped successfully")
else:
    print("Branch already mapped to another goal")
```

### `get_mapping(self, branch_name: str) -> Optional[Dict]`

Get the mapping for a specific branch.

**Parameters:**
- `branch_name: str` - Git branch name

**Returns:** `Optional[Dict]` - Mapping dictionary or None if not found

**Example:**
```python
mapping = branch_mapper.get_mapping(branch_name="feature/user-auth")
if mapping:
    print(f"Branch maps to goal: {mapping['goal_id']}")
    print(f"AI working on it: {mapping['ai_id']}")
```

### `get_branch_for_goal(self, goal_id: str) -> Optional[str]`

Find which branch is associated with a goal.

**Parameters:**
- `goal_id: str` - Goal identifier

**Returns:** `Optional[str]` - Branch name or None if not found

**Example:**
```python
branch = branch_mapper.get_branch_for_goal(goal_id="goal-123")
if branch:
    print(f"Goal {goal_id} is on branch: {branch}")
```

### `list_active_mappings(self) -> List[Dict]`

List all active branch-goal mappings.

**Returns:** `List[Dict]` - List of mapping dictionaries

**Example:**
```python
mappings = branch_mapper.list_active_mappings()
for mapping in mappings:
    print(f"{mapping['branch_name']} -> {mapping['goal_id']}")
```

### `remove_mapping(self, branch_name: str, archive: bool = True) -> bool`

Remove a branch mapping.

**Parameters:**
- `branch_name: str` - Branch to remove mapping for
- `archive: bool` - If True, archives mapping instead of deleting, default True

**Returns:** `bool` - True if removed, False if not found

**Example:**
```python
# Remove mapping and archive it
removed = branch_mapper.remove_mapping(branch_name="feature/user-auth", archive=True)

# Permanently delete mapping
removed = branch_mapper.remove_mapping(branch_name="feature/user-auth", archive=False)
```

### `get_history(self, limit: int = 50) -> List[Dict]`

Get branch mapping history.

**Parameters:**
- `limit: int` - Maximum number of history items, default 50

**Returns:** `List[Dict]` - List of historical mapping records

**Example:**
```python
history = branch_mapper.get_history(limit=20)
for record in history:
    print(f"{record['timestamp']}: {record['branch_name']} -> {record['goal_id']}")
```

---

## Documentation-Code Integrity Checker

### `class DocCodeIntegrityAnalyzer`

Analyzes integrity between documentation and codebase to ensure consistency.

#### `__init__(self, project_root: Optional[str] = None)`

Initialize the integrity analyzer.

**Parameters:**
- `project_root: Optional[str]` - Project root directory, defaults to current directory

**Example:**
```python
from empirica.utils.doc_code_integrity import DocCodeIntegrityAnalyzer

analyzer = DocCodeIntegrityAnalyzer()
```

### `analyze_cli_commands(self) -> Dict[str, List[str]]`

Analyze CLI command integrity between documentation and implementation.

**Returns:** `Dict[str, List[str]]` - Dictionary with:
- `commands_in_docs` - Commands mentioned in documentation
- `commands_in_code` - Commands actually implemented
- `missing_in_code` - Documented but not implemented
- `missing_in_docs` - Implemented but not documented

**Example:**
```python
integrity_report = analyzer.analyze_cli_commands()

print(f"Commands in docs: {len(integrity_report['commands_in_docs'])}")
print(f"Commands in code: {len(integrity_report['commands_in_code'])}")
print(f"Missing in code: {integrity_report['missing_in_code']}")
print(f"Missing in docs: {integrity_report['missing_in_docs']}")
```

### `get_detailed_gaps(self) -> Dict[str, Any]`

Get detailed information about integrity gaps.

**Returns:** `Dict[str, Any]` - Detailed gap analysis with file locations and context

**Example:**
```python
detailed_gaps = analyzer.get_detailed_gaps()
for gap_type, details in detailed_gaps.items():
    print(f"{gap_type}: {len(details)} issues found")
    for detail in details[:3]:  # Show first 3
        print(f"  - {detail['location']}: {detail['issue']}")
```

### `analyze_complete_integrity(self) -> Dict[str, Any]`

Run complete integrity analysis including deprecation and superfluity checks.

**Returns:** `Dict[str, Any]` - Comprehensive integrity report

**Example:**
```python
full_report = analyzer.analyze_complete_integrity()
print(f"Integrity score: {full_report['integrity_score']}")
print(f"Phantom commands: {full_report['phantom_commands']}")
print(f"Missing documentation: {full_report['missing_documentation']}")
```

---

## Migration Utilities

### `class MigrationRunner`

Manages database schema migrations.

#### `__init__(self, db_path: str)`

Initialize the migration runner.

**Parameters:**
- `db_path: str` - Path to database file

**Example:**
```python
from empirica.data.migrations.migration_runner import MigrationRunner

migration_runner = MigrationRunner("./sessions.db")
```

### `run_migrations(self, target_version: Optional[str] = None) -> Dict[str, Any]`

Run pending migrations up to target version.

**Parameters:**
- `target_version: Optional[str]` - Target version, runs all if None

**Returns:** `Dict[str, Any]` - Migration results

**Example:**
```python
results = migration_runner.run_migrations(target_version="1.0.5")
print(f"Migrated from {results['from_version']} to {results['to_version']}")
print(f"Applied {len(results['applied_migrations'])} migrations")
```

### `get_current_schema_version(self) -> str`

Get current schema version.

**Returns:** `str` - Current version string

**Example:**
```python
current_version = migration_runner.get_current_schema_version()
print(f"Current schema version: {current_version}")
```

### `check_pending_migrations(self) -> List[Dict[str, str]]`

Check for pending migrations.

**Returns:** `List[Dict[str, str]]` - List of pending migration dictionaries

**Example:**
```python
pending = migration_runner.check_pending_migrations()
if pending:
    print(f"Pending migrations: {len(pending)}")
    for migration in pending:
        print(f"  - {migration['version']}: {migration['description']}")
```

---

## Best Practices

1. **Use branch mapping consistently** - Always map branches to goals to maintain traceability.

2. **Run migrations safely** - Always backup before running schema migrations.

3. **Check integrity regularly** - Run doc-code integrity checks to maintain consistency.

4. **Handle errors gracefully** - All utility methods return appropriate success/failure indicators.

---

## Error Handling

Methods typically raise:
- `ValueError` for invalid parameters
- `FileNotFoundError` when files don't exist
- `PermissionError` for access issues
- `RuntimeError` for operational failures
- `sqlite3.Error` for database issues
- `git.exc.GitCommandError` for Git operations

---

**Module Locations:**
- `empirica/integrations/branch_mapping.py`
- `empirica/utils/doc_code_integrity.py`
- `empirica/data/migrations/migration_runner.py`

---

## Edit Verification

### `class EditConfidenceAssessor`

**Module:** `empirica.components.edit_verification.confidence_assessor`

Assesses epistemic confidence in a proposed file edit before attempting it. Checks context freshness, whitespace ambiguity, and match uniqueness.

```python
from empirica.components.edit_verification.confidence_assessor import EditConfidenceAssessor

assessor = EditConfidenceAssessor()
result = assessor.assess(file_path="src/main.py", old_string="def foo():", new_string="def bar():")
# Returns: {"confidence": 0.95, "strategy": "atomic", "warnings": []}
```

### `class EditStrategyExecutor`

**Module:** `empirica.components.edit_verification.strategy_executor`

Executes file edits using the strategy selected by `EditConfidenceAssessor` — atomic edit, bash fallback, or re-read-first.

---

## Release Readiness

### `class AssessmentStatus`

**Module:** `empirica.cli.command_handlers.release_commands`

Enum for release-check outcomes: `PASS`, `WARN`, `FAIL`, `SKIP`.

### `class CheckResult`

**Module:** `empirica.cli.command_handlers.release_commands`

Result of a single release-readiness check: name, status, message, and details.

---

## CLI Validation

### `class CheckInput`

**Module:** `empirica.cli.validation`

Pydantic model validating the `check-submit` command payload — session_id, vectors, approach, reasoning.

---

## BEADS Integration

### `class BeadsConfig`

**Module:** `empirica.integrations.beads.config`

Loads and caches `.empirica/config.yaml` for the BEADS workflow integration (issue tracking bridge).

---

## Action Hooks

### `class EmpiricaActionHooks`

**Module:** `empirica.integration.empirica_action_hooks`

Static-method class that writes real-time JSON feeds for tmux panel displays (12D epistemic monitor).

---

## Documentation Tools

### `class DocsExplainAgent`

**Module:** `empirica.cli.command_handlers.docs_commands`

Retrieves focused project documentation answers using Qdrant semantic search with keyword-matching fallback. Powers the `docs-explain` CLI command.

---

## Documentation Assessment

### `class EpistemicDocsAgent`

**Module:** `empirica.cli.command_handlers.docs_commands`

Epistemic Documentation Assessment Agent. Performs comprehensive doc coverage analysis using module introspection, cross-referencing docstrings against API reference documents. Powers the `docs-assess` CLI command.

---

## Release Readiness

### `class EpistemicReleaseAgent`

**Module:** `empirica.cli.command_handlers.release_commands`

Epistemic Release Agent — applies epistemic principles to release readiness. Runs checks across test status, doc coverage, version consistency, and changelog completeness. Powers the `release-check` CLI command.

---

## Vision (Experimental)

### `class BasicImageAssessment`

**Module:** `empirica.cli.command_handlers.vision_commands`

Dataclass capturing basic image metadata and visual heuristics for slide assessment — dimensions, aspect ratio, pixel count, presentation flag.

### `class SlideEpistemicAssessment`

**Module:** `empirica.vision.slide_processor`

Epistemic quality assessment of a single slide — path, slide number, and vector-based quality scores.

### `class ReadableAssessment`

**Module:** `empirica.vision.readable_translator`

Human-readable slide assessment — slide number, quality level (Excellent/Good/Fair/Needs Work), and plain-English feedback.

### `class HumanReadableTranslator`

**Module:** `empirica.vision.readable_translator`

Translates epistemic slide assessments (`SlideEpistemicAssessment`) into plain English (`ReadableAssessment`). Provides both single-slide and batch translation.

---

## Data Layer Repositories

The data layer uses the Repository pattern for database operations. All repositories extend `BaseRepository`.

### `class BaseRepository`

Abstract base class for all data repositories.

**Location:** `empirica/data/repositories/base.py`

```python
from empirica.data.repositories.base import BaseRepository

class CustomRepository(BaseRepository):
    def __init__(self, db_path: str):
        super().__init__(db_path)
```

**Methods:**
- `execute(query: str, params: tuple) -> cursor` - Execute SQL query
- `fetch_one(query: str, params: tuple) -> Optional[Dict]` - Fetch single row
- `fetch_all(query: str, params: tuple) -> List[Dict]` - Fetch all rows
- `insert(table: str, data: Dict) -> int` - Insert row, return ID
- `update(table: str, data: Dict, where: Dict) -> int` - Update rows
- `delete(table: str, where: Dict) -> int` - Delete rows

---

### `class SessionRepository`

Manages session storage and retrieval.

**Location:** `empirica/data/repositories/sessions.py`

```python
from empirica.data.repositories.sessions import SessionRepository

repo = SessionRepository(db_path)
session = repo.get_session(session_id)
sessions = repo.list_sessions(ai_id="claude-code", limit=10)
```

**Key Methods:**
- `create_session(ai_id, project_id, metadata) -> str` - Create new session
- `get_session(session_id) -> Optional[Dict]` - Get session by ID
- `list_sessions(ai_id, project_id, status, limit, offset) -> List[Dict]` - List sessions
- `update_session(session_id, updates) -> bool` - Update session
- `close_session(session_id, status) -> bool` - Close session with status

---

### `class CascadeRepository`

Manages CASCADE workflow state persistence.

**Location:** `empirica/data/repositories/cascades.py`

```python
from empirica.data.repositories.cascades import CascadeRepository

repo = CascadeRepository(db_path)
cascade = repo.create_cascade(session_id, task_context)
repo.update_phase(cascade_id, "CHECK", vectors)
```

**Key Methods:**
- `create_cascade(session_id, task_context, goal_id) -> str` - Create cascade
- `get_cascade(cascade_id) -> Optional[Dict]` - Get cascade by ID
- `update_phase(cascade_id, phase, vectors) -> bool` - Update to new phase
- `get_current_phase(cascade_id) -> str` - Get current phase
- `list_cascades(session_id) -> List[Dict]` - List cascades for session

---

### `class BranchRepository`

Manages investigation branches for parallel exploration.

**Location:** `empirica/data/repositories/branches.py`

```python
from empirica.data.repositories.branches import BranchRepository

repo = BranchRepository(db_path)
branch = repo.create_branch(session_id, hypothesis, parent_branch_id)
repo.checkpoint_branch(branch_id, findings, confidence)
```

**Key Methods:**
- `create_branch(session_id, hypothesis, parent_id) -> str` - Create branch
- `get_branch(branch_id) -> Optional[Dict]` - Get branch details
- `checkpoint_branch(branch_id, findings, confidence) -> bool` - Save checkpoint
- `merge_branches(parent_id, child_ids, strategy) -> Dict` - Merge branches
- `list_active_branches(session_id) -> List[Dict]` - List active branches

---

**API Stability:** Stable
**Last Updated:** 2026-01-03