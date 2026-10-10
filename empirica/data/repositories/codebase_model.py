"""
Codebase Model Data Repository

Thin ORM layer for codebase entity, fact, relationship, and constraint storage.
All operations use the shared sessions.db connection via BaseRepository.
"""

import json
import time
import uuid
from fnmatch import fnmatch
from typing import Any

from .base import BaseRepository


class CodebaseModelRepository(BaseRepository):
    """Data-layer repository for codebase model entities, facts, and constraints."""

    # ========================================================================
    # Entity Operations
    # ========================================================================

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        file_path: str | None = None,
        signature: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create or update an entity. Returns entity ID.

        If an entity with the same (name, file_path, entity_type, project_id) exists,
        updates it and marks it active again; otherwise creates a new entity.
        """
        now = time.time()

        # Check for existing entity. An active row wins over an invalidated twin, then the newest.
        cursor = self._execute(
            """SELECT id FROM codebase_entities
               WHERE name = ? AND entity_type = ? AND file_path IS ?
               AND project_id IS ?
               ORDER BY (last_seen IS NOT NULL), first_seen DESC""",
            (name, entity_type, file_path, project_id),
        )
        existing = cursor.fetchone()

        if existing:
            entity_id = existing[0] if isinstance(existing, tuple) else existing["id"]
            # Update signature/metadata and clear last_seen: last_seen is set by invalidate_entity() when the
            # entity is removed, so an upsert (the entity exists again) has to undo that. Leaving it set kept
            # a removed-then-re-added entity invalidated for good.
            self._execute(
                """UPDATE codebase_entities
                   SET signature = COALESCE(?, signature),
                       metadata = COALESCE(?, metadata),
                       last_seen = NULL
                   WHERE id = ?""",
                (signature, json.dumps(metadata) if metadata else None, entity_id),
            )
            self.commit()
            return entity_id

        entity_id = str(uuid.uuid4())
        self._execute(
            """INSERT INTO codebase_entities
               (id, entity_type, name, file_path, signature, first_seen, last_seen,
                project_id, session_id, metadata)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
            (
                entity_id,
                entity_type,
                name,
                file_path,
                signature,
                now,
                project_id,
                session_id,
                json.dumps(metadata or {}),
            ),
        )
        self.commit()
        return entity_id

    def find_entities(
        self,
        project_id: str | None = None,
        entity_type: str | None = None,
        file_path: str | None = None,
        name_like: str | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Query entities with optional filters."""
        query = "SELECT * FROM codebase_entities WHERE 1=1"
        params: list = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)
        if file_path:
            query += " AND file_path = ?"
            params.append(file_path)
        if name_like:
            query += " AND name LIKE ?"
            params.append(f"%{name_like}%")
        if active_only:
            query += " AND last_seen IS NULL"

        query += " ORDER BY first_seen DESC"
        cursor = self._execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    def invalidate_entity(self, entity_id: str) -> None:
        """Mark an entity as no longer existing (sets last_seen)."""
        self._execute(
            "UPDATE codebase_entities SET last_seen = ? WHERE id = ?",
            (time.time(), entity_id),
        )
        self.commit()

    def count_entities(self, project_id: str, active_only: bool = True) -> int:
        """Count entities for a project."""
        query = "SELECT COUNT(*) FROM codebase_entities WHERE project_id = ?"
        params: list = [project_id]
        if active_only:
            query += " AND last_seen IS NULL"
        cursor = self._execute(query, tuple(params))
        return cursor.fetchone()[0]

    def entities_for_file(self, file_path: str, project_id: str | None = None) -> list[dict[str, Any]]:
        """Get all active entities in a file."""
        query = "SELECT * FROM codebase_entities WHERE file_path = ? AND last_seen IS NULL"
        params: list = [file_path]
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        cursor = self._execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    # ========================================================================
    # Fact Operations
    # ========================================================================

    def create_fact(
        self,
        fact_text: str,
        evidence_type: str = "source_code",
        evidence_path: str = "",
        entity_ids: list[str] | None = None,
        confidence: float = 1.0,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Create a new fact. Returns fact ID."""
        fact_id = str(uuid.uuid4())
        self._execute(
            """INSERT INTO codebase_facts
               (id, fact_text, valid_at, invalid_at, status, entity_ids,
                evidence_type, evidence_path, confidence, project_id, session_id)
               VALUES (?, ?, ?, NULL, 'canonical', ?, ?, ?, ?, ?, ?)""",
            (
                fact_id,
                fact_text,
                time.time(),
                json.dumps(entity_ids or []),
                evidence_type,
                evidence_path,
                confidence,
                project_id,
                session_id,
            ),
        )
        self.commit()
        return fact_id

    def query_facts(
        self,
        project_id: str | None = None,
        entity_id: str | None = None,
        current_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Query facts with optional filters."""
        query = "SELECT * FROM codebase_facts WHERE 1=1"
        params: list = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if current_only:
            query += " AND invalid_at IS NULL"

        query += " ORDER BY confidence DESC, valid_at DESC LIMIT 50"
        cursor = self._execute(query, tuple(params))
        rows = [dict(row) for row in cursor.fetchall()]

        # Filter by entity_id in Python (entity_ids is JSON array)
        if entity_id:
            rows = [r for r in rows if entity_id in json.loads(r.get("entity_ids", "[]"))]

        return rows

    def invalidate_fact(self, fact_id: str) -> None:
        """Mark a fact as no longer true."""
        self._execute(
            "UPDATE codebase_facts SET invalid_at = ?, status = 'superseded' WHERE id = ?",
            (time.time(), fact_id),
        )
        self.commit()

    def facts_for_file(self, file_path: str, project_id: str | None = None) -> list[dict[str, Any]]:
        """Get current facts with evidence pointing to a file."""
        query = """SELECT * FROM codebase_facts
                   WHERE evidence_path LIKE ? AND invalid_at IS NULL"""
        params: list = [f"{file_path}%"]
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY confidence DESC"
        cursor = self._execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    # ========================================================================
    # Relationship Operations
    # ========================================================================

    def upsert_relationship(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relationship_type: str,
        project_id: str | None = None,
    ) -> str:
        """Create or update a relationship. Increments evidence_count on update."""
        now = time.time()

        cursor = self._execute(
            """SELECT id, evidence_count FROM codebase_relationships
               WHERE source_entity_id = ? AND target_entity_id = ?
               AND relationship_type = ?""",
            (source_entity_id, target_entity_id, relationship_type),
        )
        existing = cursor.fetchone()

        if existing:
            rel_id = existing[0] if isinstance(existing, tuple) else existing["id"]
            count = (existing[1] if isinstance(existing, tuple) else existing["evidence_count"]) + 1
            self._execute(
                "UPDATE codebase_relationships SET last_seen = ?, evidence_count = ? WHERE id = ?",
                (now, count, rel_id),
            )
            self.commit()
            return rel_id

        rel_id = str(uuid.uuid4())
        self._execute(
            """INSERT INTO codebase_relationships
               (id, source_entity_id, target_entity_id, relationship_type,
                weight, first_seen, last_seen, evidence_count, project_id)
               VALUES (?, ?, ?, ?, 1.0, ?, ?, 1, ?)""",
            (rel_id, source_entity_id, target_entity_id, relationship_type, now, now, project_id),
        )
        self.commit()
        return rel_id

    def get_relationships(
        self,
        entity_id: str,
        direction: str = "outgoing",
    ) -> list[dict[str, Any]]:
        """Get relationships for an entity. direction: 'outgoing', 'incoming', or 'both'."""
        results = []
        if direction in ("outgoing", "both"):
            cursor = self._execute(
                "SELECT * FROM codebase_relationships WHERE source_entity_id = ?",
                (entity_id,),
            )
            results.extend(dict(row) for row in cursor.fetchall())
        if direction in ("incoming", "both"):
            cursor = self._execute(
                "SELECT * FROM codebase_relationships WHERE target_entity_id = ?",
                (entity_id,),
            )
            results.extend(dict(row) for row in cursor.fetchall())
        return results

    # ========================================================================
    # Constraint Operations
    # ========================================================================

    def upsert_constraint(
        self,
        rule_name: str,
        constraint_type: str = "convention",
        file_pattern: str | None = None,
        description: str = "",
        examples: list[dict[str, str]] | None = None,
        severity: str = "warning",
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Create or update a constraint. Increments violation_count on update."""
        now = time.time()

        cursor = self._execute(
            "SELECT id, violation_count, examples FROM codebase_constraints WHERE rule_name = ? AND project_id IS ?",
            (rule_name, project_id),
        )
        existing = cursor.fetchone()

        if existing:
            cid = existing[0] if isinstance(existing, tuple) else existing["id"]
            count = (existing[1] if isinstance(existing, tuple) else existing["violation_count"]) + 1
            old_examples = json.loads((existing[2] if isinstance(existing, tuple) else existing["examples"]) or "[]")
            all_examples = old_examples + (examples or [])
            self._execute(
                """UPDATE codebase_constraints
                   SET violation_count = ?, last_violated = ?, examples = ?, description = ?
                   WHERE id = ?""",
                (count, now, json.dumps(all_examples), description, cid),
            )
            self.commit()
            return cid

        cid = str(uuid.uuid4())
        self._execute(
            """INSERT INTO codebase_constraints
               (id, constraint_type, rule_name, file_pattern, description,
                violation_count, last_violated, examples, severity,
                project_id, session_id, created_at)
               VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, ?)""",
            (
                cid,
                constraint_type,
                rule_name,
                file_pattern,
                description,
                json.dumps(examples or []),
                severity,
                project_id,
                session_id,
                now,
            ),
        )
        self.commit()
        return cid

    def get_constraints(
        self,
        file_path: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get constraints, optionally filtered by file glob pattern."""
        query = "SELECT * FROM codebase_constraints WHERE 1=1"
        params: list = []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY violation_count DESC"

        cursor = self._execute(query, tuple(params))
        constraints = [dict(row) for row in cursor.fetchall()]

        # File pattern matching in Python (glob)
        if file_path:
            constraints = [
                c for c in constraints if not c.get("file_pattern") or self._glob_match(file_path, c["file_pattern"])
            ]

        return constraints

    @staticmethod
    def _glob_match(path: str, pattern: str) -> bool:
        """Match a file path against a glob pattern."""
        if "**" in pattern:
            flat = pattern.replace("**/", "")
            recursive = pattern.replace("**", "*")
            return fnmatch(path, flat) or fnmatch(path, recursive)
        return fnmatch(path, pattern)

    # ========================================================================
    # Aggregate Queries (for grounded calibration evidence)
    # ========================================================================

    def session_entity_stats(self, session_id: str) -> dict[str, int]:
        """Get entity statistics for a session (for grounded calibration)."""
        cursor = self._execute(
            "SELECT entity_type, COUNT(*) FROM codebase_entities WHERE session_id = ? GROUP BY entity_type",
            (session_id,),
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

    def session_fact_count(self, session_id: str) -> int:
        """Count facts created in a session."""
        cursor = self._execute(
            "SELECT COUNT(*) FROM codebase_facts WHERE session_id = ?",
            (session_id,),
        )
        return cursor.fetchone()[0]

    def project_entity_count(self, project_id: str) -> int:
        """Count active entities in a project."""
        return self.count_entities(project_id, active_only=True)
