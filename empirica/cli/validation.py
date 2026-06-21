"""
CLI Input Validation Models

Pydantic models for validating JSON inputs to CLI commands.
Addresses CWE-20: Improper Input Validation.

Usage:
    from empirica.cli.validation import PreflightInput, validate_json_input

    validated = validate_json_input(raw_json, PreflightInput)
    # validated is now a PreflightInput instance or raises ValidationError
"""

import json
from typing import Any, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar('T', bound=BaseModel)


# =============================================================================
# Epistemic Transaction Workflow Input Models
# =============================================================================

class VectorValues(BaseModel):
    """Epistemic vector values for AI self-assessment (0.0-1.0 scale).

    Captures the 13-vector epistemic state used throughout Empirica's
    measurement workflow (PREFLIGHT, CHECK, POSTFLIGHT). Only `know` and
    `uncertainty` are required; all other vectors are optional and
    default to None.

    The vectors are grouped semantically:

    * **Knowledge axis** — `know`, `uncertainty`, `signal`, `density`
    * **Context axis** — `context`, `clarity`, `coherence`, `state`
    * **Action axis** — `change`, `completion`, `do`
    * **Engagement axis** — `engagement`, `impact`

    See `docs/reference/api/core_session_management.md` and the EWM
    protocol docs for the full vector semantics. Used by `PreflightInput`,
    `CheckInput`, and `PostflightInput`.
    """
    know: float = Field(ge=0.0, le=1.0, description="Knowledge level")
    uncertainty: float = Field(ge=0.0, le=1.0, description="Uncertainty level")
    context: float | None = Field(default=None, ge=0.0, le=1.0, description="Context understanding")
    engagement: float | None = Field(default=None, ge=0.0, le=1.0, description="Engagement level")
    clarity: float | None = Field(default=None, ge=0.0, le=1.0, description="Clarity of understanding")
    coherence: float | None = Field(default=None, ge=0.0, le=1.0, description="Coherence of knowledge")
    signal: float | None = Field(default=None, ge=0.0, le=1.0, description="Signal strength")
    density: float | None = Field(default=None, ge=0.0, le=1.0, description="Information density")
    state: float | None = Field(default=None, ge=0.0, le=1.0, description="Current state")
    change: float | None = Field(default=None, ge=0.0, le=1.0, description="Rate of change")
    completion: float | None = Field(default=None, ge=0.0, le=1.0, description="Phase-aware completion: NOETIC='Have I learned enough?' PRAXIC='Have I implemented enough?'")
    impact: float | None = Field(default=None, ge=0.0, le=1.0, description="Expected impact")
    do: float | None = Field(default=None, ge=0.0, le=1.0, description="Execution capability")


