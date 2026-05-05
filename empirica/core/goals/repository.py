#!/usr/bin/env python3
"""
Goal Repository - Database operations for Goal persistence

Provides CRUD operations for structured goals with full serialization.
MVP implementation: Simple database operations, no complex queries yet.
"""

import json
import logging
from typing import Any

from empirica.data.session_database import SessionDatabase

from .types import Goal, ScopeVector

logger = logging.getLogger(__name__)


class GoalRepository:
    """Database operations for Goal persistence"""

    def __init__(self, db_path: str | None = None):
        """
        Initialize repository

        Args:
            db_path: Optional custom database path
        """
        self.db = SessionDatabase(db_path=db_path)
        self._ensure_tables()

    def _ensure_tables(self):
        """Create goal-related tables if they don't exist"""
        try:
            # Goals table
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    objective TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    estimated_complexity REAL,
                    created_timestamp REAL NOT NULL,
                    completed_timestamp REAL,
                    is_completed BOOLEAN DEFAULT 0,
                    goal_data TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)

            # Success criteria table (normalized)
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS success_criteria (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    validation_method TEXT NOT NULL,
                    threshold REAL,
                    is_required BOOLEAN DEFAULT 1,
                    is_met BOOLEAN DEFAULT 0,
                    FOREIGN KEY (goal_id) REFERENCES goals(id)
                )
            """)

            # Dependencies table (normalized)
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS goal_dependencies (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    depends_on_goal_id TEXT NOT NULL,
                    dependency_type TEXT NOT NULL,
                    description TEXT,
                    FOREIGN KEY (goal_id) REFERENCES goals(id),
                    FOREIGN KEY (depends_on_goal_id) REFERENCES goals(id)
                )
            """)

            self.db.conn.commit()
            logger.info("Goal tables ensured in database")

        except Exception as e:
            logger.error(f"Error creating goal tables: {e}")
            raise

    def save_goal(self, goal: Goal, session_id: str | None = None, transaction_id: str | None = None) -> bool:
        """
        Save goal to database

        Args:
            goal: Goal object to save
            session_id: Optional session ID to associate with goal
            transaction_id: Optional transaction ID for epistemic linkage

        Returns:
            True if successful
        """
        try:
            # Serialize full goal as JSON for easy retrieval
            goal_data = json.dumps(goal.to_dict())

            # Resolve project_id from session (goals inherit project scope)
            project_id = None
            if session_id:
                row = self.db.conn.execute(
                    "SELECT project_id FROM sessions WHERE session_id = ?",
                    (session_id,)
                ).fetchone()
                if row:
                    project_id = row[0]

            # Insert main goal record
            self.db.conn.execute("""
                INSERT OR REPLACE INTO goals
                (id, session_id, objective, scope, estimated_complexity,
                 created_timestamp, completed_timestamp, is_completed, goal_data, project_id, transaction_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                goal.id,
                session_id,
                goal.objective,
                json.dumps(goal.scope.to_dict()),
                goal.estimated_complexity,
                goal.created_timestamp,
                goal.completed_timestamp,
                goal.is_completed,
                goal_data,
                project_id,
                transaction_id
            ))

            # Insert success criteria (delete old ones first)
            self.db.conn.execute("DELETE FROM success_criteria WHERE goal_id = ?", (goal.id,))
            for sc in goal.success_criteria:
                self.db.conn.execute("""
                    INSERT INTO success_criteria
                    (id, goal_id, description, validation_method, threshold, is_required, is_met)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    sc.id,
                    goal.id,
                    sc.description,
                    sc.validation_method,
                    sc.threshold,
                    sc.is_required,
                    sc.is_met
                ))

            # Insert dependencies (delete old ones first)
            self.db.conn.execute("DELETE FROM goal_dependencies WHERE goal_id = ?", (goal.id,))
            for dep in goal.dependencies:
                self.db.conn.execute("""
                    INSERT INTO goal_dependencies
                    (id, goal_id, depends_on_goal_id, dependency_type, description)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    dep.id,
                    goal.id,
                    dep.goal_id,
                    dep.dependency_type.value,
                    dep.description
                ))

            self.db.conn.commit()
            logger.info(f"Saved goal {goal.id}: {goal.objective[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Error saving goal {goal.id}: {e}")
            self.db.conn.rollback()
            return False

    def get_goal(self, goal_id: str) -> Goal | None:
        """
        Retrieve goal by ID (supports short ID prefix matching)

        Args:
            goal_id: Goal identifier (full UUID or short prefix like '8f5e49f2')

        Returns:
            Goal object or None if not found (also None if prefix is ambiguous)
        """
        try:
            # First try exact match
            cursor = self.db.conn.execute(
                "SELECT goal_data FROM goals WHERE id = ?",
                (goal_id,)
            )
            row = cursor.fetchone()

            if row:
                goal_dict = json.loads(row[0])
                return Goal.from_dict(goal_dict)

            # Fallback: prefix match for short IDs (like git short hashes)
            cursor = self.db.conn.execute(
                "SELECT goal_data FROM goals WHERE id LIKE ?",
                (f"{goal_id}%",)
            )
            rows = cursor.fetchall()

            if len(rows) == 1:
                # Unique prefix match
                goal_dict = json.loads(rows[0][0])
                return Goal.from_dict(goal_dict)
            elif len(rows) > 1:
                # Ambiguous prefix - log warning and return None
                logger.warning(f"Ambiguous goal prefix '{goal_id}' matches {len(rows)} goals")
                return None

            return None

        except Exception as e:
            logger.error(f"Error retrieving goal {goal_id}: {e}")
            return None

    def get_session_goals(self, session_id: str) -> list[Goal]:
        """
        Retrieve all goals for a session

        Args:
            session_id: Session identifier

        Returns:
            List of Goal objects
        """
        try:
            cursor = self.db.conn.execute(
                "SELECT goal_data FROM goals WHERE session_id = ? ORDER BY created_timestamp",
                (session_id,)
            )

            goals = []
            for row in cursor.fetchall():
                goal_dict = json.loads(row[0])
                goals.append(Goal.from_dict(goal_dict))

            return goals

        except Exception as e:
            logger.error(f"Error retrieving session goals: {e}")
            return []

    def get_transaction_goals(self, transaction_id: str) -> list[Goal]:
        """
        Retrieve all goals for an epistemic transaction.

        Goals are structurally project-scoped but temporally transaction-scoped.
        Transactions (PREFLIGHT→POSTFLIGHT measurement windows) span compaction
        boundaries, making them the natural scope for epistemic measurement.

        Args:
            transaction_id: Transaction UUID

        Returns:
            List of Goal objects created within this transaction
        """
        try:
            cursor = self.db.conn.execute(
                "SELECT goal_data FROM goals WHERE transaction_id = ? ORDER BY created_timestamp",
                (transaction_id,)
            )

            goals = []
            for row in cursor.fetchall():
                goal_dict = json.loads(row[0])
                goals.append(Goal.from_dict(goal_dict))

            return goals

        except Exception as e:
            logger.error(f"Error retrieving transaction goals: {e}")
            return []

    def query_goals_by_transaction(
        self,
        transaction_id: str,
        is_completed: bool | None = None,
        project_id: str | None = None
    ) -> list[Goal]:
        """
        Query goals filtered by transaction with optional secondary filters.

        Args:
            transaction_id: Transaction UUID (required)
            is_completed: Optional filter by completion status
            project_id: Optional filter by project (usually redundant since
                        transactions are project-scoped, but useful for validation)

        Returns:
            List of matching Goal objects
        """
        try:
            conditions = ["transaction_id = ?"]
            params: list = [transaction_id]

            if is_completed is not None:
                conditions.append("is_completed = ?")
                params.append(1 if is_completed else 0)

            if project_id:
                conditions.append("project_id = ?")
                params.append(project_id)

            where_clause = " AND ".join(conditions)
            cursor = self.db.conn.execute(
                f"SELECT goal_data FROM goals WHERE {where_clause} ORDER BY created_timestamp",
                tuple(params)
            )

            goals = []
            for row in cursor.fetchall():
                goal_dict = json.loads(row[0])
                goals.append(Goal.from_dict(goal_dict))

            return goals

        except Exception as e:
            logger.error(f"Error querying transaction goals: {e}")
            return []

    def list_active_criteria_for_session(self, session_id: str):
        """Return (Goal, SuccessCriterion) pairs for active goals in a session.

        Active = not is_completed AND status != 'planned'. A goal with N
        criteria yields N tuples. Used by the POSTFLIGHT criterion-evaluator
        bridge to find which criteria need evaluation.
        """
        from .types import Goal

        try:
            cursor = self.db.conn.execute("""
                SELECT goal_data FROM goals
                WHERE session_id = ?
                  AND is_completed = 0
                  AND COALESCE(status, 'in_progress') != 'planned'
                ORDER BY created_timestamp
            """, (session_id,))
            rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"Error listing active criteria for session {session_id}: {e}")
            return []

        pairs = []
        for row in rows:
            try:
                goal = Goal.from_dict(json.loads(row[0]))
            except Exception as e:
                logger.debug(f"Skipping malformed goal_data: {e}")
                continue
            for sc in goal.success_criteria:
                pairs.append((goal, sc))
        return pairs

    def update_is_met(self, criterion_id: str, is_met: bool) -> bool:
        """Update is_met on a SuccessCriterion. Best-effort, no raise.

        Syncs both the normalized success_criteria row and the parent goal's
        goal_data JSON blob so reads from either path see consistent state.
        Returns True if the row was updated, False if not found or on error.
        """
        try:
            cursor = self.db.conn.execute(
                "UPDATE success_criteria SET is_met = ? WHERE id = ?",
                (1 if is_met else 0, criterion_id),
            )
            if cursor.rowcount == 0:
                logger.debug(f"No success_criterion row for id={criterion_id}")
                return False

            gid_row = self.db.conn.execute(
                "SELECT goal_id FROM success_criteria WHERE id = ?",
                (criterion_id,),
            ).fetchone()
            if gid_row:
                goal = self.get_goal(gid_row[0])
                if goal:
                    for sc in goal.success_criteria:
                        if sc.id == criterion_id:
                            sc.is_met = is_met
                            break
                    self.db.conn.execute(
                        "UPDATE goals SET goal_data = ? WHERE id = ?",
                        (json.dumps(goal.to_dict()), goal.id),
                    )

            self.db.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating is_met for criterion {criterion_id}: {e}")
            try:
                self.db.conn.rollback()
            except Exception as rb_err:
                logger.debug(f"Rollback also failed: {rb_err}")
            return False

    def update_goal_completion(self, goal_id: str, is_completed: bool = True) -> bool:
        """
        Update goal completion status

        Args:
            goal_id: Goal identifier
            is_completed: Completion status

        Returns:
            True if successful
        """
        try:
            import time
            timestamp = time.time() if is_completed else None

            status = 'completed' if is_completed else 'in_progress'
            self.db.conn.execute("""
                UPDATE goals
                SET is_completed = ?, completed_timestamp = ?, status = ?
                WHERE id = ?
            """, (is_completed, timestamp, status, goal_id))

            # Also update the goal_data JSON
            goal = self.get_goal(goal_id)
            if goal:
                goal.is_completed = is_completed
                goal.completed_timestamp = timestamp
                goal_data = json.dumps(goal.to_dict())

                self.db.conn.execute(
                    "UPDATE goals SET goal_data = ? WHERE id = ?",
                    (goal_data, goal_id)
                )

            self.db.conn.commit()
            logger.info(f"Updated goal {goal_id} completion: {is_completed}")
            return True

        except Exception as e:
            logger.error(f"Error updating goal completion: {e}")
            self.db.conn.rollback()
            return False

    def query_goals(
        self,
        session_id: str | None = None,
        is_completed: bool | None = None,
        scope: ScopeVector | None = None
    ) -> list[Goal]:
        """
        Query goals with filters

        Args:
            session_id: Filter by session
            is_completed: Filter by completion status
            scope: Filter by scope

        Returns:
            List of matching Goal objects
        """
        try:
            query = "SELECT goal_data FROM goals WHERE 1=1"
            params = []

            if session_id:
                query += " AND session_id = ?"
                params.append(session_id)

            if is_completed is not None:
                query += " AND is_completed = ?"
                params.append(is_completed)

            if scope:
                query += " AND scope = ?"
                params.append(json.dumps(scope.to_dict()))

            query += " ORDER BY created_timestamp DESC"

            cursor = self.db.conn.execute(query, params)

            goals = []
            for row in cursor.fetchall():
                goal_dict = json.loads(row[0])
                goals.append(Goal.from_dict(goal_dict))

            return goals

        except Exception as e:
            logger.error(f"Error querying goals: {e}")
            return []

    def close(self):
        """Close database connection"""
        self.db.close()

    def mark_goals_stale(self, session_id: str, stale_reason: str = "memory_compact") -> int:
        """Mark all in_progress goals for a session as stale

        Called during memory compaction to signal that the AI's full context
        about these goals has been lost. Post-compact AI should re-evaluate
        these goals before continuing work.

        Args:
            session_id: Session UUID
            stale_reason: Why goals are being marked stale (e.g., "memory_compact")

        Returns:
            Number of goals marked stale
        """
        import time
        try:
            # Find all in_progress goals for this session
            cursor = self.db.conn.execute("""
                SELECT id, goal_data FROM goals
                WHERE session_id = ? AND is_completed = 0
            """, (session_id,))

            count = 0
            for row in cursor.fetchall():
                goal_id = row[0]
                goal_data = json.loads(row[1]) if row[1] else {}

                # Add stale metadata to goal_data
                if 'metadata' not in goal_data:
                    goal_data['metadata'] = {}
                goal_data['metadata']['stale_since'] = time.time()
                goal_data['metadata']['stale_reason'] = stale_reason

                self.db.conn.execute("""
                    UPDATE goals
                    SET goal_data = ?
                    WHERE id = ?
                """, (json.dumps(goal_data), goal_id))
                count += 1

            self.db.conn.commit()
            logger.info(f"Marked {count} goals as stale for session {session_id[:8]}...")
            return count

        except Exception as e:
            logger.error(f"Error marking goals stale: {e}")
            self.db.conn.rollback()
            return 0

    def get_stale_goals(self, session_id: str | None = None, project_id: str | None = None) -> list[dict[str, Any]]:
        """Get stale goals for a session or project

        Args:
            session_id: Optional session UUID filter
            project_id: Optional project UUID filter (checks all sessions in project)

        Returns:
            List of stale goal dicts with stale_since metadata
        """
        try:
            if session_id:
                cursor = self.db.conn.execute("""
                    SELECT id, objective, scope, goal_data, created_timestamp
                    FROM goals
                    WHERE session_id = ? AND is_completed = 0
                    ORDER BY created_timestamp DESC
                """, (session_id,))
            elif project_id:
                cursor = self.db.conn.execute("""
                    SELECT g.id, g.objective, g.scope, g.goal_data, g.created_timestamp
                    FROM goals g
                    JOIN sessions s ON g.session_id = s.session_id
                    WHERE s.project_id = ? AND g.is_completed = 0
                    ORDER BY g.created_timestamp DESC
                """, (project_id,))
            else:
                return []

            stale_goals = []
            for row in cursor.fetchall():
                goal_data = json.loads(row[3]) if row[3] else {}
                metadata = goal_data.get('metadata', {})

                # Only include goals that have stale metadata
                if metadata.get('stale_since'):
                    stale_goals.append({
                        'goal_id': row[0],
                        'objective': row[1],
                        'scope': json.loads(row[2]) if row[2] else {},
                        'stale_since': metadata.get('stale_since'),
                        'stale_reason': metadata.get('stale_reason'),
                        'created_timestamp': row[4]
                    })

            return stale_goals

        except Exception as e:
            logger.error(f"Error getting stale goals: {e}")
            return []

    def refresh_goal(self, goal_id: str) -> bool:
        """Mark a stale goal as refreshed (AI has regained context)

        Args:
            goal_id: Goal UUID to refresh

        Returns:
            True if refreshed, False if goal not found or not stale
        """
        import time
        try:
            cursor = self.db.conn.execute("""
                SELECT goal_data FROM goals WHERE id = ? AND is_completed = 0
            """, (goal_id,))
            row = cursor.fetchone()

            if not row:
                return False

            goal_data = json.loads(row[0]) if row[0] else {}
            metadata = goal_data.get('metadata', {})

            # Check if goal was stale
            if not metadata.get('stale_since'):
                return False

            # Clear stale flag and add refresh timestamp
            metadata['refreshed_at'] = time.time()
            metadata.pop('stale_since', None)
            metadata.pop('stale_reason', None)
            goal_data['metadata'] = metadata

            self.db.conn.execute("""
                UPDATE goals SET goal_data = ? WHERE id = ?
            """, (json.dumps(goal_data), goal_id))

            self.db.conn.commit()
            logger.info(f"Refreshed goal {goal_id[:8]}...")
            return True

        except Exception as e:
            logger.error(f"Error refreshing goal: {e}")
            self.db.conn.rollback()
            return False
