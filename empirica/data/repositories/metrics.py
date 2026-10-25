"""
Metrics Repository - Flow State and Health Score Calculations

Extracts metrics logic from SessionDatabase to improve coherence.
Handles all flow state metrics and epistemic health score calculations.
"""

from .base import BaseRepository


class MetricsRepository(BaseRepository):
    """Repository for flow state and health score metrics"""

    def calculate_flow_metrics(self, project_id: str, limit: int = 5) -> dict:
        """
        Calculate flow state metrics for recent sessions in a project.

        Flow state = optimal AI productivity characterized by:
        - High engagement + capability (know/do)
        - Clear goals + low uncertainty
        - Meaningful progress (completion/impact)

        Args:
            project_id: Project UUID
            limit: Number of recent sessions to analyze (default: 5)

        Returns:
            Dict with flow scores, trend, blockers, and triggers
        """
        from ..flow_state_calculator import (
            calculate_flow_score,
            calculate_flow_trend,
            check_flow_triggers,
            classify_flow_state,
            identify_flow_blockers,
        )

        # Get recent sessions with POSTFLIGHT vectors
        cursor = self._execute(
            """
            SELECT
                s.session_id,
                s.ai_id,
                s.start_time,
                r.engagement, r.know, r.do, r.context,
                r.clarity, r.coherence, r.signal, r.density,
                r.state, r.change, r.completion, r.impact, r.uncertainty
            FROM sessions s
            JOIN reflexes r ON s.session_id = r.session_id
            WHERE s.project_id = ?
            AND r.phase = 'POSTFLIGHT'
            ORDER BY r.timestamp DESC
            LIMIT ?
        """,
            (project_id, limit),
        )

        rows = cursor.fetchall()

        if not rows:
            return {"flow_scores": [], "current_flow": None, "trend": None, "blockers": [], "triggers_present": {}}

        # Calculate flow score for each session
        flow_data = []
        for row in rows:
            session_id = row[0]
            ai_id = row[1]
            start_time = row[2]

            # Build vectors dict from columns
            vectors = {
                "engagement": row[3],
                "know": row[4],
                "do": row[5],
                "context": row[6],
                "clarity": row[7],
                "coherence": row[8],
                "signal": row[9],
                "density": row[10],
                "state": row[11],
                "change": row[12],
                "completion": row[13],
                "impact": row[14],
                "uncertainty": row[15],
            }

            # A POSTFLIGHT with a NULL vector (a restored or imported one: reflex_import writes NULL for a vector missing from a git
            # note) has none of the inputs this score is built from. Multiplying None raised TypeError; inventing 0.5 would score a
            # session that was never assessed. It is left out.
            if any(v is None for v in vectors.values()):
                continue

            # Calculate flow score
            flow_score = calculate_flow_score(vectors)
            state_name, emoji = classify_flow_state(flow_score)

            # Calculate component contributions for display
            components = {
                "engagement": vectors["engagement"] * 0.25 * 100,
                "capability": ((vectors["know"] + vectors["do"]) / 2) * 0.20 * 100,
                "clarity": vectors["clarity"] * 0.15 * 100,
                "confidence": (1.0 - vectors["uncertainty"]) * 0.15 * 100,
                "completion": vectors["completion"] * 0.10 * 100,
                "impact": vectors["impact"] * 0.10 * 100,
                "coherence": vectors["coherence"] * 0.05 * 100,
            }

            # Generate recommendations based on low vectors
            recommendations = identify_flow_blockers(vectors)

            flow_data.append(
                {
                    "session_id": session_id,
                    "ai_id": ai_id,
                    "start_time": start_time,
                    "flow_score": flow_score,
                    "flow_state": state_name,
                    "emoji": emoji,
                    "vectors": vectors,
                    "components": components,
                    "recommendations": recommendations,
                }
            )

        # Get latest (most recent) session data
        latest = flow_data[0] if flow_data else None

        # Calculate trend
        flow_scores = [f["flow_score"] for f in reversed(flow_data)]  # Oldest to newest
        trend_desc, trend_emoji = (
            calculate_flow_trend(flow_scores) if len(flow_scores) >= 2 else ("Not enough data", "")
        )

        # Identify blockers from latest session
        blockers = identify_flow_blockers(latest["vectors"]) if latest else []

        # Check flow triggers
        triggers_present = check_flow_triggers(latest["vectors"]) if latest else {}

        return {
            "flow_scores": flow_data,
            "current_flow": latest,
            "average_flow": round(sum(flow_scores) / len(flow_scores), 1) if flow_scores else 0.0,
            "trend": {"description": trend_desc, "emoji": trend_emoji},
            "blockers": blockers,
            "triggers_present": triggers_present,
        }

    def calculate_health_score(self, project_id: str, limit: int = 5) -> dict:
        """
        Calculate epistemic health score for recent sessions in a project.

        Health score measures:
        - Epistemic completeness (findings, unknowns resolution)
        - Knowledge quality (clarity, coherence, signal)
        - Progress tracking (completion, impact)
        - Error reduction (mistakes, dead ends)

        Args:
            project_id: Project UUID
            limit: Number of recent sessions to analyze (default: 5)

        Returns:
            Dict with health score, trend, and component analysis
        """
        # Get recent sessions with POSTFLIGHT vectors
        cursor = self._execute(
            """
            SELECT
                s.session_id,
                s.ai_id,
                s.start_time,
                r.engagement, r.know, r.do, r.context,
                r.clarity, r.coherence, r.signal, r.density,
                r.state, r.change, r.completion, r.impact, r.uncertainty
            FROM sessions s
            JOIN reflexes r ON s.session_id = r.session_id
            WHERE s.project_id = ?
            AND r.phase = 'POSTFLIGHT'
            ORDER BY r.timestamp DESC
            LIMIT ?
        """,
            (project_id, limit),
        )

        rows = cursor.fetchall()

        if not rows:
            return {"health_score": 0.0, "trend": "Not enough data", "components": {}}

        # Calculate health score for each session
        health_data = []
        for row in rows:
            session_id = row[0]
            ai_id = row[1]
            start_time = row[2]

            # Build vectors dict from columns
            vectors = {
                "engagement": row[3],
                "know": row[4],
                "do": row[5],
                "context": row[6],
                "clarity": row[7],
                "coherence": row[8],
                "signal": row[9],
                "density": row[10],
                "state": row[11],
                "change": row[12],
                "completion": row[13],
                "impact": row[14],
                "uncertainty": row[15],
            }

            # A POSTFLIGHT with a NULL vector (a restored or imported one: reflex_import writes NULL for a vector missing from a git
            # note) has none of the inputs this score is built from. Multiplying None raised TypeError; inventing 0.5 would score a
            # session that was never assessed. It is left out.
            if any(v is None for v in vectors.values()):
                continue

            # Calculate health score (0-100)
            health_score = self._calculate_session_health_score(vectors)

            health_data.append(
                {
                    "session_id": session_id,
                    "ai_id": ai_id,
                    "start_time": start_time,
                    "health_score": health_score,
                    "vectors": vectors,
                }
            )

        # Get latest (most recent) session data
        latest = health_data[0] if health_data else None

        # Calculate trend
        health_scores = [h["health_score"] for h in reversed(health_data)]  # Oldest to newest
        trend_desc, trend_emoji = (
            self._calculate_health_trend(health_scores) if len(health_scores) >= 2 else ("Not enough data", "")
        )

        # Calculate component analysis
        components = self._analyze_health_components(health_data)

        return {
            "health_scores": health_data,
            "current_health": latest,
            "average_health": round(sum(health_scores) / len(health_scores), 1) if health_scores else 0.0,
            "trend": {"description": trend_desc, "emoji": trend_emoji},
            "components": components,
        }

    def _calculate_session_health_score(self, vectors: dict[str, float]) -> float:
        """
        Calculate health score (0-100) from epistemic vectors.

        Health score formula:
        - Knowledge Quality (30%): clarity + coherence + signal
        - Epistemic Progress (25%): completion + impact
        - Capability (20%): know + do
        - Confidence (15%): low uncertainty
        - Engagement (10%): focus and immersion

        Args:
            vectors: Dict of epistemic vectors (0.0-1.0)

        Returns:
            Health score (0-100)
        """
        # Extract vectors with defaults
        clarity = vectors.get("clarity", 0.5)
        coherence = vectors.get("coherence", 0.5)
        signal = vectors.get("signal", 0.5)
        completion = vectors.get("completion", 0.5)
        impact = vectors.get("impact", 0.5)
        know = vectors.get("know", 0.5)
        do = vectors.get("do", 0.5)
        uncertainty = vectors.get("uncertainty", 0.5)
        engagement = vectors.get("engagement", 0.5)

        # Calculate components
        knowledge_quality = (clarity + coherence + signal) / 3.0
        epistemic_progress = (completion + impact) / 2.0
        capability = (know + do) / 2.0
        confidence = 1.0 - uncertainty

        # Calculate health score
        health_score = (
            knowledge_quality * 0.30
            + epistemic_progress * 0.25
            + capability * 0.20
            + confidence * 0.15
            + engagement * 0.10
        )

        return round(health_score * 100, 1)

    def _calculate_health_trend(self, health_scores: list[float]) -> tuple[str, str]:
        """
        Calculate health trend from multiple scores.

        Args:
            health_scores: List of health scores (oldest to newest)

        Returns:
            Tuple of (trend_description, trend_emoji)
        """
        if len(health_scores) < 2:
            return "Not enough data", ""

        # Calculate change
        oldest = health_scores[0]
        newest = health_scores[-1]
        change = newest - oldest
        percent_change = (change / oldest) * 100 if oldest > 0 else 0

        # Determine trend
        if percent_change > 15:
            return f"📈 Improving ({percent_change:.0f}%)", "📈"
        elif percent_change > 5:
            return f"📉 Stable improvement ({percent_change:.0f}%)", "📉"
        elif percent_change > -5:
            return f"🔄 Stable ({percent_change:.0f}%)", "🔄"
        elif percent_change > -15:
            return f"📉 Declining ({percent_change:.0f}%)", "📉"
        else:
            return f"📉 Significant decline ({percent_change:.0f}%)", "📉"

    def _analyze_health_components(self, health_data: list[dict]) -> dict:
        """
        Analyze health score components across sessions.

        Args:
            health_data: List of session health data

        Returns:
            Dict with component analysis
        """
        if not health_data:
            return {}

        # Calculate averages
        latest = health_data[0]
        vectors = latest["vectors"]

        return {
            "knowledge_quality": {
                "clarity": vectors.get("clarity", 0.5),
                "coherence": vectors.get("coherence", 0.5),
                "signal": vectors.get("signal", 0.5),
                "average": (vectors.get("clarity", 0.5) + vectors.get("coherence", 0.5) + vectors.get("signal", 0.5))
                / 3.0,
            },
            "epistemic_progress": {
                "completion": vectors.get("completion", 0.5),
                "impact": vectors.get("impact", 0.5),
                "average": (vectors.get("completion", 0.5) + vectors.get("impact", 0.5)) / 2.0,
            },
            "capability": {
                "know": vectors.get("know", 0.5),
                "do": vectors.get("do", 0.5),
                "average": (vectors.get("know", 0.5) + vectors.get("do", 0.5)) / 2.0,
            },
            "confidence": {
                "uncertainty": vectors.get("uncertainty", 0.5),
                "confidence_score": 1.0 - vectors.get("uncertainty", 0.5),
            },
            "engagement": {"engagement": vectors.get("engagement", 0.5)},
        }
