"""
Investigation Branch Repository

Manages epistemic auto-merge for parallel investigation branches.
Tracks branch creation, checkpoints, scoring, and automatic merge decisions.
"""

import json
import time
import uuid

from .base import BaseRepository


class BranchRepository(BaseRepository):
    """Repository for investigation branch management (Phase 2 branching)"""

    def create_branch(
        self,
        session_id: str,
        branch_name: str,
        investigation_path: str,
        git_branch_name: str,
        preflight_vectors: dict,
        transaction_id: str | None = None,
    ) -> str:
        """Create a new investigation branch

        Args:
            session_id: Session UUID
            branch_name: Human-readable branch name
            investigation_path: What is being investigated (e.g., 'oauth2')
            git_branch_name: Git branch name
            preflight_vectors: Epistemic vectors at branch start
            transaction_id: Parent's transaction ID for epistemic continuity

        Returns:
            Branch ID
        """
        branch_id = str(uuid.uuid4())
        now = time.time()

        self._execute(
            """
            INSERT INTO investigation_branches
            (id, session_id, branch_name, investigation_path, git_branch_name,
             preflight_vectors, transaction_id, created_timestamp, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """,
            (
                branch_id,
                session_id,
                branch_name,
                investigation_path,
                git_branch_name,
                json.dumps(preflight_vectors),
                transaction_id,
                now,
            ),
        )

        self.commit()
        return branch_id

    def checkpoint_branch(
        self, branch_id: str, postflight_vectors: dict, tokens_spent: int, time_spent_minutes: int
    ) -> bool:
        """Checkpoint a branch after investigation

        Args:
            branch_id: Branch ID
            postflight_vectors: Epistemic vectors after investigation
            tokens_spent: Tokens used in investigation
            time_spent_minutes: Time spent in investigation

        Returns:
            Success boolean
        """
        now = time.time()

        self._execute(
            """
            UPDATE investigation_branches
            SET postflight_vectors = ?, tokens_spent = ?, time_spent_minutes = ?,
                checkpoint_timestamp = ?
            WHERE id = ?
        """,
            (json.dumps(postflight_vectors), tokens_spent, time_spent_minutes, now, branch_id),
        )

        self.commit()
        return True

    def get_branch(self, branch_id: str) -> dict:
        """Get branch by ID

        Args:
            branch_id: Branch UUID

        Returns:
            Dict with branch data or empty dict if not found
        """
        cursor = self._execute(
            """
            SELECT id, session_id, branch_name, investigation_path,
                   preflight_vectors, postflight_vectors, merge_score, status,
                   transaction_id
            FROM investigation_branches WHERE id = ?
        """,
            (branch_id,),
        )

        row = cursor.fetchone()
        if not row:
            return {}

        return {
            "branch_id": row[0],
            "session_id": row[1],
            "branch_name": row[2],
            "investigation_path": row[3],
            "preflight_vectors": json.loads(row[4]) if row[4] else {},
            "postflight_vectors": json.loads(row[5]) if row[5] else {},
            "merge_score": row[6],
            "status": row[7],
            "transaction_id": row[8],
        }

    def calculate_branch_merge_score(self, branch_id: str) -> dict:
        """Calculate epistemic merge score for a branch

        Score = (learning_delta × quality × confidence) / cost_penalty
        Where: confidence = 1 - uncertainty (uncertainty is a DAMPENER)

        Returns:
            Dict with merge_score, quality, and rationale
        """
        cursor = self._execute(
            """
            SELECT preflight_vectors, postflight_vectors, tokens_spent
            FROM investigation_branches WHERE id = ?
        """,
            (branch_id,),
        )

        row = cursor.fetchone()
        if not row or not row[1]:  # No postflight data yet
            return {"merge_score": 0, "quality": 0, "rationale": "No postflight data"}

        preflight = json.loads(row[0])
        postflight = json.loads(row[1])
        tokens_spent = row[2] or 0

        # 1. Calculate learning delta
        key_vectors = ["know", "do", "context", "clarity", "signal"]
        learning_deltas = []
        for key in key_vectors:
            pre_val = preflight.get(key, 0.5)
            post_val = postflight.get(key, 0.5)
            learning_deltas.append(post_val - pre_val)
        learning_delta = sum(learning_deltas) / len(key_vectors)

        # 2. Calculate quality
        coherence = postflight.get("coherence", 0.5)
        clarity = postflight.get("clarity", 0.5)
        density = postflight.get("density", 0.5)
        quality = (coherence + clarity + (1 - density)) / 3

        # 3. Calculate confidence (1 - uncertainty, CRITICAL DAMPENER)
        uncertainty = postflight.get("uncertainty", 0.5)
        confidence = 1.0 - uncertainty

        # 4. Calculate cost penalty
        cost_penalty = max(1.0, tokens_spent / 2000.0)

        # 5. Calculate final merge score
        merge_score = (learning_delta * quality * confidence) / cost_penalty

        return {
            "merge_score": round(merge_score, 4),
            "quality": round(quality, 4),
            "learning_delta": round(learning_delta, 4),
            "confidence": round(confidence, 4),
            "uncertainty_dampener": round(uncertainty, 4),
        }

    def merge_branches(self, session_id: str, investigation_round: int = 1) -> dict:
        """Auto-merge best branch based on epistemic scores

        Returns:
            Dict with winning_branch_id, merge_decision_id, rationale
        """
        cursor = self._execute(
            """
            SELECT id, branch_name, investigation_path
            FROM investigation_branches
            WHERE session_id = ? AND status = 'active'
        """,
            (session_id,),
        )

        branches = cursor.fetchall()
        if not branches:
            return {"error": "No active branches to merge"}

        # Calculate scores for all branches
        branch_scores = []
        for branch_id, branch_name, investigation_path in branches:
            score_data = self.calculate_branch_merge_score(branch_id)
            if score_data.get("merge_score", 0) > 0:
                branch_scores.append(
                    {
                        "branch_id": branch_id,
                        "branch_name": branch_name,
                        "investigation_path": investigation_path,
                        "score": score_data["merge_score"],
                        "quality": score_data["quality"],
                        "confidence": score_data["confidence"],
                        "uncertainty": score_data["uncertainty_dampener"],
                    }
                )

        if not branch_scores:
            return {"error": "No branches with valid epistemic data"}

        # Select winner (highest score)
        winner = max(branch_scores, key=lambda x: x["score"])

        # Mark winner in database
        self._execute(
            """
            UPDATE investigation_branches
            SET is_winner = TRUE, status = 'merged', merged_timestamp = ?
            WHERE id = ?
        """,
            (time.time(), winner["branch_id"]),
        )

        # Record merge decision
        decision_id = str(uuid.uuid4())
        other_branches = [b["branch_name"] for b in branch_scores if b["branch_id"] != winner["branch_id"]]
        rationale = (
            f"Selected {winner['investigation_path']}: "
            f"score={winner['score']:.4f}, "
            f"quality={winner['quality']:.4f}, "
            f"confidence={winner['confidence']:.4f} "
            f"({len(other_branches)} other paths evaluated)"
        )

        self._execute(
            """
            INSERT INTO merge_decisions
            (id, session_id, investigation_round, winning_branch_id, winning_branch_name,
             winning_score, other_branches, decision_rationale, auto_merged, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?)
        """,
            (
                decision_id,
                session_id,
                investigation_round,
                winner["branch_id"],
                winner["branch_name"],
                winner["score"],
                json.dumps(other_branches),
                rationale,
                time.time(),
            ),
        )

        self.commit()

        return {
            "success": True,
            "winning_branch_id": winner["branch_id"],
            "winning_branch_name": winner["branch_name"],
            "winning_score": round(winner["score"], 4),
            "merge_decision_id": decision_id,
            "other_branches": other_branches,
            # Records, not just names: the loser tagger needs the id and the score, and treating the names as dicts crashed it after
            # the merge had already been committed.
            "losers": [
                {"branch_id": b["branch_id"], "branch_name": b["branch_name"], "score": b["score"]}
                for b in branch_scores
                if b["branch_id"] != winner["branch_id"]
            ],
            "rationale": rationale,
        }
