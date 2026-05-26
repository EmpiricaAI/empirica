# Goals & Tasks API

**Module:** `empirica.core.goals.repository` and `empirica.core.tasks.repository`
**Category:** Task Management
**Stability:** Production Ready

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

### `get_goals_for_session(self, session_id: str, status: Optional[str] = None) -> List[Dict]`

Get all goals for a session, optionally filtered by status.

**Parameters:**
- `session_id: str` - Session identifier
- `status: Optional[str]` - Optional status filter ('in_progress', 'complete', 'blocked')

**Returns:** `List[Dict]` - List of goal dictionaries

**Example:**
```python
# Get all goals for session
all_goals = goal_repo.get_goals_for_session(session_id="abc-123")

# Get only completed goals
completed_goals = goal_repo.get_goals_for_session(
    session_id="abc-123", 
    status="complete"
)
```

### `update_goal_status(self, goal_id: str, status: str, completion_evidence: Optional[str] = None)`

Update goal status with optional completion evidence.

**Parameters:**
- `goal_id: str` - Goal identifier
- `status: str` - New status ('in_progress', 'complete', 'blocked', 'paused')
- `completion_evidence: Optional[str]` - Evidence of completion (required for 'complete' status)

**Example:**
```python
goal_repo.update_goal_status(
    goal_id="xyz-789",
    status="complete",
    completion_evidence="Authentication system implemented with tests passing"
)
```

### `add_success_criterion(self, goal_id: str, criterion: str, weight: float = 1.0)`

Add a success criterion to a goal.

**Parameters:**
- `goal_id: str` - Goal identifier
- `criterion: str` - Success criterion description
- `weight: float` - Relative importance weight (default 1.0)

**Example:**
```python
goal_repo.add_success_criterion(
    goal_id="xyz-789",
    criterion="User can login with username/password",
    weight=0.8
)
```

### `get_goal_progress(self, goal_id: str) -> Dict`

Get detailed progress information for a goal.

**Parameters:**
- `goal_id: str` - Goal identifier

**Returns:** `Dict` - Progress dictionary with completion percentage, criteria status, etc.

**Example:**
```python
progress = goal_repo.get_goal_progress(goal_id="xyz-789")
print(f"Progress: {progress['percentage_complete']}%")
print(f"Criteria met: {progress['criteria_met']}/{progress['total_criteria']}")
```

---

## Task Repository

### `create_task(self, goal_id: str, description: str, priority: str = 'medium', estimated_effort: Optional[int] = None) -> str`

Create a new task associated with a goal.

**Parameters:**
- `goal_id: str` - Parent goal identifier
- `description: str` - Task description
- `priority: str` - Priority level ('low', 'medium', 'high', 'critical'), default 'medium'
- `estimated_effort: Optional[int]` - Estimated effort in minutes

**Returns:** `str` - Task ID (UUID string)

**Example:**
```python
from empirica.core.tasks.repository import TaskRepository

task_repo = TaskRepository()
task_id = task_repo.create_task(
    goal_id="xyz-789",
    description="Design database schema for user accounts",
    priority="high",
    estimated_effort=120  # 2 hours
)
```

### `get_tasks_for_goal(self, goal_id: str, status: Optional[str] = None) -> List[Dict]`

Get all tasks for a goal, optionally filtered by status.

**Parameters:**
- `goal_id: str` - Goal identifier
- `status: Optional[str]` - Optional status filter ('pending', 'in_progress', 'complete', 'blocked')

**Returns:** `List[Dict]` - List of task dictionaries

**Example:**
```python
# Get all tasks for goal
tasks = task_repo.get_tasks_for_goal(goal_id="xyz-789")

# Get only pending tasks
pending_tasks = task_repo.get_tasks_for_goal(
    goal_id="xyz-789", 
    status="pending"
)
```

### `update_task_status(self, task_id: str, status: str, completion_evidence: Optional[str] = None)`

Update task status with optional completion evidence.

**Parameters:**
- `task_id: str` - Task identifier
- `status: str` - New status ('pending', 'in_progress', 'complete', 'blocked')
- `completion_evidence: Optional[str]` - Evidence of completion (required for 'complete' status)

**Example:**
```python
task_repo.update_task_status(
    task_id="task-456",
    status="complete",
    completion_evidence="Database schema created with proper relationships and constraints"
)
```

### `assign_task(self, task_id: str, assignee_id: str)`

Assign a task to an AI agent.

**Parameters:**
- `task_id: str` - Task identifier
- `assignee_id: str` - AI identifier for assignee

**Example:**
```python
task_repo.assign_task(task_id="task-456", assignee_id="claude-sonnet-4")
```

### `get_task_dependencies(self, task_id: str) -> List[Dict]`

