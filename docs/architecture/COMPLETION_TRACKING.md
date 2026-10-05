# Completion Tracking: Knowing When Things Are Done

"Done" means two different things in Empirica, and they are tracked in different
places:

1. **Work items** (goals and tasks) are done when someone closes them, ideally with
   evidence. This is bookkeeping, driven by CLI verbs.
2. **The transaction** is done, per phase, when the AI judges it has met that
   phase's question. This is a self-assessed vector (`completion`), later checked
   against evidence.

> **Naming.** The CLI says `task` (`goals-add-task`, `--task-id`). Storage and
> Python keep `subtask` (table `subtasks`, class `SubTask`) by a deliberate
> CLI-versus-storage boundary; see
> [UPGRADE_TO_1.10.md](../guides/UPGRADE_TO_1.10.md#breaking-change--subtask--task-rename).
> This page uses `task` for the verbs and `subtask` only where it names code.

---

## 1. Closing work items

### The verbs in use

| Verb | What it does (from `empirica/cli/command_handlers/goal_commands.py`) |
|---|---|
| `goals-add-task --goal-id G --description D` | Adds a task. `--importance {critical,high,medium,low}`, `--dependencies` (JSON array), `--estimated-tokens`, `--use-beads` |
| `goals-complete-task --task-id T [--evidence E]` | `TaskRepository.update_subtask_status(..., COMPLETED, evidence)`: sets status, `completed_timestamp` and `completion_evidence` on the row. The id may be a full UUID or an unambiguous prefix; no match exits 1 |
| `goals-progress --goal-id G` | Counts tasks with status `completed` over all tasks for the goal and reports a percentage, total, completed and remaining |
| `goals-complete --goal-id G [--reason R]` | Sets the goal `status='completed'`, `is_completed=1`, `completed_timestamp`, and `completion_reason` when the column exists; mirrors the status to Qdrant best-effort so a finished goal stops resurfacing as in progress. Optional `--run-postflight`, `--merge-branch`, `--delete-branch`, `--create-handoff`; closes a linked BEADS issue |
| `goals-list --status {planned,in_progress,blocked,completed,all,drift}` | `drift` lists goals whose `status` text and `is_completed` flag disagree |

Task states are `pending`, `in_progress`, `completed`, `blocked`, `skipped`
(`TaskStatus` in `empirica/core/tasks/types.py`). `goals-progress` counts only
`completed`; a `skipped` task stays in the remaining figure.

Evidence is a free string: a commit hash, a file path, a test result. Nothing
parses or verifies it. It is the record of why the task was closed, which is what
separates achieved from abandoned when someone reads the goal later.

### The `empirica.core.completion` package

`empirica/core/completion/` holds three classes (`CompletionTracker`,
`GitProgressQuery`, and the `CompletionRecord` / `CompletionMetrics`
dataclasses). **No production code imports it.** A search of the repository finds
only the package itself, `tests/integration/test_goal_architecture_e2e.py` and an
entry in `docs/SEMANTIC_INDEX.yaml`. The CLI verbs above go to the repositories
directly. Treat the package as a library with no live caller.

What it does if you call it:

- `CompletionTracker.track_progress(goal_id)` builds a `CompletionRecord` from the
  goal's subtasks. Unlike `goals-progress`, it counts `skipped` as completed, sums
  estimated and actual tokens, and marks the goal complete in the repository when
  the ratio reaches 1.0.
- `record_subtask_completion(subtask_id, evidence)` completes a subtask and, when
  git is available, writes a JSON note to `refs/notes/empirica/tasks/<goal_id>`
  attached to the commit named in `commit:<hash>` evidence, else to HEAD.
- `auto_update_from_recent_commits(goal_id, since)` scans `git log` messages for
  `[TASK:<id>]` (with a leading check mark), `[COMPLETE:<id>]` or
  `Addresses subtask <id>` and completes matching subtasks of that goal.
- `get_session_metrics(session_id)` aggregates goals into a `CompletionMetrics`.
- `GitProgressQuery` reads the `empirica/tasks/<goal_id>` notes back as timelines
  and team progress (`get_goal_timeline`, `get_team_progress`,
  `get_unified_timeline`, `get_recent_activity`).

`refs/notes/empirica/tasks/*` refs do exist in this repository's own notes. Which
caller wrote them was not established; the only writer found in current code is
`CompletionTracker._add_task_note`. `generate_artifacts` in
`artifacts_commands.py` (the git-notes audit-trail generator) reads the `tasks`
notes and indexes them by `goal_id`.

---

## 2. Completion of a transaction, per phase

The `completion` vector is defined by phase:

| Phase | Question |
|---|---|
| Noetic | Have I learned enough to proceed? |
| Praxic | Have I implemented enough to ship? |

(Schema description in `empirica/cli/validation.py`; the same questions appear in
the lean system prompt and the constitution skill.)

Two rules from the code govern how to rate it:

- **Scope is the transaction, not the plan.** The CHECK proceed output carries the
  reminder: rate completion for this transaction only, and if its objective is met,
  `completion = 1.0` regardless of how much of the overall plan remains
  (`_check_build_praxic_reminders` in `_workflow_check.py`).
- **CHECK is not a completion event.** It certifies the move from noetic to
  praxic; the vectors it receives are not scored as the end of the work.

### How the self-assessment is checked

The post-test collector (`empirica/core/post_test/collector.py`) emits goal-based
evidence, and `mapper.py` assigns `completion` to the `execution` category of its
scoring:

| Evidence item | Source | Supports |
|---|---|---|
| `subtask_completion_ratio` | completed over total subtasks of goals in the transaction (falls back to the session when there is no transaction id; `raw_value.scope` records which) | `completion`, `do` |
| `goal_completion_impact` | ratio scaled by 1.2, capped at 1.0, only when something completed | `impact` (not `change`; ruled 2026-10-04) |
| `goals_completed`, `goal_completion_ratio` | triage-style evidence from goals completed in the transaction scope | `do`, `completion` |

Consequence: unclosed tasks lower the grounded figure for `completion`. If the work
is done but the tasks were never closed, the self-assessment and the evidence
will disagree for a bookkeeping reason, not an epistemic one. Close tasks with
`goals-complete-task` as each finishes, and `goals-complete` before POSTFLIGHT.

The disagreement itself is what calibration measures; see
[SELF_MONITORING.md](SELF_MONITORING.md).

---

## Source files

- `empirica/cli/command_handlers/goal_commands.py`: `goals-complete-task`, `goals-progress`, `goals-complete`
- `empirica/core/tasks/repository.py`, `types.py`: task storage and `TaskStatus`
- `empirica/core/post_test/collector.py`, `mapper.py`: goal-completion evidence
- `empirica/core/completion/`: `tracker.py`, `git_query.py`, `types.py` (no live caller)
