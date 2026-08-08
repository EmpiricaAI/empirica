# Goals & Tasks API

**Module:** `empirica.core.goals.repository` and `empirica.core.tasks.repository`
**Category:** Task Management
**Stability:** Prose is hand-written; the Verified API surface section is generated from source


> ### Verified against the code
>
> The entries that documented functions which do not exist have been removed
> (43 across four files, 2026-08-01), and a **Verified API surface** section
> generated from source is appended below. `tests/test_api_docs_symbols_exist.py`
> fails CI if a function that does not exist is documented here again.

---

## Overview

The Goals & Tasks API provides structured management for objectives and their decomposition into actionable tasks. The system supports:

- Goal creation with success criteria
- Task decomposition and tracking
- Dependency management
- Progress monitoring
- Cross-session persistence

---

## Goal Repository

### `create_goal(self, session_id: str, objective: str, scope_breadth: float = None, scope_duration: float = None, scope_coordination: float = None) -> str`

Create a new goal for a session.

**Parameters:**
- `session_id: str` - Session identifier
- `objective: str` - What are you trying to accomplish?
- `scope_breadth: float` - Breadth of scope (0.0-1.0, 0=single file, 1=entire codebase)
- `scope_duration: float` - Duration scope (0.0-1.0, 0=minutes, 1=months)
- `scope_coordination: float` - Coordination scope (0.0-1.0, 0=solo, 1=heavy multi-agent)

**Returns:** `str` - Goal ID (UUID string)

**Example:**
```python
from empirica.core.goals.repository import GoalRepository

goal_repo = GoalRepository()
goal_id = goal_repo.create_goal(
    session_id="abc-123",
    objective="Implement user authentication system",
    scope_breadth=0.6,  # Multiple files/components
    scope_duration=0.4,  # Days to weeks
    scope_coordination=0.3  # Some coordination needed
)
```

### `get_goal(self, goal_id: str) -> Optional[Dict]`

Get a specific goal by ID.

**Parameters:**
- `goal_id: str` - Goal identifier

**Returns:** `Optional[Dict]` - Goal dictionary or None if not found

**Example:**
```python
goal = goal_repo.get_goal(goal_id="xyz-789")
if goal:
    print(f"Objective: {goal['objective']}")
    print(f"Status: {goal['status']}")
```

### `update_goal_status(project_id: str, goal_id: str, status: str, completion_evidence: Optional[str] = None)`

Update goal status with optional completion evidence.

> **This is a module-level function in `empirica.core.qdrant.goals`, not a
> `GoalRepository` method.** It takes `project_id` first and there is no `self`.
> The repository's own completion verb is
> `GoalRepository.update_goal_completion(goal_id, is_completed)`, which does not
> take evidence — evidence lives on the Qdrant-side call above, or on the CLI's
> `goals-complete --reason`.

**Parameters:**
- `project_id: str` - Project identifier
- `goal_id: str` - Goal identifier
- `status: str` - New status ('in_progress', 'complete', 'blocked', 'paused')
- `completion_evidence: Optional[str]` - Evidence of completion (required for 'complete' status)

**Example:**
```python
from empirica.core.qdrant.goals import update_goal_status

update_goal_status(
    project_id="proj-123",
    goal_id="xyz-789",
    status="complete",
    completion_evidence="Authentication system implemented with tests passing"
)
```

### `add_success_criterion(self, goal_id: str, validation_method: str, description: str, threshold: float = None, is_required: bool = True)`

Add a success criterion to a goal.

**Parameters:**
- `goal_id: str` - Goal identifier
- `validation_method: str` - How the criterion is checked
- `description: str` - What the criterion asserts
- `threshold: float` - Value the validation must meet, where the method produces one
- `is_required: bool` - Whether the goal can complete without it (default `True`)

There are **no `criterion` or `weight` parameters**. The criterion text goes in
`description`; `validation_method` carries how it gets checked, which is the
field that makes a criterion verifiable rather than aspirational.

**Example:**
```python
goal_repo.add_success_criterion(
    goal_id="xyz-789",
    validation_method="test_suite",
    description="User can login with username/password",
    threshold=1.0,
    is_required=True,
)
```

## Task Repository

## Advanced Goal Operations

### `get_goal_tree(self, session_id: str) -> Dict`

Get the complete goal tree for a session, with all tasks and dependencies.

> Defined on `SessionDatabase`, not on `GoalRepository`, and it takes a
> **`session_id`** — the tree is the goals belonging to a session, not the
> descendants of one goal. There is no goal-rooted variant.

**Parameters:**
- `session_id: str` - Session identifier

**Returns:** `Dict` - Tree structure with the session's goals and their tasks

**Example:**
```python
tree = session_db.get_goal_tree(session_id="sess-456")
for goal in tree["goals"]:
    print(f"Goal: {goal['objective']}")
```

## Batch Operations

## Query Methods

