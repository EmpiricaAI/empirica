"""
Breadcrumb Repository

Manages knowledge artifacts: findings, unknowns, dead ends, mistakes, and reference docs.
These breadcrumbs enable session continuity and learning transfer across AI agents.
"""

import json
import logging
import time
import uuid

from ..epistemic_source import normalize_epistemic_source
from ..visibility import normalize_visibility
from .base import BaseRepository

logger = logging.getLogger(__name__)


class BreadcrumbRepository(BaseRepository):
    """Repository for knowledge artifact management (breadcrumbs for continuity)"""

    @staticmethod
    def _dedupe_by_content(items: list[dict], content_key: str) -> list[dict]:
        """
        Deduplicate items by content field, keeping the most recent entry.

        Dual-scope logging (scope='both') writes to both session_* and project_* tables.
        UNION queries then return duplicates with different IDs but same content.
        This method removes duplicates by content text, keeping the newest.

        Args:
            items: List of dicts from UNION query
            content_key: Key containing the content to dedupe by (e.g., 'finding', 'unknown')

        Returns:
            Deduplicated list preserving order (newest first)
        """
        seen = set()
        unique = []
        for item in items:
            content = item.get(content_key, '')
            if content not in seen:
                seen.add(content)
                unique.append(item)
        return unique

    def _text_similarity(self, text1: str, text2: str) -> float:
        """Simple word-overlap similarity (Jaccard-like)."""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    def _content_hash(self, text: str) -> str:
        """MD5 hash of normalized text for exact content deduplication."""
        import hashlib
        normalized = " ".join(text.strip().lower().split())
        return hashlib.md5(normalized.encode()).hexdigest()

    def _find_duplicate_finding(self, project_id: str, finding: str) -> str | None:
        """Check if a finding with identical content already exists."""
        content_hash = self._content_hash(finding)
        cursor = self._execute("""
            SELECT id, finding FROM project_findings
            WHERE project_id = ?
            ORDER BY created_timestamp DESC
        """, (project_id,))

        for row in cursor.fetchall():
            existing_id, existing_text = row
            if self._content_hash(existing_text) == content_hash:
                return existing_id
        return None

    def _find_duplicate_unknown(self, project_id: str, unknown: str) -> str | None:
        """Check if an unknown with identical content already exists."""
        content_hash = self._content_hash(unknown)
        cursor = self._execute("""
            SELECT id, unknown FROM project_unknowns
            WHERE project_id = ?
            ORDER BY created_timestamp DESC
        """, (project_id,))

        for row in cursor.fetchall():
            existing_id, existing_text = row
            if self._content_hash(existing_text) == content_hash:
                return existing_id
        return None

    def _find_duplicate_dead_end(self, project_id: str, approach: str, why_failed: str) -> str | None:
        """Check if a dead end with identical content already exists.

        Normalizes each field individually before combining to avoid
        whitespace differences around the || separator.
        """
        def _norm(t: str) -> str:
            return " ".join((t or "").strip().lower().split())

        combined = f"{_norm(approach)}||{_norm(why_failed)}"
        target_hash = self._content_hash(combined)
        cursor = self._execute("""
            SELECT id, approach, why_failed FROM project_dead_ends
            WHERE project_id = ?
            ORDER BY created_timestamp DESC
        """, (project_id,))

        for row in cursor.fetchall():
            existing_id, existing_approach, existing_why = row
            existing_combined = f"{_norm(existing_approach)}||{_norm(existing_why)}"
            if self._content_hash(existing_combined) == target_hash:
                return existing_id
        return None

    def log_finding(
        self,
        project_id: str,
        session_id: str,
        finding: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        subject: str | None = None,
        impact: float | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        source_ids: list[str] | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
    ) -> str:
        """Log a project finding (what was learned/discovered)

        Args:
            impact: Impact score 0.0-1.0 (importance). If None, defaults to 0.5.
            transaction_id: Optional epistemic transaction ID (auto-derived if not provided).
            entity_type: Entity type (project, organization, contact, engagement). Defaults to 'project'.
            entity_id: Entity UUID. Defaults to project_id if entity_type is 'project'.

        Returns:
            finding_id - new ID if created, existing ID if duplicate found
        """
        # Check for duplicate existing finding (full content match)
        existing_id = self._find_duplicate_finding(project_id, finding)
        if existing_id:
            logger.info(f"📝 Finding deduplicated (duplicate exists): {finding[:50]}...")
            return existing_id

        finding_id = str(uuid.uuid4())

        if impact is None:
            impact = 0.5

        # Default entity scope to project
        if not entity_type:
            entity_type = 'project'
        if not entity_id and entity_type == 'project':
            entity_id = project_id

        # Auto-extract source file references from finding text
        source_refs = {}
        try:
            from empirica.utils.finding_refs import parse_doc_references, parse_file_references
            file_refs = parse_file_references(finding)
            doc_refs = parse_doc_references(finding)
            if file_refs:
                source_refs["files"] = file_refs
            if doc_refs:
                source_refs["docs"] = doc_refs
        except Exception:
            pass

        finding_data = {
            "finding": finding,
            "goal_id": goal_id,
            "subtask_id": subtask_id,
            "impact": impact,
            "transaction_id": transaction_id,
            "timestamp": time.time(),
            "source_refs": source_refs if source_refs else None,
        }

        # Serialize explicit source IDs (from source-add) as JSON for the column
        source_refs_json = json.dumps(source_ids) if source_ids else None
        visibility_tier = normalize_visibility(visibility)
        source_tag = normalize_epistemic_source(epistemic_source)

        self._execute("""
            INSERT INTO project_findings (
                id, project_id, session_id, goal_id, subtask_id,
                finding, created_timestamp, finding_data, subject, impact,
                transaction_id, entity_type, entity_id, source_refs, visibility,
                epistemic_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            finding_id, project_id, session_id, goal_id, subtask_id,
            finding, time.time(), json.dumps(finding_data), subject, impact,
            transaction_id, entity_type, entity_id, source_refs_json, visibility_tier,
            source_tag
        ))

        self.commit()
        logger.info(f"📝 Finding logged: {finding[:50]}...")

        return finding_id

    def log_unknown(
        self,
        project_id: str,
        session_id: str,
        unknown: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        subject: str | None = None,
        impact: float | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
    ) -> str:
        """Log a project unknown (what's still unclear)

        Args:
            impact: Impact score 0.0-1.0 (importance). If None, defaults to 0.5.
            transaction_id: Optional epistemic transaction ID (auto-derived if not provided).
            entity_type: Entity type (project, organization, contact, engagement).
            entity_id: Entity UUID.

        Returns:
            unknown_id - new ID if created, existing ID if duplicate found
        """
        # Check for duplicate existing unknown (full content match)
        existing_id = self._find_duplicate_unknown(project_id, unknown)
        if existing_id:
            logger.info(f"📝 Unknown deduplicated (duplicate exists): {unknown[:50]}...")
            return existing_id

        unknown_id = str(uuid.uuid4())

        if impact is None:
            impact = 0.5

        if not entity_type:
            entity_type = 'project'
        if not entity_id and entity_type == 'project':
            entity_id = project_id

        # Auto-extract source file references from unknown text
        source_refs = {}
        try:
            from empirica.utils.finding_refs import parse_file_references
            file_refs = parse_file_references(unknown)
            if file_refs:
                source_refs["files"] = file_refs
        except Exception:
            pass

        unknown_data = {
            "unknown": unknown,
            "goal_id": goal_id,
            "subtask_id": subtask_id,
            "impact": impact,
            "transaction_id": transaction_id,
            "timestamp": time.time(),
            "source_refs": source_refs if source_refs else None,
        }

        visibility_tier = normalize_visibility(visibility)
        source_tag = normalize_epistemic_source(epistemic_source)

        self._execute("""
            INSERT INTO project_unknowns (
                id, project_id, session_id, goal_id, subtask_id,
                unknown, created_timestamp, unknown_data, subject, impact,
                transaction_id, entity_type, entity_id, visibility, epistemic_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            unknown_id, project_id, session_id, goal_id, subtask_id,
            unknown, time.time(), json.dumps(unknown_data), subject, impact,
            transaction_id, entity_type, entity_id, visibility_tier, source_tag
        ))

        self.commit()
        logger.info(f"❓ Unknown logged: {unknown[:50]}...")

        return unknown_id

    def resolve_unknown(self, unknown_id: str, resolved_by: str,
                        resolution_finding_id: str | None = None):
        """Mark an unknown as resolved

        Args:
            unknown_id: Full or partial UUID (minimum 8 chars)
            resolved_by: Resolution explanation
            resolution_finding_id: Optional finding ID that answered this unknown
        """
        # Support partial UUID matching (like git short hashes)
        if len(unknown_id) < 36:
            # Partial ID - use LIKE
            self._execute("""
                UPDATE project_unknowns
                SET is_resolved = TRUE, resolved_by = ?, resolved_timestamp = ?,
                    resolution_finding_id = ?
                WHERE id LIKE ?
            """, (resolved_by, time.time(), resolution_finding_id, f"{unknown_id}%"))
        else:
            # Full ID - exact match
            self._execute("""
                UPDATE project_unknowns
                SET is_resolved = TRUE, resolved_by = ?, resolved_timestamp = ?,
                    resolution_finding_id = ?
                WHERE id = ?
            """, (resolved_by, time.time(), resolution_finding_id, unknown_id))

        self.commit()
        logger.info(f"✅ Unknown resolved: {unknown_id[:8]}...")

    def log_dead_end(
        self,
        project_id: str,
        session_id: str,
        approach: str,
        why_failed: str,
        goal_id: str | None = None,
        subtask_id: str | None = None,
        subject: str | None = None,
        impact: float = 0.5,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
    ) -> str:
        """Log a project dead end (what didn't work)

        Args:
            impact: Impact score 0.0-1.0 (importance). Default 0.5 if not provided.
            transaction_id: Optional epistemic transaction ID (auto-derived if not provided).
            entity_type: Entity type (project, organization, contact, engagement).
            entity_id: Entity UUID.

        Returns:
            dead_end_id - new ID if created, existing ID if duplicate found
        """
        # Check for duplicate existing dead end (full content match)
        existing_id = self._find_duplicate_dead_end(project_id, approach, why_failed)
        if existing_id:
            logger.info(f"📝 Dead end deduplicated (duplicate exists): {approach[:50]}...")
            return existing_id

        dead_end_id = str(uuid.uuid4())

        if not entity_type:
            entity_type = 'project'
        if not entity_id and entity_type == 'project':
            entity_id = project_id

        # Auto-extract source file references from approach/why_failed text
        source_refs = {}
        try:
            from empirica.utils.finding_refs import parse_file_references
            combined_text = f"{approach} {why_failed}"
            file_refs = parse_file_references(combined_text)
            if file_refs:
                source_refs["files"] = file_refs
        except Exception:
            pass

        dead_end_data = {
            "approach": approach,
            "why_failed": why_failed,
            "goal_id": goal_id,
            "subtask_id": subtask_id,
            "impact": impact,
            "transaction_id": transaction_id,
            "timestamp": time.time(),
            "source_refs": source_refs if source_refs else None,
        }

        visibility_tier = normalize_visibility(visibility)
        source_tag = normalize_epistemic_source(epistemic_source)

        self._execute("""
            INSERT INTO project_dead_ends (
                id, project_id, session_id, goal_id, subtask_id,
                approach, why_failed, created_timestamp, dead_end_data, subject,
                transaction_id, entity_type, entity_id, visibility, epistemic_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dead_end_id, project_id, session_id, goal_id, subtask_id,
            approach, why_failed, time.time(), json.dumps(dead_end_data), subject,
            transaction_id, entity_type, entity_id, visibility_tier, source_tag
        ))

        self.commit()
        logger.info(f"💀 Dead end logged: {approach[:50]}...")

        return dead_end_id

    # ========================================================================
    # DEPRECATED: Session-scoped breadcrumbs
    # Data migrated to project_* tables. These methods redirect to project-scoped
    # equivalents for backwards compatibility until all callers are updated.
    # ========================================================================

    def log_session_finding(self, session_id, finding, goal_id=None, subtask_id=None, subject=None, impact=None):
        """Deprecated: redirects to log_finding. Session-scoped tables merged into project_*."""
        logger.warning("log_session_finding is deprecated - use log_finding instead")
        # Resolve project_id from session
        project_id = self._resolve_project_id(session_id)
        return self.log_finding(project_id, session_id, finding, goal_id, subtask_id, subject, impact)

    def log_session_unknown(self, session_id, unknown, goal_id=None, subtask_id=None, subject=None, impact=None):
        """Deprecated: redirects to log_unknown. Session-scoped tables merged into project_*."""
        logger.warning("log_session_unknown is deprecated - use log_unknown instead")
        project_id = self._resolve_project_id(session_id)
        return self.log_unknown(project_id, session_id, unknown, goal_id, subtask_id, subject, impact)

    def log_session_dead_end(self, session_id, approach, why_failed, goal_id=None, subtask_id=None, subject=None, impact=0.5):
        """Deprecated: redirects to log_dead_end. Session-scoped tables merged into project_*."""
        logger.warning("log_session_dead_end is deprecated - use log_dead_end instead")
        project_id = self._resolve_project_id(session_id)
        return self.log_dead_end(project_id, session_id, approach, why_failed, goal_id, subtask_id, subject, impact)

    def log_session_mistake(self, session_id, mistake, why_wrong, cost_estimate=None, root_cause_vector=None, prevention=None, goal_id=None):
        """Deprecated: redirects to log_mistake. Session-scoped tables merged into project_*."""
        logger.warning("log_session_mistake is deprecated - use log_mistake instead")
        project_id = self._resolve_project_id(session_id)
        return self.log_mistake(session_id, mistake, why_wrong, cost_estimate, root_cause_vector, prevention, goal_id, project_id)

    def _resolve_project_id(self, session_id: str) -> str | None:
        """Resolve project_id from a session_id."""
        try:
            cursor = self._execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def add_reference_doc(
        self,
        project_id: str,
        doc_path: str,
        doc_type: str | None = None,
        description: str | None = None
    ) -> str:
        """Add a reference document to project"""
        doc_id = str(uuid.uuid4())

        doc_data = {
            "doc_path": doc_path,
            "doc_type": doc_type,
            "description": description
        }

        self._execute("""
            INSERT INTO project_reference_docs (
                id, project_id, doc_path, doc_type, description,
                created_timestamp, doc_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            doc_id, project_id, doc_path, doc_type, description,
            time.time(), json.dumps(doc_data)
        ))

        self.commit()
        logger.info(f"📄 Reference doc added: {doc_path}")

        return doc_id

    def get_project_findings(
        self,
        project_id: str,
        limit: int | None = None,
        subject: str | None = None,
        depth: str = "moderate",
        uncertainty: float | None = None
    ) -> list[dict]:
        """
        Get findings for a project with deprecation filtering.

        Args:
            project_id: Project identifier
            limit: Optional limit on results (applied after filtering)
            subject: Optional subject filter
            depth: Relevance depth ("minimal", "moderate", "full", "complete", "auto")
            uncertainty: Epistemic uncertainty (for auto-depth, 0.0-1.0)

        Returns:
            Filtered list of findings
        """
        if subject:
            query = """
                SELECT id, session_id, goal_id, subtask_id, finding, created_timestamp,
                       finding_data, subject, impact, project_id
                FROM project_findings
                WHERE project_id = ? AND subject = ?
                ORDER BY CASE
                    WHEN created_timestamp GLOB '[0-9]*.[0-9]*' OR created_timestamp GLOB '[0-9]*'
                    THEN CAST(created_timestamp AS REAL)
                    ELSE strftime('%s', created_timestamp)
                END DESC
            """
            params = (project_id, subject)
        else:
            query = """
                SELECT id, session_id, goal_id, subtask_id, finding, created_timestamp,
                       finding_data, subject, impact, project_id
                FROM project_findings
                WHERE project_id = ?
                ORDER BY CASE
                    WHEN created_timestamp GLOB '[0-9]*.[0-9]*' OR created_timestamp GLOB '[0-9]*'
                    THEN CAST(created_timestamp AS REAL)
                    ELSE strftime('%s', created_timestamp)
                END DESC
            """
            params = (project_id,)

        cursor = self._execute(query, params)
        findings = [dict(row) for row in cursor.fetchall()]

        # Apply deprecation filtering
        from empirica.core.findings_deprecation import FindingsDeprecationEngine

        # Auto-depth based on uncertainty if requested
        if depth == "auto" and uncertainty is not None:
            if uncertainty > 0.5:
                depth = "full"
            elif uncertainty > 0.3:
                depth = "moderate"
            else:
                depth = "minimal"

        # Calculate relevance scores
        relevance_scores = [
            FindingsDeprecationEngine.calculate_relevance_score(f)
            for f in findings
        ]

        # Filter by depth
        filtered = FindingsDeprecationEngine.filter_by_depth(
            findings,
            depth=depth,
            relevance_scores=relevance_scores,
            uncertainty=uncertainty or 0.5
        )

        # Apply limit if specified
        if limit:
            filtered = filtered[:limit]

        return filtered

    def get_project_unknowns(self, project_id: str, resolved: bool | None = None, subject: str | None = None, limit: int | None = None) -> list[dict]:
        """Get unknowns for a project (project-scoped)."""
        query = """
            SELECT id, session_id, goal_id, subtask_id, unknown, is_resolved, resolved_by,
                   created_timestamp, resolved_timestamp, unknown_data, subject, impact, project_id
            FROM project_unknowns
            WHERE project_id = ?
        """
        params: list = [project_id]

        if subject:
            query += " AND subject = ?"
            params.append(subject)

        if resolved is not None:
            query += " AND is_resolved = ?"
            params.append(resolved)

        query += " ORDER BY created_timestamp DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor = self._execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    def get_project_dead_ends(self, project_id: str, limit: int | None = None, subject: str | None = None) -> list[dict]:
        """Get all dead ends for a project (project-scoped)."""
        query = """
            SELECT id, session_id, goal_id, subtask_id, approach, why_failed,
                   created_timestamp, dead_end_data, subject, project_id
            FROM project_dead_ends
            WHERE project_id = ?
        """
        params: list = [project_id]

        if subject:
            query += " AND subject = ?"
            params.append(subject)

        query += " ORDER BY created_timestamp DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor = self._execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    def get_project_reference_docs(self, project_id: str) -> list[dict]:
        """Get all reference docs for a project"""
        cursor = self._execute("""
            SELECT * FROM project_reference_docs
            WHERE project_id = ?
            ORDER BY created_timestamp DESC
        """, (project_id,))
        return [dict(row) for row in cursor.fetchall()]

    def log_mistake(
        self,
        session_id: str,
        mistake: str,
        why_wrong: str,
        cost_estimate: str | None = None,
        root_cause_vector: str | None = None,
        prevention: str | None = None,
        goal_id: str | None = None,
        project_id: str | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
    ) -> str:
        """
        Log a mistake for learning and future prevention.

        Args:
            session_id: Session identifier
            mistake: What was done wrong
            why_wrong: Explanation of why it was wrong
            cost_estimate: Estimated time/effort wasted (e.g., "2 hours")
            root_cause_vector: Epistemic vector that caused the mistake (e.g., "KNOW", "CONTEXT")
            prevention: How to prevent this mistake in the future
            goal_id: Optional goal identifier this mistake relates to
            transaction_id: Optional epistemic transaction ID (auto-derived if not provided).
            entity_type: Entity type (project, organization, contact, engagement).
            entity_id: Entity UUID.

        Returns:
            mistake_id: UUID string
        """
        mistake_id = str(uuid.uuid4())

        if not entity_type:
            entity_type = 'project'
        if not entity_id and entity_type == 'project':
            entity_id = project_id

        # Build mistake_data JSON
        mistake_data = {
            "mistake": mistake,
            "why_wrong": why_wrong,
            "cost_estimate": cost_estimate,
            "root_cause_vector": root_cause_vector,
            "prevention": prevention,
            "transaction_id": transaction_id
        }

        visibility_tier = normalize_visibility(visibility)
        source_tag = normalize_epistemic_source(epistemic_source)

        self._execute("""
            INSERT INTO mistakes_made (
                id, session_id, goal_id, project_id, mistake, why_wrong,
                cost_estimate, root_cause_vector, prevention,
                created_timestamp, mistake_data, transaction_id,
                entity_type, entity_id, visibility, epistemic_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            mistake_id, session_id, goal_id, project_id, mistake, why_wrong,
            cost_estimate, root_cause_vector, prevention,
            time.time(), json.dumps(mistake_data), transaction_id,
            entity_type, entity_id, visibility_tier, source_tag
        ))

        self.commit()
        logger.info(f"📝 Mistake logged: {mistake[:50]}...")

        return mistake_id

    def get_mistakes(
        self,
        session_id: str | None = None,
        goal_id: str | None = None,
        limit: int = 10
    ) -> list[dict]:
        """
        Retrieve logged mistakes.

        Args:
            session_id: Optional filter by session
            goal_id: Optional filter by goal
            limit: Maximum number of results

        Returns:
            List of mistake dictionaries
        """
        if session_id and goal_id:
            cursor = self._execute("""
                SELECT * FROM mistakes_made
                WHERE session_id = ? AND goal_id = ?
                ORDER BY created_timestamp DESC
                LIMIT ?
            """, (session_id, goal_id, limit))
        elif session_id:
            cursor = self._execute("""
                SELECT * FROM mistakes_made
                WHERE session_id = ?
                ORDER BY created_timestamp DESC
                LIMIT ?
            """, (session_id, limit))
        elif goal_id:
            cursor = self._execute("""
                SELECT * FROM mistakes_made
                WHERE goal_id = ?
                ORDER BY created_timestamp DESC
                LIMIT ?
            """, (goal_id, limit))
        else:
            cursor = self._execute("""
                SELECT * FROM mistakes_made
                ORDER BY created_timestamp DESC
                LIMIT ?
            """, (limit,))

        return [dict(row) for row in cursor.fetchall()]

    def get_project_mistakes(self, project_id: str, limit: int | None = None) -> list[dict]:
        """Get mistakes for a project (uses direct project_id column)"""
        query = """
            SELECT mistake, prevention, cost_estimate, root_cause_vector, created_timestamp
            FROM mistakes_made
            WHERE project_id = ?
            ORDER BY created_timestamp DESC
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor = self._execute(query, (project_id,))
        return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Assumptions (epistemic intent layer)
    # =========================================================================

    def log_assumption(
        self,
        project_id: str,
        session_id: str,
        assumption: str,
        confidence: float = 0.5,
        domain: str | None = None,
        goal_id: str | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
    ) -> str:
        """Log an unverified belief to the assumptions table."""
        assumption_id = str(uuid.uuid4())

        if not entity_type:
            entity_type = 'project'
        if not entity_id and entity_type == 'project':
            entity_id = project_id

        visibility_tier = normalize_visibility(visibility)
        source_tag = normalize_epistemic_source(epistemic_source)

        self._execute("""
            INSERT INTO assumptions (
                id, assumption, confidence, status,
                entity_type, entity_id, project_id, session_id,
                transaction_id, goal_id, created_timestamp, visibility, epistemic_source
            ) VALUES (?, ?, ?, 'unverified', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            assumption_id, assumption, confidence,
            entity_type, entity_id, project_id, session_id,
            transaction_id, goal_id, time.time(), visibility_tier, source_tag
        ))

        self.commit()
        return assumption_id

    # =========================================================================
    # Decisions (epistemic intent layer)
    # =========================================================================

    def log_decision(
        self,
        project_id: str,
        session_id: str,
        choice: str,
        rationale: str,
        alternatives: str | None = None,
        confidence: float = 0.7,
        reversibility: str = 'exploratory',
        goal_id: str | None = None,
        transaction_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        evidence_refs: list[str] | None = None,
        visibility: str | None = None,
        epistemic_source: str | None = None,
    ) -> str:
        """Log a decision choice point to the decisions table."""
        decision_id = str(uuid.uuid4())

        if not entity_type:
            entity_type = 'project'
        if not entity_id and entity_type == 'project':
            entity_id = project_id

        evidence_refs_json = json.dumps(evidence_refs) if evidence_refs else None
        visibility_tier = normalize_visibility(visibility)
        source_tag = normalize_epistemic_source(epistemic_source)

        self._execute("""
            INSERT INTO decisions (
                id, choice, alternatives, rationale,
                confidence_at_decision, reversibility,
                entity_type, entity_id, project_id, session_id,
                transaction_id, goal_id, created_timestamp, evidence_refs, visibility,
                epistemic_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            decision_id, choice, alternatives, rationale,
            confidence, reversibility,
            entity_type, entity_id, project_id, session_id,
            transaction_id, goal_id, time.time(), evidence_refs_json, visibility_tier,
            source_tag
        ))

        self.commit()
        return decision_id
