#!/usr/bin/env python3
"""
Empirica TaskCompleted Hook — Bridge Claude Code tasks to Empirica goals.

Fires when Claude marks a task as completed. Two functions:
1. Enforce POSTFLIGHT before task completion (measurement discipline)
2. Auto-complete matching Empirica goals (task↔goal bridge)

The bridge searches for goals whose objective matches the task subject,
enabling Claude Code's task UI to drive Empirica goal completion.

Input: task_id, task_subject, task_description, teammate_name, team_name
Can block: Yes (exit code 2, stderr = feedback to Claude)
"""

import json
import logging
import sqlite3
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from project_resolver import _get_instance_suffix, get_instance_id

LOG_DIR = Path.home() / ".empirica" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("empirica.task-completed")
handler = logging.FileHandler(LOG_DIR / "task-completed.log")
handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


def _find_open_transaction(instance_id: str) -> dict | None:
    """Find open transaction for current instance."""
    # Check instance_projects for current project
    suffix = _get_instance_suffix()
    instance_file = Path.home() / ".empirica" / "instance_projects" / f"{instance_id}.json"
    if instance_file.exists():
        try:
            with open(instance_file) as f:
                data = json.load(f)
            project_path = data.get("project_path")
            if project_path:
                tx_file = Path(project_path) / ".empirica" / f"active_transaction{suffix}.json"
                if tx_file.exists():
                    with open(tx_file) as f:
                        tx_data = json.load(f)
                    if tx_data.get("status") == "open":
                        return tx_data
        except Exception:
            pass

    # Fallback: scan active_work files
    for aw_file in Path.home().glob(".empirica/active_work_*.json"):
        try:
            with open(aw_file) as f:
                data = json.load(f)
            project_path = data.get("project_path")
            if project_path:
                tx_file = Path(project_path) / ".empirica" / f"active_transaction{suffix}.json"
                if tx_file.exists():
                    with open(tx_file) as f:
                        tx_data = json.load(f)
                    if tx_data.get("status") == "open":
                        return tx_data
        except Exception:
            continue
    return None


def _get_db_path() -> Path | None:
    """Find the sessions database."""
    db_path = Path.home() / ".empirica" / "sessions" / "sessions.db"
    if db_path.exists():
        return db_path
    return None


def _find_matching_goal(db_path: Path, task_subject: str, task_description: str) -> dict | None:
    """
    Find an Empirica goal that matches the completed task.

    Search strategy (priority order):
    1. Exact claude_task_id match in goal_data metadata
    2. Fuzzy objective match against task subject (threshold: 0.6)

    Returns goal dict or None.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all active (non-completed) goals
        cursor.execute("""
            SELECT id, objective, goal_data, status
            FROM goals
            WHERE is_completed = 0
            ORDER BY created_timestamp DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return None

        # Strategy 1: Check metadata for claude_task_id
        for row in rows:
            try:
                goal_data = json.loads(row["goal_data"])
                metadata = goal_data.get("metadata", {})
                if metadata.get("claude_task_id") == task_subject:
                    return {
                        "goal_id": row["id"],
                        "objective": row["objective"],
                        "match_type": "task_id",
                        "confidence": 1.0,
                    }
            except (json.JSONDecodeError, KeyError):
                continue

        # Strategy 2: Fuzzy match objective against task subject
        best_match = None
        best_ratio = 0.0

        search_text = task_subject.lower().strip()
        for row in rows:
            objective = row["objective"].lower().strip()

            # Direct substring check first
            if search_text in objective or objective in search_text:
                ratio = 0.9
            else:
                ratio = SequenceMatcher(None, search_text, objective).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_match = {
                    "goal_id": row["id"],
                    "objective": row["objective"],
                    "match_type": "fuzzy",
                    "confidence": ratio,
                }

        # Only return if confidence is above threshold
        if best_match and best_ratio >= 0.6:
            return best_match

        return None

    except Exception as e:
        logger.warning(f"  Goal search failed: {e}")
        return None


def _auto_complete_goal(goal_id: str, task_subject: str, match_type: str, confidence: float):
    """Auto-complete an Empirica goal via CLI."""
    reason = f"Auto-completed via TaskCompleted bridge (match={match_type}, conf={confidence:.2f}): {task_subject}"
    try:
        result = subprocess.run(
            ["empirica", "goals-complete", "--goal-id", goal_id, "--reason", reason],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info(f"  Auto-completed goal {goal_id[:8]}... ({match_type} match, conf={confidence:.2f})")
        else:
            logger.warning(f"  goals-complete failed: {result.stderr}")
    except Exception as e:
        logger.warning(f"  Failed to auto-complete goal: {e}")


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    task_id = hook_input.get("task_id", "unknown")
    task_subject = hook_input.get("task_subject", "")
    task_description = hook_input.get("task_description", "")
    teammate_name = hook_input.get("teammate_name", "")

    logger.info(f"TaskCompleted: {task_id} | {task_subject} | teammate={teammate_name}")

    instance_id = get_instance_id()
    tx_data = _find_open_transaction(instance_id)

    if tx_data:
        tx_id = tx_data.get("transaction_id", "unknown")
        tool_calls = tx_data.get("tool_call_count", 0)

        # Only enforce if we have meaningful work (>3 tool calls)
        if tool_calls > 3:
            logger.info(f"  Open transaction {tx_id[:8]}... with {tool_calls} tool calls — requesting POSTFLIGHT")
            print(
                f"Task '{task_subject}' has an open transaction ({tx_id[:8]}..., "
                f"{tool_calls} tool calls). Submit POSTFLIGHT to close the measurement "
                f"cycle before completing this task.",
                file=sys.stderr,
            )
            sys.exit(2)  # Block completion
        else:
            logger.info(f"  Open transaction but only {tool_calls} tool calls — allowing completion")

    # --- Task↔Goal Bridge ---
    db_path = _get_db_path()
    if db_path and task_subject:
        match = _find_matching_goal(db_path, task_subject, task_description)
        if match:
            logger.info(
                f"  Matched goal: {match['goal_id'][:8]}... ({match['match_type']}, conf={match['confidence']:.2f})"
            )
            _auto_complete_goal(match["goal_id"], task_subject, match["match_type"], match["confidence"])
        else:
            logger.info(f"  No matching goal found for task: {task_subject}")

    # Deliberately NOT logging an artifact here.
    #
    # This used to shell the finding verb with a task-completion prefix, which typed
    # an activity record as an observation. A finding answers "what is true that I did
    # not know before"; "task X finished" answers "what did I do". Two months of that
    # produced 241 rows in this practice and 254 in cortex's, all of them open, all of
    # them competing with real findings for retrieval.
    #
    # Nothing is lost by dropping it. The Task↔Goal bridge above already records a
    # completion where it belongs — on the goal, with evidence — and a completion with
    # no matching goal is precisely the record that was never worth a finding.
    #
    # If activity records are wanted later they need their own type. Reaching for
    # `finding` because it is the verb nearest to hand is how the corpus got here.

    logger.info("  Allowing task completion")
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    main()
