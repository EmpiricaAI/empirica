"""Utility repositories for token savings, command usage, and workspace stats"""

import json
import uuid
from datetime import datetime, timezone

from .base import BaseRepository


class TokenRepository(BaseRepository):
    """Handles token savings tracking"""

    def log_token_saving(self, session_id: str, saving_type: str, tokens_saved: int, evidence: str) -> str:
        """
        Log a token saving event

        Args:
            session_id: Session UUID
            saving_type: Type of saving (e.g., 'doc_awareness', 'finding_reuse', 'mistake_prevention', 'handoff_efficiency')
            tokens_saved: Estimated tokens saved
            evidence: What was avoided/reused

        Returns:
            saving_id: UUID string
        """
        import logging

        logger = logging.getLogger(__name__)

        saving_id = str(uuid.uuid4())
        self._execute(
            """
            INSERT INTO token_savings (
                id, session_id, saving_type, tokens_saved, evidence
            ) VALUES (?, ?, ?, ?, ?)
        """,
            (saving_id, session_id, saving_type, tokens_saved, evidence),
        )

        self.commit()
        logger.info(f"💰 Token saving logged: {tokens_saved} tokens ({saving_type})")

        return saving_id

    def get_session_token_savings(self, session_id: str) -> dict:
        """
        Get token savings summary for a session

        Args:
            session_id: Session UUID

        Returns:
            Dict with total_tokens_saved, cost_saved_usd, and breakdown by type
        """
        cursor = self._execute(
            """
            SELECT saving_type, SUM(tokens_saved) as total, COUNT(*) as count
            FROM token_savings
            WHERE session_id = ?
            GROUP BY saving_type
        """,
            (session_id,),
        )

        breakdown = {}
        total = 0
        for row in cursor.fetchall():
            saving_type = row[0]
            tokens = row[1]
            count = row[2]
            breakdown[saving_type] = {"tokens": tokens, "count": count}
            total += tokens

        return {"total_tokens_saved": total, "cost_saved_usd": round(total * 0.00003, 4), "breakdown": breakdown}


