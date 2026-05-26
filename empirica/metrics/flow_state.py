"""
Flow State Metrics - Track conditions that lead to high productivity

Measures:
- CASCADE completeness (PREFLIGHT→POSTFLIGHT)
- Bootstrap usage (early context loading)
- Goal structure (active goals with subtasks)
- Learning velocity (know increase per hour)
- Session continuity (AI naming convention)
- CHECK usage (mid-session confidence checks)

Flow Score = weighted average of above factors
"""

import logging
import re
from datetime import datetime
from typing import ClassVar

logger = logging.getLogger(__name__)


class FlowStateMetrics:
    """Calculate flow state metrics for a session"""

    # Weights for flow score calculation
    WEIGHTS: ClassVar[dict[str, float]] = {
        'cascade_completeness': 0.25,
        'bootstrap_usage': 0.15,
        'goal_structure': 0.15,
        'learning_velocity': 0.20,
        'check_usage': 0.15,
        'session_continuity': 0.10
    }

    def __init__(self, db):
        """
        Args:
            db: SessionDatabase instance
        """
        self.db = db

    def calculate_flow_score(self, session_id: str) -> dict:
        """Calculate flow state score for a session

        Args:
            session_id: Session UUID

        Returns:
            Dict with:
                - flow_score: 0.0-1.0 (overall productivity)
                - components: breakdown by factor
                - recommendations: what to improve
        """
        cursor = self.db.conn.cursor()

        # Get session info
        cursor.execute("""
            SELECT ai_id, start_time, end_time
            FROM sessions
            WHERE session_id = ?
        """, (session_id,))
        session = cursor.fetchone()

        if not session:
            return {
                'flow_score': 0.0,
                'error': 'Session not found'
            }

        # Calculate each component
        cascade_score = self._check_cascade_completeness(session_id)
        bootstrap_score = self._check_bootstrap_usage(session_id)
        goal_score = self._check_goal_structure(session_id)
        learning_score = self._check_learning_velocity(session_id, session)
        check_score = self._check_check_usage(session_id, session)
        continuity_score = self._check_session_continuity(session['ai_id'])

        # Calculate weighted flow score
        flow_score = (
            cascade_score * self.WEIGHTS['cascade_completeness'] +
            bootstrap_score * self.WEIGHTS['bootstrap_usage'] +
            goal_score * self.WEIGHTS['goal_structure'] +
            learning_score * self.WEIGHTS['learning_velocity'] +
            check_score * self.WEIGHTS['check_usage'] +
            continuity_score * self.WEIGHTS['session_continuity']
        )

        # Generate recommendations
        recommendations = []
        if cascade_score < 0.5:
            recommendations.append("Complete CASCADE workflow: Add PREFLIGHT and POSTFLIGHT")
        if bootstrap_score < 0.5:
            recommendations.append("Load project-bootstrap early for better context")
        if goal_score < 0.5:
            recommendations.append("Create goals with tasks for better structure")
        if learning_score < 0.3:
            recommendations.append("Increase learning rate: More investigation, less guessing")
        if check_score < 0.5:
            recommendations.append("Use CHECK for high-scope goals to validate direction")
        if continuity_score < 0.5:
            recommendations.append("Use AI naming convention: <model>-<workstream>")

        return {
            'flow_score': round(flow_score, 2),
            'components': {
                'cascade_completeness': round(cascade_score, 2),
                'bootstrap_usage': round(bootstrap_score, 2),
                'goal_structure': round(goal_score, 2),
                'learning_velocity': round(learning_score, 2),
                'check_usage': round(check_score, 2),
                'session_continuity': round(continuity_score, 2)
            },
            'recommendations': recommendations,
            'session_id': session_id,
            'ai_id': session['ai_id']
        }

    def _check_cascade_completeness(self, session_id: str) -> float:
        """Check if session has PREFLIGHT and POSTFLIGHT"""
        cursor = self.db.conn.cursor()

        cursor.execute("""
            SELECT COUNT(DISTINCT phase)
            FROM reflexes
            WHERE session_id = ? AND phase IN ('PREFLIGHT', 'POSTFLIGHT')
        """, (session_id,))

        phase_count = cursor.fetchone()[0]

        # 2 phases (PREFLIGHT + POSTFLIGHT) = 1.0
        # 1 phase = 0.5
        # 0 phases = 0.0
        return min(phase_count / 2.0, 1.0)

    def _check_bootstrap_usage(self, session_id: str) -> float:
        """Check if bootstrap was loaded early (indicates good context awareness)"""
        cursor = self.db.conn.cursor()

        # Check if any findings reference "bootstrap" or "project context"
        cursor.execute("""
            SELECT COUNT(*)
            FROM project_findings
            WHERE session_id = ?
              AND (finding LIKE '%bootstrap%' OR finding LIKE '%project context%')
        """, (session_id,))

        bootstrap_refs = cursor.fetchone()[0]

        # Any bootstrap reference = good (1.0)
        # No bootstrap = assume not used (0.3 baseline)
        return 1.0 if bootstrap_refs > 0 else 0.3

    def _check_goal_structure(self, session_id: str) -> float:
        """Check if session has goals with subtasks"""
        cursor = self.db.conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(g.id) as goal_count,
                COUNT(st.id) as subtask_count
            FROM goals g
            LEFT JOIN subtasks st ON g.id = st.goal_id
            WHERE g.session_id = ?
        """, (session_id,))

        result = cursor.fetchone()
        goal_count = result[0]
        subtask_count = result[1]

        if goal_count == 0:
            return 0.3  # No goals = low structure
        elif subtask_count == 0:
            return 0.6  # Has goals but no subtasks
        else:
            # Has goals with subtasks = good structure
            return min(1.0, 0.7 + (subtask_count / goal_count) * 0.3)

    def _check_learning_velocity(self, session_id: str, session: dict) -> float:
        """Check rate of knowledge increase (know delta / time)"""
        cursor = self.db.conn.cursor()

        # Get PREFLIGHT and POSTFLIGHT know values
        cursor.execute("""
            SELECT phase, know
            FROM reflexes
            WHERE session_id = ? AND phase IN ('PREFLIGHT', 'POSTFLIGHT')
            ORDER BY timestamp
        """, (session_id,))

        reflexes = cursor.fetchall()

        if len(reflexes) < 2:
            return 0.5  # Can't calculate without both phases

        preflight_know = reflexes[0]['know']
        postflight_know = reflexes[-1]['know']
        know_delta = postflight_know - preflight_know

        # Calculate session duration in hours
        start_time = datetime.fromisoformat(str(session['start_time']))
        end_time = datetime.fromisoformat(str(session['end_time'])) if session['end_time'] else datetime.now()
        duration_hours = (end_time - start_time).total_seconds() / 3600

        if duration_hours == 0:
            return 0.5

        # Learning velocity = know increase per hour
        # 0.3/hr = excellent (1.0)
        # 0.15/hr = good (0.5)
        # <0 = negative learning (0.0)
        learning_velocity = know_delta / duration_hours
        return max(0.0, min(learning_velocity / 0.3, 1.0))

    def _check_check_usage(self, session_id: str, session: dict) -> float:
        """Check if CHECK was used appropriately for session scope

        Returns:
            1.0 = CHECK used appropriately for scope
            0.7 = CHECK used but scope was low (unnecessary but not harmful)
            0.5 = No CHECK for low-scope session (acceptable)
            0.3 = No CHECK for high-scope session (should have used it)
        """
        cursor = self.db.conn.cursor()

        # Check if session has CHECK phase
        cursor.execute("""
            SELECT COUNT(*)
            FROM reflexes
            WHERE session_id = ? AND phase = 'CHECK'
        """, (session_id,))
        has_check = cursor.fetchone()[0] > 0

        # Get goal scope for session (if any)
        cursor.execute("""
            SELECT goal_data
            FROM goals
            WHERE session_id = ?
            ORDER BY created_timestamp DESC
            LIMIT 1
        """, (session_id,))
        goal = cursor.fetchone()

        if goal and goal['goal_data']:
            import json
            try:
                goal_data = json.loads(goal['goal_data'])
                scope = goal_data.get('scope', {})
                scope_breadth = scope.get('breadth', 0.3)
                scope_duration = scope.get('duration', 0.2)
            except Exception:
                scope_breadth = 0.3
                scope_duration = 0.2
            is_high_scope = scope_breadth >= 0.6 or scope_duration >= 0.5

            if has_check and is_high_scope:
                return 1.0  # Perfect: CHECK used for high-scope
            elif has_check and not is_high_scope:
                return 0.7  # CHECK used but not needed (still good practice)
            elif not has_check and is_high_scope:
                return 0.3  # Missing: Should have used CHECK
            else:
                return 0.5  # No CHECK for low-scope (acceptable)
        else:
            # No goal defined, check by session duration as proxy
            if session['end_time']:
                start_time = datetime.fromisoformat(str(session['start_time']))
                end_time = datetime.fromisoformat(str(session['end_time']))
                duration_hours = (end_time - start_time).total_seconds() / 3600

                if duration_hours >= 0.5:  # 30+ minutes
                    if has_check:
                        return 1.0  # Good: CHECK for long session
                    else:
                        return 0.3  # Missing: Should have checked
                else:
                    if has_check:
                        return 0.7  # Extra CHECK (not harmful)
                    else:
                        return 0.5  # Quick session, CHECK optional
            else:
                # Session not ended yet, default to middle ground
                return 0.7 if has_check else 0.5

    def _check_session_continuity(self, ai_id: str) -> float:
        """Check if AI naming follows convention: <model>-<workstream>"""
        # Pattern: model-context or model-context-detail
        pattern = r'^[a-z0-9]+-[a-z0-9-]+$'

        if re.match(pattern, ai_id.lower()):
            return 1.0  # Follows convention
        else:
            return 0.5  # Generic name (e.g., "claude", "ai")
