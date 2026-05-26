"""
CLI surface regression test for the subtask→task rename (clean break).

Pins:
1. New verbs (goals-add-task, goals-complete-task, goals-get-tasks) are
   registered in the parser.
2. Old verbs (goals-add-subtask, goals-complete-subtask, goals-get-subtasks)
   are GONE — invoking them prints argparse's "invalid choice" error.
3. --task-id is the canonical flag; --subtask-id no longer exists on
   goals-complete-task.
4. New aliases (goal-add-task, goal-complete-task) work.

The clean-break decision means no backward-compat shim — old verbs are not
deprecated, they're removed.
"""

import subprocess


def _run(args: list[str], expect_exit: int | None = None) -> tuple[int, str, str]:
    p = subprocess.run(
        ["empirica", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if expect_exit is not None:
        assert p.returncode == expect_exit, (
            f"expected exit {expect_exit}, got {p.returncode}\n"
            f"stdout: {p.stdout}\nstderr: {p.stderr}"
        )
    return p.returncode, p.stdout, p.stderr


class TestNewVerbsRegistered:
    def test_goals_add_task_help_works(self):
        rc, out, err = _run(["goals-add-task", "--help"], expect_exit=0)
        assert "goals-add-task" in out
        assert "--goal-id" in out
        assert "--description" in out

    def test_goals_complete_task_help_works(self):
        rc, out, err = _run(["goals-complete-task", "--help"], expect_exit=0)
        assert "goals-complete-task" in out
        assert "--task-id" in out
        assert "--evidence" in out

    def test_goals_get_tasks_help_works(self):
        rc, out, err = _run(["goals-get-tasks", "--help"], expect_exit=0)
        assert "goals-get-tasks" in out
        assert "--goal-id" in out


class TestOldVerbsRemoved:
    def test_goals_add_subtask_is_gone(self):
        rc, out, err = _run(["goals-add-subtask", "--help"])
        assert rc != 0
        assert "invalid choice: 'goals-add-subtask'" in err

    def test_goals_complete_subtask_is_gone(self):
        rc, out, err = _run(["goals-complete-subtask", "--help"])
        assert rc != 0
        assert "invalid choice: 'goals-complete-subtask'" in err

    def test_goals_get_subtasks_is_gone(self):
        rc, out, err = _run(["goals-get-subtasks", "--help"])
        assert rc != 0
        assert "invalid choice: 'goals-get-subtasks'" in err


class TestSubtaskIdFlagRemoved:
    def test_complete_task_rejects_subtask_id_flag(self):
        rc, out, err = _run([
            "goals-complete-task",
            "--subtask-id", "deadbeef",
            "--evidence", "should not work",
        ])
        assert rc != 0
        # argparse reports the missing-required first, but the --subtask-id flag
        # must NOT have been silently accepted as --task-id (clean break, no alias).
        assert "required" in err and "--task-id" in err

    def test_complete_task_requires_task_id(self):
        rc, out, err = _run(["goals-complete-task"])
        assert rc != 0
        assert "--task-id" in err


class TestNewAliases:
    def test_goal_add_task_singular_alias(self):
        rc, out, err = _run(["goal-add-task", "--help"], expect_exit=0)
        assert "--goal-id" in out

    def test_goal_complete_task_singular_alias(self):
        rc, out, err = _run(["goal-complete-task", "--help"], expect_exit=0)
        assert "--task-id" in out


class TestCompleteTaskRejectsBadUUID:
    """The fix from yesterday's transaction must still hold after the rename."""

    def test_phantom_uuid_returns_nonzero(self):
        rc, out, err = _run([
            "goals-complete-task",
            "--task-id", "deadbeef-cafe-1234-5678-aaaabbbbcccc",
            "--evidence", "should fail",
        ])
        assert rc == 1
        assert "No task matches" in err or "task_not_found" in (out + err)
