"""Task completion must not be logged as a finding.

The hook used to shell `finding-log --finding "Task completed: <subject>"` on every
task completion. That types an activity record as an observation: a finding answers
"what is true that I did not know before", while "task X finished" answers "what did
I do". Two months of it produced 241 open rows in this practice and 254 in cortex's,
all competing with real findings for retrieval — and cortex correctly refused to
sweep theirs while the writer was still emitting, since cleanup against a live writer
reads as progress while the corpus refills.

This is a source-level guard rather than a behavioural test: the hook shells out and
exits, so the cheap, durable assertion is that the finding-log invocation is not in
the file. It fails if anyone reintroduces it, which is the only regression that
matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "task-completed.py"
)


@pytest.fixture(scope="module")
def source() -> str:
    return HOOK.read_text(encoding="utf-8")


def test_the_hook_does_not_log_task_completion_as_a_finding(source):
    """POSITIVE CONTROL — the invocation that produced ~500 mis-typed rows.

    Matches the argv element, not a bare substring. The first version of this test
    asserted on the prose and failed against the fixed file, because the comment
    explaining the removal quoted the very strings it was banning — a guard tripping
    on its own documentation.
    """
    assert '"finding-log"' not in source, (
        "task-completed.py is invoking the finding verb again — a completion belongs "
        "on the goal via the Task<->Goal bridge, not in the finding corpus"
    )


def test_the_goal_bridge_that_replaces_it_is_still_there(source):
    """NEGATIVE CONTROL — removing the finding-log is only correct because the
    bridge already records completions where they belong. If the bridge went away
    too, the first assertion would pass while completions vanished entirely."""
    assert "_find_matching_goal" in source
    assert "_auto_complete_goal" in source
