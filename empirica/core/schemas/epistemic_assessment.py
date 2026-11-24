"""
Canonical Epistemic Assessment Schema

This is THE single source of truth for the 13-vector epistemic assessment format.

Used by:
- CLI parser (empirica preflight-submit, check-submit, postflight-submit)
- PersonaHarness (apply persona priors)
- SentinelOrchestrator (merge multi-persona assessments)
- Validation layer (ensure format correctness)

Format: Nested structure with foundation/comprehension/execution tiers
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AssessmentType(Enum):
    """
    Explicit epistemic assessment checkpoints.

    These represent the three explicit assessment moments in the CASCADE workflow:
    - PRE: Baseline epistemic state at session start (was PREFLIGHT)
    - CHECK: Decision point assessment (can occur 0-N times during workflow)
    - POST: Final calibration assessment at session end (was POSTFLIGHT)

    Note: CASCADE workflow phases (think, investigate, act) are implicit guidance,
    not tracked as explicit states. Only these assessment checkpoints are tracked.
    """

    PRE = "pre"
    CHECK = "check"
    POST = "post"


class CascadePhase(Enum):
    """
    DEPRECATED: Use AssessmentType instead.

    CASCADE workflow phases. This enum is deprecated in favor of AssessmentType
    which distinguishes explicit assessment checkpoints (PRE/CHECK/POST) from
    implicit workflow guidance (think/investigate/act).

    Migration:
    - PREFLIGHT → AssessmentType.PRE
    - CHECK → AssessmentType.CHECK
    - POSTFLIGHT → AssessmentType.POST
    - THINK, INVESTIGATE, ACT → No longer tracked as explicit states

    Note: Deprecation is documented here. Usage-site warnings will be added in Phase 2.
    """

    PREFLIGHT = "preflight"
    THINK = "think"
    INVESTIGATE = "investigate"
    CHECK = "check"
    ACT = "act"
    POSTFLIGHT = "postflight"


@dataclass
class VectorAssessment:
    """
    Single epistemic vector assessment

    Attributes:
        score: Confidence/assessment value (0.0-1.0)
        rationale: GENUINE reasoning (not template, not heuristic)
        evidence: Optional supporting facts
        warrants_investigation: Whether this vector triggers investigation
        investigation_priority: Priority if investigation warranted (0-10)
    """

    score: float
    rationale: str
    evidence: str | None = None
    warrants_investigation: bool = False
    investigation_priority: int = 0

    def __post_init__(self):
        """Validate score range"""
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"Score must be in [0.0, 1.0], got {self.score}")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        result = {"score": self.score, "rationale": self.rationale}
        if self.evidence:
            result["evidence"] = self.evidence
        if self.warrants_investigation:
            result["warrants_investigation"] = self.warrants_investigation
            result["investigation_priority"] = self.investigation_priority
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VectorAssessment":
        """Parse from dictionary"""
        return cls(
            score=float(data["score"]),
            rationale=str(data["rationale"]),
            evidence=data.get("evidence"),
            warrants_investigation=data.get("warrants_investigation", False),
            investigation_priority=data.get("investigation_priority", 0),
        )


@dataclass
class EpistemicAssessmentSchema:
    """
    Canonical 13-vector epistemic assessment

    This is THE format for all epistemic assessments across:
    - PREFLIGHT, THINK, INVESTIGATE, CHECK, ACT, POSTFLIGHT phases
    - CLI commands, PersonaHarness, SentinelOrchestrator
    - Human assessments and AI self-assessments

    Structure:
    - Gate: engagement (must pass threshold to proceed)
    - Foundation (Tier 0): know, do, context
    - Comprehension (Tier 1): clarity, coherence, signal, density
    - Execution (Tier 2): state, change, completion, impact
    - Meta: uncertainty (high uncertainty → investigate)
    """

    # Gate
    engagement: VectorAssessment

    # Foundation (Tier 0)
    foundation_know: VectorAssessment
    foundation_do: VectorAssessment
    foundation_context: VectorAssessment

    # Comprehension (Tier 1)
    comprehension_clarity: VectorAssessment
    comprehension_coherence: VectorAssessment
    comprehension_signal: VectorAssessment
    comprehension_density: VectorAssessment

    # Execution (Tier 2)
    execution_state: VectorAssessment
    execution_change: VectorAssessment
    execution_completion: VectorAssessment
    execution_impact: VectorAssessment

    # Meta
    uncertainty: VectorAssessment

    # Metadata
    phase: CascadePhase = CascadePhase.PREFLIGHT
    round_num: int = 0
    investigation_count: int = 0

    def to_nested_dict(self) -> dict[str, Any]:
        """
        Convert to nested format (for the CLI)

        Returns nested dict with structure:
        {
          "engagement": {"score": 0.85, "rationale": "..."},
          "foundation": {
            "know": {"score": 0.70, "rationale": "..."},
            "do": {"score": 0.75, "rationale": "..."},
            "context": {"score": 0.80, "rationale": "..."}
          },
          "comprehension": {...},
          "execution": {...},
          "uncertainty": {"score": 0.40, "rationale": "..."}
        }
        """
        return {
            "engagement": self.engagement.to_dict(),
            "foundation": {
                "know": self.foundation_know.to_dict(),
                "do": self.foundation_do.to_dict(),
                "context": self.foundation_context.to_dict(),
            },
            "comprehension": {
                "clarity": self.comprehension_clarity.to_dict(),
                "coherence": self.comprehension_coherence.to_dict(),
                "signal": self.comprehension_signal.to_dict(),
                "density": self.comprehension_density.to_dict(),
            },
            "execution": {
                "state": self.execution_state.to_dict(),
                "change": self.execution_change.to_dict(),
                "completion": self.execution_completion.to_dict(),
                "impact": self.execution_impact.to_dict(),
            },
            "uncertainty": self.uncertainty.to_dict(),
        }

    def to_flat_dict(self) -> dict[str, float]:
        """
        Convert to flat score dictionary (for storage/comparison)

        Returns: {
            "engagement": 0.85,
            "know": 0.70,
            "do": 0.75,
            ...
        }
        """
        return {
            "engagement": self.engagement.score,
            "know": self.foundation_know.score,
            "do": self.foundation_do.score,
            "context": self.foundation_context.score,
            "clarity": self.comprehension_clarity.score,
            "coherence": self.comprehension_coherence.score,
            "signal": self.comprehension_signal.score,
            "density": self.comprehension_density.score,
            "state": self.execution_state.score,
            "change": self.execution_change.score,
            "completion": self.execution_completion.score,
            "impact": self.execution_impact.score,
            "uncertainty": self.uncertainty.score,
        }

    @classmethod
    def from_nested_dict(
        cls, data: dict[str, Any], phase: CascadePhase = CascadePhase.PREFLIGHT
    ) -> "EpistemicAssessmentSchema":
        """
        Parse from nested format (CLI input)

        Args:
            data: Nested dict with foundation/comprehension/execution structure
            phase: Current CASCADE phase

        Returns:
            EpistemicAssessmentSchema instance
        """
        return cls(
            engagement=VectorAssessment.from_dict(data["engagement"]),
            foundation_know=VectorAssessment.from_dict(data["foundation"]["know"]),
            foundation_do=VectorAssessment.from_dict(data["foundation"]["do"]),
            foundation_context=VectorAssessment.from_dict(data["foundation"]["context"]),
            comprehension_clarity=VectorAssessment.from_dict(data["comprehension"]["clarity"]),
            comprehension_coherence=VectorAssessment.from_dict(data["comprehension"]["coherence"]),
            comprehension_signal=VectorAssessment.from_dict(data["comprehension"]["signal"]),
            comprehension_density=VectorAssessment.from_dict(data["comprehension"]["density"]),
            execution_state=VectorAssessment.from_dict(data["execution"]["state"]),
            execution_change=VectorAssessment.from_dict(data["execution"]["change"]),
            execution_completion=VectorAssessment.from_dict(data["execution"]["completion"]),
            execution_impact=VectorAssessment.from_dict(data["execution"]["impact"]),
            uncertainty=VectorAssessment.from_dict(data["uncertainty"]),
            phase=phase,
        )

    def apply_persona_priors(
        self, persona_priors: dict[str, float], strength: float = 1.0
    ) -> "EpistemicAssessmentSchema":
        """
        Apply persona priors to this assessment

        Blends baseline assessment with persona-specific domain knowledge.

        Args:
            persona_priors: Dict of prior values {"know": 0.90, "uncertainty": 0.15, ...}
            strength: How strongly to apply priors (0.0-1.0)
                     1.0 = full persona expertise (PREFLIGHT)
                     0.8 = strong influence (THINK)
                     0.5 = moderate influence (other phases)

        Returns:
            New EpistemicAssessmentSchema with priors applied
        """

        def blend_vector(vector: VectorAssessment, prior: float, vector_name: str) -> VectorAssessment:
            """Blend baseline with persona prior"""
            blended_score = vector.score * (1 - strength) + prior * strength
            blended_rationale = f"{vector.rationale} [Persona prior: {prior:.2f}, strength: {strength:.1f}]"

            return VectorAssessment(
                score=blended_score,
                rationale=blended_rationale,
                evidence=vector.evidence,
                warrants_investigation=vector.warrants_investigation,
                investigation_priority=vector.investigation_priority,
            )

        return EpistemicAssessmentSchema(
            engagement=blend_vector(self.engagement, persona_priors["engagement"], "engagement"),
            foundation_know=blend_vector(self.foundation_know, persona_priors["know"], "know"),
            foundation_do=blend_vector(self.foundation_do, persona_priors["do"], "do"),
            foundation_context=blend_vector(self.foundation_context, persona_priors["context"], "context"),
            comprehension_clarity=blend_vector(self.comprehension_clarity, persona_priors["clarity"], "clarity"),
            comprehension_coherence=blend_vector(
                self.comprehension_coherence, persona_priors["coherence"], "coherence"
            ),
            comprehension_signal=blend_vector(self.comprehension_signal, persona_priors["signal"], "signal"),
            comprehension_density=blend_vector(self.comprehension_density, persona_priors["density"], "density"),
            execution_state=blend_vector(self.execution_state, persona_priors["state"], "state"),
            execution_change=blend_vector(self.execution_change, persona_priors["change"], "change"),
            execution_completion=blend_vector(self.execution_completion, persona_priors["completion"], "completion"),
            execution_impact=blend_vector(self.execution_impact, persona_priors["impact"], "impact"),
            uncertainty=blend_vector(self.uncertainty, persona_priors["uncertainty"], "uncertainty"),
            phase=self.phase,
            round_num=self.round_num,
            investigation_count=self.investigation_count,
        )

    def calculate_tier_confidences(self, weights: dict[str, float] | None = None) -> dict[str, float]:
        """
        Calculate tier-level confidence scores

        Args:
            weights: Optional custom weights for tiers
                    Default: equal weighting within tiers

        Returns:
            Dict with foundation_confidence, comprehension_confidence,
            execution_confidence, overall_confidence
        """
        if weights is None:
            weights = {"foundation": 0.30, "comprehension": 0.25, "execution": 0.30, "engagement": 0.15}

        foundation_confidence = (
            self.foundation_know.score + self.foundation_do.score + self.foundation_context.score
        ) / 3

        comprehension_confidence = (
            self.comprehension_clarity.score
            + self.comprehension_coherence.score
            + self.comprehension_signal.score
            + self.comprehension_density.score
        ) / 4

        execution_confidence = (
            self.execution_state.score
            + self.execution_change.score
            + self.execution_completion.score
            + self.execution_impact.score
        ) / 4

        overall_confidence = (
            foundation_confidence * weights["foundation"]
            + comprehension_confidence * weights["comprehension"]
            + execution_confidence * weights["execution"]
            + self.engagement.score * weights["engagement"]
        )

        return {
            "foundation_confidence": foundation_confidence,
            "comprehension_confidence": comprehension_confidence,
            "execution_confidence": execution_confidence,
            "overall_confidence": overall_confidence,
        }

    def determine_action(self, thresholds: dict[str, float] | None = None) -> str:
        """
        Determine recommended action based on assessment

        Args:
            thresholds: Optional custom thresholds
                       Default: standard CASCADE thresholds

        Returns:
            "proceed", "investigate", or "escalate"
        """
        if thresholds is None:
            thresholds = {"uncertainty_trigger": 0.40, "confidence_to_proceed": 0.75, "engagement_gate": 0.60}

        # Gate check
        if self.engagement.score < thresholds["engagement_gate"]:
            return "escalate"  # Not engaged enough

        # Uncertainty check
        if self.uncertainty.score > thresholds["uncertainty_trigger"]:
            return "investigate"

        # Confidence check
        confidences = self.calculate_tier_confidences()
        if confidences["overall_confidence"] < thresholds["confidence_to_proceed"]:
            return "investigate"

        return "proceed"

    # ===== BACKWARDS COMPATIBILITY LAYER =====
    # Properties that map NEW field names to OLD field names
    # This allows database/dashboard code to keep using OLD names

    @property
    def know(self):
        """Backwards compat: know → foundation_know"""
        return self.foundation_know

    @property
    def do(self):
        """Backwards compat: do → foundation_do"""
        return self.foundation_do

    @property
    def context(self):
        """Backwards compat: context → foundation_context"""
        return self.foundation_context

    @property
    def clarity(self):
        """Backwards compat: clarity → comprehension_clarity"""
        return self.comprehension_clarity

    @property
    def coherence(self):
        """Backwards compat: coherence → comprehension_coherence"""
        return self.comprehension_coherence

    @property
    def signal(self):
        """Backwards compat: signal → comprehension_signal"""
        return self.comprehension_signal

    @property
    def density(self):
        """Backwards compat: density → comprehension_density"""
        return self.comprehension_density

    @property
    def state(self):
        """Backwards compat: state → execution_state"""
        return self.execution_state

    @property
    def change(self):
        """Backwards compat: change → execution_change"""
        return self.execution_change

    @property
    def completion(self):
        """Backwards compat: completion → execution_completion"""
        return self.execution_completion

    @property
    def impact(self):
        """Backwards compat: impact → execution_impact"""
        return self.execution_impact

    @property
    def engagement_gate_passed(self):
        """Backwards compat: Check if engagement >= 0.6"""
        return self.engagement.score >= 0.6

    @property
    def assessment_id(self):
        """Backwards compat: Generate assessment ID from phase and timestamp"""
        import uuid

        return f"assessment_{self.phase.value}_{uuid.uuid4().hex[:8]}"

    @property
    def foundation_confidence(self):
        """Backwards compat: Calculate foundation tier confidence"""
        return (self.foundation_know.score + self.foundation_do.score + self.foundation_context.score) / 3

    @property
    def comprehension_confidence(self):
        """Backwards compat: Calculate comprehension tier confidence"""
        return (
            self.comprehension_clarity.score
            + self.comprehension_coherence.score
            + self.comprehension_signal.score
            + (1.0 - self.comprehension_density.score)  # Density is inverted
        ) / 4

    @property
    def execution_confidence(self):
        """Backwards compat: Calculate execution tier confidence"""
        return (
            self.execution_state.score
            + self.execution_change.score
            + self.execution_completion.score
            + self.execution_impact.score
        ) / 4

    @property
    def overall_confidence(self):
        """Backwards compat: Calculate overall confidence using canonical weights"""
        # Canonical weights: Foundation 35%, Comprehension 25%, Execution 25%, Engagement 15%
        return (
            self.foundation_confidence * 0.35
            + self.comprehension_confidence * 0.25
            + self.execution_confidence * 0.25
            + self.engagement.score * 0.15
        )

    @property
    def recommended_action(self):
        """Backwards compat: Determine recommended action based on thresholds"""
        from empirica.core.canonical.reflex_frame import Action

        # Gate check
        if not self.engagement_gate_passed:
            return Action.CLARIFY

        # Critical flags
        if self.comprehension_coherence.score < 0.5:
            return Action.RESET
        if self.comprehension_density.score > 0.9:
            return Action.RESET
        if self.execution_change.score < 0.5:
            return Action.STOP

        # Uncertainty-driven investigation
        if self.uncertainty.score > 0.7:
            return Action.INVESTIGATE

        # Low foundation confidence
        if self.foundation_confidence < 0.6:
            return Action.INVESTIGATE

        # Otherwise proceed
        return Action.PROCEED if self.overall_confidence >= 0.7 else Action.INVESTIGATE

    @property
    def coherence_critical(self):
        """Backwards compat: Check if coherence score is below critical threshold"""
        return self.comprehension_coherence.score < 0.5

    @property
    def density_critical(self):
        """Backwards compat: Check if density score is above critical threshold"""
        return self.comprehension_density.score > 0.9

    @property
    def change_critical(self):
        """Backwards compat: Check if change score is below critical threshold"""
        return self.execution_change.score < 0.5


def _check_vector(vector_data: dict, vector_name: str) -> None:
    """Validate vector has required score and rationale fields."""
    if "score" not in vector_data:
        raise ValueError(f"Missing score in {vector_name}")
    if "rationale" not in vector_data:
        raise ValueError(f"Missing rationale in {vector_name}")
    score = float(vector_data["score"])
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"Score out of range [0.0, 1.0] in {vector_name}: {score}")


def _validate_nested_keys(data: dict, parent_key: str, required_keys: list[str]) -> None:
    """Validate that a nested dict contains all required keys."""
    for key in required_keys:
        if key not in data[parent_key]:
            raise ValueError(f"Missing {parent_key} key: {key}")


def validate_assessment(data: dict[str, Any]) -> bool:
    """
    Validate assessment dictionary format

    Checks:
    - All 13 vectors present
    - Each vector has score, rationale
    - Scores in valid range [0.0, 1.0]
    - Proper nesting (foundation/comprehension/execution)

    Args:
        data: Assessment dictionary to validate

    Returns:
        True if valid

    Raises:
        ValueError: If validation fails
    """
    required_top = ["engagement", "foundation", "comprehension", "execution", "uncertainty"]
    for key in required_top:
        if key not in data:
            raise ValueError(f"Missing top-level key: {key}")

    required_foundation = ["know", "do", "context"]
    required_comprehension = ["clarity", "coherence", "signal", "density"]
    required_execution = ["state", "change", "completion", "impact"]

    _validate_nested_keys(data, "foundation", required_foundation)
    _validate_nested_keys(data, "comprehension", required_comprehension)
    _validate_nested_keys(data, "execution", required_execution)

    # Validate all vectors have score + rationale
    _check_vector(data["engagement"], "engagement")
    for key in required_foundation:
        _check_vector(data["foundation"][key], f"foundation.{key}")
    for key in required_comprehension:
        _check_vector(data["comprehension"][key], f"comprehension.{key}")
    for key in required_execution:
        _check_vector(data["execution"][key], f"execution.{key}")
    _check_vector(data["uncertainty"], "uncertainty")

    return True


def parse_assessment_dict(
    data: dict[str, Any], phase: CascadePhase = CascadePhase.PREFLIGHT
) -> EpistemicAssessmentSchema:
    """
    Parse and validate assessment dictionary

    Args:
        data: Nested assessment dictionary
        phase: Current CASCADE phase

    Returns:
        EpistemicAssessmentSchema instance

    Raises:
        ValueError: If validation fails
    """
    validate_assessment(data)
    return EpistemicAssessmentSchema.from_nested_dict(data, phase)
