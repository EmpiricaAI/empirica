#!/usr/bin/env python3
"""
Goal Repository - Database operations for Goal persistence

Provides CRUD operations for structured goals with full serialization.
MVP implementation: Simple database operations, no complex queries yet.
"""

import json
import logging
import time
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
                    description TEXT,
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
                    "SELECT project_id FROM sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
                if row:
                    project_id = row[0]

            # Insert main goal record
            self.db.conn.execute(
                """
                INSERT OR REPLACE INTO goals
                (id, session_id, objective, description, scope, estimated_complexity,
                 created_timestamp, completed_timestamp, is_completed, goal_data, project_id, transaction_id,
                 engagement_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    goal.id,
                    session_id,
                    goal.objective,
                    goal.description,
                    json.dumps(goal.scope.to_dict()),
                    goal.estimated_complexity,
                    goal.created_timestamp,
                    goal.completed_timestamp,
                    goal.is_completed,
                    goal_data,
                    project_id,
                    transaction_id,
                    goal.engagement_id,
                ),
            )

            # Insert success criteria (delete old ones first)
            self.db.conn.execute("DELETE FROM success_criteria WHERE goal_id = ?", (goal.id,))
            for sc in goal.success_criteria:
                self.db.conn.execute(
                    """
                    INSERT INTO success_criteria
                    (id, goal_id, description, validation_method, threshold, is_required, is_met)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (sc.id, goal.id, sc.description, sc.validation_method, sc.threshold, sc.is_required, sc.is_met),
                )

            # Insert dependencies (delete old ones first)
            self.db.conn.execute("DELETE FROM goal_dependencies WHERE goal_id = ?", (goal.id,))
            for dep in goal.dependencies:
                self.db.conn.execute(
                    """
                    INSERT INTO goal_dependencies
                    (id, goal_id, depends_on_goal_id, dependency_type, description)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (dep.id, goal.id, dep.goal_id, dep.dependency_type.value, dep.description),
                )

            self.db.conn.commit()
            logger.info(f"Saved goal {goal.id}: {goal.objective[:50]}...")
            return True

        except Exception as e:
            logger.error(f"Error saving goal {goal.id}: {e}")
            self.db.conn.rollback()
            return False

    # Shortest prefix `goals-list` prints, so the shortest a user could have copied.
    MIN_PREFIX_LEN = 8

    def _goal_from_row(self, row) -> Goal | None:
        """Build a Goal from a (id, goal_data) row, healing an id-less blob.

        `goal_data` is a serialized COPY; the `id` column is the identity. Measured
        2026-07-30: 88 of 1431 goals on this practice carry a blob with no `id` key.
        `Goal.from_dict` does `data["id"]`, so those raised KeyError, a broad
        `except` swallowed it into a log line, and the caller reported "Goal not
        found" for a row sitting right there — 6% of goals unaddressable, and the
        error surfaced as a bare `'id'` that named nothing.

        Injecting the column when the blob omits it is a repair, not a workaround:
        the column is authoritative and the blob is derived.
        """
        goal_id, blob = row[0], row[1]
        try:
            data = json.loads(blob) if blob else {}
        except (TypeError, ValueError) as e:
            logger.warning(f"Goal {goal_id}: goal_data is not valid JSON ({e}) — rebuilding from columns")
            data = {}
        if not isinstance(data, dict):
            data = {}

        # Fill anything the blob lacks from the COLUMNS, which are authoritative.
        # `goal_data` is a serialized cache and is literally `{}` for 88 of 1431
        # goals here — so those were entirely unreachable, reported as "Goal not
        # found" for rows whose objective and status sit in plain columns.
        if not data.get("id") or not data.get("objective"):
            cols = self._goal_columns(goal_id)
            if cols:
                data = {**cols, **{k: v for k, v in data.items() if v not in (None, "", [], {})}}
                data["id"] = goal_id

        try:
            return Goal.from_dict(data)
        except KeyError as e:
            # Name the missing field. A bare KeyError repr reads as `'id'` and sends
            # the reader hunting for a missing goal rather than a malformed record.
            logger.error(f"Goal {goal_id}: cannot deserialize — missing required field {e}")
            return None

    def _goal_columns(self, goal_id: str) -> dict | None:
        """Reconstruct the Goal-shaped fields from the goals TABLE.

        The columns are the durable record; `goal_data` is a derived blob that can be
        empty or partial. `success_criteria` genuinely lives only in the blob, so it
        comes back empty here — an empty list is honest (we do not know them) rather
        than fabricated, and every other field is real.
        """
        try:
            row = self.db.conn.execute(
                "SELECT objective, scope, estimated_complexity, created_timestamp, "
                "completed_timestamp, is_completed, engagement_id FROM goals WHERE id = ?",
                (goal_id,),
            ).fetchone()
        except Exception as e:
            logger.debug(f"Goal {goal_id}: column rebuild failed ({e})")
            return None
        if not row:
            return None

        try:
            scope = json.loads(row[1]) if row[1] else {}
        except (TypeError, ValueError):
            scope = {}
        # isinstance guard before the subset test: the scope COLUMN carries legacy
        # encodings too (a float, or a label like "project_wide"), and `set(0.7)`
        # raises. I added this exact tolerance to Goal.from_dict and then wrote the
        # same intolerance one function away — the legacy shapes live in BOTH the
        # blob and the column, so both readers need it.
        if not isinstance(scope, dict) or not {"breadth", "duration", "coordination"} <= set(scope):
            scope = {"breadth": 0.5, "duration": 0.5, "coordination": 0.5}

        return {
            "id": goal_id,
            "objective": row[0] or "",
            "success_criteria": [],
            "scope": scope,
            "estimated_complexity": row[2],
            "created_timestamp": row[3] or time.time(),
            "completed_timestamp": row[4],
            "is_completed": bool(row[5]),
            "engagement_id": row[6],
        }

    def get_goal(self, goal_id: str) -> Goal | None:
        """
        Retrieve goal by ID (supports short ID prefix matching)

        Args:
            goal_id: Goal identifier (full UUID or short prefix like '8f5e49f2')

        Returns:
            Goal object or None if not found (also None if prefix is ambiguous)
        """
        # An empty or whitespace id is an ARGUMENT error, not a lookup. Left to the
        # prefix path it becomes LIKE '%', which matches every goal — and resolves to
        # one of them whenever the table happens to hold exactly one.
        if not goal_id or not str(goal_id).strip():
            logger.warning("get_goal called with an empty goal_id — refusing to resolve")
            return None
        goal_id = str(goal_id).strip()

        try:
            # First try exact match. Select the id COLUMN too: it is the authoritative
            # identity, while `goal_data` is a serialized copy that can be incomplete.
            cursor = self.db.conn.execute("SELECT id, goal_data FROM goals WHERE id = ?", (goal_id,))
            row = cursor.fetchone()

            if row:
                return self._goal_from_row(row)

            # Fallback: prefix match for short IDs (like git short hashes).
            #
            # MINIMUM LENGTH is load-bearing. Without it a two-character fragment
            # resolved to whichever goal happened to start with it, and the caller
            # attached tracked work to an unrelated goal WITH A SUCCESS MESSAGE
            # (reported by cortex, 2026-07-30: `--goal-id 6a` from an empty shell
            # extraction landed a task under "Add MCP resource exposure to Cortex MCP").
            #
            # Silently parenting work to the wrong goal is worse than failing: it is
            # indistinguishable from not tracking the work at all, and nothing later
            # looks at that goal. 8 matches what `goals-list` actually prints, so it is
            # the shortest prefix a user could legitimately have copied.
            if len(goal_id) < self.MIN_PREFIX_LEN:
                logger.warning(
                    f"Goal id prefix '{goal_id}' is shorter than {self.MIN_PREFIX_LEN} characters — "
                    "refusing to prefix-match (too easy to hit the wrong goal)"
                )
                return None

            cursor = self.db.conn.execute("SELECT id, goal_data FROM goals WHERE id LIKE ?", (f"{goal_id}%",))
            rows = cursor.fetchall()

            if len(rows) == 1:
                return self._goal_from_row(rows[0])
            elif len(rows) > 1:
                # Ambiguous prefix — refuse rather than pick one.
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
                "SELECT goal_data FROM goals WHERE session_id = ? ORDER BY created_timestamp", (session_id,)
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
                "SELECT goal_data FROM goals WHERE transaction_id = ? ORDER BY created_timestamp", (transaction_id,)
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
        self, transaction_id: str, is_completed: bool | None = None, project_id: str | None = None
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
                f"SELECT goal_data FROM goals WHERE {where_clause} ORDER BY created_timestamp", tuple(params)
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
            cursor = self.db.conn.execute(
                """
                SELECT goal_data FROM goals
                WHERE session_id = ?
                  AND is_completed = 0
                  AND COALESCE(status, 'in_progress') != 'planned'
                ORDER BY created_timestamp
            """,
                (session_id,),
            )
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

    def add_success_criterion(
        self,
        goal_id: str,
        validation_method: str,
        description: str,
        threshold: float | None = None,
        is_required: bool = True,
    ) -> str | None:
        """Add a SuccessCriterion to an existing goal. Returns the new criterion ID.

        Inserts into the normalized success_criteria table AND syncs the
        parent goal's goal_data JSON so reads from either path see the
        new criterion. Returns None if the goal doesn't exist.
        """
        import uuid

        from .types import SuccessCriterion

        goal = self.get_goal(goal_id)
        if goal is None:
            logger.error(f"Cannot add criterion — goal not found: {goal_id}")
            return None

        crit_id = str(uuid.uuid4())
        try:
            self.db.conn.execute(
                """
                INSERT INTO success_criteria
                (id, goal_id, description, validation_method, threshold, is_required, is_met)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
                (crit_id, goal.id, description, validation_method, threshold, is_required),
            )

            new_sc = SuccessCriterion(
                id=crit_id,
                description=description,
                validation_method=validation_method,
                threshold=threshold,
                is_required=is_required,
                is_met=False,
            )
            goal.success_criteria.append(new_sc)
            self.db.conn.execute(
                "UPDATE goals SET goal_data = ? WHERE id = ?",
                (json.dumps(goal.to_dict()), goal.id),
            )

            self.db.conn.commit()
            return crit_id
        except Exception as e:
            logger.error(f"Error adding success_criterion to goal {goal_id}: {e}")
            try:
                self.db.conn.rollback()
            except Exception as rb_err:
                logger.debug(f"Rollback also failed: {rb_err}")
            return None

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

            status = "completed" if is_completed else "in_progress"
            self.db.conn.execute(
                """
                UPDATE goals
                SET is_completed = ?, completed_timestamp = ?, status = ?
                WHERE id = ?
            """,
                (is_completed, timestamp, status, goal_id),
            )

            # Also update the goal_data JSON
            goal = self.get_goal(goal_id)
            if goal:
                goal.is_completed = is_completed
                goal.completed_timestamp = timestamp
                goal_data = json.dumps(goal.to_dict())

                self.db.conn.execute("UPDATE goals SET goal_data = ? WHERE id = ?", (goal_data, goal_id))

            self.db.conn.commit()
            logger.info(f"Updated goal {goal_id} completion: {is_completed}")
            return True

        except Exception as e:
            logger.error(f"Error updating goal completion: {e}")
            self.db.conn.rollback()
            return False

    def query_goals(
        self, session_id: str | None = None, is_completed: bool | None = None, scope: ScopeVector | None = None
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
            cursor = self.db.conn.execute(
                """
                SELECT id, goal_data FROM goals
                WHERE session_id = ? AND is_completed = 0
            """,
                (session_id,),
            )

            count = 0
            for row in cursor.fetchall():
                goal_id = row[0]
                goal_data = json.loads(row[1]) if row[1] else {}

                # Membership is the wrong test: goals created through the normal path
                # serialise `"metadata": null`, so the key EXISTS and is None. The old
                # `"metadata" not in goal_data` guard skipped initialisation and the
                # next line raised on None — for 1277 of this practice's goals, i.e.
                # essentially all of them. The verb has never worked outside a fixture.
                if not isinstance(goal_data.get("metadata"), dict):
                    goal_data["metadata"] = {}
                goal_data["metadata"]["stale_since"] = time.time()
                goal_data["metadata"]["stale_reason"] = stale_reason

                self.db.conn.execute(
                    """
                    UPDATE goals
                    SET goal_data = ?
                    WHERE id = ?
                """,
                    (json.dumps(goal_data), goal_id),
                )
                count += 1

            self.db.conn.commit()
            logger.info(f"Marked {count} goals as stale for session {session_id[:8]}...")
            return count

        except Exception as e:
            # Roll back, then RAISE. Returning 0 here made a crash indistinguishable
            # from "no goals to mark" — the caller printed ok:true, marked 0, while
            # an exception scrolled past on stderr. The sole caller is the CLI
            # handler, which already wraps this in its own try/except, so failing
            # loudly costs nothing and buys an honest exit code.
            logger.error(f"Error marking goals stale: {e}")
            self.db.conn.rollback()
            raise

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
                cursor = self.db.conn.execute(
                    """
                    SELECT id, objective, scope, goal_data, created_timestamp
                    FROM goals
                    WHERE session_id = ? AND is_completed = 0
                    ORDER BY created_timestamp DESC
                """,
                    (session_id,),
                )
            elif project_id:
                cursor = self.db.conn.execute(
                    """
                    SELECT g.id, g.objective, g.scope, g.goal_data, g.created_timestamp
                    FROM goals g
                    JOIN sessions s ON g.session_id = s.session_id
                    WHERE s.project_id = ? AND g.is_completed = 0
                    ORDER BY g.created_timestamp DESC
                """,
                    (project_id,),
                )
            else:
                return []

            stale_goals = []
            for row in cursor.fetchall():
                goal_data = json.loads(row[3]) if row[3] else {}
                metadata = goal_data.get("metadata", {})

                # Only include goals that have stale metadata
                if metadata.get("stale_since"):
                    stale_goals.append(
                        {
                            "goal_id": row[0],
                            "objective": row[1],
                            "scope": json.loads(row[2]) if row[2] else {},
                            "stale_since": metadata.get("stale_since"),
                            "stale_reason": metadata.get("stale_reason"),
                            "created_timestamp": row[4],
                        }
                    )

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
            cursor = self.db.conn.execute(
                """
                SELECT goal_data FROM goals WHERE id = ? AND is_completed = 0
            """,
                (goal_id,),
            )
            row = cursor.fetchone()

            if not row:
                return False

            goal_data = json.loads(row[0]) if row[0] else {}
            metadata = goal_data.get("metadata", {})

            # Check if goal was stale
            if not metadata.get("stale_since"):
                return False

            # Clear stale flag and add refresh timestamp
            metadata["refreshed_at"] = time.time()
            metadata.pop("stale_since", None)
            metadata.pop("stale_reason", None)
            goal_data["metadata"] = metadata

            self.db.conn.execute(
                """
                UPDATE goals SET goal_data = ? WHERE id = ?
            """,
                (json.dumps(goal_data), goal_id),
            )

            self.db.conn.commit()
            logger.info(f"Refreshed goal {goal_id[:8]}...")
            return True

        except Exception as e:
            logger.error(f"Error refreshing goal: {e}")
            self.db.conn.rollback()
            return False
