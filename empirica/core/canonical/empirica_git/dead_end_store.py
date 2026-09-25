"""
Git Dead End Store - Failed Approaches in Git Notes

Stores dead ends (approaches that didn't work) in git notes for sync.
Dead ends prevent future AIs from repeating failed investigations.

Key Features:
- Store dead ends in git notes (refs/notes/empirica/dead_ends/<dead-end-id>)
- Track approach and why it failed
- Enable cross-device sync to prevent re-exploration
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class GitDeadEndStore:
    """
    Git-based dead end storage for epistemic sync

    Storage Format (git notes):
        refs/notes/empirica/dead_ends/<dead-end-id>

    Dead End Data:
        {
            "dead_end_id": "uuid",
            "project_id": "project-uuid",
            "session_id": "session-uuid",
            "ai_id": "claude-code",
            "created_at": "2026-01-22T...",
            "approach": "Tried using X to solve Y",
            "why_failed": "X doesn't support feature Z needed for Y",
            "goal_id": "goal-uuid" (optional),
            "subtask_id": "subtask-uuid" (optional)
        }
    """

    def __init__(self, workspace_root: str | None = None):
        """Initialize git dead end store"""
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

    def store_dead_end(
        self,
        dead_end_id: str,
        project_id: str,
        session_id: str,
        ai_id: str,
        approach: str,
        why_failed: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
    ) -> bool:
        """
        Store dead end in git notes

        Args:
            dead_end_id: Dead end UUID
            project_id: Project identifier
            session_id: Session identifier
            ai_id: AI that logged the dead end
            approach: What approach was tried
            why_failed: Why it didn't work
            goal_id: Optional goal reference
            subtask_id: Optional subtask reference

        Returns:
            bool: Success
        """
        if not self._git_available:
            logger.debug("Not in git repo, skipping dead end storage")
            return False

        if not self._has_commits():
            logger.debug("Git repo has no commits yet, skipping dead end storage")
            return False

        try:
            payload = {
                "dead_end_id": dead_end_id,
                "project_id": project_id,
                "session_id": session_id,
                "ai_id": ai_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "approach": approach,
                "why_failed": why_failed,
                "goal_id": goal_id,
                "subtask_id": subtask_id,
            }

            payload_json = json.dumps(payload, indent=2)

            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, check=True
            )
            commit_hash = result.stdout.strip()

            note_ref = f"empirica/dead_ends/{dead_end_id}"
            subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "add", "-f", "-F", "-", commit_hash],
                input=payload_json,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )

            logger.info(f"✓ Stored dead end {dead_end_id[:8]} in git notes")
            return True

        except Exception as e:
            logger.warning(f"Failed to store dead end in git: {e}")
            return False

    def load_dead_end(self, dead_end_id: str) -> dict[str, Any] | None:
        """Load dead end from git notes"""
        if not self._git_available or not self._has_commits():
            return None

        try:
            note_ref = f"empirica/dead_ends/{dead_end_id}"

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
            logger.warning(f"Failed to load dead end from git: {e}")
            return None

    def discover_dead_ends(
        self, project_id: str | None = None, session_id: str | None = None, ai_id: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Discover dead ends from git notes

        Args:
            project_id: Filter by project
            session_id: Filter by session
            ai_id: Filter by AI

        Returns:
            List[Dict]: Matching dead ends
        """
        if not self._git_available:
            return []

        try:
            result = subprocess.run(
                ["git", "for-each-ref", "refs/notes/empirica/dead_ends/"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return []

            dead_ends = []

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                parts = line.split("\t")
                if len(parts) < 2:
                    continue

                ref = parts[1]
                if not ref.startswith("refs/notes/empirica/dead_ends/"):
                    continue

                dead_end_id = ref.split("/")[-1]
                dead_end_data = self.load_dead_end(dead_end_id)

                if not dead_end_data:
                    continue

                # Apply filters
                if project_id and dead_end_data.get("project_id") != project_id:
                    continue
                if session_id and dead_end_data.get("session_id") != session_id:
                    continue
                if ai_id and dead_end_data.get("ai_id") != ai_id:
                    continue

                dead_ends.append(dead_end_data)

            return dead_ends

        except Exception as e:
            logger.warning(f"Failed to discover dead ends: {e}")
            return []

    def search_similar(self, approach_keywords: list[str]) -> list[dict[str, Any]]:
        """
        Search for dead ends with similar approaches

        Args:
            approach_keywords: Keywords to match in approach text

        Returns:
            List[Dict]: Dead ends with matching keywords
        """
        all_dead_ends = self.discover_dead_ends()
        matching = []

        for dead_end in all_dead_ends:
            approach_lower = dead_end.get("approach", "").lower()
            if any(kw.lower() in approach_lower for kw in approach_keywords):
                matching.append(dead_end)

        return matching
