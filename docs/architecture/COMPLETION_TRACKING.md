# Completion Tracking - Knowing When Things Are Done

**Module:** `empirica.core.completion`

The Completion system tracks goal and task progress with evidence mapping. It prevents infinite loops by providing clear signals when work is complete.

## Philosophy

Completion requires evidence:
- **Track subtasks**: Completed, remaining, blocked
- **Map evidence**: Link completions to commits, files, artifacts
- **Auto-detect**: Scan git commits for task completion markers
- **Aggregate**: Session-level metrics for efficiency tracking

---

## Classes

### CompletionRecord

Completion status for a single goal.

```python
@dataclass
class CompletionRecord:
    goal_id: str
    completion_percentage: float         # 0.0 to 1.0
    completed_subtasks: List[str]        # SubTask IDs
    remaining_subtasks: List[str]        # SubTask IDs
    blocked_subtasks: List[str]          # SubTask IDs
    estimated_remaining_tokens: int
    actual_tokens_used: int
    completion_evidence: Dict[str, str]  # subtask_id -> evidence
    last_updated: float
```

**Evidence types:**
- `commit:abc1234` - Git commit reference
- `file:src/auth.py` - File path
- `test:passed` - Test result
- Custom string evidence

### CompletionMetrics

Aggregate metrics across multiple goals (session-level).

```python
@dataclass
class CompletionMetrics:
    goals_completed: int
    goals_in_progress: int
    goals_blocked: int
    total_tokens_used: int
    average_completion_rate: float
    efficiency_score: float  # actual/estimated tokens (lower is better)
```

### CompletionTracker

Main tracker that monitors goal and task completion.

```python
tracker = CompletionTracker(db_path=None, enable_git_notes=True)

# Track progress for a goal
record = tracker.track_progress(goal_id="abc123")
print(f"Progress: {record.completion_percentage:.1%}")
print(f"Remaining: {record.remaining_subtasks}")

# Mark subtask complete with evidence
tracker.record_subtask_completion(
    subtask_id="subtask-uuid",
    evidence="commit:abc1234"
)

# Auto-detect completions from git commits
auto_completed = tracker.auto_update_from_recent_commits(
    goal_id="abc123",
    since="1 hour ago"
)
print(f"Auto-completed {auto_completed} subtasks")

# Get session-level metrics
metrics = tracker.get_session_metrics(session_id="session-uuid")
print(f"Completed: {metrics.goals_completed}")
print(f"Efficiency: {metrics.efficiency_score:.2f}")
```

**Auto-detection patterns:**
Scans git commit messages for:
- `✅ [TASK:subtask-uuid]`
- `[COMPLETE:subtask-uuid]`
- `Addresses subtask subtask-uuid`

### GitProgressQuery

Query git notes for team progress tracking. Enables lead AIs to see what agents accomplished.

```python
query = GitProgressQuery()

# Get timeline for a goal
timeline = query.get_goal_timeline(goal_id="abc123", max_commits=100)
for commit in timeline['commits']:
    print(f"{commit['hash']}: {commit['message']}")
    if commit['task']:
        print(f"  → Completed: {commit['task']['description']}")

# Multi-goal team progress
progress = query.get_team_progress(goal_ids=["goal1", "goal2", "goal3"])
print(f"Total completed tasks: {progress['total_completed_tasks']}")

# Unified timeline (tasks + epistemic checkpoints)
unified = query.get_unified_timeline(
    session_id="session-uuid",
    goal_id="goal-uuid"
)
for event in unified['timeline']:
    print(f"{event['datetime']}: {event['type']}")
    if 'epistemic_state' in event:
        print(f"  Know: {event['epistemic_state']['know']}")

# Recent activity across all goals
activity = query.get_recent_activity(hours=24)
print(f"Commits in last 24h: {activity['commit_count']}")
```

---

## Git Notes Integration

Task completion metadata is stored in git notes for:
- Distributed storage (travels with repo)
- Lead AI queries (cross-agent visibility)
- Audit trail (who completed what, when)

**Namespace:** `refs/notes/empirica/tasks/{goal_id}`

```json
{
  "subtask_id": "abc-123",
  "goal_id": "xyz-789",
  "description": "Implement OAuth2 flow",
  "epistemic_importance": "high",
  "completed_timestamp": 1704628800,
  "completion_evidence": "commit:abc1234",
  "actual_tokens": 1500,
  "estimated_tokens": 2000
}
```

---

## Integration with CASCADE

```
PREFLIGHT ──────► CHECK ──────► POSTFLIGHT ──────► POST-TEST
    │               │               │                  │
    ▼               ▼               ▼                  ▼
Set goals     Track progress    Record completion  Grounded Verification
              CompletionTracker  Update metrics    (evidence-based calibration)
              ↓
              Goals auto-mark complete when
              completion_percentage >= 1.0
```

---

## CLI Integration

```bash
# Track goal progress
empirica goals-progress --goal-id <ID>

# Mark subtask complete
empirica goals-complete-task --task-id <ID> --evidence "commit:abc1234"

# Get session metrics
empirica monitor --session-id <ID>
```

---

## Source Files

- `empirica/core/completion/tracker.py` - Main CompletionTracker class
- `empirica/core/completion/types.py` - CompletionRecord, CompletionMetrics dataclasses
- `empirica/core/completion/git_query.py` - GitProgressQuery for team tracking