class PreflightInput(BaseModel):
    """Pydantic input schema for the `preflight-submit` CLI command.

    PREFLIGHT opens an epistemic measurement transaction. The AI declares
    its baseline epistemic state across the 13 vectors, plus optional
    work_context and work_type metadata that adjust grounded calibration
    normalization.

    Fields:
        session_id: UUID of the active Empirica session (required, 1-100 chars)
        vectors: Dict of vector_name -> float (0.0-1.0). Must include at
            least 'know' and 'uncertainty'. See `VectorValues` for the
            full set of valid keys.
        reasoning: Free-text explanation for the assessment (optional,
            max 5000 chars). Captured for retrospective grounding.
        task_context: Brief task description used for pattern retrieval
            from prior transactions (optional, max 2000 chars).
        work_context: Project maturity context — one of `greenfield`,
            `iteration`, `investigation`, `refactor`. Adjusts calibration
            normalization baselines.
        work_type: Domain context — one of `code`, `infra`, `research`,
            `release`, `debug`, `config`, `docs`, `data`, `comms`,
            `design`, `audit`, `remote-ops`. Determines which evidence
            sources are relevant for grounded calibration. `remote-ops`
            means the local Sentinel has no signal for this work
            (SSH/customer machines/remote config) and self-assessment
            stands unchallenged.

    Raises:
        ValueError: via field validators when session_id is empty,
            vectors dict is empty, an unknown vector key is used, a
            value is outside 0.0-1.0, or required vectors (know,
            uncertainty) are missing.
    """
    session_id: str = Field(min_length=1, max_length=100, description="Session identifier")
    vectors: dict[str, float] = Field(description="Epistemic vector values")
    reasoning: str | None = Field(default="", max_length=5000, description="Reasoning for assessment")
    task_context: str | None = Field(default="", max_length=2000, description="Context for pattern retrieval")
    work_context: str | None = Field(
        default=None, description="Work context for maturity-aware calibration normalization",
        pattern="^(greenfield|iteration|investigation|refactor)$",
    )
    work_type: str | None = Field(
        default=None,
        description=(
            "Type of work being done — determines which evidence sources are "
            "relevant for grounded calibration. Use 'remote-ops' for work on "
            "machines the local Sentinel doesn't observe (SSH, customer "
            "machines, remote config) — self-assessment stands."
        ),
        pattern="^(code|infra|research|release|debug|config|docs|data|comms|design|audit|remote-ops)$",
    )
    domain: str | None = Field(
        default=None,
        description="Domain classification for compliance check selection (e.g., cybersec, payments, default)",
        pattern="^[a-z][a-z0-9_-]*$",
    )
    criticality: str | None = Field(
        default=None,
        description="Criticality level — determines required check rigor",
        pattern="^(low|medium|high|critical)$",
    )
    predicted_check_outcomes: dict[str, float] | None = Field(
        default=None,
        description=(
            "AI's predicted probability of each compliance check passing. "
            "Keys are check_ids from ServiceRegistry. Used for Brier scoring "
            "of check-outcome predictions (B4)."
        ),
    )
    voice: str | None = Field(
        default=None,
        description=(
            "Optional voice profile name to load for outreach drafting. "
            "When set (or when work_type=comms with no override), PREFLIGHT "
            "response includes a voice_guidance block with tendencies + "
            "anti-patterns scoped to the platform register. Profile is "
            "resolved via the empirica voice loader (project-local "
            ".empirica/voice/ overrides ~/.empirica/voice/)."
        ),
        pattern="^[a-z][a-z0-9_-]*$",
    )
    retrospective_reason: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Acknowledgment that clears the retrospective soft-gate. The gate "
            "fires at PREFLIGHT when the previous transaction made substantive "
            "praxic tool calls but logged 0 epistemic artifacts (on a "
            "non-mechanical work_type). Provide a one-line reason why nothing "
            "was logged (e.g. 'pure mechanical rename, no decisions') to "
            "acknowledge and proceed — or instead log the missed artifacts, "
            "which clears the gate on the next transaction's POSTFLIGHT."
        ),
    )

    @field_validator('session_id')
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        """Validate session_id format."""
        if not v or not v.strip():
            raise ValueError('session_id cannot be empty')
        return v.strip()

    @field_validator('vectors')
    @classmethod
    def validate_vectors(cls, v: dict[str, float]) -> dict[str, float]:
        """Validate vector values are in valid range."""
        if not v:
            raise ValueError('vectors cannot be empty')

        valid_keys = {'know', 'uncertainty', 'context', 'engagement', 'clarity',
                      'coherence', 'signal', 'density', 'state', 'change',
                      'completion', 'impact', 'do'}

        for key, value in v.items():
            if key not in valid_keys:
                raise ValueError(f'Unknown vector key: {key}')
            if not isinstance(value, (int, float)):
                raise ValueError(f'Vector {key} must be a number, got {type(value).__name__}')
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f'Vector {key} must be between 0.0 and 1.0, got {value}')

        # Require at least know and uncertainty
        if 'know' not in v or 'uncertainty' not in v:
            raise ValueError('vectors must include at least "know" and "uncertainty"')

        return v


class CheckInput(BaseModel):
    """Input model for check-submit command."""
    session_id: str = Field(min_length=1, max_length=100, description="Session identifier")
    vectors: dict[str, float] | None = Field(default=None, description="Updated vector values")
    approach: str | None = Field(default="", max_length=2000, description="Planned approach")
    reasoning: str | None = Field(default="", max_length=5000, description="Reasoning for check")

    @field_validator('session_id')
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        """Validate session_id is non-empty."""
        if not v or not v.strip():
            raise ValueError('session_id cannot be empty')
        return v.strip()

    @field_validator('vectors')
    @classmethod
    def validate_vectors(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        """Validate optional vector values are in valid 0.0-1.0 range."""
        if v is None:
            return v
        for key, value in v.items():
            if not isinstance(value, (int, float)):
                raise ValueError(f'Vector {key} must be a number')
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f'Vector {key} must be between 0.0 and 1.0')
        return v