### `search_goals(project_id: str, query: str, item_type: Optional[str] = None, status: Optional[str] = None, ai_id: Optional[str] = None, include_subtasks: bool = False, limit: int = None) -> List[Dict]`

Semantic search over goals with optional filters.

> A module-level function in `empirica.core.qdrant.goals`, not a
> `GoalRepository` method. `project_id` is required and comes first; there is no
> `session_id` filter.

**Parameters:**
- `project_id: str` - Project identifier (required)
- `query: str` - Text to search for
- `item_type: Optional[str]` - Restrict to a goal or subtask type
- `status: Optional[str]` - Filter by status
- `ai_id: Optional[str]` - Filter by the practice that logged it
- `include_subtasks: bool` - Include subtasks in results (default `False`)
- `limit: int` - Maximum results

**Returns:** `List[Dict]` - Matching goal dictionaries

**Example:**
```python
from empirica.core.qdrant.goals import search_goals

matching_goals = search_goals(project_id="proj-123", query="authentication", limit=20)
for goal in matching_goals:
    print(f"Match: {goal['objective']}")
```

## CLI Commands

### `goals-add-dependency`

Add a goal-to-goal dependency relationship.

```bash
empirica goals-add-dependency --goal-id <GOAL_ID> --depends-on <DEPENDS_ON_GOAL_ID> [--type <TYPE>] [--description <DESC>]
```

**Parameters:**
- `--goal-id` - Goal that has the dependency
- `--depends-on` - Goal that must be completed first
- `--type` - Dependency type: `blocks`, `enables`, `informs` (default: `blocks`)
- `--description` - Optional description of the relationship

**Example:**
```bash
# Goal B depends on Goal A (Goal A must complete before B can start)
empirica goals-add-dependency \
  --goal-id abc-456 \
  --depends-on abc-123 \
  --type blocks \
  --description "Authentication must be complete before implementing user profiles"
```

**Output (JSON):**
```json
{
  "ok": true,
  "dependency_id": "dep-789",
  "goal_id": "abc-456",
  "depends_on_goal_id": "abc-123",
  "type": "blocks"
}
```

---

## Best Practices

1. **Define clear success criteria** when creating goals to enable proper progress tracking.

2. **Break down complex goals** into manageable tasks with specific, measurable outcomes.

3. **Establish dependencies** between tasks to ensure proper execution order.

4. **Update status regularly** to maintain accurate progress tracking.

5. **Provide meaningful completion evidence** to enable knowledge transfer and verification.

6. **Use appropriate priorities** to help with task scheduling and resource allocation.

---

## Error Handling

Methods typically raise:
- `ValueError` for invalid parameters
- `sqlite3.Error` for database issues
- `KeyError` when referenced entities don't exist

---

---

## Data Classes

### ScopeVector

Goal scope as epistemic vectors. AI self-assesses, Sentinel validates coherence.

```python
from empirica.core.goals.types import ScopeVector

@dataclass
class ScopeVector:
    breadth: float      # 0.0-1.0: How wide (0=single function, 1=entire codebase)
    duration: float     # 0.0-1.0: Expected lifetime (0=minutes, 1=months)
    coordination: float # 0.0-1.0: Multi-agent coordination needed

# Example
scope = ScopeVector(breadth=0.6, duration=0.4, coordination=0.3)
```

| Field | Range | Low Value | High Value |
|-------|-------|-----------|------------|
| `breadth` | 0.0-1.0 | Single file/function | Entire codebase |
| `duration` | 0.0-1.0 | Minutes/hours | Weeks/months |
| `coordination` | 0.0-1.0 | Solo work | Heavy multi-agent |

---

### DependencyType (Enum)

Dependency relationship types between goals.

```python
from empirica.core.goals.types import DependencyType

class DependencyType(Enum):
    PREREQUISITE = "prerequisite"     # Must complete before starting
    CONCURRENT = "concurrent"         # Can work on simultaneously
    INFORMATIONAL = "informational"   # Nice to have context
```

---

### SuccessCriterion

Measurable success criterion for goal completion.

```python
from empirica.core.goals.types import SuccessCriterion

@dataclass
class SuccessCriterion:
    id: str
    description: str
    validation_method: str    # "completion", "quality_gate", "metric_threshold"
    threshold: Optional[float] = None
    is_required: bool = True
    is_met: bool = False
```

**Validation Methods:**
- `completion` - Binary done/not done
- `quality_gate` - Passes quality checks
- `metric_threshold` - Numeric value meets threshold

---

### GoalDecision

Result of goal creation decision logic.

