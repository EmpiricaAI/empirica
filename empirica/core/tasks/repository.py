#!/usr/bin/env python3
"""
Task Repository - Database operations for SubTask persistence

Provides CRUD operations for tasks and decompositions.
MVP implementation: Simple database operations for task tracking.
"""

import json
import logging

from empirica.data.session_database import SessionDatabase

from .types import EpistemicImportance, SubTask, TaskDecomposition, TaskStatus

logger = logging.getLogger(__name__)


class TaskRepository:
    """Database operations for Task persistence"""

    def __init__(self, db_path: str | None = None):
        """
        Initialize repository

        Args:
            db_path: Optional custom database path
        """
        self.db = SessionDatabase(db_path=db_path)
        self._ensure_tables()

    def _resolve_subtask_id(self, subtask_id: str) -> str | None:
        """
        Resolve subtask ID (partial or full) to full UUID, verifying it exists.

        Args:
            subtask_id: Partial (8+ chars) or full UUID

        Returns:
            Full UUID or None if not found
        """
        try:
            cursor = self.db.conn.execute(
                "SELECT id FROM subtasks WHERE id LIKE ? ORDER BY created_timestamp DESC",
                (f"{subtask_id}%",)
            )
            results = cursor.fetchall()

            if not results:
                logger.warning(f"No subtask found matching: {subtask_id}")
                return None

            if len(results) > 1:
                logger.warning(f"Multiple subtasks match '{subtask_id}' - using most recent")

            resolved = results[0][0]
            logger.debug(f"Resolved subtask ID '{subtask_id}' to {resolved}")
            return resolved

        except Exception as e:
            logger.error(f"Error resolving subtask ID {subtask_id}: {e}")
            return None

    def _ensure_tables(self):
        """Create task-related tables if they don't exist"""
        try:
            # SubTasks table
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS subtasks (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    epistemic_importance TEXT NOT NULL,
                    estimated_tokens INTEGER,
                    actual_tokens INTEGER,
                    completion_evidence TEXT,
                    notes TEXT,
                    created_timestamp REAL NOT NULL,
                    completed_timestamp REAL,
                    subtask_data TEXT NOT NULL,
                    FOREIGN KEY (goal_id) REFERENCES goals(id)
                )
            """)

            # Task dependencies table
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS subtask_dependencies (
                    subtask_id TEXT NOT NULL,
                    depends_on_subtask_id TEXT NOT NULL,
                    PRIMARY KEY (subtask_id, depends_on_subtask_id),
                    FOREIGN KEY (subtask_id) REFERENCES subtasks(id),
                    FOREIGN KEY (depends_on_subtask_id) REFERENCES subtasks(id)
                )
            """)

            # Task decompositions (metadata)
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS task_decompositions (
                    goal_id TEXT PRIMARY KEY,
                    total_estimated_tokens INTEGER,
                    created_timestamp REAL NOT NULL,
                    decomposition_data TEXT NOT NULL,
                    FOREIGN KEY (goal_id) REFERENCES goals(id)
                )
            """)

            self.db.conn.commit()
            logger.info("Task tables ensured in database")

        except Exception as e:
            logger.error(f"Error creating task tables: {e}")
            raise

    def save_subtask(self, subtask: SubTask) -> bool:
        """
        Save subtask to database

        Args:
            subtask: SubTask object to save

        Returns:
            True if successful
        """
        try:
            # Serialize full subtask as JSON
            subtask_data = json.dumps(subtask.to_dict())

            # Insert main subtask record
            self.db.conn.execute("""
                INSERT OR REPLACE INTO subtasks
                (id, goal_id, description, status, epistemic_importance,
                 estimated_tokens, actual_tokens, completion_evidence, notes,
                 created_timestamp, completed_timestamp, subtask_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                subtask.id,
                subtask.goal_id,
                subtask.description,
                subtask.status.value,
                subtask.epistemic_importance.value,
                subtask.estimated_tokens,
                subtask.actual_tokens,
                subtask.completion_evidence,
                subtask.notes,
                subtask.created_timestamp,
                subtask.completed_timestamp,
                subtask_data
            ))

            # Insert dependencies (delete old ones first)
            self.db.conn.execute(
                "DELETE FROM subtask_dependencies WHERE subtask_id = ?",
                (subtask.id,)
            )
            for dep_id in subtask.dependencies:
                self.db.conn.execute("""
                    INSERT INTO subtask_dependencies
                    (subtask_id, depends_on_subtask_id)
                    VALUES (?, ?)
                """, (subtask.id, dep_id))

            self.db.conn.commit()
            logger.info(f"Saved subtask {subtask.id}: {subtask.description[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Error saving subtask {subtask.id}: {e}")
            self.db.conn.rollback()
            return False

    def get_subtask(self, subtask_id: str) -> SubTask | None:
        """
        Retrieve subtask by ID (supports partial UUID)

        Args:
            subtask_id: SubTask identifier (partial or full UUID)

        Returns:
            SubTask object or None if not found
        """
        try:
            # Resolve partial UUID to full UUID
            resolved_id = self._resolve_subtask_id(subtask_id)
            if not resolved_id:
                return None

            cursor = self.db.conn.execute(
                "SELECT subtask_data FROM subtasks WHERE id = ?",
                (resolved_id,)
            )
            row = cursor.fetchone()

            if row:
                subtask_dict = json.loads(row[0])
                return SubTask.from_dict(subtask_dict)

            return None

        except Exception as e:
            logger.error(f"Error retrieving subtask {subtask_id}: {e}")
            return None

    def get_goal_subtasks(self, goal_id: str) -> list[SubTask]:
        """
        Retrieve all subtasks for a goal

        Args:
            goal_id: Goal identifier

        Returns:
            List of SubTask objects
        """
        try:
            cursor = self.db.conn.execute(
                "SELECT subtask_data FROM subtasks WHERE goal_id = ? ORDER BY created_timestamp",
                (goal_id,)
            )

            subtasks = []
            for row in cursor.fetchall():
                subtask_dict = json.loads(row[0])
                subtasks.append(SubTask.from_dict(subtask_dict))

            return subtasks

        except Exception as e:
            logger.error(f"Error retrieving goal subtasks: {e}")
            return []

    def update_subtask_status(
        self,
        subtask_id: str,
        status: TaskStatus,
        completion_evidence: str | None = None
    ) -> bool:
        """
        Update subtask status (supports partial UUID)

        Args:
            subtask_id: SubTask identifier (partial or full UUID)
            status: New status
            completion_evidence: Optional evidence (commit hash, etc.)

        Returns:
            True if successful
        """
        try:
            import time

            # Resolve partial UUID to full UUID
            resolved_id = self._resolve_subtask_id(subtask_id)
            if not resolved_id:
                logger.error(f"Cannot resolve subtask ID: {subtask_id}")
                return False

            timestamp = time.time() if status == TaskStatus.COMPLETED else None

            self.db.conn.execute("""
                UPDATE subtasks
                SET status = ?, completed_timestamp = ?, completion_evidence = ?
                WHERE id = ?
            """, (status.value, timestamp, completion_evidence, resolved_id))

            # Also update the subtask_data JSON
            subtask = self.get_subtask(resolved_id)
            if subtask:
                subtask.status = status
                subtask.completed_timestamp = timestamp
                if completion_evidence:
                    subtask.completion_evidence = completion_evidence
                subtask_data = json.dumps(subtask.to_dict())

                self.db.conn.execute(
                    "UPDATE subtasks SET subtask_data = ? WHERE id = ?",
                    (subtask_data, resolved_id)
                )

            self.db.conn.commit()
            logger.info(f"Updated subtask {resolved_id} status: {status.value}")
            return True

        except Exception as e:
            logger.error(f"Error updating subtask status: {e}")
            self.db.conn.rollback()
            return False

    def save_decomposition(self, decomposition: TaskDecomposition) -> bool:
        """
        Save task decomposition metadata

        Args:
            decomposition: TaskDecomposition object

        Returns:
            True if successful
        """
        try:
            # Save all subtasks first
            for subtask in decomposition.subtasks:
                self.save_subtask(subtask)

            # Save decomposition metadata
            decomposition_data = json.dumps(decomposition.to_dict())

            self.db.conn.execute("""
                INSERT OR REPLACE INTO task_decompositions
                (goal_id, total_estimated_tokens, created_timestamp, decomposition_data)
                VALUES (?, ?, ?, ?)
            """, (
                decomposition.goal_id,
                decomposition.total_estimated_tokens,
                decomposition.created_timestamp,
                decomposition_data
            ))

            self.db.conn.commit()
            logger.info(f"Saved decomposition for goal {decomposition.goal_id}")
            return True

        except Exception as e:
            logger.error(f"Error saving decomposition: {e}")
            self.db.conn.rollback()
            return False

    def get_decomposition(self, goal_id: str) -> TaskDecomposition | None:
        """
        Retrieve task decomposition for a goal

        Args:
            goal_id: Goal identifier

        Returns:
            TaskDecomposition object or None if not found
        """
        try:
            cursor = self.db.conn.execute(
                "SELECT decomposition_data FROM task_decompositions WHERE goal_id = ?",
                (goal_id,)
            )
            row = cursor.fetchone()

            if row:
                decomposition_dict = json.loads(row[0])
                return TaskDecomposition.from_dict(decomposition_dict)

            return None

        except Exception as e:
            logger.error(f"Error retrieving decomposition: {e}")
            return None

    def query_subtasks(
        self,
        goal_id: str | None = None,
        status: TaskStatus | None = None,
        epistemic_importance: EpistemicImportance | None = None
    ) -> list[SubTask]:
        """
        Query subtasks with filters

        Args:
            goal_id: Filter by goal
            status: Filter by status
            epistemic_importance: Filter by importance

        Returns:
            List of matching SubTask objects
        """
        try:
            query = "SELECT subtask_data FROM subtasks WHERE 1=1"
            params = []

            if goal_id:
                query += " AND goal_id = ?"
                params.append(goal_id)

            if status:
                query += " AND status = ?"
                params.append(status.value)

            if epistemic_importance:
                query += " AND epistemic_importance = ?"
                params.append(epistemic_importance.value)

            query += " ORDER BY created_timestamp"

            cursor = self.db.conn.execute(query, params)

            subtasks = []
            for row in cursor.fetchall():
                subtask_dict = json.loads(row[0])
                subtasks.append(SubTask.from_dict(subtask_dict))

            return subtasks

        except Exception as e:
            logger.error(f"Error querying subtasks: {e}")
            return []

    def close(self):
        """Close database connection"""
        self.db.close()
