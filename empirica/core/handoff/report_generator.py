#!/usr/bin/env python3
"""
Epistemic Handoff Report Generator

Generates compressed, semantic session summaries for multi-agent coordination.

Structure:
- Session metadata (who, when, what)
- Epistemic trajectory (PREFLIGHT → POSTFLIGHT deltas)
- Key learnings (findings, gaps filled)
- Context for next session
- Recommended next steps

Output formats:
- Markdown (human-readable, ~2,500 tokens)
- Compressed JSON (storage, ~800 tokens)
"""

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class EpistemicHandoffReportGenerator:
    """
    Generate compressed epistemic handoff reports for session resumption

    Combines:
    - Vector deltas (what changed epistemically)
    - Key learnings (what was discovered)
    - Context (what next AI needs to know)
    - Recommendations (suggested next steps)
    """

    def __init__(self, db_path: str | None = None):
        """
        Initialize report generator

        Args:
            db_path: Optional path to session database
        """
        from empirica.data.session_database import SessionDatabase

        self.db = SessionDatabase(db_path)

    def generate_handoff_report(
        self,
        session_id: str,
        task_summary: str,
        key_findings: list[str],
        remaining_unknowns: list[str],
        next_session_context: str,
        artifacts_created: list[str] | None = None,
        start_assessment: dict | None = None,
        end_assessment: dict | None = None,
        handoff_subtype: str = "complete",
    ) -> dict[str, Any]:
        """
        Generate comprehensive handoff report

        Args:
            session_id: Session UUID
            task_summary: What was accomplished (2-3 sentences)
            key_findings: What was learned (3-5 bullet points)
            remaining_unknowns: What's still unclear
            next_session_context: Critical context for next session
            artifacts_created: Files/commits produced
            start_assessment: PREFLIGHT assessment (optional, will query if not provided)
            end_assessment: POSTFLIGHT or CHECK assessment (optional, will query if not provided)
            handoff_subtype: "complete" (PREFLIGHT→POSTFLIGHT) or "investigation" (PREFLIGHT→CHECK)

        Returns:
            {
                'session_id': str,
                'ai_id': str,
                'timestamp': str,
                'handoff_subtype': str,  # "complete" or "investigation"
                'task_summary': str,
                'duration_seconds': float,
                'epistemic_deltas': Dict[str, float],
                'key_findings': List[str],
                'knowledge_gaps_filled': List[Dict],
                'remaining_unknowns': List[str],
                'noetic_tools': List[str],
                'next_session_context': str,
                'recommended_next_steps': List[str],
                'artifacts_created': List[str],
                'calibration_status': str,
                'overall_confidence_delta': float,
                'markdown': str,  # Full markdown report
                'compressed_json': str  # Minimal JSON for storage
            }
        """
        logger.info(f"📋 Generating {handoff_subtype} handoff report for session {session_id[:8]}...")

        # Fetch assessments if not provided
        if start_assessment is None:
            start_assessment = self._get_preflight_assessment(session_id)

        if end_assessment is None:
            if handoff_subtype == "investigation":
                # Get most recent CHECK
                checks = self.db.get_check_phase_assessments(session_id)
                end_assessment = checks[-1] if checks else None
            else:
                # Get POSTFLIGHT
                end_assessment = self._get_postflight_assessment(session_id)

        if not start_assessment or not end_assessment:
            raise ValueError(
                f"Missing assessments for {handoff_subtype} handoff. "
                f"PREFLIGHT: {bool(start_assessment)}, END: {bool(end_assessment)}\n\n"
                f"💡 Handoff reports require assessments:\n"
                f"   Investigation handoff: PREFLIGHT + CHECK\n"
                f"   Complete handoff: PREFLIGHT + POSTFLIGHT\n"
            )

        # Calculate vector deltas
        deltas = self._calculate_deltas(start_assessment.get("vectors", {}), end_assessment.get("vectors", {}))

        # Check calibration (pass end_assessment to detect investigation handoffs)
        calibration = self._check_calibration(session_id, deltas, end_assessment)

        # Identify knowledge gaps filled
        gaps_filled = self._identify_filled_gaps(
            start_assessment.get("vectors", {}), end_assessment.get("vectors", {}), key_findings
        )

        # Extract investigation tools used
        tools_used = self._extract_noetic_tools(session_id)

        # Generate recommendations
        recommendations = self._generate_recommendations(
            end_assessment.get("vectors", {}), remaining_unknowns, calibration
        )

        # Get session metadata
        session_meta = self._get_session_metadata(session_id)

        # Calculate duration
        duration = self._calculate_duration(session_id)

        # Build structured report
        report = {
            "session_id": session_id,
            "ai_id": session_meta.get("ai_id", "unknown"),
            "timestamp": datetime.now().isoformat(),
            "handoff_subtype": handoff_subtype,
            "task_summary": task_summary,
            "duration_seconds": duration,
            "epistemic_deltas": deltas,
            "key_findings": key_findings,
            "knowledge_gaps_filled": gaps_filled,
            "remaining_unknowns": remaining_unknowns,
            "noetic_tools": tools_used,
            "next_session_context": next_session_context,
            "recommended_next_steps": recommendations,
            "artifacts_created": artifacts_created or [],
            "calibration_status": calibration["status"],
            "overall_confidence_delta": deltas.get("overall_confidence"),
        }

        # Generate markdown
        report["markdown"] = self._generate_markdown(report, start_assessment, end_assessment, calibration)

        # Generate compressed JSON (minimal, for storage)
        report["compressed_json"] = self._compress_report(report)

        logger.info(f"✅ Handoff report generated ({len(report['compressed_json'])} chars)")

        return report

    def generate_planning_handoff(
        self,
        session_id: str,
        task_summary: str,
        key_findings: list[str],
        remaining_unknowns: list[str],
        next_session_context: str,
        artifacts_created: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Generate planning handoff (documentation without CASCADE workflow assessments)

        Used for:
        - Multi-session planning (no PREFLIGHT/POSTFLIGHT yet)
        - Architecture/design phase handoffs
        - Documentation of progress without epistemic measurements

        Args: Same as generate_handoff_report

        Returns: Dictionary with same structure but no epistemic_deltas
        """
        logger.info(f"📋 Generating planning handoff for session {session_id[:8]}...")

        # Get session metadata
        session_meta = self._get_session_metadata(session_id)

        # Calculate duration
        duration = self._calculate_duration(session_id)

        # Build planning handoff (no epistemic deltas)
        report = {
            "session_id": session_id,
            "ai_id": session_meta.get("ai_id", "unknown"),
            "timestamp": datetime.now().isoformat(),
            "task_summary": task_summary,
            "duration_seconds": duration,
            "epistemic_deltas": {},  # Empty - no CASCADE workflow
            "key_findings": key_findings,
            "knowledge_gaps_filled": [],  # Can't measure without assessments
            "remaining_unknowns": remaining_unknowns,
            "noetic_tools": [],  # Not tracked in planning mode
            "next_session_context": next_session_context,
            "recommended_next_steps": [],  # Can't recommend without epistemic data
            "artifacts_created": artifacts_created or [],
            "calibration_status": "planning-only (no CASCADE workflow)",
            "overall_confidence_delta": 0.0,
            "handoff_type": "planning",  # Mark as planning handoff
        }

        # Generate markdown for planning handoff
        markdown_lines = [
            f"# Planning Handoff: {session_meta.get('ai_id', 'Unknown')}",
            "",
            f"**Session:** {session_id[:8]}...",
            "**Type:** Planning (documentation, no CASCADE workflow)",
            f"**Time:** {report['timestamp']}",
            f"**Duration:** {duration:.1f}s",
            "",
            "## Task Summary",
            f"{task_summary}",
            "",
            "## Key Findings",
        ]

        for finding in key_findings:
            markdown_lines.append(f"- {finding}")

        markdown_lines.extend(
            [
                "",
                "## Remaining Unknowns",
            ]
        )

        for unknown in remaining_unknowns:
            markdown_lines.append(f"- {unknown}")

        markdown_lines.extend(
            [
                "",
                "## Context for Next Session",
                f"{next_session_context}",
                "",
                "## Artifacts Created",
            ]
        )

        if artifacts_created:
            for artifact in artifacts_created:
                markdown_lines.append(f"- {artifact}")
        else:
            markdown_lines.append("- (none)")

        report["markdown"] = "\n".join(markdown_lines)

        # Generate compressed JSON
        report["compressed_json"] = self._compress_planning_handoff(report)

        logger.info(f"✅ Planning handoff generated ({len(report['compressed_json'])} chars)")

        return report

    def _get_preflight_assessment(self, session_id: str) -> dict | None:
        """Fetch PREFLIGHT assessment from database (now uses reflexes table)"""
        try:
            vectors_data = self.db.get_latest_vectors(session_id, phase="PREFLIGHT")
            if not vectors_data:
                return None

            return {
                "vectors": vectors_data.get("vectors", {}),
                "reasoning": vectors_data.get("reasoning", ""),
                "timestamp": vectors_data.get("timestamp"),
            }
        except Exception as e:
            logger.warning(f"Failed to fetch PREFLIGHT: {e}")
            return None

    def _get_postflight_assessment(self, session_id: str) -> dict | None:
        """Fetch POSTFLIGHT assessment from database (now uses reflexes table)"""
        try:
            vectors_data = self.db.get_latest_vectors(session_id, phase="POSTFLIGHT")
            if not vectors_data:
                return None

            return {
                "vectors": vectors_data.get("vectors", {}),
                "reasoning": vectors_data.get("reasoning", ""),
                "timestamp": vectors_data.get("timestamp"),
            }
        except Exception as e:
            logger.warning(f"Failed to fetch POSTFLIGHT: {e}")
            return None

    def _calculate_deltas(
        self, preflight_vectors: dict[str, float], postflight_vectors: dict[str, float]
    ) -> dict[str, float]:
        """
        Calculate epistemic vector deltas

        Returns deltas for all 13 vectors + overall_confidence
        """
        deltas = {}

        # Standard 13 vectors
        vector_keys = [
            "know",
            "do",
            "context",
            "clarity",
            "coherence",
            "signal",
            "density",
            "state",
            "change",
            "completion",
            "impact",
            "engagement",
            "uncertainty",
        ]

        # A delta needs a value on BOTH sides. A vector the later assessment did not make (a sparse CHECK leaves most NULL) used to
        # default to 0.0, which reported a drop to zero that nobody assessed; it is left out of the deltas instead.
        for key in vector_keys:
            before = preflight_vectors.get(key)
            after = postflight_vectors.get(key)
            if before is not None and after is not None:
                deltas[key] = round(after - before, 3)

        # Calculate overall confidence (inverse of uncertainty), only when uncertainty was assessed on both sides
        before_unc = preflight_vectors.get("uncertainty")
        after_unc = postflight_vectors.get("uncertainty")
        if before_unc is not None and after_unc is not None:
            deltas["overall_confidence"] = round((1.0 - after_unc) - (1.0 - before_unc), 3)

        return deltas

    def _check_calibration(self, session_id: str, deltas: dict[str, float], end_assessment: dict) -> dict:
        """
        Get calibration status - prioritize genuine introspection, validate with heuristics

        For investigation handoffs (PREFLIGHT→CHECK), calibration is not applicable
        since CHECK is a decision gate, not a learning measurement.

        Returns:
            {
                'status': 'well_calibrated' | 'overconfident' | 'underconfident' | 'investigation-only',
                'reasoning': str,
                'source': 'introspection' | 'heuristic' | 'n/a',
                'heuristic_validation': Optional[str]  # If introspection available
            }
        """
        # Check if this is investigation handoff (CHECK phase)
        # Investigation handoffs don't have calibration - they're decision gates
        if end_assessment and end_assessment.get("phase") == "CHECK":
            return {
                "status": "investigation-only",
                "reasoning": "Investigation handoff (PREFLIGHT→CHECK) - calibration not applicable",
                "source": "n/a",
            }

        # PRIMARY: Use AI's genuine self-assessment from POSTFLIGHT
        # This is what the AI actually believed about their calibration during introspection
        try:
            vectors_data = self.db.get_latest_vectors(session_id, phase="POSTFLIGHT")
            if vectors_data:
                metadata = vectors_data.get("metadata", {})
                if metadata and isinstance(metadata, dict) and metadata.get("calibration_accuracy"):
                    genuine_status = metadata["calibration_accuracy"]
                    genuine_reasoning = vectors_data.get("reasoning", "Genuine self-assessment from POSTFLIGHT")

                    # Run heuristic validation for cross-check
                    heuristic_result = self._heuristic_calibration_check(deltas)

                    # Check if introspection matches heuristic
                    mismatch_note = None
                    if heuristic_result["status"] != genuine_status:
                        mismatch_note = f"Note: Heuristic suggests '{heuristic_result['status']}' but AI assessed '{genuine_status}' - trusting introspection"
                        logger.info(f"Calibration mismatch for {session_id[:8]}...: {mismatch_note}")

                    return {
                        "status": genuine_status,
                        "reasoning": genuine_reasoning,
                        "source": "introspection",
                        "heuristic_validation": mismatch_note,
                    }

        except Exception as e:
            logger.debug(f"Could not fetch genuine calibration: {e}")

        # FALLBACK: Heuristic calibration check
        # Use heuristic without warning for investigation handoffs
        heuristic_result = self._heuristic_calibration_check(deltas)
        heuristic_result["source"] = "heuristic"
        heuristic_result["reasoning"] += " (heuristic-based assessment)"

        return heuristic_result

    def _heuristic_calibration_check(self, deltas: dict[str, float]) -> dict:
        """
        Heuristic calibration check based on vector deltas

        Used as:
        1. Fallback when genuine introspection missing
        2. Validation check against AI's self-assessment
        """
        if "know" not in deltas or "uncertainty" not in deltas:
            # A comparison needs both deltas. Defaulting the missing one to 0.0 certified "knowledge gain matches uncertainty
            # reduction" about a comparison that was never made.
            return {
                "status": "not_assessable",
                "reasoning": "know or uncertainty was not assessed on both sides, so calibration cannot be judged",
            }
        know_delta = deltas["know"]
        uncertainty_delta = -deltas["uncertainty"]  # Negative because uncertainty should decrease

        # Well-calibrated: similar magnitudes
        if abs(know_delta - uncertainty_delta) < 0.15:
            status = "well_calibrated"
            reasoning = "Knowledge gain matches uncertainty reduction"
        # Overconfident: uncertainty reduced more than knowledge gained
        elif uncertainty_delta > know_delta + 0.20:
            status = "overconfident"
            reasoning = "Uncertainty reduced significantly but knowledge gain modest"
        # Underconfident: knowledge gained but uncertainty stayed high
        elif know_delta > uncertainty_delta + 0.20:
            status = "underconfident"
            reasoning = "Significant knowledge gained but uncertainty remains elevated"
        else:
            status = "well_calibrated"
            reasoning = "Reasonable epistemic progression"

        return {"status": status, "reasoning": reasoning}

    def _identify_filled_gaps(
        self, preflight_vectors: dict, postflight_vectors: dict, key_findings: list[str]
    ) -> list[dict]:
        """
        Identify which knowledge gaps were filled during session

        Returns:
            [
                {
                    'gap': 'Understanding of API patterns',
                    'before': 'Uncertain about factory methods',
                    'after': 'Fully documented Goal.create() and SubTask.create()',
                    'confidence_change': 0.25
                }
            ]
        """
        gaps = []

        # Check KNOW vector improvement
        know_delta = postflight_vectors.get("know", 0) - preflight_vectors.get("know", 0)
        if know_delta >= 0.15:  # Significant improvement
            gaps.append(
                {
                    "gap": "Domain knowledge",
                    "before": f"KNOW: {preflight_vectors.get('know', 0):.2f}",
                    "after": f"KNOW: {postflight_vectors.get('know', 0):.2f}",
                    "confidence_change": round(know_delta, 2),
                }
            )

        # Check UNCERTAINTY reduction
        uncertainty_delta = preflight_vectors.get("uncertainty", 1) - postflight_vectors.get("uncertainty", 1)
        if uncertainty_delta >= 0.20:  # Significant reduction
            gaps.append(
                {
                    "gap": "Task uncertainty",
                    "before": f"UNCERTAINTY: {preflight_vectors.get('uncertainty', 1):.2f}",
                    "after": f"UNCERTAINTY: {postflight_vectors.get('uncertainty', 1):.2f}",
                    "confidence_change": round(uncertainty_delta, 2),
                }
            )

        # Check CONTEXT improvement
        context_delta = postflight_vectors.get("context", 0) - preflight_vectors.get("context", 0)
        if context_delta >= 0.15:
            gaps.append(
                {
                    "gap": "Contextual understanding",
                    "before": f"CONTEXT: {preflight_vectors.get('context', 0):.2f}",
                    "after": f"CONTEXT: {postflight_vectors.get('context', 0):.2f}",
                    "confidence_change": round(context_delta, 2),
                }
            )

        # Extract from key findings (heuristic)
        for finding in (key_findings or [])[:3]:  # Top 3 findings
            if any(keyword in finding.lower() for keyword in ["learned", "discovered", "found", "validated"]):
                gaps.append(
                    {
                        "gap": "Investigation finding",
                        "before": "Unknown",
                        "after": finding[:150],  # Truncate
                        "confidence_change": None,
                    }
                )

        return gaps

    def _extract_noetic_tools(self, session_id: str) -> list[str]:
        """
        Extract which investigation tools were used during session

        Queries database for tool usage
        """
        tools_used = set()

        try:
            cursor = self.db.conn.cursor()

            # Query noetic_tools table if it exists
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='noetic_tools'
            """)

            if cursor.fetchone():
                cursor.execute(
                    """
                    SELECT DISTINCT tool_name FROM noetic_tools
                    WHERE session_id = ?
                """,
                    (session_id,),
                )

                for row in cursor.fetchall():
                    tools_used.add(row[0])

        except Exception as e:
            logger.debug(f"No investigation tools tracked: {e}")

        return sorted(tools_used) if tools_used else ["N/A"]

    def _generate_recommendations(
        self, postflight_vectors: dict, remaining_unknowns: list[str], calibration: dict
    ) -> list[str]:
        """
        Generate recommended next steps based on epistemic state
        """
        recommendations = []

        # NULL = not assessed, which is NOT zero: `get("uncertainty", 0)` read a CHECK that skipped uncertainty as "no uncertainty" and
        # told it "Ready for execution". A permissive recommendation needs every input it rests on.
        uncertainty = postflight_vectors.get("uncertainty")
        know = postflight_vectors.get("know")
        do = postflight_vectors.get("do")
        completion = postflight_vectors.get("completion")

        if uncertainty is None:
            recommendations.append("Uncertainty was not assessed in the latest check - assess it before acting")
        elif uncertainty > 0.40:
            recommendations.append(f"Continue investigation - uncertainty still elevated ({uncertainty:.2f})")

        # Check remaining unknowns
        if remaining_unknowns:
            recommendations.append(f"Address {len(remaining_unknowns)} remaining unknown(s)")

        # Check calibration
        if calibration["status"] == "overconfident":
            recommendations.append("Validate assumptions - showing overconfidence pattern")
        elif calibration["status"] == "underconfident":
            recommendations.append("Consider execution - showing underconfidence pattern")

        # Check if ready for next phase
        if (
            uncertainty is not None
            and know is not None
            and do is not None
            and uncertainty < 0.30
            and know > 0.80
            and do > 0.80
        ):
            recommendations.append("Ready for execution - strong epistemic foundation")

        # Check completion
        if completion is not None and completion >= 0.90:
            recommendations.append("Task appears complete - consider POSTFLIGHT review")

        # Default if no specific recommendations
        if not recommendations:
            recommendations.append("Continue with planned next steps")

        return recommendations

    def _get_session_metadata(self, session_id: str) -> dict:
        """Get session metadata from database"""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                SELECT ai_id, start_time, bootstrap_level, components_loaded
                FROM sessions
                WHERE session_id = ?
            """,
                (session_id,),
            )

            row = cursor.fetchone()
            if row:
                return {
                    "ai_id": row["ai_id"],
                    "start_time": row["start_time"],
                    "bootstrap_level": row["bootstrap_level"],
                    "components_loaded": row["components_loaded"],
                }
        except Exception as e:
            logger.warning(f"Failed to fetch session metadata: {e}")

        return {
            "ai_id": "unknown",
            "start_time": datetime.now().isoformat(),
            "bootstrap_level": 1,
            "components_loaded": 0,
        }

    def _calculate_duration(self, session_id: str) -> float:
        """Calculate session duration in seconds"""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute(
                """
                SELECT start_time, end_time FROM sessions
                WHERE session_id = ?
            """,
                (session_id,),
            )

            row = cursor.fetchone()
            if row and row["start_time"]:
                start = datetime.fromisoformat(row["start_time"])
                end = datetime.fromisoformat(row["end_time"]) if row["end_time"] else datetime.now()
                return (end - start).total_seconds()
        except Exception as e:
            logger.debug(f"Could not calculate duration: {e}")

        return 0.0

    def _generate_markdown(self, report: dict, start_assessment: dict, end_assessment: dict, calibration: dict) -> str:
        """Generate full markdown report"""

        # Format duration
        duration_min = report["duration_seconds"] / 60
        duration_str = f"{duration_min:.1f} min" if duration_min < 60 else f"{duration_min / 60:.1f} hrs"

        # Build vector delta table
        delta_table = self._build_delta_table(
            start_assessment.get("vectors", {}), end_assessment.get("vectors", {}), report["epistemic_deltas"]
        )

        # Format lists
        findings_md = "\n".join(f"- {f}" for f in report["key_findings"])
        unknowns_md = (
            "\n".join(f"- {u}" for u in report["remaining_unknowns"]) if report["remaining_unknowns"] else "- None"
        )
        recommendations_md = "\n".join(f"- {r}" for r in report["recommended_next_steps"])
        gaps_md = self._format_gaps(report["knowledge_gaps_filled"])
        artifacts_md = (
            "\n".join(f"- `{a}`" for a in report["artifacts_created"])
            if report["artifacts_created"]
            else "- None tracked"
        )
        tools_md = ", ".join(report["noetic_tools"])

        # Calibration indicator
        cal_status = report["calibration_status"]
        cal_source = calibration.get("source", "unknown")
        confidence_delta = report["overall_confidence_delta"]
        confidence_change = "n/a" if confidence_delta is None else f"{confidence_delta:+.3f}"
        cal_emoji = "✅" if cal_status == "well_calibrated" else "⚠️"

        # Show source and any validation notes
        cal_display = f"{cal_emoji} **{cal_status.replace('_', ' ').title()}** ({cal_source})"
        if calibration.get("heuristic_validation"):
            cal_display += f"\n\n> {calibration['heuristic_validation']}"

        markdown = f"""# Epistemic Handoff Report

