"""
Git Assumption Store - Unverified Beliefs in Git Notes

Stores assumptions in git notes for sync.
Assumptions track what the AI believes but hasn't verified.

Storage: refs/notes/empirica/assumptions/<assumption-id>
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class GitAssumptionStore:
    """Git-based assumption storage for epistemic sync."""

    def __init__(self, workspace_root: str | None = None):
        self.workspace_root = workspace_root or os.getcwd()
        self._git_available = self._check_git_repo()

    def _check_git_repo(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"], cwd=self.workspace_root, capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _has_commits(self) -> bool:
        if not self._git_available:
            return False
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def store_assumption(
        self,
        assumption_id: str,
        project_id: str,
        session_id: str,
        ai_id: str,
        assumption: str,
        confidence: float = 0.5,
        domain: str | None = None,
        goal_id: str | None = None,
    ) -> bool:
        if not self._git_available or not self._has_commits():
            return False

        try:
            payload = {
                "assumption_id": assumption_id,
                "project_id": project_id,
                "session_id": session_id,
                "ai_id": ai_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "assumption": assumption,
                "confidence": confidence,
                "domain": domain,
                "status": "unverified",
                "goal_id": goal_id,
            }

            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, check=True
            )
            commit_hash = result.stdout.strip()

            note_ref = f"empirica/assumptions/{assumption_id}"
            subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "add", "-f", "-F", "-", commit_hash],
                input=json.dumps(payload, indent=2),
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )

            logger.info(f"✓ Stored assumption {assumption_id[:8]} in git notes")
            return True

        except Exception as e:
            logger.warning(f"Failed to store assumption in git: {e}")
            return False

    def load_assumption(self, assumption_id: str) -> dict[str, Any] | None:
        if not self._git_available or not self._has_commits():
            return None

        try:
            note_ref = f"empirica/assumptions/{assumption_id}"
            result = subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "list"], cwd=self.workspace_root, capture_output=True, text=True
            )

            if result.returncode != 0 or not result.stdout.strip():
                return None

            parts = result.stdout.strip().split()
            if len(parts) < 2:
                return None
            commit_hash = parts[1]

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
            logger.warning(f"Failed to load assumption from git: {e}")
            return None

    def discover_assumptions(
        self,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self._git_available:
            return []

        try:
            result = subprocess.run(
                ["git", "for-each-ref", "refs/notes/empirica/assumptions/"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return []

            assumptions = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                ref = parts[1]
                if not ref.startswith("refs/notes/empirica/assumptions/"):
                    continue

                assumption_id = ref.split("/")[-1]
                data = self.load_assumption(assumption_id)
                if not data:
                    continue

                if project_id and data.get("project_id") != project_id:
                    continue
                if session_id and data.get("session_id") != session_id:
                    continue

                assumptions.append(data)

            return assumptions

        except Exception as e:
            logger.warning(f"Failed to discover assumptions: {e}")
            return []
