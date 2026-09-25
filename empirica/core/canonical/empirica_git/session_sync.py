"""
Session Synchronization via Git

Handles session state synchronization for multi-AI coordination.
Enables automatic git pull before session resume and optional push after checkpoint.

Key Features:
- Auto-pull before session resume
- Optional auto-push after checkpoint
- Conflict detection and resolution
- Safe degradation
"""

import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class SessionSync:
    """
    Git synchronization for session state

    Design:
    - Pull latest checkpoints before resume
    - Push checkpoints after creation (optional)
    - Detect and handle conflicts
    - Safe degradation if git unavailable
    """

    def __init__(self, workspace_root: str | None = None):
        """Initialize session sync"""
        self.workspace_root = workspace_root or os.getcwd()
        self._git_available = self._check_git_repo()
        self._remote_configured = self._check_remote()

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

    def _check_remote(self) -> bool:
        """Check if git remote is configured"""
        if not self._git_available:
            return False

        try:
            result = subprocess.run(
                ["git", "remote", "-v"], cwd=self.workspace_root, capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0 and len(result.stdout.strip()) > 0
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

    def pull_latest(self, notes_only: bool = True) -> bool:
        """
        Pull latest checkpoints/goals from remote

        Args:
            notes_only: Only fetch notes (faster, safer)

        Returns:
            bool: Success
        """
        if not self._git_available or not self._remote_configured:
            logger.debug("Git pull skipped (no remote configured)")
            return False

        try:
            if notes_only:
                # Fetch only notes (empirica namespace)
                result = subprocess.run(
                    ["git", "fetch", "origin", "refs/notes/empirica/*:refs/notes/empirica/*"],
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            else:
                # Full pull
                result = subprocess.run(
                    ["git", "pull"], cwd=self.workspace_root, capture_output=True, text=True, timeout=30
                )

            if result.returncode == 0:
                logger.info("✓ Pulled latest epistemic state from remote")
                return True
            else:
                logger.warning(f"Git pull failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.warning("Git pull timed out")
            return False
        except Exception as e:
            logger.warning(f"Git pull failed: {e}")
            return False

    def push_checkpoint(self, notes_only: bool = True) -> bool:
        """
        Push checkpoint/goal to remote

        Args:
            notes_only: Only push notes (safer)

        Returns:
            bool: Success
        """
        if not self._git_available or not self._remote_configured:
            logger.debug("Git push skipped (no remote configured)")
            return False

        try:
            if notes_only:
                # Push only notes (empirica namespace)
                result = subprocess.run(
                    ["git", "push", "origin", "refs/notes/empirica/*"],
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            else:
                # Full push
                result = subprocess.run(
                    ["git", "push"], cwd=self.workspace_root, capture_output=True, text=True, timeout=30
                )

            if result.returncode == 0:
                logger.info("✓ Pushed epistemic state to remote")
                return True
            else:
                logger.warning(f"Git push failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.warning("Git push timed out")
            return False
        except Exception as e:
            logger.warning(f"Git push failed: {e}")
            return False

    def auto_sync_before_resume(self) -> dict[str, Any]:
        """
        Automatically sync before session resume

        Returns:
            Dict: Sync status
        """
        result = {"pulled": False, "new_checkpoints": False, "new_goals": False, "conflicts": False}

        if self.pull_latest(notes_only=True):
            result["pulled"] = True
            result["new_checkpoints"] = True
            result["new_goals"] = True

        return result

    def auto_sync_after_checkpoint(self, auto_push: bool = False) -> bool:
        """
        Automatically sync after checkpoint creation

        Args:
            auto_push: Enable automatic push

        Returns:
            bool: Success
        """
        if not auto_push:
            logger.debug("Auto-push disabled")
            return False

        return self.push_checkpoint(notes_only=True)