class CommandRepository(BaseRepository):
    """Handles command usage tracking"""

    def log_command_usage(
        self,
        session_id: str,
        command: str,
        args: dict | None = None,
        success: bool = True,
        error_msg: str | None = None,
    ) -> str:
        """
        Log command usage

        Args:
            session_id: Session UUID
            command: Command name
            args: Command arguments
            success: Whether command succeeded
            error_msg: Error message if failed

        Returns:
            usage_id: UUID string
        """
        usage_id = str(uuid.uuid4())
        self._execute(
            """
            INSERT INTO command_usage (
                id, session_id, command, args, success, error_msg, executed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                usage_id,
                session_id,
                command,
                json.dumps(args) if args else None,
                success,
                error_msg,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return usage_id

    def get_command_usage_stats(self, days: int = 30) -> dict:
        """Get command usage statistics for legacy detection

        Args:
            days: Number of days to analyze

        Returns:
            Dict with usage stats, legacy candidates, broken commands
        """
        import time

        since_timestamp = time.time() - (days * 24 * 3600)

        # Most used commands
        cursor = self._execute(
            """
            SELECT command_name, COUNT(*) as usage_count,
                   AVG(execution_time_ms) as avg_time_ms,
                   MAX(timestamp) as last_used
            FROM command_usage
            WHERE timestamp >= ?
            GROUP BY command_name
            ORDER BY usage_count DESC
        """,
            (since_timestamp,),
        )

        most_used = [dict(row) for row in cursor.fetchall()]

        # Rarely used (legacy candidates)
        cursor = self._execute(
            """
            SELECT command_name, COUNT(*) as usage_count,
                   MAX(timestamp) as last_used,
                   (? - MAX(timestamp)) / 86400.0 as days_since_last_use
            FROM command_usage
            WHERE timestamp >= ?
            GROUP BY command_name
            HAVING usage_count < 5
            ORDER BY usage_count ASC
        """,
            (time.time(), since_timestamp),
        )

        legacy_candidates = [dict(row) for row in cursor.fetchall()]

        # Low success rate (broken/confusing)
        cursor = self._execute(
            """
            SELECT command_name,
                   COUNT(*) as total_uses,
                   SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes,
                   ROUND(100.0 * SUM(CASE WHEN success THEN 1 ELSE 0 END) / COUNT(*), 2) as success_rate
            FROM command_usage
            WHERE timestamp >= ?
            GROUP BY command_name
            HAVING total_uses >= 10 AND success_rate < 50
            ORDER BY success_rate ASC
        """,
            (since_timestamp,),
        )

        broken_commands = [dict(row) for row in cursor.fetchall()]

        return {
            "period_days": days,
            "most_used": most_used[:10],
            "legacy_candidates": legacy_candidates,
            "broken_commands": broken_commands,
            "total_commands_tracked": len(most_used),
        }


class WorkspaceRepository(BaseRepository):
    """Handles workspace-level operations"""

    def get_workspace_overview(self) -> dict:
        """
        Get epistemic overview of all projects in workspace.

        Returns:
            Dictionary with:
            - total_projects: int
            - projects: List of project dicts with epistemic health
            - workspace_stats: Aggregate statistics
        """
        # Get all projects with their latest epistemic state
        cursor = self._execute("""
            SELECT
                p.id,
                p.name,
                p.description,
                p.status,
                -- Counted live: projects.total_sessions has no production writer (link_session_to_project has no caller; session
                -- creation sets project_id with a raw UPDATE), so health and dead_end_ratio stayed neutral for every project.
                (SELECT COUNT(*) FROM sessions WHERE project_id = p.id) AS total_sessions,
                p.last_activity_timestamp,
                -- Get latest epistemic vectors from most recent session reflex
                (SELECT r.know FROM reflexes r
                 JOIN sessions s ON s.session_id = r.session_id
                 WHERE s.project_id = p.id
                 ORDER BY r.timestamp DESC LIMIT 1) as latest_know,
                (SELECT r.uncertainty FROM reflexes r
                 JOIN sessions s ON s.session_id = r.session_id
                 WHERE s.project_id = p.id
                 ORDER BY r.timestamp DESC LIMIT 1) as latest_uncertainty,
                -- Counts
                (SELECT COUNT(*) FROM project_findings WHERE project_id = p.id) as findings_count,
                (SELECT COUNT(*) FROM project_unknowns WHERE project_id = p.id AND is_resolved = 0) as unknowns_count,
                (SELECT COUNT(*) FROM project_dead_ends WHERE project_id = p.id) as dead_ends_count
            FROM projects p
            ORDER BY p.last_activity_timestamp DESC
        """)

        projects = []
        for row in cursor.fetchall():
            project = {
                "project_id": row[0],
                "name": row[1],
                "description": row[2],
                "status": row[3],
                "total_sessions": row[4],
                "last_activity": row[5],
                "epistemic_state": {
                    "know": row[6] if row[6] is not None else 0.5,
                    "uncertainty": row[7] if row[7] is not None else 0.5,
                },
                "findings_count": row[8],
                "unknowns_count": row[9],
                "dead_ends_count": row[10],
            }

            # Calculate health metrics
            if project["total_sessions"] > 0:
                dead_end_ratio = project["dead_ends_count"] / project["total_sessions"]
                project["dead_end_ratio"] = dead_end_ratio

                # Health score (0-1): weighted by knowledge and uncertainty
                know = project["epistemic_state"]["know"]
                uncertainty = project["epistemic_state"]["uncertainty"]
                health = (know * 0.6) + ((1 - uncertainty) * 0.4) - (dead_end_ratio * 0.2)
                project["health_score"] = max(0.0, min(1.0, health))
            else:
                project["dead_end_ratio"] = 0.0
                project["health_score"] = 0.5

            projects.append(project)

        # Get workspace-level stats
        workspace_stats = self._get_workspace_aggregate_stats()

        return {"total_projects": len(projects), "projects": projects, "workspace_stats": workspace_stats}

    def _get_workspace_aggregate_stats(self) -> dict:
        """Get workspace-level aggregated statistics"""
        cursor = self._execute("""
            SELECT
                COUNT(DISTINCT p.id) as total_projects,
                COUNT(DISTINCT s.session_id) as total_sessions,
                COUNT(DISTINCT CASE WHEN s.end_time IS NULL THEN s.session_id END) as active_sessions,
                AVG(CASE WHEN r.know IS NOT NULL THEN r.know END) as avg_know,
                AVG(CASE WHEN r.uncertainty IS NOT NULL THEN r.uncertainty END) as avg_uncertainty
            FROM projects p
            LEFT JOIN sessions s ON s.project_id = p.id
            LEFT JOIN reflexes r ON r.session_id = s.session_id
        """)

        row = cursor.fetchone()
        return {
            "total_projects": row[0] or 0,
            "total_sessions": row[1] or 0,
            "active_sessions": row[2] or 0,
            "avg_know": round(row[3], 2) if row[3] else 0.5,
            "avg_uncertainty": round(row[4], 2) if row[4] else 0.5,
        }

    def get_workspace_stats(self, project_ids: list[str]) -> dict:
        """
        Get aggregated workspace statistics for specific projects

        Args:
            project_ids: List of project UUIDs

        Returns:
            Dict with workspace stats
        """
        if not project_ids:
            return {"total_sessions": 0, "total_findings": 0, "total_unknowns": 0, "projects": []}

        placeholders = ",".join("?" * len(project_ids))

        # Get session count
        cursor = self._execute(
            f"""
            SELECT COUNT(*) as count
            FROM sessions
            WHERE project_id IN ({placeholders})
        """,
            tuple(project_ids),
        )
        total_sessions = cursor.fetchone()["count"]

        # Get findings count
        cursor = self._execute(
            f"""
            SELECT COUNT(*) as count
            FROM project_findings
            WHERE project_id IN ({placeholders})
        """,
            tuple(project_ids),
        )
        total_findings = cursor.fetchone()["count"]

        # Get unknowns count
        cursor = self._execute(
            f"""
            SELECT COUNT(*) as count
            FROM project_unknowns
            WHERE project_id IN ({placeholders})
            AND is_resolved = 0
        """,
            tuple(project_ids),
        )
        total_unknowns = cursor.fetchone()["count"]

        return {
            "total_sessions": total_sessions,
            "total_findings": total_findings,
            "total_unknowns": total_unknowns,
            "total_projects": len(project_ids),
        }
