"""
Bayesian Belief Manager

Updates AI priors based on historical performance.
Implements calibration loop: POSTFLIGHT deltas → update beliefs → inform next PREFLIGHT.

NOTE (2026-01-21): Primary calibration source is now `vector_trajectories` table which has
clean start/end vectors for 253+ sessions. This module is kept for:
1. POSTFLIGHT belief updates (proper PREFLIGHT→POSTFLIGHT comparison)
2. .breadcrumbs.yaml calibration export
3. Backward compatibility

DO NOT use update_belief() (singular) during CHECK - it pollutes calibration data.
Only update_beliefs() (plural) at POSTFLIGHT does proper baseline comparison.

Bayesian Update:
    posterior_mean = (prior_var * observation + obs_var * prior_mean) / (prior_var + obs_var)
    posterior_var = 1 / (1/prior_var + 1/obs_var)

Author: Claude Code
Date: 2025-12-30
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


@dataclass
class Belief:
    """A Bayesian belief about a vector."""

    vector_name: str
    mean: float
    variance: float
    evidence_count: int
    prior_mean: float
    prior_variance: float
    last_updated: datetime


class BayesianBeliefManager:
    """
    Manages Bayesian beliefs for epistemic vector calibration.

    Tracks how well AI self-assessments predict actual outcomes,
    updating priors to improve future assessments.
    """

    # Default priors for new vectors (uninformative)
    DEFAULT_PRIOR_MEAN = 0.5
    DEFAULT_PRIOR_VARIANCE = 0.25  # High variance = uncertain

    # Observation variance (how much we trust single observations)
    OBSERVATION_VARIANCE = 0.1

    # Maximum magnitude for bias corrections (prevents calibration drift)
    # ±0.25 is generous: know=0.45 corrects to 0.70 (threshold), not beyond
    MAX_CORRECTION_MAGNITUDE = 0.25

    # Vectors to track
    TRACKED_VECTORS: ClassVar[list[str]] = [
        "engagement",
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
        "uncertainty",
    ]

    def __init__(self, db):
        """Initialize with database connection."""
        self.db = db
        self.conn = db.conn

    def get_beliefs(self, ai_id: str) -> dict[str, Belief]:
        """
        Get current beliefs for an AI.

        Returns beliefs aggregated across all sessions for this AI.
        """
        cursor = self.conn.cursor()

        # Get most recent belief for each vector
        cursor.execute(
            """
            SELECT bb.vector_name, bb.mean, bb.variance, bb.evidence_count,
                   bb.prior_mean, bb.prior_variance, bb.last_updated
            FROM bayesian_beliefs bb
            JOIN cascades c ON bb.cascade_id = c.cascade_id
            JOIN sessions s ON c.session_id = s.session_id
            WHERE s.ai_id = ?
            ORDER BY bb.last_updated DESC
        """,
            (ai_id,),
        )

        beliefs = {}
        seen_vectors = set()

        for row in cursor.fetchall():
            vector_name = row[0]
            if vector_name not in seen_vectors:
                beliefs[vector_name] = Belief(
                    vector_name=vector_name,
                    mean=row[1],
                    variance=row[2],
                    evidence_count=row[3],
                    prior_mean=row[4],
                    prior_variance=row[5],
                    last_updated=row[6],
                )
                seen_vectors.add(vector_name)

        # Fill in defaults for missing vectors
        for vector in self.TRACKED_VECTORS:
            if vector not in beliefs:
                beliefs[vector] = Belief(
                    vector_name=vector,
                    mean=self.DEFAULT_PRIOR_MEAN,
                    variance=self.DEFAULT_PRIOR_VARIANCE,
                    evidence_count=0,
                    prior_mean=self.DEFAULT_PRIOR_MEAN,
                    prior_variance=self.DEFAULT_PRIOR_VARIANCE,
                    last_updated=None,
                )

        return beliefs

    def get_calibration_adjustments(self, ai_id: str) -> dict[str, float]:
        """
        Get calibration adjustments for PREFLIGHT based on historical beliefs.

        Returns adjustment factors: positive means AI tends to underestimate,
        negative means AI tends to overestimate.
        """
        beliefs = self.get_beliefs(ai_id)
        adjustments = {}

        for vector, belief in beliefs.items():
            if belief.evidence_count >= 3:  # Need sufficient evidence
                # Compare mean (actual performance) to prior_mean (self-assessment)
                # If mean > prior_mean: AI underestimates (adjustment positive)
                # If mean < prior_mean: AI overestimates (adjustment negative)
                adjustment = belief.mean - belief.prior_mean

                # Weight by confidence (inverse variance)
                confidence = 1.0 / (belief.variance + 0.01)
                confidence = min(confidence, 10.0)  # Cap confidence

                # Scale adjustment by evidence count (more evidence = trust adjustment more)
                evidence_weight = min(belief.evidence_count / 10.0, 1.0)

                raw_adjustment = adjustment * evidence_weight

                # Cap correction magnitude to prevent calibration drift
                # Without this, corrections grow unbounded and override honest self-assessments
                capped = max(-self.MAX_CORRECTION_MAGNITUDE, min(self.MAX_CORRECTION_MAGNITUDE, raw_adjustment))
                adjustments[vector] = capped

        return adjustments

    def update_belief(
        self, session_id: str, vector_name: str, observation: float, phase: str = "CHECK", round_num: int = 1
    ) -> dict | None:
        """
        Update belief for a single vector during CHECK phase.

        This is the incremental update interface, used during mid-loop assessments.
        For full PREFLIGHT→POSTFLIGHT comparison, use update_beliefs() instead.

        Args:
            session_id: The session ID
            vector_name: The vector to update (e.g., 'know', 'uncertainty')
            observation: The observed/assessed value
            phase: The CASCADE phase (CHECK, POSTFLIGHT)
            round_num: The round number within the phase

        Returns:
            Dict with belief update details, or None if update failed
        """
        if vector_name not in self.TRACKED_VECTORS:
            return None

        cursor = self.conn.cursor()

        # Get AI ID for this session
        cursor.execute("SELECT ai_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            return None
        ai_id = row[0]

        # Get current belief for this vector
        current_beliefs = self.get_beliefs(ai_id)
        belief = current_beliefs.get(vector_name)

        if not belief:
            prior_mean = self.DEFAULT_PRIOR_MEAN
            prior_var = self.DEFAULT_PRIOR_VARIANCE
            evidence_count = 0
        else:
            prior_mean = belief.mean
            prior_var = belief.variance
            evidence_count = belief.evidence_count

        # Bayesian update
        obs_var = self.OBSERVATION_VARIANCE

        posterior_mean = (prior_var * observation + obs_var * prior_mean) / (prior_var + obs_var)
        posterior_var = 1.0 / (1.0 / prior_var + 1.0 / obs_var)
        new_evidence_count = evidence_count + 1

        # Get or create cascade_id for this session
        cursor.execute(
            """
            SELECT cascade_id FROM cascades
            WHERE session_id = ?
            ORDER BY started_at DESC LIMIT 1
        """,
            (session_id,),
        )
        cascade_row = cursor.fetchone()
        cascade_id = cascade_row[0] if cascade_row else str(uuid.uuid4())

        # Store the update
        belief_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO bayesian_beliefs (
                belief_id, cascade_id, vector_name,
                mean, variance, evidence_count,
                prior_mean, prior_variance, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                belief_id,
                cascade_id,
                vector_name,
                posterior_mean,
                posterior_var,
                new_evidence_count,
                observation,
                self.OBSERVATION_VARIANCE,
                datetime.now(),
            ),
        )

        self.conn.commit()

        return {
            "vector_name": vector_name,
            "phase": phase,
            "round_num": round_num,
            "prior_mean": prior_mean,
            "prior_variance": prior_var,
            "observation": observation,
            "posterior_mean": posterior_mean,
            "posterior_variance": posterior_var,
            "evidence_count": new_evidence_count,
            "calibration_delta": observation - prior_mean,
        }

    def update_beliefs(
        self,
        cascade_id: str,
        session_id: str,
        preflight_vectors: dict[str, float],
        postflight_vectors: dict[str, float],
    ) -> dict[str, dict]:
        """
        Update beliefs based on PREFLIGHT → POSTFLIGHT comparison.

        The delta between self-assessment (preflight) and measured outcome (postflight)
        becomes evidence for updating our beliefs about this AI's calibration.

        Args:
            cascade_id: The CASCADE cycle ID
            session_id: The session ID
            preflight_vectors: Self-assessed vectors at session start
            postflight_vectors: Measured vectors at session end

        Returns:
            Dict of updated beliefs with deltas
        """
        cursor = self.conn.cursor()

        # Get AI ID for this session
        cursor.execute("SELECT ai_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            return {}
        ai_id = row[0]

        # Get current beliefs
        current_beliefs = self.get_beliefs(ai_id)

        updates = {}

        for vector in self.TRACKED_VECTORS:
            pre_val = preflight_vectors.get(vector)
            post_val = postflight_vectors.get(vector)

            if pre_val is None or post_val is None:
                continue

            # The "observation" is the actual outcome (postflight)
            observation = post_val

            # Get current belief
            belief = current_beliefs.get(vector)
            if not belief:
                prior_mean = self.DEFAULT_PRIOR_MEAN
                prior_var = self.DEFAULT_PRIOR_VARIANCE
                evidence_count = 0
            else:
                prior_mean = belief.mean
                prior_var = belief.variance
                evidence_count = belief.evidence_count

            # Bayesian update
            obs_var = self.OBSERVATION_VARIANCE

            # Posterior mean: weighted average of prior and observation
            posterior_mean = (prior_var * observation + obs_var * prior_mean) / (prior_var + obs_var)

            # Posterior variance: decreases with more evidence
            posterior_var = 1.0 / (1.0 / prior_var + 1.0 / obs_var)

            # Increment evidence count
            new_evidence_count = evidence_count + 1

            # Store the update
            belief_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO bayesian_beliefs (
                    belief_id, cascade_id, vector_name,
                    mean, variance, evidence_count,
                    prior_mean, prior_variance, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    belief_id,
                    cascade_id,
                    vector,
                    posterior_mean,
                    posterior_var,
                    new_evidence_count,
                    pre_val,
                    self.OBSERVATION_VARIANCE,
                    datetime.now(),
                ),
            )

            updates[vector] = {
                "prior_mean": prior_mean,
                "prior_variance": prior_var,
                "observation": observation,
                "posterior_mean": posterior_mean,
                "posterior_variance": posterior_var,
                "evidence_count": new_evidence_count,
                "calibration_delta": observation - pre_val,  # How wrong was self-assessment
            }

        self.conn.commit()
        return updates

    def get_calibration_report(self, ai_id: str) -> dict:
        """
        Generate a calibration report for an AI.

        Shows how well-calibrated the AI's self-assessments are.
        """
        beliefs = self.get_beliefs(ai_id)
        adjustments = self.get_calibration_adjustments(ai_id)

        report: dict[str, Any] = {
            "ai_id": ai_id,
            "total_evidence": sum(b.evidence_count for b in beliefs.values()),
            "vectors": {},
            "calibration_summary": {"overestimates": [], "underestimates": [], "well_calibrated": []},
        }

        for vector, belief in beliefs.items():
            adjustment = adjustments.get(vector, 0)

            vector_report = {
                "mean": belief.mean,
                "variance": belief.variance,
                "evidence_count": belief.evidence_count,
                "adjustment": adjustment,
                "confidence": 1.0 / (belief.variance + 0.01) if belief.variance else 0,
            }
            report["vectors"][vector] = vector_report

            # Categorize calibration
            if belief.evidence_count >= 3:
                if adjustment < -0.1:
                    report["calibration_summary"]["overestimates"].append(vector)
                elif adjustment > 0.1:
                    report["calibration_summary"]["underestimates"].append(vector)
                else:
                    report["calibration_summary"]["well_calibrated"].append(vector)

        return report


def apply_calibration_to_vectors(vectors: dict[str, float], adjustments: dict[str, float]) -> dict[str, float]:
    """
    Apply calibration adjustments to self-assessed vectors.

    Used during PREFLIGHT to correct for known biases.
    """
    calibrated = {}

    for vector, value in vectors.items():
        adjustment = adjustments.get(vector, 0)
        # Apply adjustment, clamping to [0, 1]
        calibrated[vector] = max(0.0, min(1.0, value + adjustment))

    return calibrated


def _find_yaml_section(
    lines: list[str],
    comments: list[str],
    headers: list[str],
) -> tuple[int, int]:
    """Find start/end indices of a YAML section by comment and header markers.

    Returns (section_start, section_end). Both are -1 if not found.
    """
    section_start = -1
    section_end = -1
    in_section = False

    for i, line in enumerate(lines):
        if any(c in line for c in comments) and section_start == -1:
            section_start = i
        elif any(line.strip().startswith(h) for h in headers):
            if section_start == -1:
                section_start = i
            in_section = True
        elif in_section and line.strip() and not line.startswith(" ") and not line.startswith("\t"):
            section_end = i
            break

    if in_section and section_end == -1:
        section_end = len(lines)

    return section_start, section_end


def export_calibration_to_breadcrumbs(ai_id: str, db, git_root: str | None = None) -> bool:
    """
    Export calibration data to .breadcrumbs.yaml for instant session-start availability.

    This creates a calibration cache layer that doesn't require DB queries.
    Called automatically after POSTFLIGHT to keep calibration fresh.

    Args:
        ai_id: The AI identifier (e.g., 'claude-code')
        db: Database connection with bayesian_beliefs access
        git_root: Git repository root (auto-detects if None)

    Returns:
        True if calibration was written successfully
    """
    import os
    import subprocess
    from datetime import datetime

    # Auto-detect git root if not provided
    if not git_root:
        try:
            result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                git_root = result.stdout.strip()
            else:
                # Not a git checkout: no breadcrumbs file to write. Quiet and correct.
                return False
        except Exception as e:
            logger.warning(
                f"calibration export: could not resolve git root ({type(e).__name__}: {e}); breadcrumbs NOT refreshed"
            )
            return False

    breadcrumbs_path = os.path.join(git_root, ".breadcrumbs.yaml")

    # Get calibration data
    try:
        belief_manager = BayesianBeliefManager(db)
        adjustments = belief_manager.get_calibration_adjustments(ai_id)
        report = belief_manager.get_calibration_report(ai_id)

        if not adjustments and not report:
            return False  # nothing to export yet — a fresh practice, not a failure
    except Exception as e:
        # The caller logs a False at debug. Without this line a persistent
        # failure here leaves .breadcrumbs.yaml frozen at its last export while
        # every SessionStart keeps injecting it as current calibration.
        logger.warning(
            f"calibration export: reading beliefs FAILED ({type(e).__name__}: {e}); breadcrumbs NOT refreshed"
        )
        return False

    # Read existing .breadcrumbs.yaml if present
    existing_lines = []
    calibration_start = -1
    calibration_end = -1

    if os.path.exists(breadcrumbs_path):
        try:
            with open(breadcrumbs_path) as f:
                existing_lines = f.readlines()
        except OSError as e:
            # Raised outside every try until now; the caller's broad except
            # turned it into a debug line and a frozen calibration cache.
            logger.warning(
                f"calibration export: reading {breadcrumbs_path} FAILED ({type(e).__name__}: {e}); breadcrumbs NOT refreshed"
            )
            return False

        calibration_start, calibration_end = _find_yaml_section(
            existing_lines,
            comments=["# Bayesian calibration", "# Learning trajectory"],
            headers=["calibration:", "learning_trajectory:"],
        )

    # Build calibration YAML block
    timestamp = datetime.now().isoformat()
    total_evidence = report.get("total_evidence", 0) if report else 0
    summary = report.get("calibration_summary", {}) if report else {}

    # Sort adjustments by magnitude (most impactful first)
    sorted_adjustments = sorted(adjustments.items(), key=lambda x: abs(x[1]), reverse=True)

    calibration_yaml = f"""
