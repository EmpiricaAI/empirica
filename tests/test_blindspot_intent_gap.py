"""Intent-gap blindspot detection — the least-noisy unknown-unknown signal.

A candidate is an OPEN subtask under a non-terminal goal whose findings, unknowns,
and dead_ends are all empty (stated intent, no coverage, no acknowledgment, no
attempt). Any of the three lists — or a terminal status — masks it out.
"""

from __future__ import annotations

from empirica.core.blindspots import detect_intent_gaps


def _goal(status="active", subtasks=None, objective="Ship X"):
    return {"goal_id": "g1", "objective": objective, "status": status, "subtasks": subtasks or []}


def _sub(status="pending", findings=None, unknowns=None, dead_ends=None, desc="assess Y"):
    return {
        "subtask_id": "s1",
        "description": desc,
        "status": status,
        "findings": findings or [],
        "unknowns": unknowns or [],
        "dead_ends": dead_ends or [],
    }


def test_open_uncovered_unacknowledged_subtask_is_a_candidate():
    gaps = detect_intent_gaps([_goal(subtasks=[_sub()])])
    assert len(gaps) == 1
    g = gaps[0]
    assert g["kind"] == "intent_gap"
    assert g["intent"] == "assess Y"
    assert g["objective"] == "Ship X"
    assert g["subtask_id"] == "s1"


def test_finding_masks_it_covered():
    assert detect_intent_gaps([_goal(subtasks=[_sub(findings=["found something"])])]) == []


def test_unknown_masks_it_acknowledged():
    # an acknowledged gap is a known-unknown, not a blindspot
    assert detect_intent_gaps([_goal(subtasks=[_sub(unknowns=["what about Z?"])])]) == []


def test_planned_goal_excluded_by_default():
    # dormant 'planned' goal's untouched subtask is backlog, not an active blindspot
    assert detect_intent_gaps([_goal(status="planned", subtasks=[_sub()])]) == []


def test_planned_goal_included_when_active_only_false():
    gaps = detect_intent_gaps([_goal(status="planned", subtasks=[_sub()])], active_only=False)
    assert len(gaps) == 1  # backlog view surfaces it


def test_in_progress_blocked_active_goals_are_in_scope():
    for st in ("in_progress", "blocked", "active"):
        assert len(detect_intent_gaps([_goal(status=st, subtasks=[_sub()])])) == 1, st


def test_dead_end_masks_it_attempted():
    assert detect_intent_gaps([_goal(subtasks=[_sub(dead_ends=["tried A, failed"])])]) == []


def test_terminal_subtask_status_masks_it():
    for st in ("completed", "complete", "done", "cancelled", "abandoned"):
        assert detect_intent_gaps([_goal(subtasks=[_sub(status=st)])]) == [], st


def test_terminal_goal_status_masks_all_its_subtasks():
    for st in ("completed", "abandoned", "cancelled", "stale", "done"):
        assert detect_intent_gaps([_goal(status=st, subtasks=[_sub()])]) == [], st


def test_empty_and_none_tree():
    assert detect_intent_gaps([]) == []
    assert detect_intent_gaps(None) == []


def test_multiple_goals_only_uncovered_open_subtasks():
    tree = [
        _goal(objective="A", subtasks=[_sub(desc="a1"), _sub(desc="a2", findings=["x"])]),  # a1 gap, a2 covered
        _goal(objective="B", status="completed", subtasks=[_sub(desc="b1")]),  # goal terminal → skip
    ]
    gaps = detect_intent_gaps(tree)
    assert [g["intent"] for g in gaps] == ["a1"]


def test_missing_status_treated_as_open():
    # a subtask/goal with no status is non-terminal → still a candidate
    sub = {"subtask_id": "s", "description": "d", "findings": [], "unknowns": [], "dead_ends": []}
    goal = {"goal_id": "g", "objective": "o", "subtasks": [sub]}
    assert len(detect_intent_gaps([goal])) == 1