Get dependencies for a task.

**Parameters:**
- `task_id: str` - Task identifier

**Returns:** `List[Dict]` - List of dependency dictionaries

**Example:**
```python
deps = task_repo.get_task_dependencies(task_id="task-456")
for dep in deps:
    print(f"Depends on: {dep['dependency_task_id']} - {dep['relationship']}")
```

### `create_dependency(self, dependent_task_id: str, dependency_task_id: str, relationship: str = 'blocks')`

Create a dependency relationship between tasks.

**Parameters:**
- `dependent_task_id: str` - Task that depends on another
- `dependency_task_id: str` - Task that is depended on
- `relationship: str` - Relationship type ('blocks', 'precedes', 'parallel'), default 'blocks'

**Example:**
```python
# Database schema must be created before implementation
task_repo.create_dependency(
    dependent_task_id="impl-task-789",  # Implementation task
    dependency_task_id="design-task-123",  # Design task
    relationship="blocks"
)
```

---

## Advanced Goal Operations

### `decompose_goal(self, goal_id: str, decomposition_strategy: str = 'horizontal') -> List[str]`

Decompose a goal into tasks using a specific strategy.

**Parameters:**
- `goal_id: str` - Goal identifier to decompose
- `decomposition_strategy: str` - Strategy ('horizontal', 'vertical', 'functional', 'sequential'), default 'horizontal'

**Returns:** `List[str]` - List of created task IDs

**Example:**
```python
task_ids = goal_repo.decompose_goal(
    goal_id="xyz-789",
    decomposition_strategy="functional"
)
print(f"Created {len(task_ids)} tasks for goal")
```

### `get_goal_tree(self, goal_id: str) -> Dict`

Get complete goal tree with all tasks and dependencies.

**Parameters:**
- `goal_id: str` - Goal identifier

**Returns:** `Dict` - Tree structure with goal and all tasks

**Example:**
```python
tree = goal_repo.get_goal_tree(goal_id="xyz-789")
print(f"Goal: {tree['objective']}")
for task in tree['tasks']:
    print(f"  - {task['description']} [{task['status']}]")
```

### `calculate_goal_confidence(self, goal_id: str) -> float`

Calculate overall confidence in achieving a goal based on task progress and dependencies.

**Parameters:**
- `goal_id: str` - Goal identifier

**Returns:** `float` - Confidence score (0.0-1.0)

**Example:**
```python
confidence = goal_repo.calculate_goal_confidence(goal_id="xyz-789")
print(f"Goal confidence: {confidence:.2f}")
```

---

## Batch Operations

### `bulk_create_tasks(self, goal_id: str, task_descriptions: List[str]) -> List[str]`

Create multiple tasks for a goal in a single operation.

**Parameters:**
- `goal_id: str` - Parent goal identifier
- `task_descriptions: List[str]` - List of task descriptions

**Returns:** `List[str]` - List of created task IDs

**Example:**
```python
task_descriptions = [
    "Create user model",
    "Implement authentication controller",
    "Design login UI",
    "Set up password hashing"
]

task_ids = task_repo.bulk_create_tasks(goal_id="xyz-789", task_descriptions=task_descriptions)
print(f"Created {len(task_ids)} tasks")
```

### `update_multiple_goals_status(self, goal_ids: List[str], status: str)`

Update status for multiple goals.

**Parameters:**
- `goal_ids: List[str]` - List of goal identifiers
- `status: str` - New status for all goals

**Example:**
```python
goal_repo.update_multiple_goals_status(
    goal_ids=["goal-1", "goal-2", "goal-3"], 
    status="blocked"
)
```

---

## Query Methods

### `search_goals(self, query: str, session_id: Optional[str] = None, project_id: Optional[str] = None) -> List[Dict]`

Search goals by text query with optional filters.

**Parameters:**
- `query: str` - Text to search for in objectives
- `session_id: Optional[str]` - Optional session filter
- `project_id: Optional[str]` - Optional project filter

**Returns:** `List[Dict]` - Matching goal dictionaries

**Example:**
```python
matching_goals = goal_repo.search_goals(query="authentication", project_id="proj-123")
for goal in matching_goals:
    print(f"Match: {goal['objective']}")
```

### `get_goals_ready_for_work(self, session_id: str, max_results: int = 10) -> List[Dict]`

Get goals ready for immediate work (not blocked, not complete).

**Parameters:**
- `session_id: str` - Session identifier
- `max_results: int` - Maximum number of results, default 10

**Returns:** `List[Dict]` - Ready goal dictionaries

**Example:**
```python
ready_goals = goal_repo.get_goals_ready_for_work(session_id="abc-123", max_results=5)
for goal in ready_goals:
    print(f"Ready: {goal['objective']}")
```

---

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