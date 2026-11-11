"""
Git Source Store - Epistemic Sources in Git Notes

Stores epistemic sources (references, documents, URLs) in git notes for sync.
Sources track what evidence was consulted or produced during work.

Storage: refs/notes/empirica/sources/<source-id>
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class GitSourceStore:
    """Git-based source storage for epistemic sync."""

    def __init__(self, workspace_root: str | None = None):
        self.workspace_root = workspace_root or os.getcwd()
        self._git_available = self._check_git_repo()

    def _check_git_repo(self) -> bool:
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

    def store_source(
        self,
        source_id: str,
        project_id: str,
        session_id: str,
        title: str,
        source_type: str = "document",
        source_url: str | None = None,
        doc_path: str | None = None,
        description: str | None = None,
        confidence: float = 0.7,
        direction: str = "noetic",
        ai_id: str = "",  # canonical: caller passes ai_id from InstanceResolver.ai_id()
        created_at: str | None = None,
        extra: dict | None = None,
    ) -> bool:
        """Write the source's note. ``extra`` carries the lifecycle (archived, reason, target, time) when the note is
        rewritten after one, and ``created_at`` keeps the original date (goal 25f4ab9a)."""
        if not self._git_available or not self._has_commits():
            return False

        try:
            payload = {
                **(extra or {}),
                "source_id": source_id,
                "project_id": project_id,
                "session_id": session_id,
                "ai_id": ai_id,
                "created_at": created_at or datetime.now(timezone.utc).isoformat(),
                "title": title,
                "source_type": source_type,
                "source_url": source_url,
                "doc_path": doc_path,
                "description": description,
                "confidence": confidence,
                "direction": direction,
            }

            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace_root, capture_output=True, text=True, check=True
            )
            commit_hash = result.stdout.strip()

            note_ref = f"empirica/sources/{source_id}"
            subprocess.run(
                ["git", "notes", f"--ref={note_ref}", "add", "-f", "-F", "-", commit_hash],
                input=json.dumps(payload, indent=2),
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True,
            )

            logger.info(f"Source {source_id[:8]} stored in git notes")
            return True

        except Exception as e:
            logger.warning(f"Failed to store source in git: {e}")
            return False

    def load_source(self, source_id: str) -> dict | None:
        """The source's note, or None (no git, no note, unreadable)."""
        if not self._git_available:
            return None
        ref = f"empirica/sources/{source_id}"
        try:
            listed = subprocess.run(
                ["git", "notes", f"--ref={ref}", "list"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            parts = listed.stdout.split()
            if listed.returncode != 0 or len(parts) < 2:
                return None
            shown = subprocess.run(
                ["git", "notes", f"--ref={ref}", "show", parts[1]],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return json.loads(shown.stdout) if shown.returncode == 0 else None
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as exc:
            logger.warning("source note %s not readable: %s", source_id[:8], exc)
            return None

    def discover_sources(self) -> list[dict]:
        """Every source note in this repository (the rebuild reads these, goal 25f4ab9a)."""
        if not self._git_available:
            return []
        try:
            refs = subprocess.run(
                ["git", "for-each-ref", "--format=%(refname)", "refs/notes/empirica/sources/"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("source notes not listed: %s", exc)
            return []
        out = []
        for ref in refs.stdout.split():
            note = self.load_source(ref.rsplit("/", 1)[-1])
            if note:
                out.append(note)
        return out