**Session:** `{report["session_id"][:12]}...`
**AI Agent:** {report["ai_id"]}
**Date:** {report["timestamp"]}
**Duration:** {duration_str}
**Task:** {report["task_summary"]}

---

## Epistemic Trajectory

{delta_table}

**Overall Confidence Change:** {confidence_change}
**Calibration Status:** {cal_display}

---

## What I Accomplished

{findings_md}

---

## What I Learned

### Knowledge Gaps Filled

{gaps_md}

### Key Insights

{findings_md}

---

## Remaining Unknowns

{unknowns_md}

---

## Investigation Tools Used

{tools_md}

---

## Context for Next Session

{report["next_session_context"]}

---

## Recommended Next Steps

{recommendations_md}

---

## Artifacts Created

{artifacts_md}

---

**Generated:** {datetime.now().isoformat()}
**Format:** Epistemic Handoff Report v1.0
"""

        return markdown

    def _build_delta_table(self, start_vectors: dict, end_vectors: dict, deltas: dict) -> str:
        """Build markdown table of vector deltas"""

        # Group vectors by tier
        tiers = {
            "Foundation": ["know", "do", "context"],
            "Comprehension": ["clarity", "coherence", "signal", "density"],
            "Execution": ["state", "change", "completion", "impact"],
            "Meta": ["engagement", "uncertainty"],
        }

        lines = ["| Vector | Before | After | Delta | Status |", "|--------|--------|-------|-------|--------|"]

        for tier_name, vectors in tiers.items():
            lines.append(f"| **{tier_name}** | | | | |")

            for vec in vectors:
                before = start_vectors.get(vec)
                after = end_vectors.get(vec)
                delta = deltas.get(vec)

                if before is None or after is None or delta is None:
                    # Not assessed on one side: say so rather than print 0.00 for a value nobody gave
                    shown = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
                    lines.append(f"| {vec.upper()} | {shown(before)} | {shown(after)} | n/a | — |")
                    continue

                # Status indicator
                if abs(delta) < 0.05:
                    status = "→ Stable"
                elif delta > 0:
                    status = "✅ Improved" if vec != "uncertainty" else "⚠️ Increased"
                else:
                    status = "⚠️ Decreased" if vec != "uncertainty" else "✅ Reduced"

                lines.append(f"| {vec.upper()} | {before:.2f} | {after:.2f} | {delta:+.2f} | {status} |")

        return "\n".join(lines)

    def _format_gaps(self, gaps: list[dict]) -> str:
        """Format knowledge gaps as markdown"""
        if not gaps:
            return "- No significant knowledge gaps tracked"

        lines = []
        for gap in gaps:
            conf = gap.get("confidence_change")
            conf_str = f" (+{conf:.2f})" if conf else ""
            lines.append(f"- **{gap['gap']}**{conf_str}")
            lines.append(f"  - Before: {gap['before']}")
            lines.append(f"  - After: {gap['after']}")

        return "\n".join(lines)

    def _compress_report(self, report: dict) -> str:
        """
        Generate minimal JSON for storage (~800 tokens)

        Strips verbose markdown, keeps critical data
        """
        compressed = {
            "s": report["session_id"][:8],  # Short session ID
            "ai": report["ai_id"],
            "ts": report["timestamp"],
            "task": report["task_summary"][:200],  # Truncate
            "dur": round(report["duration_seconds"], 1),
            "deltas": {
                k: round(v, 2)
                for k, v in report["epistemic_deltas"].items()
                if abs(v) >= 0.10  # Only significant deltas
            },
            "findings": [f[:150] for f in report["key_findings"][:5]],  # Top 5, truncate
            "gaps": [
                {
                    "g": gap["gap"][:50],
                    "b": gap["before"][:50],
                    "a": gap["after"][:50],
                    "c": gap.get("confidence_change"),
                }
                for gap in report["knowledge_gaps_filled"][:3]  # Top 3
            ],
            "unknowns": [u[:100] for u in report["remaining_unknowns"][:5]],
            "next": report["next_session_context"][:300],
            "recommend": [r[:100] for r in report["recommended_next_steps"][:3]],
            "artifacts": [a[:100] for a in report["artifacts_created"][:10]],
            "tools": report["noetic_tools"][:5],
            "cal": report["calibration_status"],
        }

        return json.dumps(compressed, separators=(",", ":"))

    def _compress_planning_handoff(self, report: dict) -> str:
        """
        Generate minimal JSON for planning handoff storage

        Similar to epistemic handoff but without deltas/calibration
        """
        compressed = {
            "s": report["session_id"][:8],  # Short session ID
            "ai": report["ai_id"],
            "ts": report["timestamp"],
            "task": report["task_summary"][:200],  # Truncate
            "dur": round(report["duration_seconds"], 1),
            "type": "planning",  # Mark as planning
            "findings": [f[:150] for f in report["key_findings"][:5]],  # Top 5, truncate
            "unknowns": [u[:100] for u in report["remaining_unknowns"][:5]],
            "next": report["next_session_context"][:300],
            "artifacts": [a[:100] for a in report["artifacts_created"][:10]],
        }

        return json.dumps(compressed, separators=(",", ":"))
