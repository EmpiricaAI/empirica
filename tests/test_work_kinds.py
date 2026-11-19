"""Work kinds on goals, tasks and roadmap items (goal 1dfb9911; David confirmed the six on 2026-11-19).

The forecast shows the recent mix by kind and recommends one; that needs the kind recorded where the work is planned.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

from empirica.core.work_kinds import COMMIT_TYPE, KINDS

_ROOT = Path(__file__).resolve().parent.parent


def test_the_six_kinds_and_each_maps_to_a_conventional_commit_type():
    assert KINDS == ("fix", "refine", "simplify", "expand", "investigate", "sustain")
    assert set(COMMIT_TYPE) == set(KINDS)
    assert set(COMMIT_TYPE.values()) <= {"fix", "feat", "refactor", "perf", "docs", "test", "chore", "ci", "build"}


def _goal_parsers():
    from empirica.cli.parsers.checkpoint_parsers import add_checkpoint_parsers

    root = argparse.ArgumentParser()
    add_checkpoint_parsers(root.add_subparsers(dest="command"))
    return root


@pytest.mark.parametrize(
    "verb", [["goals-create", "--objective", "x"], ["goals-add-task", "--goal-id", "g", "--description", "d"]]
)
def test_both_goal_verbs_take_a_kind_from_the_closed_set(verb):
    root = _goal_parsers()
    assert root.parse_args([*verb, "--kind", "simplify"]).kind == "simplify"
    with pytest.raises(SystemExit):
        root.parse_args([*verb, "--kind", "polish"])


def test_a_tasks_kind_survives_its_stored_form():
    from empirica.core.tasks.types import SubTask

    t = SubTask.create(goal_id="g", description="d", kind="investigate")
    assert SubTask.from_dict(t.to_dict()).kind == "investigate"
    assert SubTask.from_dict({**t.to_dict(), "kind": None}).kind is None
    legacy = {k: v for k, v in t.to_dict().items() if k != "kind"}
    assert SubTask.from_dict(legacy).kind is None


def test_goals_create_puts_the_kind_in_the_goals_metadata():
    from empirica.cli.command_handlers.goal_commands import _parse_goal_config

    args = argparse.Namespace(
        config=None, session_id="s", objective="o", description=None, scope_breadth=None, scope_duration=None,
        scope_coordination=None, estimated_complexity=None, constraints=None, metadata='{"a": 1}', engagement_id="e",
        output="json", success_criteria=None, success_criteria_file=None, kind="expand",
    )  # fmt: skip
    assert _parse_goal_config(args)["metadata"] == {"a": 1, "kind": "expand"}


def test_a_roadmap_item_may_carry_a_kind_from_the_same_set():
    spec = importlib.util.spec_from_file_location("release_kinds", _ROOT / "scripts" / "release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert tuple(mod.WORK_KINDS) == KINDS  # release.py does not import empirica; the copy is held here
    item = {"id": "x", "title": "X", "prediction": "P.", "confidence": 0.5, "evidence": "measured", "basis": "B.",
            "falsifier": "F.", "horizon": "1.18.0", "steered": False, "status": "predicted"}  # fmt: skip
    doc = {"forecast": {"made_in": "1.17.11", "made_on": "2026-11-19"}, "items": [{**item, "kind": "expand"}]}
    mod.validate_roadmap(doc)
    with pytest.raises(ValueError, match="kind"):
        mod.validate_roadmap({**doc, "items": [{**item, "kind": "polish"}]})


def test_a_tasks_kind_survives_the_goal_note_and_a_rebuild():
    """Tasks travel in the goal's note as TASK_FIELDS; the rebuild's insert_tasks writes them back (goal 25f4ab9a)."""
    import json
    import sqlite3

    from empirica.core.canonical.empirica_git import goal_note_state as gns

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE subtasks (id TEXT, goal_id TEXT, description TEXT, status TEXT, epistemic_importance TEXT, "
        "completion_evidence TEXT, created_timestamp REAL, completed_timestamp REAL, subtask_data TEXT)"
    )
    conn.execute(
        "INSERT INTO subtasks VALUES ('t1', 'g', 'd', 'pending', 'medium', NULL, 1.0, NULL, ?)",
        (json.dumps({"findings": [], "kind": "sustain"}),),
    )
    conn.execute("INSERT INTO subtasks VALUES ('t2', 'g', 'e', 'pending', 'medium', NULL, 2.0, NULL, '{}')")
    noted = gns._tasks(conn, "g")
    assert noted[0]["kind"] == "sustain" and "kind" not in noted[1]
    conn.execute("DELETE FROM subtasks")
    assert gns.insert_tasks(conn, "g", noted) == 2
    kinds = [json.loads(r[0]).get("kind") for r in conn.execute("SELECT subtask_data FROM subtasks ORDER BY id")]
    assert kinds == ["sustain", None]
