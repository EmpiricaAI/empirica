"""
Goal and Subtask Repository

Manages goal trees and subtask investigation tracking for sessions.
Encapsulates all database operations for goals/subtasks domain.
"""

import json
import time
import uuid

from .base import BaseRepository


class GoalDataRepository(BaseRepository):
    """Data-layer repository for goal and subtask management.

    Note: This is the thin ORM layer used by SessionDatabase.
    For business logic and structured Goal objects, see
    empirica.core.goals.repository.GoalRepository.
    """

    @staticmethod
    def _dedupe_by_objective(items: list[dict]) -> list[dict]:
        """
        Deduplicate goals by objective text, keeping the most recent entry.

        Goals with the same objective may be created across multiple sessions.
        This method removes duplicates by objective text, keeping the newest.
        """
        seen = set()
        unique = []
        for item in items:
            objective = item.get('objective', '')
            if objective not in seen:
                seen.add(objective)
                unique.append(item)
        return unique

    def create_goal(self, session_id: str, objective: str, scope_breadth: float | None = None,
                   scope_duration: float | None = None, scope_coordination: float | None = None,
                   beads_issue_id: str | None = None, status: str = 'in_progress',
                   description: str | None = None) -> str:
        """Create a new goal for this session

        Args:
            session_id: Session UUID
            objective: Title-shaped statement of what you're trying to accomplish (~256 char cap)
            description: Optional rich body for context, success criteria detail, links (8000 char cap)
            scope_breadth: 0.0-1.0 (0=single file, 1=entire codebase)
            scope_duration: 0.0-1.0 (0=minutes, 1=months)
            scope_coordination: 0.0-1.0 (0=solo, 1=heavy multi-agent)
            beads_issue_id: Optional BEADS issue ID (e.g., "bd-a1b2")
            status: Initial status — 'planned' (logged, not started) or 'in_progress' (active)

        Returns:
            goal_id (UUID string)
        """
        if status not in ('planned', 'in_progress'):
            raise ValueError(f"Initial status must be 'planned' or 'in_progress', got '{status}'")

        goal_id = str(uuid.uuid4())

        # Build scope JSON from individual vectors
        scope_data = {
            'breadth': scope_breadth,
            'duration': scope_duration,
            'coordination': scope_coordination
        }

        self._execute("""
            INSERT INTO goals (id, session_id, objective, description, scope, status, created_timestamp, is_completed, goal_data, beads_issue_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (goal_id, session_id, objective, description, json.dumps(scope_data), status, time.time(), json.dumps({}), beads_issue_id))

        self.commit()
        return goal_id

    def create_subtask(self, goal_id: str, description: str, importance: str = 'medium') -> str:
        """Create a subtask within a goal

        Args:
            goal_id: Parent goal UUID
            description: What are you investigating/implementing?
            importance: 'critical' | 'high' | 'medium' | 'low'

        Returns:
            subtask_id (UUID string)
        """
        subtask_id = str(uuid.uuid4())

        # Build subtask_data JSON with investigation tracking
        subtask_data = {
            'findings': [],
            'unknowns': [],
            'dead_ends': []
        }

        self._execute("""
            INSERT INTO subtasks (id, goal_id, description, epistemic_importance, status, created_timestamp, subtask_data)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (subtask_id, goal_id, description, importance, time.time(), json.dumps(subtask_data)))

        self.commit()
        return subtask_id

    def update_subtask_findings(self, subtask_id: str, findings: list[str]):
        """Update findings for a subtask

        Args:
            subtask_id: Subtask UUID
            findings: List of finding strings
        """
        # Get current subtask_data
        cursor = self._execute("SELECT subtask_data FROM subtasks WHERE id = ?", (subtask_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Subtask {subtask_id} not found")

        subtask_data = json.loads(row[0])
        subtask_data['findings'] = findings

        self._execute("""
            UPDATE subtasks SET subtask_data = ? WHERE id = ?
        """, (json.dumps(subtask_data), subtask_id))

        self.commit()

    def update_subtask_unknowns(self, subtask_id: str, unknowns: list[str]):
        """Update unknowns for a subtask

        Args:
            subtask_id: Subtask UUID
            unknowns: List of unknown strings
        """
        # Get current subtask_data
        cursor = self._execute("SELECT subtask_data FROM subtasks WHERE id = ?", (subtask_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Subtask {subtask_id} not found")

        subtask_data = json.loads(row[0])
        subtask_data['unknowns'] = unknowns

        self._execute("""
            UPDATE subtasks SET subtask_data = ? WHERE id = ?
        """, (json.dumps(subtask_data), subtask_id))

        self.commit()

    def update_subtask_dead_ends(self, subtask_id: str, dead_ends: list[str]):
        """Update dead ends for a subtask

        Args:
            subtask_id: Subtask UUID
            dead_ends: List of dead end strings (e.g., "Attempted X - blocked by Y")
        """
        # Get current subtask_data
        cursor = self._execute("SELECT subtask_data FROM subtasks WHERE id = ?", (subtask_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Subtask {subtask_id} not found")

        subtask_data = json.loads(row[0])
        subtask_data['dead_ends'] = dead_ends

        self._execute("""
            UPDATE subtasks SET subtask_data = ? WHERE id = ?
        """, (json.dumps(subtask_data), subtask_id))

        self.commit()

    def complete_subtask(self, subtask_id: str, evidence: str):
        """Mark subtask as completed with evidence

        Args:
            subtask_id: Subtask UUID
            evidence: Evidence of completion (e.g., "Documented in design doc", "PR merged")
        """
        self._execute("""
            UPDATE subtasks
            SET status = 'completed',
                completion_evidence = ?,
                completed_timestamp = ?
            WHERE id = ?
        """, (evidence, time.time(), subtask_id))

        self.commit()

    def get_goal_tree(self, session_id: str) -> list[dict]:
        """Get complete goal tree for a session

        Returns list of goals with nested subtasks

        Args:
            session_id: Session UUID

        Returns:
            List of goal dicts, each with 'subtasks' list
        """
        cursor = self._execute("""
            SELECT id, objective, status, scope, estimated_complexity
            FROM goals WHERE session_id = ? ORDER BY created_timestamp
        """, (session_id,))

        goals = []
        for row in cursor.fetchall():
            goal_id = row[0]
            # Handle legacy scope formats: could be JSON dict, float, or string like "project_wide"
            scope_data = {}
            if row[3]:
                try:
                    parsed = json.loads(row[3])
                    if isinstance(parsed, dict):
                        scope_data = parsed
                    # If it's a float/int (legacy), ignore - scope_data stays {}
                except (json.JSONDecodeError, TypeError):
                    # Legacy string value like "project_wide" - ignore
                    pass

            # Get subtasks for this goal
            subtask_cursor = self._execute("""
                SELECT id, description, epistemic_importance, status, subtask_data
                FROM subtasks WHERE goal_id = ? ORDER BY created_timestamp
            """, (goal_id,))

            subtasks = []
            for sub_row in subtask_cursor.fetchall():
                subtask_data = json.loads(sub_row[4]) if sub_row[4] else {}
                subtasks.append({
                    'subtask_id': sub_row[0],
                    'description': sub_row[1],
                    'importance': sub_row[2],
                    'status': sub_row[3],
                    'findings': subtask_data.get('findings', []),
                    'unknowns': subtask_data.get('unknowns', []),
                    'dead_ends': subtask_data.get('dead_ends', [])
                })

            # Ensure scope_data is a dict before calling .get() (defensive check for legacy data)
            if not isinstance(scope_data, dict):
                scope_data = {}

            goals.append({
                'goal_id': goal_id,
                'objective': row[1],
                'status': row[2],
                'scope_breadth': scope_data.get('breadth'),
                'scope_duration': scope_data.get('duration'),
                'scope_coordination': scope_data.get('coordination'),
                'estimated_complexity': row[4],
                'subtasks': subtasks
            })

        return goals

    def query_unknowns_summary(self, session_id: str) -> dict:
        """Get summary of all unknowns in a session (for CHECK decisions)

        Args:
            session_id: Session UUID

        Returns:
            Dict with total_unknowns count and breakdown by goal
        """
        cursor = self._execute("""
            SELECT g.id, g.objective, s.id, s.subtask_data
            FROM goals g
            LEFT JOIN subtasks s ON g.id = s.goal_id
            WHERE g.session_id = ? AND g.status = 'in_progress'
        """, (session_id,))

        total_unknowns = 0
        unknowns_by_goal = {}

        for row in cursor.fetchall():
            goal_id, objective, _, subtask_data_json = row

            if goal_id not in unknowns_by_goal:
                unknowns_by_goal[goal_id] = {
                    'goal_id': goal_id,
                    'objective': objective,
                    'unknown_count': 0
                }

            if subtask_data_json:
                subtask_data = json.loads(subtask_data_json)
                unknowns = subtask_data.get('unknowns', [])
                unknowns_count = len([u for u in unknowns if u])  # Count non-empty unknowns
                unknowns_by_goal[goal_id]['unknown_count'] += unknowns_count
                total_unknowns += unknowns_count

        return {
            'total_unknowns': total_unknowns,
            'unknowns_by_goal': list(unknowns_by_goal.values())
        }

    def get_project_goals(self, project_id: str) -> dict:
        """Get incomplete and active goals for a project.

        JSON columns (`scope`, `goal_data`) are decoded to native dicts so
        consumers (extension renderer, AI bootstrap) don't have to do a
        second json.loads — they were previously returned as escaped
        strings inside the row dict.
        """
        def _decode(row_dict: dict, *cols: str) -> dict:
            for c in cols:
                v = row_dict.get(c)
                if isinstance(v, str) and v:
                    try:
                        row_dict[c] = json.loads(v)
                    except (ValueError, TypeError):
                        pass  # leave as string if it isn't valid JSON
            return row_dict

        # Get incomplete goals
        cursor = self._execute("""
            SELECT id, objective, scope, status, created_timestamp
            FROM goals
            WHERE session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)
            AND is_completed = 0
            ORDER BY created_timestamp DESC
        """, (project_id,))
        incomplete_goals = [_decode(dict(row), 'scope') for row in cursor.fetchall()]
        # Deduplicate by objective (same goal may be created across sessions)
        incomplete_goals = self._dedupe_by_objective(incomplete_goals)

        # Get active goals with subtask counts
        cursor = self._execute("""
            SELECT g.id, g.objective, g.scope, g.status, g.goal_data,
                   COUNT(DISTINCT s.id) as subtask_count,
                   SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END) as completed_subtasks
            FROM goals g
            LEFT JOIN subtasks s ON g.id = s.goal_id
            WHERE g.session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)
            AND g.is_completed = 0
            GROUP BY g.id
            ORDER BY g.created_timestamp DESC
        """, (project_id,))
        active_goals = [_decode(dict(row), 'scope', 'goal_data') for row in cursor.fetchall()]
        # Deduplicate by objective (same goal may be created across sessions)
        active_goals = self._dedupe_by_objective(active_goals)

        return {
            'incomplete_work': incomplete_goals,
            'goals': active_goals
        }

    def mark_goals_stale(self, session_id: str, stale_reason: str = "memory_compact") -> int:
        """Record compact metadata on in_progress goals (status unchanged).

        Goals stay in_progress across compaction — the post-compact AI
        picks them up naturally via goals-list. The 'stale' status was
        removed: goals are either planned, in_progress, or completed.

        Args:
            session_id: Session UUID
            stale_reason: Why compaction happened (recorded in goal_data metadata)

        Returns:
            Number of goals annotated
        """
        cursor = self._execute("""
            SELECT id, goal_data FROM goals
            WHERE session_id = ? AND status = 'in_progress'
        """, (session_id,))

        count = 0
        for row in cursor.fetchall():
            goal_id = row[0]
            goal_data = json.loads(row[1]) if row[1] else {}

            # Record compaction event in metadata (status stays in_progress)
            goal_data['last_compact'] = time.time()
            goal_data['compact_reason'] = stale_reason

            self._execute("""
                UPDATE goals SET goal_data = ? WHERE id = ?
            """, (json.dumps(goal_data), goal_id))
            count += 1

        self.commit()
        return count

    def get_stale_goals(self, session_id: str | None = None, project_id: str | None = None) -> list[dict]:
        """Get stale goals for a session or project

        Args:
            session_id: Optional session UUID filter
            project_id: Optional project UUID filter (checks all sessions in project)

        Returns:
            List of stale goal dicts with stale_since metadata
        """
        if session_id:
            cursor = self._execute("""
                SELECT id, objective, status, scope, goal_data, created_timestamp
                FROM goals
                WHERE session_id = ? AND status = 'stale'
                ORDER BY created_timestamp DESC
            """, (session_id,))
        elif project_id:
            cursor = self._execute("""
                SELECT g.id, g.objective, g.status, g.scope, g.goal_data, g.created_timestamp
                FROM goals g
                JOIN sessions s ON g.session_id = s.session_id
                WHERE s.project_id = ? AND g.status = 'stale'
                ORDER BY g.created_timestamp DESC
            """, (project_id,))
        else:
            return []

        stale_goals = []
        for row in cursor.fetchall():
            goal_data = json.loads(row[4]) if row[4] else {}
            stale_goals.append({
                'goal_id': row[0],
                'objective': row[1],
                'status': row[2],
                'scope': json.loads(row[3]) if row[3] else {},
                'stale_since': goal_data.get('stale_since'),
                'stale_reason': goal_data.get('stale_reason'),
                'created_timestamp': row[5]
            })

        return stale_goals

    def activate_goal(self, goal_id: str, transaction_id: str | None = None) -> bool:
        """Activate a planned goal — set status to in_progress and link to transaction.

        Args:
            goal_id: Goal UUID (prefix match supported)
            transaction_id: Current transaction UUID to link the goal to

        Returns:
            True if activated, False if goal not found or not planned
        """
        # Prefix match on goal_id
        cursor = self._execute("""
            SELECT id, goal_data FROM goals WHERE id LIKE ? AND status = 'planned'
        """, (f"{goal_id}%",))
        row = cursor.fetchone()

        if not row:
            return False

        full_id = row[0]
        goal_data = json.loads(row[1]) if row[1] else {}
        goal_data['activated_at'] = time.time()

        params = [full_id]
        sql = "UPDATE goals SET status = 'in_progress', goal_data = ?"
        if transaction_id:
            sql += ", transaction_id = ?"
            params = [json.dumps(goal_data), transaction_id, full_id]
        else:
            params = [json.dumps(goal_data), full_id]
        sql += " WHERE id = ?"

        self._execute(sql, tuple(params))
        self.commit()
        return True

    def refresh_goal(self, goal_id: str) -> bool:
        """No-op — stale status removed. Goals stay in_progress across compaction.

        Kept for backward compatibility with CLI command.

        Args:
            goal_id: Goal UUID

        Returns:
            True if goal exists and is in_progress, False otherwise
        """
        cursor = self._execute("""
            SELECT goal_data FROM goals WHERE id = ? AND status = 'in_progress'
        """, (goal_id,))
        row = cursor.fetchone()

        if not row:
            return False

        goal_data = json.loads(row[0]) if row[0] else {}
        goal_data['refreshed_at'] = time.time()

        self._execute("""
            UPDATE goals SET goal_data = ? WHERE id = ?
        """, (json.dumps(goal_data), goal_id))

        self.commit()
        return True
