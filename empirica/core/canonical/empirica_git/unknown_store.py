"""
Git Unknown Store - Epistemic Unknowns in Git Notes

Stores unknowns (open questions, uncertainties) in git notes for sync.
Unknowns represent gaps in knowledge that need investigation.

Key Features:
- Store unknowns in git notes (refs/notes/empirica/unknowns/<unknown-id>)
- Track resolution status
- Enable cross-device sync via standard git push/pull
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class GitUnknownStore:
    """
    Git-based unknown storage for epistemic sync

    Storage Format (git notes):
        refs/notes/empirica/unknowns/<unknown-id>

    Unknown Data:
        {
            "unknown_id": "uuid",
            "project_id": "project-uuid",
            "session_id": "session-uuid",
            "ai_id": "claude-code",
            "created_at": "2026-01-22T...",
            "unknown": "How does X interact with Y?",
            "goal_id": "goal-uuid" (optional),
            "subtask_id": "subtask-uuid" (optional),
            "resolved": false,
            "resolved_by": null,
            "resolved_at": null
        }
    """

    def __init__(self, workspace_root: str | None = None):
        """Initialize git unknown store"""
        self.workspace_root = workspace_root or os.getcwd()
        self._git_available = self._check_git_repo()

    def _check_git_repo(self) -> bool:
        """Check if we're in a git repository"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"], cwd=self.workspace_root, capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
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
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def store_unknown(
        self,
        unknown_id: str,
        project_id: str,
        session_id: str,
        ai_id: str,
        unknown: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        resolved: bool = False,
        resolved_by: str | None = None,
        created_at: str | None = None,
        resolved_at: str | None = None,
    ) -> bool:
        """
        Store unknown in git notes

        Args:
            unknown_id: Unknown UUID
            project_id: Project identifier
            session_id: Session identifier
            ai_id: AI that logged the unknown
            unknown: The question/uncertainty text
            goal_id: Optional goal reference
            subtask_id: Optional subtask reference
            resolved: Whether this unknown has been resolved
            resolved_by: How it was resolved

        Returns:
            bool: Success
        """
        if not self._git_available:
            logger.debug("Not in git repo, skipping unknown storage")
            return False

        if not self._has_commits():
            logger.debug("Git repo has no commits yet, skipping unknown storage")
            return False

        try:
            payload = {
                "unknown_id": unknown_id,
                "project_id": project_id,
                "session_id": session_id,
                "ai_id": ai_id,
                # From the caller when it knows: a backfill writing an old row
                # must not date it today. Notes are canonical, so a fabricated
                # timestamp here survives a rebuild and overwrites the true one.
                "created_at": created_at or datetime.now(timezone.utc).isoformat(),
                "unknown": unknown,
                "goal_id": goal_id,
                "subtask_id": subtask_id,
                "resolved": resolved,
                "resolved_by": resolved_by,
                "resolved_at": (resolved_at or datetime.now(timezone.utc).isoformat()) if resolved else None,
            }

            payload_json = json.dumps(payload, indent=2)

            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, check=True
            )
            commit_hash = result.stdout.strip()

            note_ref = f"empirica/unknowns/{unknown_id}"
            subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "add", "-f", "-F", "-", commit_hash],
                input=payload_json,
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )

            logger.info(f"✓ Stored unknown {unknown_id[:8]} in git notes")
            return True

        except Exception as e:
            logger.warning(f"Failed to store unknown in git: {e}")
            return False

    def load_unknown(self, unknown_id: str) -> dict[str, Any] | None:
        """Load unknown from git notes"""
        if not self._git_available or not self._has_commits():
            return None

        try:
            note_ref = f"empirica/unknowns/{unknown_id}"

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
            logger.warning(f"Failed to load unknown from git: {e}")
            return None

    def resolve_unknown(self, unknown_id: str, resolved_by: str) -> bool:
        """
        Mark unknown as resolved

        Args:
            unknown_id: Unknown UUID
            resolved_by: How it was resolved

        Returns:
            bool: Success
        """
        unknown_data = self.load_unknown(unknown_id)
        if not unknown_data:
            return False

        # Update resolution status
        unknown_data["resolved"] = True
        unknown_data["resolved_by"] = resolved_by
        unknown_data["resolved_at"] = datetime.now(timezone.utc).isoformat()

        # Re-store with updated status
        return self.store_unknown(
            unknown_id=unknown_id,
            project_id=unknown_data["project_id"],
            session_id=unknown_data["session_id"],
            ai_id=unknown_data["ai_id"],
            unknown=unknown_data["unknown"],
            goal_id=unknown_data.get("goal_id"),
            subtask_id=unknown_data.get("subtask_id"),
            resolved=True,
            resolved_by=resolved_by,
        )

    def discover_unknowns(
        self,
        project_id: str | None = None,
        session_id: str | None = None,
        ai_id: str | None = None,
        include_resolved: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Discover unknowns from git notes

        Args:
            project_id: Filter by project
            session_id: Filter by session
            ai_id: Filter by AI
            include_resolved: Include resolved unknowns

        Returns:
            List[Dict]: Matching unknowns
        """
        if not self._git_available:
            return []

        try:
            result = subprocess.run(
                ["git", "for-each-ref", "refs/notes/empirica/unknowns/"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return []

            unknowns = []

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                parts = line.split("\t")
                if len(parts) < 2:
                    continue

                ref = parts[1]
                if not ref.startswith("refs/notes/empirica/unknowns/"):
                    continue

                unknown_id = ref.split("/")[-1]
                unknown_data = self.load_unknown(unknown_id)

                if not unknown_data:
                    continue

                # Apply filters
                if project_id and unknown_data.get("project_id") != project_id:
                    continue
                if session_id and unknown_data.get("session_id") != session_id:
                    continue
                if ai_id and unknown_data.get("ai_id") != ai_id:
                    continue
                if not include_resolved and unknown_data.get("resolved"):
                    continue

                unknowns.append(unknown_data)

            return unknowns

        except Exception as e:
            logger.warning(f"Failed to discover unknowns: {e}")
            return []
