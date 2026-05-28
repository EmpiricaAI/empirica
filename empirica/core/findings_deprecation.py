#!/usr/bin/env python3
"""
Epistemic Artifact Deprecation System

Implements N-step recursive loading with time decay, impact weighting,
and relevance scoring to prevent bootstrap bloat at scale.

Key insight: Not all findings are equally relevant. Recent high-impact
findings should be prioritized, while old low-impact findings fade.
"""

import logging
import math
from datetime import datetime
from typing import ClassVar

logger = logging.getLogger(__name__)


class FindingsDeprecationEngine:
    """Calculate relevance scores and filter findings by depth."""

    # Deprecation constants
    TIME_DECAY_TAU_DAYS = 30   # e-folding time constant for exp decay (~21d half-life)
    LONGEVITY_DECAY_K = 2.0    # longevity modulator lengthens tau: tau = TAU * (1 + K*longevity)
                               # (findings pass impact; lessons/eidetic pass confidence)
    COMPLETION_PENALTY = 0.3   # completed goals reduced by 30%
    DELTA_BOOST_FACTOR = 0.2   # execution state delta boost

    # Tier thresholds
    TIER_THRESHOLDS: ClassVar[dict[str, float]] = {
        "minimal": 0.80,    # Only high relevance (Tier 0)
        "moderate": 0.60,   # Recent context (Tiers 0-1)
        "full": 0.40,       # Extended history (Tiers 0-2)
        "complete": 0.0     # All findings (Tiers 0-3+)
    }

    @staticmethod
    def calculate_time_decay(created_timestamp, longevity: float | None = None) -> float:
        """
        Exponential time-decay weight: exp(-age_days / tau).

        Base tau = 30 days (e-folding time, ~21-day half-life: 30d -> 0.37,
        90d -> 0.05). NOTE: this is the e-folding constant, not the half-life —
        the earlier docstring claiming "30d -> 0.5" was wrong.

        Longevity modulation (David-locked, decay thread prop_j7y7f4): when
        `longevity` is given, high-longevity artifacts get a longer tau so
        durable knowledge does not fade like tactical noise:
            tau = 30 * (1 + 2*longevity)   # 0->30d, 0.5->60d, 1.0->90d
        The longevity modulator is impact for findings, and confidence for
        lessons/eidetic facts (a well-established fact stays relevant longer).
        e.g. a longevity-0.9 artifact: 30d->0.70, 90d->0.34, 180d->0.12.

        When `longevity` is None the curve is flat (current behaviour) — used by
        calculate_relevance_score, which already weights impact separately and
        must NOT double-count it here.

        Args:
            created_timestamp: Unix timestamp (float or numeric string) of creation
            longevity: Optional 0.0-1.0 modulator (impact|confidence); lengthens tau

        Returns:
            Float 0.0-1.0, where 1.0 = just created, ~0.0 = very old
        """
        # Handle string timestamps (numeric only; ISO strings must be normalised
        # to unix by the caller — float() would raise and we'd assume 0.5).
        if isinstance(created_timestamp, str):
            try:
                created_timestamp = float(created_timestamp)
            except (ValueError, TypeError):
                # If can't parse, assume recent (score = 0.5)
                return 0.5

        now = datetime.now().timestamp()
        age_days = (now - created_timestamp) / 86400

        tau = FindingsDeprecationEngine.TIME_DECAY_TAU_DAYS
        if longevity is not None:
            tau *= 1 + FindingsDeprecationEngine.LONGEVITY_DECAY_K * max(0.0, min(1.0, longevity))

        decay = math.exp(-age_days / tau)
        return max(0.0, min(1.0, decay))

    @staticmethod
    def calculate_completion_factor(goal_completion: float | None) -> float:
        """
        Calculate completion penalty factor.

        Completed goals (completion=1.0) reduce by 30%
        In-progress goals (completion=0.0) full weight

        Args:
            goal_completion: Goal completion percentage 0.0-1.0 or None

        Returns:
            Multiplication factor 0.7-1.0
        """
        if goal_completion is None:
            return 1.0

        # 1.0 - (completion * 0.3)
        # completion=0: factor=1.0
        # completion=1: factor=0.7
        return max(0.7, 1.0 - (goal_completion * FindingsDeprecationEngine.COMPLETION_PENALTY))

    @staticmethod
    def calculate_relevance_score(
        finding: dict,
        execution_state_delta: float = 0.0,
        goal_completion: float | None = None
    ) -> float:
        """
        Calculate 0.0-1.0 relevance score for a finding.

        Factors:
        - 40% time decay (newer = higher)
        - 30% impact weight * completion factor
        - 20% execution state delta (learning boost)
        - 10% task semantic similarity (future enhancement)

        Args:
            finding: Finding dict with 'created_timestamp', 'impact'
            execution_state_delta: State improvement in current session
            goal_completion: Goal completion percentage if applicable

        Returns:
            Float 0.0-1.0 relevance score
        """
        # Component 1: Time decay (40%) — flat curve here ON PURPOSE: impact is
        # weighted separately at component 2, so passing impact into the decay
        # would double-count it. The impact-modulated tau is for the standalone
        # recency-rerank path (pattern_retrieval._apply_findings_recency).
        created_ts = finding.get('created_timestamp')
        if not created_ts:
            time_score = 0.5
        else:
            time_score = FindingsDeprecationEngine.calculate_time_decay(created_ts)

        # Component 2: Impact weight * completion factor (30%)
        impact = finding.get('impact')
        if impact is None:
            impact = 0.5  # Default for NULL values

        completion_factor = FindingsDeprecationEngine.calculate_completion_factor(goal_completion)
        impact_score = impact * completion_factor

        # Component 3: Execution state delta (20%)
        # Boost relevance if there's significant learning happening
        delta_score = 1.0 + (execution_state_delta * FindingsDeprecationEngine.DELTA_BOOST_FACTOR)
        delta_score = max(0.8, min(1.2, delta_score))  # Clamp 0.8-1.2

        # Component 4: Task semantic match (10%)
        task_match = 0.5  # Default neutral

        # Weighted combination
        relevance = (
            0.40 * time_score +
            0.30 * impact_score +
            0.20 * delta_score +
            0.10 * task_match
        )

        return max(0.0, min(1.0, relevance))

    @staticmethod
    def filter_by_depth(
        findings: list[dict],
        depth: str = "auto",
        relevance_scores: list[float] | None = None,
        uncertainty: float = 0.5
    ) -> list[dict]:
        """
        Filter findings by depth tier and relevance threshold.

        Depth options:
        - "minimal": Only high-relevance (threshold 0.80)
        - "moderate": Recent context (threshold 0.60)
        - "full": Extended history (threshold 0.40)
        - "complete": All findings (threshold 0.0)
        - "auto": Based on uncertainty
            * uncertainty > 0.5: "full" (need context)
            * 0.3 < uncertainty <= 0.5: "moderate"
            * uncertainty <= 0.3: "minimal" (confident, focused)

        Args:
            findings: List of finding dicts
            depth: Depth level string
            relevance_scores: Pre-calculated scores (else calculate)
            uncertainty: Current epistemic uncertainty 0.0-1.0

        Returns:
            Filtered list of findings
        """
        if not findings:
            return []

        # Calculate scores if not provided
        if relevance_scores is None:
            relevance_scores = [
                FindingsDeprecationEngine.calculate_relevance_score(f)
                for f in findings
            ]

        # Determine actual depth
        if depth == "auto":
            if uncertainty > 0.5:
                depth = "full"
            elif uncertainty > 0.3:
                depth = "moderate"
            else:
                depth = "minimal"

        # Get threshold
        threshold = FindingsDeprecationEngine.TIER_THRESHOLDS.get(depth, 0.0)

        # Filter by threshold
        filtered = [
            finding for finding, score in zip(findings, relevance_scores)
            if score >= threshold
        ]

        logger.info(
            f"Filtered findings: {len(filtered)}/{len(findings)} "
            f"(depth={depth}, threshold={threshold:.2f})"
        )

        return filtered

    @staticmethod
    def get_findings_summary(
        findings: list[dict],
        relevance_scores: list[float] | None = None
    ) -> dict:
        """
        Generate summary statistics about findings relevance distribution.

        Returns:
            Dict with count, avg_relevance, tier_distribution
        """
        if not findings:
            return {
                "total": 0,
                "loaded": 0,
                "avg_relevance": 0.0,
                "tier_distribution": {}
            }

        if relevance_scores is None:
            relevance_scores = [
                FindingsDeprecationEngine.calculate_relevance_score(f)
                for f in findings
            ]

        # Count by tier
        tiers = {
            "Tier 0 (high)": sum(1 for s in relevance_scores if s >= 0.80),
            "Tier 1 (medium)": sum(1 for s in relevance_scores if 0.60 <= s < 0.80),
            "Tier 2 (low)": sum(1 for s in relevance_scores if 0.40 <= s < 0.60),
            "Tier 3+ (archive)": sum(1 for s in relevance_scores if s < 0.40),
        }

        return {
            "total": len(findings),
            "loaded": len(findings),
            "avg_relevance": sum(relevance_scores) / len(relevance_scores),
            "tier_distribution": tiers
        }
