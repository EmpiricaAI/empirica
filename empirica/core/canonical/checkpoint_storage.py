"""
Checkpoint Storage Module

Handles SQLite and JSON file storage for epistemic checkpoints.
Provides queryable index with pointers to git commits.

Part of the GitEnhancedReflexLogger refactoring (extracted from 1,156 line file).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc

logger = logging.getLogger(__name__)


class CheckpointStorage:
    """
    SQLite and JSON file storage for epistemic checkpoints.

    Architecture (Pointer-based):
    - Git: Authoritative source for signed epistemic states (immutable, verifiable)
    - SQLite: Queryable index with pointers to git commits + noema metadata
    - JSON: Full audit trail (optional file-based storage)
    """

    def __init__(self, session_id: str, base_log_dir: Path):
        """
        Initialize checkpoint storage.

        Args:
            session_id: Session identifier
            base_log_dir: Base directory for checkpoint logs
        """
        self.session_id = session_id
        self.base_log_dir = base_log_dir
        self.base_log_dir.mkdir(parents=True, exist_ok=True)

    def save_to_sqlite(
        self, checkpoint: dict[str, Any], git_commit_sha: str | None = None, git_notes_ref: str | None = None
    ) -> bool:
        """
        Save checkpoint pointer to SQLite reflexes table.

        Args:
            checkpoint: Compressed checkpoint dictionary containing:
                - session_id, phase, round, timestamp
                - vectors (all 13 epistemic dimensions)
                - noema (epistemic signature, learning efficiency, etc.)
                - metadata (task, decision, etc.)
            git_commit_sha: Git commit SHA (pointer to authoritative source)
            git_notes_ref: Git notes reference path for retrieval

        Returns:
            True if successful, False otherwise
        """
        try:
            from empirica.data.session_database import SessionDatabase

            # Extract data from checkpoint
            session_id = checkpoint.get("session_id")
            phase = checkpoint.get("phase")
            round_num = checkpoint.get("round", 1)
            vectors = checkpoint.get("vectors", {})

            if not session_id or not phase:
                logger.error("Cannot save checkpoint: missing session_id or phase")
                return False

            # An automatic checkpoint (check-submit, after a risky CHECK) is a git-note anchor, not an
            # assessment: the CHECK that triggered it has already written its own row. A second CHECK-phase
            # row, with no reasoning and no transaction, is what the Sentinel and every "latest CHECK"
            # reader then take as the practitioner's current state.
            if (checkpoint.get("meta") or {}).get("auto_checkpoint"):
                logger.debug(f"Auto-checkpoint for {session_id} phase={phase}: git note and json only, no reflexes row")
                return True

            db = SessionDatabase()

            try:
                # Extract noema metadata for quick filtering
                noema = checkpoint.get("noema", {})
                epistemic_signature = noema.get("epistemic_signature")

                # Prepare metadata with git pointers
                metadata_dict = checkpoint.get("meta", {})
                metadata_dict["git_commit_sha"] = git_commit_sha
                metadata_dict["git_notes_ref"] = git_notes_ref

                # Store pointer + noema metadata in reflexes table
                db.store_vectors(
                    session_id=session_id,
                    phase=phase,
                    vectors=vectors,
                    cascade_id=metadata_dict.get("cascade_id"),
                    round_num=round_num,
                    metadata=metadata_dict,
                    reasoning=metadata_dict.get("reasoning"),
                    transaction_id=metadata_dict.get("transaction_id"),
                )

                logger.debug(
                    f"Checkpoint pointer saved to SQLite: "
                    f"session={session_id}, phase={phase}, round={round_num}, "
                    f"git_commit={git_commit_sha[:7] if git_commit_sha else 'none'}, "
                    f"noema_sig={epistemic_signature}"
                )
                return True

            finally:
                db.close()

        except Exception as e:
            logger.error(f"Failed to save checkpoint to SQLite: {e}", exc_info=True)
            return False

    def load_from_sqlite(self, phase: str | None = None, max_age_hours: int = 24) -> dict[str, Any] | None:
        """
        Load checkpoint from SQLite fallback storage (JSON files).

        Args:
            phase: Filter by phase (optional)
            max_age_hours: Maximum age in hours

        Returns:
            Checkpoint dictionary or None
        """
        checkpoint_dir = self.base_log_dir / "checkpoints" / self.session_id

        if not checkpoint_dir.exists():
            return None

        # Get all checkpoint files
        checkpoint_files = sorted(
            checkpoint_dir.glob("checkpoint_*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )

        cutoff_time = datetime.now(UTC) - timedelta(hours=max_age_hours)

        for filepath in checkpoint_files:
            try:
                with open(filepath) as f:
                    checkpoint = json.load(f)

                # Check age
                checkpoint_time = datetime.fromisoformat(checkpoint["timestamp"])
                if checkpoint_time < cutoff_time:
                    continue

                # Check phase filter
                if phase and checkpoint.get("phase") != phase:
                    continue

                return checkpoint

            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.debug(f"Failed to load checkpoint {filepath}: {e}")
                continue

        return None

    def save_to_json(self, checkpoint: dict[str, Any]) -> Path | None:
        """
        Save checkpoint to JSON file for audit trail.

        Args:
            checkpoint: Checkpoint dictionary

        Returns:
            Path to saved file or None if failed
        """
        try:
            checkpoint_dir = self.base_log_dir / "checkpoints" / self.session_id
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

            phase = checkpoint.get("phase", "UNKNOWN")
            round_num = checkpoint.get("round", 1)
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

            filename = f"checkpoint_{phase}_{round_num}_{timestamp}.json"
            filepath = checkpoint_dir / filename

            with open(filepath, "w") as f:
                json.dump(checkpoint, f, indent=2)

            logger.debug(f"Checkpoint saved to JSON: {filepath}")
            return filepath

        except Exception as e:
            logger.warning(f"Failed to save checkpoint to JSON: {e}")
            return None