class PostflightInput(BaseModel):
    """Pydantic input schema for the `postflight-submit` CLI command.

    POSTFLIGHT closes an epistemic measurement transaction. The AI
    declares its updated epistemic state after doing the work; the
    system computes deltas, runs grounded verification, and produces
    a calibration score.

    Fields:
        session_id: UUID of the active Empirica session (required, 1-100 chars)
        vectors: Dict of vector_name -> float (0.0-1.0). Required (the
            POSTFLIGHT-PREFLIGHT delta is the primary measurement
            signal). See `VectorValues` for valid keys.
        reasoning: Free-text retrospective explaining what was learned
            and how vectors changed (optional, max 5000 chars).
        learnings: Distilled key learnings from the transaction
            (optional, max 5000 chars).
        goal_id: Optional goal UUID this transaction was working on,
            for goal-progress linkage.

    Raises:
        ValueError: via field validators when session_id is empty,
            vectors dict is empty, or any value is outside 0.0-1.0.
    """
    session_id: str = Field(min_length=1, max_length=100, description="Session identifier")
    vectors: dict[str, float] = Field(description="Final epistemic vector values")
    reasoning: str | None = Field(default="", max_length=5000, description="Reasoning for assessment")
    learnings: str | None = Field(default="", max_length=5000, description="Key learnings from session")
    goal_id: str | None = Field(default=None, max_length=100, description="Associated goal ID")
    # B3: AI-reasoned grounded state (three-vector model)
    grounded_vectors: dict[str, float] | None = Field(
        default=None,
        description=(
            "AI-reasoned grounded state after seeing deterministic service "
            "observations. If omitted, falls back to observed (legacy behavior)."
        ),
    )
    grounded_rationale: str | None = Field(
        default=None,
        max_length=5000,
        description=(
            "AI's reasoning for any divergence between self-assessed vectors "
            "and grounded_vectors. Documents why the AI adjusted (or didn't)."
        ),
    )
    # Phase 2 T3: agent self-coverage block (paper section 4.1).
    # Informative, NOT gating — the AI sees its own coverage echoed
    # back so it can self-correct on subsequent transactions ("95%
    # confidence with 8% file coverage" is now visible, not hidden).
    coverage: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional agent self-coverage report. Documented dimensions: "
            "files_inspected/files_relevant, artifacts_inspected/artifacts_relevant, "
            "citations_made/citations_available, subagents_dispatched/subagents_relevant, "
            "tools_invoked/tools_available, scalar (0.0-1.0), notes. "
            "Free-form keys are preserved for forward compatibility."
        ),
    )

    @field_validator('session_id')
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        """Validate session_id is non-empty."""
        if not v or not v.strip():
            raise ValueError('session_id cannot be empty')
        return v.strip()

    @field_validator('vectors')
    @classmethod
    def validate_vectors(cls, v: dict[str, float]) -> dict[str, float]:
        """Validate required vector values are in valid 0.0-1.0 range."""
        if not v:
            raise ValueError('vectors cannot be empty')
        for key, value in v.items():
            if not isinstance(value, (int, float)):
                raise ValueError(f'Vector {key} must be a number')
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f'Vector {key} must be between 0.0 and 1.0')
        return v


# =============================================================================
# Finding/Unknown Input Models
# =============================================================================

class FindingInput(BaseModel):
    """Pydantic input schema for the `finding-log` CLI command.

    Findings record concrete discoveries made during noetic or praxic
    work — observations, root causes, behavioral patterns, dependencies
    learned, etc. They are first-class epistemic artifacts and feed
    into the calibration loop and pattern retrieval.

    Fields:
        session_id: UUID of the active Empirica session (1-100 chars)
        finding: The discovery text (1-5000 chars). Should be specific
            and actionable — "Auth middleware uses JWT in cookie, not
            Bearer header" rather than "Auth is complicated".
        impact: How significant this finding is (0.0-1.0, default 0.5).
            Higher impact findings get prioritized in retrieval.
        domain: Optional domain tag for filtering (e.g. "auth", "db",
            "frontend"). Max 100 chars.
        goal_id: Optional goal UUID this finding contributes to.
    """
    session_id: str = Field(min_length=1, max_length=100)
    finding: str = Field(min_length=1, max_length=5000)
    impact: float = Field(ge=0.0, le=1.0, default=0.5)
    domain: str | None = Field(default=None, max_length=100)
    goal_id: str | None = Field(default=None, max_length=100)


class UnknownInput(BaseModel):
    """Pydantic input schema for the `unknown-log` CLI command.

    Unknowns record open questions that need investigation. Logging an
    unknown is the noetic-phase complement to logging a finding —
    findings are what you DO know, unknowns are what you don't.
    Unknowns can later be resolved (which generates a finding) or
    determined to be out-of-scope.

    Fields:
        session_id: UUID of the active Empirica session (1-100 chars)
        unknown: The open question text (1-5000 chars). Phrase as a
            question or "I don't know X" statement.
        impact: How important resolving this unknown is (0.0-1.0,
            default 0.5). Higher impact unknowns are surfaced more
            prominently.
        goal_id: Optional goal UUID this unknown blocks.
    """
    session_id: str = Field(min_length=1, max_length=100)
    unknown: str = Field(min_length=1, max_length=5000)
    impact: float = Field(ge=0.0, le=1.0, default=0.5)
    goal_id: str | None = Field(default=None, max_length=100)


# =============================================================================
# Validation Utilities
# =============================================================================

def validate_json_input(raw_json: str, model: type[T]) -> T:
    """
    Parse and validate JSON input against a Pydantic model.

    Args:
        raw_json: Raw JSON string
        model: Pydantic model class to validate against

    Returns:
        Validated model instance

    Raises:
        ValueError: If JSON is invalid or validation fails
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    return model.model_validate(data)


def validate_dict_input(data: dict[str, Any], model: type[T]) -> T:
    """
    Validate a dictionary against a Pydantic model.

    Args:
        data: Dictionary to validate
        model: Pydantic model class to validate against

    Returns:
        Validated model instance

    Raises:
        ValueError: If validation fails
    """
    return model.model_validate(data)


def safe_validate(data: dict[str, Any], model: type[T]) -> tuple[T | None, str | None]:
    """
    Safely validate data, returning (validated, None) or (None, error_message).

    Args:
        data: Dictionary to validate
        model: Pydantic model class

    Returns:
        Tuple of (validated_model, error_message)
    """
    try:
        validated = model.model_validate(data)
        return validated, None
    except Exception as e:
        return None, str(e)
