"""
Git Mistake Store - Mistakes Made in Git Notes

Stores mistakes (errors to avoid in future) in git notes for sync.
Mistakes are learning opportunities that should prevent repeat failures.

Key Features:
- Store mistakes in git notes (refs/notes/empirica/mistakes/<mistake-id>)
- Track what went wrong, why, and how to prevent
- Enable cross-device sync for calibration
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class GitMistakeStore:
    """
    Git-based mistake storage for epistemic sync

    Storage Format (git notes):
        refs/notes/empirica/mistakes/<mistake-id>

    Mistake Data:
        {
            "mistake_id": "uuid",
            "project_id": "project-uuid",
            "session_id": "session-uuid",
            "ai_id": "claude-code",
            "created_at": "2026-01-22T...",
            "mistake": "Made assumption X without verifying",
            "why_wrong": "X was outdated, API changed in v2",
            "prevention": "Always check API version before assuming behavior",
            "cost_estimate": "2 hours",
            "root_cause_vector": "KNOW",
            "goal_id": "goal-uuid" (optional),
            "subtask_id": "subtask-uuid" (optional)
        }
    """

    def __init__(self, workspace_root: str | None = None):
        """Initialize git mistake store"""
        self.workspace_root = workspace_root or os.getcwd()
        self._git_available = self._check_git_repo()

    def _check_git_repo(self) -> bool:
        """Check if we're in a git repository"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"], cwd=self.workspace_root, capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            # Not "not a repo": git could not answer. Reading this as absence
            # skipped the note write with a DEBUG line nobody sees, leaving a
            # SQLite row with no note for `rebuild` to import.
            logger.warning(
                "git could not answer in %s (%s); the git-notes write is skipped for this call",
                self.workspace_root,
                type(exc).__name__,
            )
            return False

    def _has_commits(self) -> bool:
        """Check if repo has at least one commit"""
        if not self._git_available:
            return False
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            # Not "not a repo": git could not answer. Reading this as absence
            # skipped the note write with a DEBUG line nobody sees, leaving a
            # SQLite row with no note for `rebuild` to import.
            logger.warning(
                "git could not answer in %s (%s); the git-notes write is skipped for this call",
                self.workspace_root,
                type(exc).__name__,
            )
            return False

    def store_mistake(
        self,
        mistake_id: str,
        project_id: str,
        session_id: str,
        ai_id: str,
        mistake: str,
        why_wrong: str,
        prevention: str | None = None,
        cost_estimate: str | None = None,
        root_cause_vector: str | None = None,
        goal_id: str | None = None,
        subtask_id: str | None = None,
    ) -> bool:
        """
        Store mistake in git notes

        Args:
            mistake_id: Mistake UUID
            project_id: Project identifier
            session_id: Session identifier
            ai_id: AI that logged the mistake
            mistake: What was done wrong
            why_wrong: Why it was wrong
            prevention: How to prevent in future
            cost_estimate: Time/resources wasted
            root_cause_vector: Epistemic vector that caused it (KNOW, DO, etc.)
            goal_id: Optional goal reference
            subtask_id: Optional subtask reference

        Returns:
            bool: Success
        """
        if not self._git_available:
            logger.debug("Not in git repo, skipping mistake storage")
            return False

        if not self._has_commits():
            logger.debug("Git repo has no commits yet, skipping mistake storage")
            return False

        try:
            payload = {
                "mistake_id": mistake_id,
                "project_id": project_id,
                "session_id": session_id,
                "ai_id": ai_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "mistake": mistake,
                "why_wrong": why_wrong,
                "prevention": prevention,
                "cost_estimate": cost_estimate,
                "root_cause_vector": root_cause_vector,
                "goal_id": goal_id,
                "subtask_id": subtask_id,
            }

            payload_json = json.dumps(payload, indent=2)

            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, check=True
            )
            commit_hash = result.stdout.strip()

            note_ref = f"empirica/mistakes/{mistake_id}"
            subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "add", "-f", "-F", "-", commit_hash],
                input=payload_json,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )

            logger.info(f"✓ Stored mistake {mistake_id[:8]} in git notes")
            return True

        except Exception as e:
            logger.warning(f"Failed to store mistake in git: {e}")
            return False

    def load_mistake(self, mistake_id: str) -> dict[str, Any] | None:
        """Load mistake from git notes"""
        if not self._git_available or not self._has_commits():
            return None

        try:
            note_ref = f"empirica/mistakes/{mistake_id}"

            # List which commit has the note (notes can be on any commit, not just HEAD)
            result = subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "list"], cwd=self.workspace_root, capture_output=True, text=True
            )

            if result.returncode != 0 or not result.stdout.strip():
                return None

            # Format is: <blob> <commit>
            parts = result.stdout.strip().split()
            if len(parts) < 2:
                return None
            commit_hash = parts[1]

            # Load note from the commit it's actually attached to
            result = subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "show", commit_hash],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return None

            return json.loads(result.stdout)

        except Exception as e:
            logger.warning(f"Failed to load mistake from git: {e}")
            return None

    def discover_mistakes(
        self,
        project_id: str | None = None,
        session_id: str | None = None,
        ai_id: str | None = None,
        root_cause_vector: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Discover mistakes from git notes

        Args:
            project_id: Filter by project
            session_id: Filter by session
            ai_id: Filter by AI
            root_cause_vector: Filter by epistemic root cause

        Returns:
            List[Dict]: Matching mistakes
        """
        if not self._git_available:
            return []

        try:
            result = subprocess.run(
                ["git", "for-each-ref", "refs/notes/empirica/mistakes/"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return []

            mistakes = []

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                parts = line.split("\t")
                if len(parts) < 2:
                    continue

                ref = parts[1]
                if not ref.startswith("refs/notes/empirica/mistakes/"):
                    continue

                mistake_id = ref.split("/")[-1]
                mistake_data = self.load_mistake(mistake_id)

                if not mistake_data:
                    continue

                # Apply filters
                if project_id and mistake_data.get("project_id") != project_id:
                    continue
                if session_id and mistake_data.get("session_id") != session_id:
                    continue
                if ai_id and mistake_data.get("ai_id") != ai_id:
                    continue
                if root_cause_vector and mistake_data.get("root_cause_vector") != root_cause_vector:
                    continue

                mistakes.append(mistake_data)

            return mistakes

        except Exception as e:
            logger.warning(f"Failed to discover mistakes: {e}")
            return []

    def get_by_root_cause(self) -> dict[str, list[dict[str, Any]]]:
        """
        Group mistakes by root cause vector

        Returns:
            Dict mapping vector name to list of mistakes
        """
        all_mistakes = self.discover_mistakes()
        by_vector: dict[str, list[dict[str, Any]]] = {}

        for mistake in all_mistakes:
            vector = mistake.get("root_cause_vector", "UNKNOWN")
            if vector not in by_vector:
                by_vector[vector] = []
            by_vector[vector].append(mistake)

        return by_vector
