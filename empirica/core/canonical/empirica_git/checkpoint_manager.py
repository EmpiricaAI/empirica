"""
Automatic Git Checkpoint Management

Handles automatic checkpoint creation during CASCADE phases.
Detects git repo and safely degrades if not present.

Key Features:
- Auto-checkpoint after PREFLIGHT/CHECK/POSTFLIGHT
- Phase tagging (phase, round, ai_id)
- Safe degradation (no-op if not in git repo)
- Configurable via --no-git flag
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc

logger = logging.getLogger(__name__)


class CheckpointManager:
    """
    Manages automatic git checkpoints for CASCADE phases

    Design:
    - Detects git repo automatically
    - Creates checkpoints in git notes (refs/notes/empirica/checkpoints)
    - Compresses epistemic state (~85% token reduction)
    - Tags with phase, round, ai_id for Sentinel routing
    """

    def __init__(self, workspace_root: str | None = None):
        """
        Initialize checkpoint manager

        Args:
            workspace_root: Root directory (defaults to cwd)
        """
        self.workspace_root = workspace_root or os.getcwd()
        self._git_available = self._check_git_repo()

    def _check_git_repo(self) -> bool:
        """Check if we're in a git repository"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--git-dir'],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _has_commits(self) -> bool:
        """Check if repo has at least one commit (HEAD exists)"""
        if not self._git_available:
            return False
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def is_enabled(self, no_git_flag: bool = False) -> bool:
        """
        Check if checkpoints are enabled

        Logic:
        - If --no-git flag set: disabled
        - If in git repo with commits: enabled
        - Otherwise: disabled

        Args:
            no_git_flag: User explicitly disabled git

        Returns:
            bool: Whether checkpoints should be created
        """
        if no_git_flag:
            logger.debug("Git checkpoints disabled via --no-git flag")
            return False

        if not self._git_available:
            logger.debug("Not in git repository, checkpoints disabled")
            return False

        if not self._has_commits():
            logger.debug("Git repo has no commits yet, checkpoints disabled (create initial commit first)")
            return False

        return True

    def auto_checkpoint(
        self,
        session_id: str,
        ai_id: str,
        phase: str,
        vectors: dict[str, float],
        round_num: int = 1,
        metadata: dict[str, Any] | None = None,
        no_git_flag: bool = False
    ) -> str | None:
        """
        Automatically create checkpoint if conditions met

        Args:
            session_id: Session identifier
            ai_id: AI identifier
            phase: CASCADE phase (PREFLIGHT, CHECK, POSTFLIGHT, etc.)
            vectors: Epistemic vectors (13-D state)
            round_num: Round number within phase
            metadata: Additional metadata
            no_git_flag: User disabled git

        Returns:
            str: Checkpoint hash if created, None if skipped
        """
        if not self.is_enabled(no_git_flag):
            return None

        try:
            checkpoint_hash = self._create_checkpoint(
                session_id=session_id,
                ai_id=ai_id,
                phase=phase,
                vectors=vectors,
                round_num=round_num,
                metadata=metadata or {}
            )

            logger.info(f"✓ Created git checkpoint: {checkpoint_hash[:8]} (phase={phase}, ai={ai_id})")
            return checkpoint_hash

        except Exception as e:
            logger.warning(f"Failed to create git checkpoint: {e}")
            # Safe degradation - don't fail the CASCADE
            return None

    def _create_checkpoint(
        self,
        session_id: str,
        ai_id: str,
        phase: str,
        vectors: dict[str, float],
        round_num: int,
        metadata: dict[str, Any]
    ) -> str:
        """
        Create compressed checkpoint in git notes

        Format (compressed for ~85% token reduction):
        {
            "session_id": "abc123",
            "ai_id": "empirica",  # caller passes from InstanceResolver.ai_id()
            "phase": "PREFLIGHT",
            "round": 1,
            "timestamp": "2025-11-27T...",
            "vectors": {
                "engagement": 0.85,
                "know": 0.70,
                ...
            },
            "metadata": {
                "confidence": 0.82,
                "recommended_action": "PROCEED"
            }
        }
        """
        checkpoint_data = {
            'session_id': session_id,
            'ai_id': ai_id,
            'phase': phase,
            'round': round_num,
            'timestamp': datetime.now(UTC).isoformat(),
            'vectors': vectors,
            'metadata': metadata
        }

        # Serialize to compact JSON
        checkpoint_json = json.dumps(checkpoint_data, separators=(',', ':'))

        # Get current commit hash
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=True
        )
        commit_hash = result.stdout.strip()

        # Add git note (refs/notes/empirica/checkpoints)
        subprocess.run(
            ['git', 'notes', '--ref=empirica/checkpoints', 'add', '-f', '-m', checkpoint_json, commit_hash],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=True
        )

        return commit_hash

    def load_checkpoint(
        self,
        session_id: str | None = None,
        ai_id: str | None = None,
        commit_hash: str | None = None
    ) -> dict[str, Any] | None:
        """
        Load checkpoint from git notes

        Args:
            session_id: Filter by session
            ai_id: Filter by AI
            commit_hash: Load specific commit

        Returns:
            Dict: Checkpoint data or None if not found
        """
        if not self._git_available:
            return None

        try:
            # If commit_hash specified, load that checkpoint
            if commit_hash:
                return self._load_checkpoint_by_hash(commit_hash)

            # Otherwise, find latest matching checkpoint
            return self._find_latest_checkpoint(session_id=session_id, ai_id=ai_id)

        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}")
            return None

    def _load_checkpoint_by_hash(self, commit_hash: str) -> dict[str, Any] | None:
        """Load checkpoint from specific commit"""
        result = subprocess.run(
            ['git', 'notes', '--ref=empirica/checkpoints', 'show', commit_hash],
            cwd=self.workspace_root,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return None

        return json.loads(result.stdout)

    def load_recent_checkpoints(
        self,
        session_id: str | None = None,
        ai_id: str | None = None,
        count: int = 5
    ) -> list[dict[str, Any]]:
        """
        Load recent checkpoints matching filters

        Args:
            session_id: Filter by session
            ai_id: Filter by AI
            count: Number of recent checkpoints to return

        Returns:
            List of checkpoint dicts (most recent first)
        """
        if not self._git_available:
            return []

        try:
            # Get all checkpoints
            result = subprocess.run(
                ['git', 'log', '--all', '--pretty=format:%H'],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return []

            commit_hashes = result.stdout.strip().split('\n')
            checkpoints = []

            # Search for matching checkpoints
            for commit_hash in commit_hashes:
                if len(checkpoints) >= count:
                    break

                checkpoint = self._load_checkpoint_by_hash(commit_hash)
                if not checkpoint:
                    continue

                # Apply filters
                if session_id and checkpoint.get('session_id') != session_id:
                    continue
                if ai_id and checkpoint.get('ai_id') != ai_id:
                    continue

                checkpoints.append(checkpoint)

            return checkpoints
        except Exception as e:
            logger.warning(f"Failed to load recent checkpoints: {e}")
            return []

    def _find_latest_checkpoint(
        self,
        session_id: str | None = None,
        ai_id: str | None = None
    ) -> dict[str, Any] | None:
        """Find latest checkpoint matching filters"""
        # Get all checkpoints
        result = subprocess.run(
            ['git', 'log', '--all', '--pretty=format:%H'],
            cwd=self.workspace_root,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return None

        commit_hashes = result.stdout.strip().split('\n')

        # Search for matching checkpoint
        for commit_hash in commit_hashes:
            checkpoint = self._load_checkpoint_by_hash(commit_hash)
            if not checkpoint:
                continue

            # Apply filters
            if session_id and checkpoint.get('session_id') != session_id:
                continue
            if ai_id and checkpoint.get('ai_id') != ai_id:
                continue

            return checkpoint

        return None


def auto_checkpoint(
    session_id: str,
    ai_id: str,
    phase: str,
    vectors: dict[str, float],
    round_num: int = 1,
    metadata: dict[str, Any] | None = None,
    no_git_flag: bool = False
) -> str | None:
    """
    Convenience function for automatic checkpoint creation

    Usage in CASCADE commands:
        from empirica.core.canonical.empirica_git import auto_checkpoint

        auto_checkpoint(
            session_id=session_id,
            ai_id=args.ai_id,
            phase='PREFLIGHT',
            vectors={'engagement': 0.85, 'know': 0.70, ...},
            metadata={'confidence': 0.82}
        )
    """
    manager = CheckpointManager()
    return manager.auto_checkpoint(
        session_id=session_id,
        ai_id=ai_id,
        phase=phase,
        vectors=vectors,
        round_num=round_num,
        metadata=metadata,
        no_git_flag=no_git_flag
    )