```python
from empirica.core.goals.decision_logic import GoalDecision, decide_goal_creation

decision = decide_goal_creation(
    clarity=0.8,
    signal=0.7,
    know=0.5,
    context=0.4
)

print(decision.should_create_goal_now)  # False
print(decision.suggested_action)        # 'investigate_first'
print(decision.reasoning)               # Explanation
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `should_create_goal_now` | bool | Create goal immediately? |
| `reasoning` | str | Human-readable explanation |
| `suggested_action` | str | 'create_goal', 'investigate_first', 'ask_clarification' |
| `confidence` | float | Confidence in decision |
| `clarity_score` | float | Input clarity |
| `signal_score` | float | Input signal quality |
| `know_score` | float | Domain knowledge |
| `context_score` | float | Environment context |

**Decision Matrix:**

| Condition | Suggested Action |
|-----------|-----------------|
| High clarity + signal + know + context | `create_goal` |
| High clarity + signal, low know/context | `investigate_first` |
| Low clarity or signal | `ask_clarification` |

---

## Implementation Files

### TaskStatus (Enum)

Task completion status.

```python
from empirica.core.tasks.types import TaskStatus

class TaskStatus(Enum):
    PENDING = "pending"           # Not started
    IN_PROGRESS = "in_progress"   # Currently working
    COMPLETED = "completed"       # Done
    BLOCKED = "blocked"           # Blocked by dependency
    SKIPPED = "skipped"           # Decided not to do
```

---

### EpistemicImportance (Enum)

Task importance from epistemic perspective.

```python
from empirica.core.tasks.types import EpistemicImportance

class EpistemicImportance(Enum):
    CRITICAL = "critical"   # Required for goal success
    HIGH = "high"           # Important but not blocking
    MEDIUM = "medium"       # Nice to have
    LOW = "low"             # Optional enhancement
```

---

## Implementation Files

- `empirica/core/goals/types.py` - ScopeVector, DependencyType, SuccessCriterion, Goal
- `empirica/core/goals/decision_logic.py` - GoalDecision, decide_goal_creation
- `empirica/core/goals/repository.py` - GoalRepository
- `empirica/core/tasks/repository.py` - TaskRepository
- `empirica/core/tasks/types.py` - TaskStatus, EpistemicImportance, SubTask

---

**Module Location:** `empirica/core/goals/repository.py`, `empirica/core/tasks/repository.py`
**API Stability:** Stable
**Last Updated:** 2026-01-09

---

## Verified API surface

Generated from source. Every entry below exists; the signatures are the real ones.


### `empirica/core/goals/repository.py`


#### class `GoalRepository`

- `save_goal(self, goal: Goal, session_id: str | None=None, transaction_id: str | None=None) -> bool` — Save goal to database
- `get_goal(self, goal_id: str) -> Goal | None` — Retrieve goal by ID (supports short ID prefix matching)
- `get_session_goals(self, session_id: str) -> list[Goal]` — Retrieve all goals for a session
- `get_transaction_goals(self, transaction_id: str) -> list[Goal]` — Retrieve all goals for an epistemic transaction.
- `query_goals_by_transaction(self, transaction_id: str, is_completed: bool | None=None, project_id: str | None=None) -> list[Goal]` — Query goals filtered by transaction with optional secondary filters.
- `list_active_criteria_for_session(self, session_id: str)` — Return (Goal, SuccessCriterion) pairs for active goals in a session.
- `add_success_criterion(self, goal_id: str, validation_method: str, description: str, threshold: float | None=None, is_required: bool=True) -> str | None` — Add a SuccessCriterion to an existing goal. Returns the new criterion ID.
- `update_is_met(self, criterion_id: str, is_met: bool) -> bool` — Update is_met on a SuccessCriterion. Best-effort, no raise.
- `update_goal_completion(self, goal_id: str, is_completed: bool=True) -> bool` — Update goal completion status
- `query_goals(self, session_id: str | None=None, is_completed: bool | None=None, scope: ScopeVector | None=None) -> list[Goal]` — Query goals with filters
- `close(self)` — Close database connection
- `mark_goals_stale(self, session_id: str, stale_reason: str='memory_compact') -> int` — Mark all in_progress goals for a session as stale
- `get_stale_goals(self, session_id: str | None=None, project_id: str | None=None) -> list[dict[str, Any]]` — Get stale goals for a session or project
- `refresh_goal(self, goal_id: str) -> bool` — Mark a stale goal as refreshed (AI has regained context)


### `empirica/core/tasks/repository.py`


#### class `TaskRepository`

- `save_subtask(self, subtask: SubTask) -> bool` — Save subtask to database
- `get_subtask(self, subtask_id: str) -> SubTask | None` — Retrieve subtask by ID (supports partial UUID)
- `get_goal_subtasks(self, goal_id: str) -> list[SubTask]` — Retrieve all subtasks for a goal
- `update_subtask_status(self, subtask_id: str, status: TaskStatus, completion_evidence: str | None=None) -> bool` — Update subtask status (supports partial UUID)
- `save_decomposition(self, decomposition: TaskDecomposition) -> bool` — Save task decomposition metadata
- `get_decomposition(self, goal_id: str) -> TaskDecomposition | None` — Retrieve task decomposition for a goal
- `query_subtasks(self, goal_id: str | None=None, status: TaskStatus | None=None, epistemic_importance: EpistemicImportance | None=None) -> list[SubTask]` — Query subtasks with filters
- `close(self)` — Close database connection

