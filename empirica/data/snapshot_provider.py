#!/usr/bin/env python3
"""
Epistemic Snapshot Provider - Creation and persistence management

Responsibilities:
- Create snapshots from current session state
- Save/load snapshots from database
- Calculate compression metrics
- Manage snapshot history
- Export/import to JSON files

Integration:
- Uses SessionDatabase for persistence
- Works with epistemic_snapshot.py data classes

Usage:
    provider = EpistemicSnapshotProvider()

    # Create snapshot from current session
    snapshot = provider.create_snapshot_from_session(
        session_id="session_123",
        context_summary="Security analysis complete..."
    )

    # Save to database
    provider.save_snapshot(snapshot)

    # Load latest snapshot
    latest = provider.get_latest_snapshot(session_id)

    # Export/import
    provider.export_snapshot_to_file(snapshot, "snapshot.json")
    loaded = provider.import_snapshot_from_file("snapshot.json")
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from empirica.data.epistemic_snapshot import ContextSummary, EpistemicStateSnapshot

# Import Empirica components
from empirica.data.session_database import SessionDatabase

_VECTOR_NAMES = (
    "ENGAGEMENT",
    "KNOW",
    "DO",
    "CONTEXT",
    "CLARITY",
    "COHERENCE",
    "SIGNAL",
    "DENSITY",
    "STATE",
    "CHANGE",
    "COMPLETION",
    "IMPACT",
    "UNCERTAINTY",
)


class EpistemicSnapshotProvider:
    """
    Creates and manages epistemic snapshots

    Integrates with:
    - SessionDatabase (persistence)
    - epistemic_snapshot.py (data structures)
    """

    def __init__(self, db: SessionDatabase | None = None):
        """
        Initialize snapshot provider

        Args:
            db: SessionDatabase instance (or create new one)
        """
        self.db = db or SessionDatabase()

    def create_snapshot_from_session(
        self,
        session_id: str,
        context_summary: ContextSummary | None = None,
        context_summary_text: str | None = None,
        semantic_tags: dict | None = None,
        evidence_refs: list[str] | None = None,
        cascade_phase: str | None = None,
        domain_vectors: dict[str, dict[str, float]] | None = None,
    ) -> EpistemicStateSnapshot:
        """
        Create epistemic snapshot from current session state

        Args:
            session_id: Session to snapshot
            context_summary: Full ContextSummary object (takes precedence)
            context_summary_text: Narrative text (if ContextSummary not provided)
            semantic_tags: Semantic metadata (if ContextSummary not provided)
            evidence_refs: Evidence references (if ContextSummary not provided)
            cascade_phase: Current cascade phase
            domain_vectors: Optional domain-specific vectors

        Returns:
            EpistemicStateSnapshot

        Raises:
            ValueError: If session not found
        """
        # Get session data
        session = self.db.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Get latest preflight assessment (13 vectors)
        preflight = self.db.get_preflight_assessment(session_id)
        vectors = self._extract_vectors_from_assessment(preflight) if preflight else self._get_default_vectors()

        # Build context summary if not provided
        if context_summary is None:
            context_summary = ContextSummary(
                semantic=semantic_tags or {}, narrative=context_summary_text or "", evidence_refs=evidence_refs or []
            )

        # Get previous snapshot for delta calculation
        previous_snapshot = self.get_latest_snapshot(session_id)
        delta = None
        previous_snapshot_id = None

        if previous_snapshot:
            # Create temporary snapshot for delta calculation
            temp_snapshot = EpistemicStateSnapshot(
                snapshot_id="temp",
                session_id=session_id,
                ai_id=session["ai_id"],
                timestamp=datetime.now().isoformat(),
                vectors=vectors,
            )
            delta = temp_snapshot.calculate_delta(previous_snapshot)
            previous_snapshot_id = previous_snapshot.snapshot_id

        # Estimate token counts
        original_tokens = self._estimate_original_context_tokens(session_id)
        snapshot_tokens = self._estimate_snapshot_tokens(vectors, context_summary, domain_vectors)

        # Calculate compression ratio
        compression_ratio = 0.0
        if original_tokens > 0:
            compression_ratio = 1.0 - (snapshot_tokens / original_tokens)

        # Create snapshot
        snapshot = EpistemicStateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            session_id=session_id,
            ai_id=session["ai_id"],
            timestamp=datetime.now().isoformat(),
            cascade_phase=cascade_phase,
            vectors=vectors,
            delta=delta,
            previous_snapshot_id=previous_snapshot_id,
            context_summary=context_summary,
            db_session_ref=session_id,
            domain_vectors=domain_vectors,
            original_context_tokens=original_tokens,
            snapshot_tokens=snapshot_tokens,
            compression_ratio=compression_ratio,
            fidelity_score=self._estimate_fidelity(vectors, context_summary),
            information_loss_estimate=self._estimate_information_loss(compression_ratio),
            transfer_count=0,  # New snapshot, no transfers yet
        )

        return snapshot

    def save_snapshot(self, snapshot: EpistemicStateSnapshot):
        """
        Save snapshot to database

        Args:
            snapshot: Snapshot to save
        """
        cursor = self.db.conn.cursor()

        # Convert context_summary to JSON
        context_summary_json = None
        if snapshot.context_summary:
            context_summary_json = json.dumps(snapshot.context_summary.to_dict())

        cursor.execute(
            """
            INSERT INTO epistemic_snapshots (
                snapshot_id, session_id, ai_id, timestamp,
                cascade_phase, cascade_id, vectors, delta, previous_snapshot_id,
                context_summary, evidence_refs, db_session_ref,
                domain_vectors, original_context_tokens, snapshot_tokens,
                compression_ratio, information_loss_estimate, fidelity_score,
                transfer_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                snapshot.snapshot_id,
                snapshot.session_id,
                snapshot.ai_id,
                snapshot.timestamp,
                snapshot.cascade_phase,
                snapshot.cascade_id,
                json.dumps(snapshot.vectors),
                json.dumps(snapshot.delta) if snapshot.delta else None,
                snapshot.previous_snapshot_id,
                context_summary_json,
                json.dumps(snapshot.context_summary.evidence_refs) if snapshot.context_summary else None,
                snapshot.db_session_ref,
                json.dumps(snapshot.domain_vectors) if snapshot.domain_vectors else None,
                snapshot.original_context_tokens,
                snapshot.snapshot_tokens,
                snapshot.compression_ratio,
                snapshot.information_loss_estimate,
                snapshot.fidelity_score,
                snapshot.transfer_count,
                snapshot.created_at,
            ),
        )

        self.db.conn.commit()

        # NEW: Trigger action hooks for dashboard update (optional)
        try:
            from empirica.integration.empirica_action_hooks import EmpiricaActionHooks

            EmpiricaActionHooks.update_snapshot_status(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "session_id": snapshot.session_id,
                    "ai_id": snapshot.ai_id,
                    "cascade_phase": snapshot.cascade_phase,
                    "vectors": snapshot.vectors,
                    "delta": snapshot.delta,
                    "original_context_tokens": snapshot.original_context_tokens,
                    "snapshot_tokens": snapshot.snapshot_tokens,
                    "compression_ratio": snapshot.compression_ratio,
                    "fidelity_score": snapshot.fidelity_score,
                    "information_loss_estimate": snapshot.information_loss_estimate,
                    "transfer_count": snapshot.transfer_count,
                    "reliability": snapshot.estimate_memory_reliability(),
                    "should_refresh": snapshot.should_refresh(),
                    "refresh_reason": snapshot.get_refresh_reason() if snapshot.should_refresh() else None,
                    "created_at": snapshot.created_at,
                }
            )
        except Exception:
            pass  # Action hooks are optional

        import sys

        print(
            f"📸 Snapshot saved: {snapshot.snapshot_id} (compression: {snapshot.compression_ratio:.1%})",
            file=sys.stderr,
        )

    def get_latest_snapshot(self, session_id: str) -> EpistemicStateSnapshot | None:
        """
        Get most recent snapshot for session

        Args:
            session_id: Session identifier

        Returns:
            Latest EpistemicStateSnapshot or None
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM epistemic_snapshots
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """,
            (session_id,),
        )

        row = cursor.fetchone()
        if not row:
            return None

        return self._row_to_snapshot(row)

    def get_snapshot_by_id(self, snapshot_id: str) -> EpistemicStateSnapshot | None:
        """
        Get snapshot by ID

        Args:
            snapshot_id: Snapshot identifier

        Returns:
            EpistemicStateSnapshot or None
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM epistemic_snapshots
            WHERE snapshot_id = ?
        """,
            (snapshot_id,),
        )

        row = cursor.fetchone()
        if not row:
            return None

        return self._row_to_snapshot(row)

    def get_snapshot_history(self, session_id: str, limit: int = 10) -> list[EpistemicStateSnapshot]:
        """
        Get snapshot history for session

        Args:
            session_id: Session identifier
            limit: Maximum snapshots to return

        Returns:
            List of EpistemicStateSnapshot (newest first)
        """
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM epistemic_snapshots
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """,
            (session_id, limit),
        )

        return [self._row_to_snapshot(row) for row in cursor.fetchall()]

    def export_snapshot_to_file(self, snapshot: EpistemicStateSnapshot, filepath: str) -> str:
        """
        Export snapshot to JSON file for cross-AI transfer

        Args:
            snapshot: Snapshot to export
            filepath: Destination file path

        Returns:
            Absolute path to exported file
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            f.write(snapshot.to_json())

        print(f"📤 Snapshot exported: {filepath}")
        return str(path.absolute())

    def import_snapshot_from_file(self, filepath: str) -> EpistemicStateSnapshot:
        """
        Import snapshot from JSON file

        Args:
            filepath: Source file path

        Returns:
            EpistemicStateSnapshot

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Snapshot file not found: {filepath}")

        with open(path) as f:
            snapshot = EpistemicStateSnapshot.from_json(f.read())

        print(f"📥 Snapshot imported: {filepath}")
        return snapshot

    def _extract_vectors_from_assessment(self, assessment: dict) -> dict[str, float]:
        """
        Extract the 13 epistemic vectors from a preflight assessment.

        ``get_latest_vectors`` returns the values nested under ``vectors`` with the names the reflexes table has (``know``,
        ``do``, ...); a flat ``{name: value}`` dict is accepted too. This used to map LEGACY column names
        (``epistemic_humility``, ``cognitive_flexibility``, ...) that no table has, so every lookup missed and every snapshot
        was thirteen 0.5s whatever the session had reported, which also made every snapshot-to-snapshot delta zero.

        A vector that was not reported falls back to the neutral 0.5, as it always did for a session with no preflight.

        Args:
            assessment: Preflight assessment dictionary

        Returns:
            Dictionary of 13 vectors keyed by upper-case name
        """
        nested = assessment.get("vectors")
        source = nested if isinstance(nested, dict) else assessment
        by_lower = {str(k).lower(): v for k, v in source.items()}

        vectors: dict[str, float] = {}
        for name in _VECTOR_NAMES:
            value = by_lower.get(name.lower())
            vectors[name] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.5

        return vectors

    def _get_default_vectors(self) -> dict[str, float]:
        """Get default 13 vectors (neutral state)"""
        return {
            "ENGAGEMENT": 0.5,
            "KNOW": 0.5,
            "DO": 0.5,
            "CONTEXT": 0.5,
            "CLARITY": 0.5,
            "COHERENCE": 0.5,
            "SIGNAL": 0.5,
            "DENSITY": 0.5,
            "STATE": 0.5,
            "CHANGE": 0.5,
            "COMPLETION": 0.5,
            "IMPACT": 0.5,
            "UNCERTAINTY": 0.5,
        }

    def _estimate_original_context_tokens(self, session_id: str) -> int:
        """
        Estimate original context tokens (conversation history)

        Rough estimation:
        - Each cascade: ~500 tokens (task + assessments)
        - Each assessment: ~200 tokens
        - Session metadata: ~100 tokens
        """
        session = self.db.get_session(session_id)
        if not session:
            return 0

        # Get cascades
        cascades = self.db.get_session_cascades(session_id)
        cascade_count = len(cascades)

        # Estimate tokens
        session_metadata_tokens = 100
        per_cascade_tokens = 500

        total_tokens = session_metadata_tokens + (cascade_count * per_cascade_tokens)
        return total_tokens

    def _estimate_snapshot_tokens(
        self, vectors: dict[str, float], context_summary: ContextSummary | None, domain_vectors: dict | None
    ) -> int:
        """
        Estimate snapshot token count

        Breakdown:
        - 13 vectors: ~150 tokens
        - Context summary: ~200 tokens
        - Domain vectors: ~100 tokens per domain
        - Metadata: ~50 tokens
        """
        tokens = 150  # Core vectors
        tokens += 50  # Metadata

        if context_summary and context_summary.narrative:
            # Rough estimate: 1 token per 4 characters
            tokens += len(context_summary.narrative) // 4

        if domain_vectors:
            tokens += len(domain_vectors) * 100

        return tokens

    def _estimate_fidelity(self, vectors: dict[str, float], context_summary: ContextSummary | None) -> float:
        """
        Estimate snapshot fidelity (how well it represents original context)

        Higher fidelity when:
        - Context summary is rich (semantic + narrative)
        - Vectors are well-defined (not all neutral)
        - Evidence references are provided
        """
        fidelity = 0.85  # Base fidelity

        # Bonus for rich context summary
        if context_summary:
            if context_summary.semantic:
                fidelity += 0.05
            if context_summary.narrative:
                fidelity += 0.05
            if context_summary.evidence_refs:
                fidelity += 0.05

        # Penalty if too many neutral vectors (0.4-0.6 range)
        neutral_count = sum(1 for v in vectors.values() if 0.4 <= v <= 0.6)
        if neutral_count > 8:  # More than half are neutral
            fidelity -= 0.05

        return max(0.0, min(1.0, fidelity))

    def _estimate_information_loss(self, compression_ratio: float) -> float:
        """
        Estimate information loss from compression

        Higher compression = more loss (but not linear)
        95% compression ≈ 10% information loss (acceptable)
        """
        # Non-linear relationship: high compression is okay for epistemic state
        if compression_ratio >= 0.95:
            return 0.10  # 10% loss at 95% compression (acceptable)
        elif compression_ratio >= 0.90:
            return 0.08
        elif compression_ratio >= 0.80:
            return 0.05
        else:
            return 0.03

    def _row_to_snapshot(self, row) -> EpistemicStateSnapshot:
        """
        Convert database row to EpistemicStateSnapshot

        Args:
            row: SQLite row

        Returns:
            EpistemicStateSnapshot
        """
        row_dict = dict(row)

        # Parse JSON fields
        vectors = json.loads(row_dict["vectors"])
        delta = json.loads(row_dict["delta"]) if row_dict.get("delta") else None
        domain_vectors = json.loads(row_dict["domain_vectors"]) if row_dict.get("domain_vectors") else None

        # Parse context summary
        context_summary = None
        if row_dict.get("context_summary"):
            context_summary_data = json.loads(row_dict["context_summary"])
            context_summary = ContextSummary.from_dict(context_summary_data)

        return EpistemicStateSnapshot(
            snapshot_id=row_dict["snapshot_id"],
            session_id=row_dict["session_id"],
            ai_id=row_dict["ai_id"],
            timestamp=row_dict["timestamp"],
            cascade_phase=row_dict.get("cascade_phase"),
            cascade_id=row_dict.get("cascade_id"),
            vectors=vectors,
            delta=delta,
            previous_snapshot_id=row_dict.get("previous_snapshot_id"),
            context_summary=context_summary,
            db_session_ref=row_dict["db_session_ref"],
            domain_vectors=domain_vectors,
            original_context_tokens=row_dict["original_context_tokens"],
            snapshot_tokens=row_dict["snapshot_tokens"],
            compression_ratio=row_dict["compression_ratio"],
            information_loss_estimate=row_dict["information_loss_estimate"],
            fidelity_score=row_dict["fidelity_score"],
            transfer_count=row_dict["transfer_count"],
            created_at=row_dict["created_at"],
        )
