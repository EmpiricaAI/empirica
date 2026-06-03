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
                ['git', 'rev-parse', '--git-dir'],
                cwd=self.workspace_root,
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _has_commits(self) -> bool:
        if not self._git_available:
            return False
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=self.workspace_root,
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def store_source(
        self,
        source_id: str,
        project_id: str,
        session_id: str,
        title: str,
        source_type: str = 'document',
        source_url: str | None = None,
        doc_path: str | None = None,
        description: str | None = None,
        confidence: float = 0.7,
        direction: str = 'noetic',
        ai_id: str = '',  # canonical: caller passes ai_id from InstanceResolver.ai_id()
    ) -> bool:
        if not self._git_available or not self._has_commits():
            return False

        try:
            payload = {
                'source_id': source_id,
                'project_id': project_id,
                'session_id': session_id,
                'ai_id': ai_id,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'title': title,
                'source_type': source_type,
                'source_url': source_url,
                'doc_path': doc_path,
                'description': description,
                'confidence': confidence,
                'direction': direction,
            }

            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=self.workspace_root,
                capture_output=True, text=True, check=True
            )
            commit_hash = result.stdout.strip()

            note_ref = f'empirica/sources/{source_id}'
            subprocess.run(
                ['git', 'notes', f'--ref={note_ref}', 'add', '-f', '-m',
                 json.dumps(payload, indent=2), commit_hash],
                cwd=self.workspace_root,
                capture_output=True, text=True, check=True
            )

            logger.info(f"Source {source_id[:8]} stored in git notes")
            return True

        except Exception as e:
            logger.warning(f"Failed to store source in git: {e}")
            return False
