#!/usr/bin/env python3
"""
Completion Tracker - Track goal and task completion with evidence

Provides automatic and manual completion tracking.
MVP implementation: Simple progress calculation, manual evidence recording.
Future: Add git commit parsing for automatic evidence mapping.
"""

import json
import logging
import re
import subprocess
import time

from empirica.core.goals.repository import GoalRepository
from empirica.core.tasks.repository import TaskRepository
from empirica.core.tasks.types import TaskStatus

from .types import CompletionMetrics, CompletionRecord

logger = logging.getLogger(__name__)


class CompletionTracker:
    """
    Track goal and task completion with evidence mapping

    MVP: Simple completion percentage calculation based on task status.
    Phase 2: Git notes integration for task metadata storage.
    """

    def __init__(self, db_path: str | None = None, enable_git_notes: bool = True):
        """
        Initialize tracker

        Args:
            db_path: Optional custom database path
            enable_git_notes: Enable git notes for task metadata (default: True)
        """
        self.goal_repo = GoalRepository(db_path=db_path)
        self.task_repo = TaskRepository(db_path=db_path)
        self.enable_git_notes = enable_git_notes
        self.git_available = self._check_git_available()

    def track_progress(self, goal_id: str) -> CompletionRecord:
        """
        Calculate completion status for a goal

        Args:
            goal_id: Goal identifier

        Returns:
            CompletionRecord with current status
        """
        try:
            # Get all subtasks for this goal
            subtasks = self.task_repo.get_goal_subtasks(goal_id)

            if not subtasks:
                logger.warning(f"No subtasks found for goal {goal_id}")
                return CompletionRecord(
                    goal_id=goal_id,
                    completion_percentage=0.0,
                    completed_subtasks=[],
                    remaining_subtasks=[],
                    blocked_subtasks=[],
                    estimated_remaining_tokens=0,
                    actual_tokens_used=0,
                    completion_evidence={},
                )

            # Categorize subtasks by status
            completed = []
            remaining = []
            blocked = []
            completion_evidence = {}

            total_estimated_tokens = 0
            total_actual_tokens = 0

            for subtask in subtasks:
                if subtask.status == TaskStatus.COMPLETED:
                    completed.append(subtask.id)
                    if subtask.completion_evidence:
                        completion_evidence[subtask.id] = subtask.completion_evidence
                    if subtask.actual_tokens:
                        total_actual_tokens += subtask.actual_tokens
                elif subtask.status == TaskStatus.BLOCKED:
                    blocked.append(subtask.id)
                elif subtask.status == TaskStatus.SKIPPED:
                    # Count skipped as "completed" for percentage calculation
                    completed.append(subtask.id)
                else:
                    remaining.append(subtask.id)

                if subtask.estimated_tokens:
                    total_estimated_tokens += subtask.estimated_tokens

            # Calculate completion percentage (completed + skipped / total)
            completion_percentage = len(completed) / len(subtasks) if subtasks else 0.0

            # Estimate remaining tokens (only for non-completed tasks)
            remaining_tokens = sum(
                st.estimated_tokens or 0
                for st in subtasks
                if st.status not in [TaskStatus.COMPLETED, TaskStatus.SKIPPED]
            )

            record = CompletionRecord(
                goal_id=goal_id,
                completion_percentage=completion_percentage,
                completed_subtasks=completed,
                remaining_subtasks=remaining,
                blocked_subtasks=blocked,
                estimated_remaining_tokens=remaining_tokens,
                actual_tokens_used=total_actual_tokens,
                completion_evidence=completion_evidence,
            )

            # Update goal completion status if 100%
            if completion_percentage >= 1.0:
                self.goal_repo.update_goal_completion(goal_id, True)

            logger.info(
                f"Goal {goal_id} progress: {completion_percentage:.1%} ({len(completed)}/{len(subtasks)} tasks)"
            )

            return record

        except Exception as e:
            logger.error(f"Error tracking progress for goal {goal_id}: {e}")
            raise

    def auto_update_completion(self, goal_id: str) -> CompletionRecord:
        """
        Automatically update completion based on current task status

        MVP: Just calls track_progress (future: git commit parsing)

        Args:
            goal_id: Goal identifier

        Returns:
            Updated CompletionRecord
        """
        # MVP: Same as track_progress
        # Future: Parse git log, map commits to subtasks, update evidence
        return self.track_progress(goal_id)

    def record_subtask_completion(self, subtask_id: str, evidence: str | None = None) -> bool:
        """
        Mark subtask as complete with optional evidence

        Phase 2: Also adds git note for task metadata

        Args:
            subtask_id: SubTask identifier
            evidence: Completion evidence (commit hash, file path, etc.)

        Returns:
            True if successful
        """
        try:
            success = self.task_repo.update_subtask_status(subtask_id, TaskStatus.COMPLETED, evidence)

            if success:
                logger.info(f"Marked subtask {subtask_id} as completed")

                # Phase 2: Add git note for task metadata
                if self.git_available:
                    # Extract commit hash from evidence if present
                    commit_hash = None
                    if evidence and evidence.startswith("commit:"):
                        commit_hash = evidence.split(":", 1)[1]

                    self._add_task_note(subtask_id, commit_hash)

            return success

        except Exception as e:
            logger.error(f"Error recording subtask completion: {e}")
            return False

    def get_session_metrics(self, session_id: str) -> CompletionMetrics:
        """
        Calculate aggregate completion metrics for a session

        Args:
            session_id: Session identifier

        Returns:
            CompletionMetrics with aggregated statistics
        """
        try:
            # Get all goals for session
            goals = self.goal_repo.get_session_goals(session_id)

            if not goals:
                return CompletionMetrics(
                    goals_completed=0,
                    goals_in_progress=0,
                    goals_blocked=0,
                    total_tokens_used=0,
                    average_completion_rate=0.0,
                    efficiency_score=0.0,
                )

            # Calculate metrics
            completed_count = 0
            in_progress_count = 0
            blocked_count = 0
            total_tokens = 0
            total_completion = 0.0
            total_estimated = 0

            for goal in goals:
                record = self.track_progress(goal.id)

                if record.completion_percentage >= 1.0:
                    completed_count += 1
                elif record.completion_percentage == 0.0:
                    # Check if blocked
                    if record.blocked_subtasks:
                        blocked_count += 1
                    else:
                        in_progress_count += 1
                else:
                    in_progress_count += 1

                total_tokens += record.actual_tokens_used
                total_completion += record.completion_percentage
                total_estimated += record.estimated_remaining_tokens + record.actual_tokens_used

            # Calculate averages
            avg_completion = total_completion / len(goals) if goals else 0.0
            efficiency = total_tokens / total_estimated if total_estimated > 0 else 1.0

            return CompletionMetrics(
                goals_completed=completed_count,
                goals_in_progress=in_progress_count,
                goals_blocked=blocked_count,
                total_tokens_used=total_tokens,
                average_completion_rate=avg_completion,
                efficiency_score=efficiency,
            )

        except Exception as e:
            logger.error(f"Error calculating session metrics: {e}")
            raise

    def auto_update_from_recent_commits(self, goal_id: str, since: str = "1 hour ago") -> int:
        """
        Scan recent git commits and auto-mark subtasks complete

        Looks for commit message patterns like:
        - ✅ [TASK:subtask-uuid]
        - [COMPLETE:subtask-uuid]
        - Addresses subtask subtask-uuid

        Args:
            goal_id: Goal to update
            since: Time period to scan (git log --since format)

        Returns:
            Number of subtasks auto-completed
        """
        try:
            # Get recent commits
            result = subprocess.run(
                ["git", "log", f"--since={since}", "--format=%H"], capture_output=True, text=True, check=True, cwd="."
            )

            if not result.stdout.strip():
                logger.debug("No recent commits found")
                return 0

            commit_hashes = result.stdout.strip().split("\n")
            auto_completed = 0

            for commit_hash in commit_hashes:
                if not commit_hash:
                    continue

                # Get commit message
                msg_result = subprocess.run(
                    ["git", "log", "-1", "--format=%B", commit_hash],
                    capture_output=True,
                    text=True,
                    check=True,
                    cwd=".",
                )

                # Look for task completion markers
                patterns = [
                    r"✅\s*\[TASK:([a-f0-9-]+)\]",
                    r"\[COMPLETE:([a-f0-9-]+)\]",
                    r"Addresses subtask ([a-f0-9-]+)",
                ]

                for pattern in patterns:
                    match = re.search(pattern, msg_result.stdout, re.IGNORECASE)
                    if match:
                        subtask_id = match.group(1)

                        # Verify this subtask belongs to the goal
                        subtask = self.task_repo.get_subtask(subtask_id)
                        if subtask and subtask.goal_id == goal_id:
                            # Check if already completed
                            if subtask.status != TaskStatus.COMPLETED:
                                # Auto-mark complete
                                if self.record_subtask_completion(subtask_id, evidence=f"commit:{commit_hash[:7]}"):
                                    auto_completed += 1
                                    logger.info(
                                        f"Auto-completed subtask {subtask_id[:8]} from commit {commit_hash[:7]}"
                                    )

            return auto_completed

        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to scan git commits: {e}")
            return 0
        except FileNotFoundError:
            logger.debug("Git not available or not a git repository")
            return 0

    def _check_git_available(self) -> bool:
        """
        Check if git is available and we're in a git repository

        Returns:
            True if git available and in repo
        """
        if not self.enable_git_notes:
            return False

        try:
            result = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, timeout=2, cwd=".")
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _add_task_note(self, subtask_id: str, commit_hash: str | None = None) -> str | None:
        """
        Add task completion note to git

        Stores structured metadata in git notes for lead AI queries.
        Uses goal-specific namespace: refs/notes/empirica/tasks/<goal_id>

        Args:
            subtask_id: SubTask UUID
            commit_hash: Commit hash to attach note to (default: HEAD)

        Returns:
            Note SHA if successful, None if failed
        """
        if not self.git_available:
            return None

        try:
            # Get subtask details
            subtask = self.task_repo.get_subtask(subtask_id)
            if not subtask:
                logger.warning(f"Cannot add git note: subtask {subtask_id} not found")
                return None

            # Build note data
            note_data = {
                "subtask_id": subtask.id,
                "goal_id": subtask.goal_id,
                "description": subtask.description,
                "epistemic_importance": subtask.epistemic_importance.value,
                "completed_timestamp": subtask.completed_timestamp or time.time(),
                "completion_evidence": subtask.completion_evidence,
                "actual_tokens": subtask.actual_tokens,
                "estimated_tokens": subtask.estimated_tokens,
            }

            # Serialize to JSON
            note_json = json.dumps(note_data, indent=2)

            # Use goal-specific namespace for efficient queries
            note_ref = f"empirica/tasks/{subtask.goal_id}"

            # Attach to specified commit or HEAD
            target = commit_hash or "HEAD"

            # Add note to git
            result = subprocess.run(
                ["git", "notes", "--ref", note_ref, "add", "-f", "-F", "-", target],
                input=note_json,
                capture_output=True,
                timeout=5,
                cwd=".",
                text=True,
            )

            if result.returncode != 0:
                logger.warning(f"Failed to add task git note (ref={note_ref}): {result.stderr}")
                return None

            # Get note SHA
            result = subprocess.run(
                ["git", "notes", "--ref", note_ref, "list", target], capture_output=True, timeout=2, cwd=".", text=True
            )

            note_sha = result.stdout.strip().split()[0] if result.stdout else None
            logger.info(f"Task git note added: {note_sha} (task={subtask_id[:8]}, goal={subtask.goal_id[:8]})")

            return note_sha

        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            logger.warning(f"Git note operation failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Error adding task note: {e}")
            return None

    def close(self):
        """Close repository connections"""
        self.goal_repo.close()
        self.task_repo.close()
