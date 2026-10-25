"""Vector repository for epistemic vector storage and retrieval"""

import hashlib
import json
import subprocess
import time

from .base import BaseRepository


def _get_project_id_from_session(conn, session_id: str) -> str | None:
    """Get project_id from the session's already-resolved project_id.

    This is the AUTHORITATIVE source - the session table stores project_id
    that was resolved at session creation time (via project-switch, etc).

    Args:
        conn: Database connection
        session_id: Session UUID to look up

    Returns:
        project_id from the session, or None if not found
    """
    try:
        cursor = conn.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def _get_project_id_fallback() -> str | None:
    """Fallback project_id detection for CI/headless environments.

    Only used when session lookup fails. Uses git remote URL hash
    for reproducibility across instances.

    WARNING: This is a fallback, not the primary mechanism.
    Session's project_id should always be preferred.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            url = result.stdout.strip()
            return hashlib.sha256(url.encode()).hexdigest()[:16]

        # Git toplevel path hash as last resort
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            path = result.stdout.strip()
            return hashlib.sha256(path.encode()).hexdigest()[:16]
    except Exception:
        pass
    return None


class VectorRepository(BaseRepository):
    """Handles epistemic vector operations"""

    def store_vectors(
        self,
        session_id: str,
        phase: str,
        vectors: dict[str, float],
        cascade_id: str | None = None,
        round_num: int = 1,
        metadata: dict | None = None,
        reasoning: str | None = None,
        project_id: str | None = None,
        transaction_id: str | None = None,
    ) -> int:
        """
        Store epistemic vectors in the reflexes table

        Args:
            session_id: Session identifier
            phase: Current phase (PREFLIGHT, CHECK, ACT, POSTFLIGHT)
            vectors: Dictionary of epistemic vectors. A vector that is absent, or not a number, is stored as NULL ("not
                assessed"), never as an invented 0.5: CHECK commonly supplies only some of the 13, and readers must treat
                NULL as unknown. The values actually supplied are kept in ``reflex_data``.
            cascade_id: Optional cascade identifier
            round_num: Current round number
            metadata: Optional additional metadata
            reasoning: Optional reasoning text

        Returns:
            Row ID of the inserted record
        """
        # Auto-detect project_id if not provided
        # Priority: 1) Session's project_id (authoritative)
        #           2) Fallback for CI/headless (git-based hash)
        if project_id is None:
            project_id = _get_project_id_from_session(self.conn, session_id)
            if project_id is None:
                project_id = _get_project_id_fallback()

        # Extract the 13 vectors, providing default values if not present
        vector_names = [
            "engagement",
            "know",
            "do",
            "context",
            "clarity",
            "coherence",
            "signal",
            "density",
            "state",
            "change",
            "completion",
            "impact",
            "uncertainty",
        ]

        # A vector the caller did not supply, or supplied as something that is not a number, is NULL: "not assessed". It used to be
        # an invented 0.5, which every reader then took for a measurement (53 of the last 60 CHECK rows carried some, because CHECK
        # supplies a mean of 7 of 13). A bool is not a measurement either, though it is an int. The real values stay in reflex_data.
        vector_values = []
        for name in vector_names:
            value = vectors.get(name)
            vector_values.append(value if isinstance(value, (int, float)) and not isinstance(value, bool) else None)

        # Create a reflex data entry with optional metadata
        reflex_data = {
            "session_id": session_id,
            "phase": phase,
            "round": round_num,
            "vectors": vectors,
            "timestamp": time.time(),
            "project_id": project_id,
            "transaction_id": transaction_id,
        }

        # Merge in any additional metadata if provided
        if metadata:
            reflex_data.update(metadata)

        cursor = self._execute(
            """
            INSERT INTO reflexes (
                session_id, cascade_id, phase, round, timestamp,
                engagement, know, do, context,
                clarity, coherence, signal, density,
                state, change, completion, impact, uncertainty,
                reflex_data, reasoning, project_id, transaction_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                session_id,
                cascade_id,
                phase,
                round_num,
                time.time(),
                *vector_values,  # Unpack the 13 vector values
                json.dumps(reflex_data),
                reasoning,
                project_id,
                transaction_id,
            ),
        )

        self.commit()
        return cursor.lastrowid or 0

    def get_last_recorded_vectors(self, session_id: str) -> dict[str, float]:
        """Each vector's newest RECORDED value over all of a session's rows, whatever the phase.

        A CHECK commonly assesses only some of the 13, leaving the rest NULL ("not assessed"), so the newest row alone is sparse; a
        reader that needs a complete picture takes the last value each vector was actually recorded with instead of inventing one."""
        names = (
            "engagement", "know", "do", "context", "clarity", "coherence", "signal",
            "density", "state", "change", "completion", "impact", "uncertainty",
        )  # fmt: skip
        cursor = self._execute(
            f"SELECT {', '.join(names)} FROM reflexes WHERE session_id = ? ORDER BY timestamp DESC", (session_id,)
        )
        recorded: dict[str, float] = {}
        for row in cursor.fetchall():
            for offset, name in enumerate(names):
                if name not in recorded and row[offset] is not None:
                    recorded[name] = row[offset]
        return recorded

    def get_latest_vectors(self, session_id: str, phase: str | None = None) -> dict | None:
        """
        Get the latest epistemic vectors for a session from the reflexes table

        Args:
            session_id: Session identifier
            phase: Optional phase filter

        Returns:
            Dictionary with vectors, metadata, timestamp, etc. or None if not found
        """
        query = """
            SELECT * FROM reflexes
            WHERE session_id = ?
        """
        params = [session_id]

        if phase:
            query += " AND phase = ?"
            params.append(phase)

        query += " ORDER BY timestamp DESC LIMIT 1"

        cursor = self._execute(query, params)
        result = cursor.fetchone()

        if result:
            row_dict = dict(result)

            # Extract the 13 vector values from the result
            vectors = {}
            for vector_name in [
                "engagement",
                "know",
                "do",
                "context",
                "clarity",
                "coherence",
                "signal",
                "density",
                "state",
                "change",
                "completion",
                "impact",
                "uncertainty",
            ]:
                if vector_name in row_dict:
                    value = row_dict[vector_name]
                    if value is not None:
                        vectors[vector_name] = float(value)

            # Return full data structure
            return {
                "session_id": row_dict["session_id"],
                "cascade_id": row_dict.get("cascade_id"),
                "phase": row_dict["phase"],
                "round": row_dict.get("round", 1),
                "timestamp": row_dict["timestamp"],
                "vectors": vectors,
                "metadata": json.loads(row_dict["reflex_data"]) if row_dict.get("reflex_data") else {},
                "reasoning": row_dict.get("reasoning"),
                "evidence": row_dict.get("evidence"),
            }

        return None

    def get_preflight_vectors(self, session_id: str) -> dict | None:
        """Get latest PREFLIGHT vectors for session (convenience method)"""
        return self.get_latest_vectors(session_id, phase="PREFLIGHT")

    def get_check_vectors(self, session_id: str, cycle: int | None = None) -> list[dict]:
        """Get CHECK phase vectors, optionally filtered by cycle"""
        vectors = self.get_vectors_by_phase(session_id, phase="CHECK")
        if cycle is not None:
            return [v for v in vectors if v.get("round") == cycle]
        return vectors

    def get_postflight_vectors(self, session_id: str) -> dict | None:
        """Get latest POSTFLIGHT vectors for session (convenience method)"""
        return self.get_latest_vectors(session_id, phase="POSTFLIGHT")

    def get_vectors_by_phase(self, session_id: str, phase: str) -> list[dict]:
        """Get all vectors for a specific phase"""
        cursor = self._execute(
            """
            SELECT * FROM reflexes
            WHERE session_id = ? AND phase = ?
            ORDER BY timestamp ASC
        """,
            (session_id, phase),
        )

        results = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            # Build vectors dict from columns
            vectors = {
                "engagement": row_dict.get("engagement"),
                "know": row_dict.get("know"),
                "do": row_dict.get("do"),
                "context": row_dict.get("context"),
                "clarity": row_dict.get("clarity"),
                "coherence": row_dict.get("coherence"),
                "signal": row_dict.get("signal"),
                "density": row_dict.get("density"),
                "state": row_dict.get("state"),
                "change": row_dict.get("change"),
                "completion": row_dict.get("completion"),
                "impact": row_dict.get("impact"),
                "uncertainty": row_dict.get("uncertainty"),
            }
            # Remove None values
            vectors = {k: v for k, v in vectors.items() if v is not None}

            results.append(
                {
                    "session_id": row_dict["session_id"],
                    "cascade_id": row_dict.get("cascade_id"),
                    "phase": row_dict["phase"],
                    "round": row_dict.get("round", 1),
                    "timestamp": row_dict["timestamp"],
                    "vectors": vectors,
                    "metadata": json.loads(row_dict["reflex_data"]) if row_dict.get("reflex_data") else {},
                    "reasoning": row_dict.get("reasoning"),
                    "evidence": row_dict.get("evidence"),
                }
            )

        return results

    def get_session_vector_history(self, session_id: str) -> list[dict]:
        """
        Get complete vector history for a session

        Args:
            session_id: Session UUID

        Returns:
            List of all vector records
        """
        cursor = self._execute(
            """
            SELECT * FROM reflexes
            WHERE session_id = ?
            ORDER BY timestamp ASC
        """,
            (session_id,),
        )

        results = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            # Build vectors dict from columns
            vectors = {
                "engagement": row_dict.get("engagement"),
                "know": row_dict.get("know"),
                "do": row_dict.get("do"),
                "context": row_dict.get("context"),
                "clarity": row_dict.get("clarity"),
                "coherence": row_dict.get("coherence"),
                "signal": row_dict.get("signal"),
                "density": row_dict.get("density"),
                "state": row_dict.get("state"),
                "change": row_dict.get("change"),
                "completion": row_dict.get("completion"),
                "impact": row_dict.get("impact"),
                "uncertainty": row_dict.get("uncertainty"),
            }
            # Remove None values
            vectors = {k: v for k, v in vectors.items() if v is not None}

            results.append(
                {
                    "session_id": row_dict["session_id"],
                    "cascade_id": row_dict.get("cascade_id"),
                    "phase": row_dict["phase"],
                    "round": row_dict.get("round", 1),
                    "timestamp": row_dict["timestamp"],
                    "vectors": vectors,
                    "metadata": json.loads(row_dict["reflex_data"]) if row_dict.get("reflex_data") else {},
                    "reasoning": row_dict.get("reasoning"),
                    "evidence": row_dict.get("evidence"),
                }
            )

        return results