# Learning trajectory (PREFLIGHT→POSTFLIGHT deltas — measures learning, NOT calibration bias)
# For actual bias corrections see grounded_calibration.grounded_bias_corrections
learning_trajectory:
  last_updated: "{timestamp}"
  ai_id: {ai_id}
  observations: {total_evidence}
  session_deltas:
"""

    for vector, adj in sorted_adjustments:
        sign = "+" if adj >= 0 else ""
        calibration_yaml += f"    {vector}: {sign}{adj:.2f}\n"

    calibration_yaml += """  readiness:
    min_know: 0.70
    max_uncertainty: 0.35
  summary:
"""

    overestimates = summary.get("overestimates", [])
    underestimates = summary.get("underestimates", [])
    well_calibrated = summary.get("well_calibrated", [])

    calibration_yaml += f"    overestimates: [{', '.join(overestimates)}]\n"
    calibration_yaml += f"    underestimates: [{', '.join(underestimates)}]\n"
    calibration_yaml += f"    well_calibrated: [{', '.join(well_calibrated[:5])}]\n"

    # Write updated file
    try:
        if calibration_start >= 0:
            # Replace existing calibration section
            new_lines = existing_lines[:calibration_start] + [calibration_yaml] + existing_lines[calibration_end:]
        elif existing_lines:
            # Append to existing file
            new_lines = existing_lines + ["\n", calibration_yaml]
        else:
            # New file with default breadcrumbs config + calibration
            new_lines = [
                "# Breadcrumbs Configuration\n",
                "# Auto-generated with Empirica calibration\n",
                "\n",
                "git:\n",
                "  recent_commits: 5\n",
                "  modified_files: true\n",
                "  current_branch: true\n",
                "\n",
                "epistemic:\n",
                "  enabled: true\n",
                '  scale: "1-5 (1=guessing, 3=reasonable, 5=certain)"\n',
                "  track_uncertainties: true\n",
                "  track_decisions: true\n",
                "\n",
                "task:\n",
                "  extract_last_task: 500\n",
                calibration_yaml,
            ]

        with open(breadcrumbs_path, "w") as f:
            f.writelines(new_lines)

        return True
    except Exception as e:
        logger.warning(
            f"calibration export: writing {breadcrumbs_path} FAILED ({type(e).__name__}: {e}); breadcrumbs NOT refreshed"
        )
        return False
