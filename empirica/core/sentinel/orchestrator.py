"""
Sentinel Orchestrator - Domain-Aware Epistemic Governance

The Sentinel orchestrates epistemic subagents with:
- Automatic persona selection based on task analysis
- Parallel agent spawning with persona-seeded priors
- Domain profile loading for compliance gates
- Enhanced CHECK with hard gates and escalation

Usage:
    sentinel = Sentinel(session_id=session_id)

    # Load domain profile for compliance
    sentinel.load_domain_profile("healthcare")

    # Orchestrate a task with automatic persona selection
    result = sentinel.orchestrate(
        task="Review patient data handling for HIPAA compliance",
        max_agents=3
    )

    # Check compliance gates
    gate_result = sentinel.check_compliance(vectors, findings, unknowns)
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

from .decision_logic import DecisionLogic, PersonaMatch

UTC = timezone.utc

logger = logging.getLogger(__name__)


class GateAction(Enum):
    """Actions that compliance gates can take"""
    PROCEED = "proceed"           # Continue execution
    INVESTIGATE = "investigate"   # Return to noetic phase
    HALT_AND_AUDIT = "halt_and_audit"  # Stop and log for audit
    REQUIRE_HUMAN = "require_human_review"  # Pause for human approval
    ESCALATE = "escalate"         # Escalate to higher authority
    LOG_AND_CONTINUE = "log_and_continue"  # Log concern but proceed


class LoopMode(Enum):
    """Who decides loop count"""
    USER = "user"           # User specifies exact count
    AI = "ai"               # AI chooses based on task
    SENTINEL = "sentinel"   # Sentinel governs with convergence


class MergeStrategy(Enum):
    """Strategies for merging parallel agent results"""
    CONSENSUS = "consensus"       # All agents must agree
    BEST_SCORE = "best_score"     # Take highest merge_score result
    WEIGHTED = "weighted"         # Weight by merge_score
    UNION = "union"               # Combine all findings
    INTERSECTION = "intersection" # Only common findings


class GatePhase(Enum):
    """Phase during which a gate operates"""
    NOETIC = "noetic"     # Cognition/investigation phase
    PRAXIC = "praxic"     # Action/execution phase
    CHECK = "check"       # During CHECK gate transition
    ANY = "any"           # Applies to all phases


# =============================================================================
# DUAL DEFENSE LAYERS
# =============================================================================

@dataclass
class NoeticFilter:
    """
    Noetic Filter - Cognition-level defense layer.

    Operates during NOETIC phase to filter what investigation paths are allowed.
    Prevents exploration of harmful/restricted domains before any action occurs.

    Examples:
    - Block investigation of exploit development
    - Restrict access to sensitive codebase areas
    - Prevent deep-diving into user credentials
    """
    filter_id: str
    name: str
    blocked_patterns: list[str] = field(default_factory=list)  # Regex patterns to block
    blocked_domains: list[str] = field(default_factory=list)   # Domain areas to block
    allow_with_justification: bool = False  # If True, can proceed with explicit justification
    action_on_match: GateAction = GateAction.INVESTIGATE
    log_matches: bool = True
    description: str | None = None

    def evaluate(self, investigation_context: dict[str, Any]) -> dict | None:
        """
        Evaluate if investigation should be filtered.

        Args:
            investigation_context: Dict with task, path, domain, vectors

        Returns:
            None if allowed, Dict with filter info if blocked
        """
        import re

        task = investigation_context.get("task", "")
        path = investigation_context.get("path", "")
        domain = investigation_context.get("domain", "")
        combined = f"{task} {path} {domain}".lower()

        # Check blocked patterns
        for pattern in self.blocked_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return {
                    "filter_id": self.filter_id,
                    "matched_pattern": pattern,
                    "action": self.action_on_match.value,
                    "allow_with_justification": self.allow_with_justification,
                }

        # Check blocked domains
        for blocked in self.blocked_domains:
            if blocked.lower() in combined:
                return {
                    "filter_id": self.filter_id,
                    "matched_domain": blocked,
                    "action": self.action_on_match.value,
                    "allow_with_justification": self.allow_with_justification,
                }

        return None  # Allowed


@dataclass
class AxiologicGate:
    """
    Axiologic Detection Layer - Action/value-level defense.

    Operates during PRAXIC phase to validate actions against value constraints.
    Ensures actions align with ethical guidelines and domain values.

    Examples:
    - Prevent deletion of critical files without confirmation
    - Block push to main branch without review
    - Require audit trail for sensitive operations
    """
    gate_id: str
    name: str
    action_patterns: list[str] = field(default_factory=list)   # Action patterns to gate
    value_constraints: dict[str, Any] = field(default_factory=dict)  # Value thresholds
    required_vectors: dict[str, float] = field(default_factory=dict)  # Min vectors required
    action_on_violation: GateAction = GateAction.REQUIRE_HUMAN
    audit_required: bool = True
    description: str | None = None

    def evaluate(self, action_context: dict[str, Any]) -> dict | None:
        """
        Evaluate if action should be gated.

        Args:
            action_context: Dict with action, target, vectors, metadata

        Returns:
            None if allowed, Dict with gate info if blocked
        """
        import re

        action = action_context.get("action", "")
        target = action_context.get("target", "")
        vectors = action_context.get("vectors", {})
        combined = f"{action} {target}".lower()

        # Check action patterns
        for pattern in self.action_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return {
                    "gate_id": self.gate_id,
                    "matched_pattern": pattern,
                    "action": self.action_on_violation.value,
                    "audit_required": self.audit_required,
                }

        # Check vector requirements
        for vector_name, min_value in self.required_vectors.items():
            actual = vectors.get(vector_name, 0.5)
            if actual < min_value:
                return {
                    "gate_id": self.gate_id,
                    "vector_violation": {
                        "vector": vector_name,
                        "required": min_value,
                        "actual": actual,
                    },
                    "action": self.action_on_violation.value,
                    "audit_required": self.audit_required,
                }

        return None  # Allowed


@dataclass
class ComplianceGate:
    """A compliance gate that runs during CHECK"""
    gate_id: str
    condition: str  # e.g., "uncertainty > 0.5", "pii_detected"
    action: GateAction
    threshold: float | None = None
    description: str | None = None
    priority: str = "medium"  # low, medium, high, critical

    def evaluate(self, context: dict[str, Any]) -> bool:
        """
        Evaluate if gate condition is met.

        Args:
            context: Dict with vectors, findings, flags

        Returns:
            True if condition is met (gate triggers)
        """
        # Simple condition evaluation
        condition = self.condition.lower()

        # Vector-based conditions
        if ">" in condition or "<" in condition:
            parts = condition.replace(">", " > ").replace("<", " < ").split()
            if len(parts) >= 3:
                vector_name = parts[0]
                operator = parts[1]
                threshold = float(parts[2])

                vectors = context.get("vectors", {})
                value = vectors.get(vector_name, 0.5)

                if (operator == ">" and value > threshold) or (operator == "<" and value < threshold):
                    return True

        # Flag-based conditions
        if condition in context.get("flags", {}):
            return context["flags"][condition]

        # Custom conditions
        if condition == "pii_detected":
            return context.get("flags", {}).get("pii_detected", False)

        if condition == "high_risk":
            uncertainty = context.get("vectors", {}).get("uncertainty", 0.5)
            impact = context.get("vectors", {}).get("impact", 0.5)
            return uncertainty > 0.6 and impact > 0.7

        return False


@dataclass
class DomainProfile:
    """Domain-specific configuration for Sentinel"""
    name: str
    compliance_framework: str | None = None  # HIPAA, SOX, etc.

    # MCO overrides
    uncertainty_trigger: float = 0.5
    confidence_to_proceed: float = 0.75
    signal_quality_min: float = 0.6

    # Compliance gates
    gates: list[ComplianceGate] = field(default_factory=list)

    # Persona restrictions
    allowed_personas: list[str] = field(default_factory=list)
    required_personas: list[str] = field(default_factory=list)

    # Tool restrictions
    restricted_tools: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)

    # Audit requirements
    audit_all_actions: bool = False
    audit_retention_days: int = 90

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'DomainProfile':
        """Load from dictionary"""
        gates = [
            ComplianceGate(
                gate_id=g.get("gate_id", str(uuid.uuid4())[:8]),
                condition=g["condition"],
                action=GateAction(g["action"]),
                threshold=g.get("threshold"),
                description=g.get("description"),
                priority=g.get("priority", "medium")
            )
            for g in data.get("gates", [])
        ]

        return cls(
            name=data["name"],
            compliance_framework=data.get("compliance_framework"),
            uncertainty_trigger=data.get("uncertainty_trigger", 0.5),
            confidence_to_proceed=data.get("confidence_to_proceed", 0.75),
            signal_quality_min=data.get("signal_quality_min", 0.6),
            gates=gates,
            allowed_personas=data.get("allowed_personas", []),
            required_personas=data.get("required_personas", []),
            restricted_tools=data.get("restricted_tools", []),
            allowed_tools=data.get("allowed_tools", []),
            audit_all_actions=data.get("audit_all_actions", False),
            audit_retention_days=data.get("audit_retention_days", 90)
        )


@dataclass
class OrchestrationResult:
    """Result of orchestrating a task"""
    ok: bool
    task: str
    personas_selected: list[PersonaMatch]
    agents_spawned: list[str]  # branch_ids
    aggregated_findings: list[str] = field(default_factory=list)
    aggregated_unknowns: list[str] = field(default_factory=list)
    merge_strategy: MergeStrategy = MergeStrategy.UNION
    merged_vectors: dict[str, float] = field(default_factory=dict)
    compliance_check: dict[str, Any] | None = None
    error: str | None = None
    loop_info: dict[str, Any] | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert multi-persona result to dictionary representation."""
        return {
            "ok": self.ok,
            "task": self.task,
            "personas_selected": [p.to_dict() for p in self.personas_selected],
            "agents_spawned": self.agents_spawned,
            "aggregated_findings": self.aggregated_findings,
            "aggregated_unknowns": self.aggregated_unknowns,
            "merge_strategy": self.merge_strategy.value,
            "merged_vectors": self.merged_vectors,
            "compliance_check": self.compliance_check,
            "error": self.error,
            "timestamp": self.timestamp
        }


@dataclass
class LoopRecord:
    """Record of a single epistemic loop (PREFLIGHT → POSTFLIGHT)"""
    loop_number: int
    preflight_vectors: dict[str, float]
    postflight_vectors: dict[str, float]
    delta: dict[str, float]
    findings_count: int = 0
    unknowns_count: int = 0
    check_decision: str = "proceed"
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert loop record to dictionary representation."""
        return {
            "loop": self.loop_number,
            "preflight": self.preflight_vectors,
            "postflight": self.postflight_vectors,
            "delta": self.delta,
            "findings": self.findings_count,
            "unknowns": self.unknowns_count,
            "decision": self.check_decision,
            "timestamp": self.timestamp
        }


@dataclass
class EpistemicLoopTracker:
    """
    Tracks epistemic loops (PREFLIGHT → POSTFLIGHT cycles) for a session.

    Uses scope vectors to determine expected loops, tracks convergence,
    and provides termination recommendations.

    Usage:
        tracker = EpistemicLoopTracker(scope_breadth=0.6, scope_duration=0.5)
        tracker.start_loop(preflight_vectors)
        # ... work happens ...
        tracker.complete_loop(postflight_vectors, findings, unknowns)

        if tracker.should_continue():
            # Another loop needed
        else:
            # Converged or max loops reached
    """
    # Scope vectors (same as goals)
    scope_breadth: float = 0.5      # How wide (more = more loops)
    scope_duration: float = 0.5     # Expected lifetime (more = more loops)
    scope_coordination: float = 0.3 # Multi-agent needed

    # Loop configuration
    max_loops: int | None = None  # Hard limit (None = derive from scope)
    min_loops: int = 1               # Minimum before allowing termination
    convergence_threshold: float = 0.03  # Delta below this = converged
    convergence_window: int = 2      # Consecutive low-delta loops to confirm

    # Loop mode
    mode: LoopMode = LoopMode.SENTINEL

    # State
    current_loop: int = 0
    loop_history: list[LoopRecord] = field(default_factory=list)
    cumulative_delta: dict[str, float] = field(default_factory=dict)
    _current_preflight: dict[str, float] | None = field(default=None, repr=False)

    def __post_init__(self):
        """Derive max_loops from scope if not specified"""
        if self.max_loops is None:
            # Higher scope = more loops expected
            # breadth 0.8 + duration 0.7 → ~5 loops
            # breadth 0.3 + duration 0.3 → ~2 loops
            scope_factor = (self.scope_breadth + self.scope_duration) / 2
            self.max_loops = max(2, min(10, int(scope_factor * 8) + 1))

    def start_loop(self, preflight_vectors: dict[str, float]) -> int:
        """
        Start a new epistemic loop.

        Args:
            preflight_vectors: PREFLIGHT vector state

        Returns:
            Loop number (1-indexed)
        """
        self.current_loop += 1
        self._current_preflight = preflight_vectors.copy()

        logger.info(f"Started epistemic loop {self.current_loop}/{self.max_loops}")
        return self.current_loop

    def complete_loop(
        self,
        postflight_vectors: dict[str, float],
        findings_count: int = 0,
        unknowns_count: int = 0,
        check_decision: str = "proceed"
    ) -> LoopRecord:
        """
        Complete current epistemic loop.

        Args:
            postflight_vectors: POSTFLIGHT vector state
            findings_count: Number of findings this loop
            unknowns_count: Number of unknowns remaining
            check_decision: Final CHECK decision

        Returns:
            LoopRecord for this loop
        """
        if self._current_preflight is None:
            raise ValueError("No loop in progress - call start_loop() first")

        # Calculate delta
        delta = {}
        for key in postflight_vectors:
            pre = self._current_preflight.get(key, 0.5)
            post = postflight_vectors.get(key, 0.5)
            delta[key] = round(post - pre, 4)

        # Update cumulative delta
        for key, value in delta.items():
            self.cumulative_delta[key] = self.cumulative_delta.get(key, 0) + value

        # Create record
        record = LoopRecord(
            loop_number=self.current_loop,
            preflight_vectors=self._current_preflight,
            postflight_vectors=postflight_vectors,
            delta=delta,
            findings_count=findings_count,
            unknowns_count=unknowns_count,
            check_decision=check_decision
        )

        self.loop_history.append(record)
        self._current_preflight = None

        logger.info(
            f"Completed loop {self.current_loop}: "
            f"delta_know={delta.get('know', 0):+.3f}, "
            f"delta_uncertainty={delta.get('uncertainty', 0):+.3f}"
        )

        return record

    def should_continue(self) -> bool:
        """
        Determine if another loop should be run.

        Returns:
            True if more loops needed, False if done
        """
        # Mode: USER - they decide
        if self.mode == LoopMode.USER:
            return self.current_loop < self.max_loops

        # Check minimum
        if self.current_loop < self.min_loops:
            return True

        # Check maximum
        if self.current_loop >= self.max_loops:
            logger.info(f"Max loops ({self.max_loops}) reached")
            return False

        # Check convergence (SENTINEL and AI modes)
        if self._is_converged():
            logger.info("Convergence detected - learning plateaued")
            return False

        # Check uncertainty threshold
        if self.loop_history:
            last = self.loop_history[-1]
            uncertainty = last.postflight_vectors.get("uncertainty", 1.0)
            if uncertainty < 0.25:
                logger.info(f"Low uncertainty ({uncertainty:.2f}) - confident enough")
                return False

        return True

    def _is_converged(self) -> bool:
        """Check if learning has plateaued"""
        if len(self.loop_history) < self.convergence_window:
            return False

        # Check last N loops for low delta
        recent = self.loop_history[-self.convergence_window:]
        for record in recent:
            # Check if any key vector had significant change
            know_delta = abs(record.delta.get("know", 0))
            uncertainty_delta = abs(record.delta.get("uncertainty", 0))

            if know_delta > self.convergence_threshold:
                return False
            if uncertainty_delta > self.convergence_threshold:
                return False

        return True

    def get_summary(self) -> dict[str, Any]:
        """Get loop tracking summary"""
        return {
            "current_loop": self.current_loop,
            "max_loops": self.max_loops,
            "min_loops": self.min_loops,
            "loops_completed": len(self.loop_history),
            "mode": self.mode.value,
            "scope": {
                "breadth": self.scope_breadth,
                "duration": self.scope_duration,
                "coordination": self.scope_coordination
            },
            "cumulative_delta": self.cumulative_delta,
            "converged": self._is_converged(),
            "should_continue": self.should_continue(),
            "loop_history": [r.to_dict() for r in self.loop_history]
        }

    def estimate_remaining_loops(self) -> int:
        """Estimate how many more loops might be needed"""
        max_loops = self.max_loops or 10
        if not self.loop_history:
            return max_loops - self.current_loop

        # Based on learning rate
        last = self.loop_history[-1]
        uncertainty = last.postflight_vectors.get("uncertainty", 0.5)

        if uncertainty < 0.25:
            return 0
        elif uncertainty < 0.35:
            return 1
        else:
            # Estimate based on current delta rate
            avg_delta = abs(self.cumulative_delta.get("uncertainty", 0)) / len(self.loop_history)
            if avg_delta > 0:
                remaining = int((uncertainty - 0.25) / avg_delta) + 1
                return min(remaining, max_loops - self.current_loop)

        return max_loops - self.current_loop


class Sentinel:
    """
    Domain-aware epistemic governance orchestrator.

    The Sentinel:
    1. Analyzes tasks to select appropriate personas
    2. Spawns parallel epistemic agents with persona priors
    3. Aggregates results using configurable merge strategies
    4. Enforces domain compliance gates during CHECK
    5. Provides audit trails for regulated domains

    Attributes:
        session_id: Current session ID
        domain_profile: Active domain profile (if any)
        decision_logic: DecisionLogic instance for persona selection
    """

    # Default domain profiles
    DEFAULT_PROFILES: ClassVar[dict[str, DomainProfile]] = {
        "general": DomainProfile(
            name="general",
            uncertainty_trigger=0.5,
            confidence_to_proceed=0.75
        ),
        "healthcare": DomainProfile(
            name="healthcare",
            compliance_framework="HIPAA",
            uncertainty_trigger=0.3,  # More cautious
            confidence_to_proceed=0.85,
            gates=[
                ComplianceGate(
                    gate_id="pii_check",
                    condition="pii_detected",
                    action=GateAction.HALT_AND_AUDIT,
                    description="Halt if PII detected without authorization"
                ),
                ComplianceGate(
                    gate_id="high_uncertainty",
                    condition="uncertainty > 0.4",
                    action=GateAction.REQUIRE_HUMAN,
                    description="Require human review for uncertain medical decisions"
                )
            ],
            audit_all_actions=True,
            audit_retention_days=2555  # 7 years for HIPAA
        ),
        "finance": DomainProfile(
            name="finance",
            compliance_framework="SOX",
            uncertainty_trigger=0.35,
            confidence_to_proceed=0.80,
            gates=[
                ComplianceGate(
                    gate_id="transaction_limit",
                    condition="high_risk",
                    action=GateAction.ESCALATE,
                    description="Escalate high-risk financial operations"
                )
            ],
            audit_all_actions=True
        )
    }

    def __init__(
        self,
        session_id: str,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333
    ):
        """
        Initialize Sentinel.

        Args:
            session_id: Current session ID
            qdrant_host: Qdrant server host
            qdrant_port: Qdrant server port
        """
        self.session_id = session_id
        self.domain_profile: DomainProfile | None = None
        self.decision_logic = DecisionLogic(
            qdrant_host=qdrant_host,
            qdrant_port=qdrant_port
        )
        self._spawn_fn: Callable | None = None
        self._aggregate_fn: Callable | None = None

        # Epistemic loop tracking
        self.loop_tracker: EpistemicLoopTracker | None = None

    def init_loop_tracking(
        self,
        scope_breadth: float = 0.5,
        scope_duration: float = 0.5,
        scope_coordination: float = 0.3,
        max_loops: int | None = None,
        mode: LoopMode = LoopMode.SENTINEL
    ) -> EpistemicLoopTracker:
        """
        Initialize epistemic loop tracking.

        Args:
            scope_breadth: Task breadth (0-1, higher = more loops)
            scope_duration: Expected duration (0-1, higher = more loops)
            scope_coordination: Multi-agent coordination (0-1)
            max_loops: Hard limit (None = derive from scope)
            mode: Who controls loop count (USER/AI/SENTINEL)

        Returns:
            Initialized EpistemicLoopTracker
        """
        self.loop_tracker = EpistemicLoopTracker(
            scope_breadth=scope_breadth,
            scope_duration=scope_duration,
            scope_coordination=scope_coordination,
            max_loops=max_loops,
            mode=mode
        )

        logger.info(
            f"Loop tracking initialized: max_loops={self.loop_tracker.max_loops}, "
            f"mode={mode.value}"
        )

        return self.loop_tracker

    def start_loop(self, preflight_vectors: dict[str, float]) -> int:
        """Start an epistemic loop (call at PREFLIGHT)"""
        if not self.loop_tracker:
            self.init_loop_tracking()
        return self.loop_tracker.start_loop(preflight_vectors)

    def complete_loop(
        self,
        postflight_vectors: dict[str, float],
        findings_count: int = 0,
        unknowns_count: int = 0
    ) -> dict[str, Any]:
        """
        Complete an epistemic loop (call at POSTFLIGHT).

        Returns dict with loop record and whether to continue.
        """
        if not self.loop_tracker:
            raise ValueError("Loop tracking not initialized")

        record = self.loop_tracker.complete_loop(
            postflight_vectors=postflight_vectors,
            findings_count=findings_count,
            unknowns_count=unknowns_count
        )

        return {
            "loop": record.to_dict(),
            "should_continue": self.loop_tracker.should_continue(),
            "loops_remaining": self.loop_tracker.estimate_remaining_loops(),
            "converged": self.loop_tracker._is_converged()
        }

    def get_loop_summary(self) -> dict[str, Any] | None:
        """Get current loop tracking summary"""
        if not self.loop_tracker:
            return None
        return self.loop_tracker.get_summary()

    def load_domain_profile(
        self,
        profile_name: str,
        custom_profile: dict[str, Any] | None = None
    ) -> DomainProfile:
        """
        Load a domain profile for compliance configuration.

        Args:
            profile_name: Name of built-in profile or custom profile
            custom_profile: Optional custom profile dict

        Returns:
            Loaded DomainProfile
        """
        if custom_profile:
            self.domain_profile = DomainProfile.from_dict(custom_profile)
        elif profile_name in self.DEFAULT_PROFILES:
            self.domain_profile = self.DEFAULT_PROFILES[profile_name]
        else:
            logger.warning(f"Unknown profile {profile_name}, using general")
            self.domain_profile = self.DEFAULT_PROFILES["general"]

        logger.info(
            f"Loaded domain profile: {self.domain_profile.name} "
            f"(framework={self.domain_profile.compliance_framework})"
        )

        return self.domain_profile

    def select_personas(
        self,
        task: str,
        max_personas: int = 3
    ) -> list[PersonaMatch]:
        """
        Select personas for a task using DecisionLogic.

        Args:
            task: Task description
            max_personas: Maximum personas to select

        Returns:
            List of PersonaMatch objects
        """
        # Apply domain restrictions if profile loaded
        excluded = []
        required = []

        if self.domain_profile:
            if self.domain_profile.allowed_personas:
                # Only allow listed personas
                excluded = []  # Will filter after
            required = self.domain_profile.required_personas

        matches = self.decision_logic.select_personas(
            task=task,
            max_personas=max_personas,
            required_domains=required,
            excluded_personas=excluded
        )

        # Filter by allowed if specified
        if self.domain_profile and self.domain_profile.allowed_personas:
            matches = [
                m for m in matches
                if m.persona_id in self.domain_profile.allowed_personas
            ]

        return matches

    def orchestrate(
        self,
        task: str,
        max_agents: int = 3,
        merge_strategy: MergeStrategy = MergeStrategy.UNION,
        execute_agents: bool = False
    ) -> OrchestrationResult:
        """
        Orchestrate a task with automatic persona selection and agent spawning.

        Args:
            task: Task to orchestrate
            max_agents: Maximum agents to spawn
            merge_strategy: How to merge agent results
            execute_agents: If True, actually spawn and run agents

        Returns:
            OrchestrationResult with personas, agents, and merged results
        """
        try:
            # 1. Select personas
            personas = self.select_personas(task, max_personas=max_agents)

            if not personas:
                return OrchestrationResult(
                    ok=False,
                    task=task,
                    personas_selected=[],
                    agents_spawned=[],
                    error="No suitable personas found for task"
                )

            logger.info(
                f"Orchestrating task with {len(personas)} personas: "
                f"{[p.persona_id for p in personas]}"
            )

            # 2. Spawn agents (if execute_agents is True)
            agents_spawned = []
            if execute_agents and self._spawn_fn:
                for persona in personas:
                    try:
                        branch_id = self._spawn_fn(
                            session_id=self.session_id,
                            task=task,
                            persona=persona.persona_id
                        )
                        agents_spawned.append(branch_id)
                    except Exception as e:
                        logger.warning(f"Failed to spawn agent {persona.persona_id}: {e}")

            # 3. Aggregate results (if agents were spawned)
            aggregated_findings = []
            aggregated_unknowns = []
            merged_vectors = {}

            if agents_spawned and self._aggregate_fn:
                try:
                    aggregate_result = self._aggregate_fn(
                        session_id=self.session_id,
                        strategy=merge_strategy.value
                    )
                    aggregated_findings = aggregate_result.get("findings", [])
                    aggregated_unknowns = aggregate_result.get("unknowns", [])
                    merged_vectors = aggregate_result.get("vectors", {})
                except Exception as e:
                    logger.warning(f"Failed to aggregate: {e}")

            # 4. Run compliance check if profile loaded
            compliance_check = None
            if self.domain_profile:
                compliance_check = self.check_compliance(
                    vectors=merged_vectors or {"uncertainty": 0.5},
                    findings=aggregated_findings,
                    unknowns=aggregated_unknowns
                )

            return OrchestrationResult(
                ok=True,
                task=task,
                personas_selected=personas,
                agents_spawned=agents_spawned,
                aggregated_findings=aggregated_findings,
                aggregated_unknowns=aggregated_unknowns,
                merge_strategy=merge_strategy,
                merged_vectors=merged_vectors,
                compliance_check=compliance_check
            )

        except Exception as e:
            logger.error(f"Orchestration failed: {e}")
            return OrchestrationResult(
                ok=False,
                task=task,
                personas_selected=[],
                agents_spawned=[],
                error=str(e)
            )

    def check_compliance(
        self,
        vectors: dict[str, float],
        findings: list[str],
        unknowns: list[str],
        flags: dict[str, bool] | None = None
    ) -> dict[str, Any]:
        """
        Run compliance gates and return CHECK decision.

        Args:
            vectors: Current epistemic vectors
            findings: Current findings
            unknowns: Current unknowns
            flags: Optional flags (e.g., pii_detected)

        Returns:
            Dict with decision, triggered_gates, actions
        """
        flags = flags or {}

        if not self.domain_profile:
            # No profile, use default logic.
            # Gate uses META UNCERTAINTY ONLY (2026-04-07).
            uncertainty = vectors.get("uncertainty", 0.5)

            if uncertainty <= 0.35:
                return {
                    "decision": "proceed",
                    "triggered_gates": [],
                    "actions": [],
                    "rationale": "Meta uncertainty within readiness threshold"
                }
            else:
                return {
                    "decision": "investigate",
                    "triggered_gates": [],
                    "actions": [],
                    "rationale": f"Meta uncertainty above threshold: {uncertainty:.2f} > 0.35"
                }

        # Build context for gate evaluation
        context = {
            "vectors": vectors,
            "findings": findings,
            "unknowns": unknowns,
            "flags": flags
        }

        # Evaluate gates
        triggered_gates = []
        actions = []

        for gate in self.domain_profile.gates:
            if gate.evaluate(context):
                triggered_gates.append(gate.gate_id)
                actions.append({
                    "gate_id": gate.gate_id,
                    "action": gate.action.value,
                    "priority": gate.priority,
                    "description": gate.description
                })

        # Determine decision based on gates
        if any(a["action"] == GateAction.HALT_AND_AUDIT.value for a in actions):
            decision = "halt"
        elif any(a["action"] == GateAction.REQUIRE_HUMAN.value for a in actions):
            decision = "require_human"
        elif any(a["action"] == GateAction.ESCALATE.value for a in actions):
            decision = "escalate"
        elif any(a["action"] == GateAction.INVESTIGATE.value for a in actions):
            decision = "investigate"
        else:
            # Check standard thresholds
            uncertainty = vectors.get("uncertainty", 0.5)
            know = vectors.get("know", 0.5)

            if uncertainty > self.domain_profile.uncertainty_trigger:
                decision = "investigate"
            elif know >= self.domain_profile.confidence_to_proceed:
                decision = "proceed"
            else:
                decision = "investigate"

        return {
            "decision": decision,
            "triggered_gates": triggered_gates,
            "actions": actions,
            "profile": self.domain_profile.name,
            "framework": self.domain_profile.compliance_framework,
            "rationale": self._build_rationale(decision, triggered_gates, vectors)
        }

    def _build_rationale(
        self,
        decision: str,
        triggered_gates: list[str],
        vectors: dict[str, float]
    ) -> str:
        """Build human-readable rationale for decision"""
        if triggered_gates:
            return f"Gates triggered: {', '.join(triggered_gates)}"
        elif decision == "proceed":
            return f"Vectors meet thresholds (know={vectors.get('know', 0):.2f}, uncertainty={vectors.get('uncertainty', 1):.2f})"
        else:
            return f"Vectors below threshold (know={vectors.get('know', 0):.2f}, uncertainty={vectors.get('uncertainty', 1):.2f})"

    def register_spawn_function(self, fn: Callable) -> None:
        """Register function to spawn epistemic agents"""
        self._spawn_fn = fn

    def register_aggregate_function(self, fn: Callable) -> None:
        """Register function to aggregate agent results"""
        self._aggregate_fn = fn

    def get_domain_stats(self) -> dict[str, Any]:
        """Get statistics about current domain configuration"""
        if not self.domain_profile:
            return {"profile": None}

        return {
            "profile": self.domain_profile.name,
            "framework": self.domain_profile.compliance_framework,
            "gates_count": len(self.domain_profile.gates),
            "uncertainty_trigger": self.domain_profile.uncertainty_trigger,
            "confidence_to_proceed": self.domain_profile.confidence_to_proceed,
            "audit_enabled": self.domain_profile.audit_all_actions,
            "restricted_tools": self.domain_profile.restricted_tools
        }

    def wire_agent_infrastructure(self) -> None:
        """
        Wire Sentinel to existing agent-spawn/agent-aggregate infrastructure.

        This connects the Sentinel to CLI commands for actual agent execution.
        """
        from empirica.core.agents.epistemic_agent import spawn_epistemic_agent

        def spawn_fn(session_id: str, task: str, persona: str) -> str:
            """Spawn an epistemic agent via existing infrastructure"""
            config = {
                "session_id": session_id,
                "task": task,
                "persona": persona,
                "cascade_style": "exploratory"
            }
            result = spawn_epistemic_agent(config)
            return result.branch_id

        def aggregate_fn(session_id: str, strategy: str = "union") -> dict[str, Any]:
            """Aggregate agent results"""
            from empirica.core.agents.epistemic_agent import (
                aggregate_branches,  # pyright: ignore[reportAttributeAccessIssue]
            )
            return aggregate_branches(session_id, strategy=strategy)

        self.register_spawn_function(spawn_fn)
        self.register_aggregate_function(aggregate_fn)

        logger.info("Wired Sentinel to agent infrastructure")

    def auto_orchestrate(
        self,
        task: str,
        max_agents: int = 3,
        merge_strategy: MergeStrategy = MergeStrategy.UNION,
        scope_breadth: float = 0.5,
        scope_duration: float = 0.5
    ) -> OrchestrationResult:
        """
        Full autonomous orchestration with agent spawning and loop tracking.

        This is the main entry point for Sentinel-governed autonomous workflows.

        Args:
            task: Task to orchestrate
            max_agents: Maximum parallel agents
            merge_strategy: How to merge results
            scope_breadth: Task scope (higher = more loops expected)
            scope_duration: Expected duration (higher = more loops)

        Returns:
            OrchestrationResult with full tracking
        """
        # Initialize loop tracking
        self.init_loop_tracking(
            scope_breadth=scope_breadth,
            scope_duration=scope_duration,
            mode=LoopMode.SENTINEL
        )

        # Wire agent infrastructure if not done
        if not self._spawn_fn:
            try:
                self.wire_agent_infrastructure()
            except Exception as e:
                logger.warning(f"Could not wire agent infrastructure: {e}")

        # Run orchestration with agent execution
        result = self.orchestrate(
            task=task,
            max_agents=max_agents,
            merge_strategy=merge_strategy,
            execute_agents=True
        )

        # Add loop tracking info
        if self.loop_tracker:
            result.loop_info = self.get_loop_summary()

        return result

    @classmethod
    def from_goal(cls, goal_id: str, session_id: str) -> 'Sentinel':
        """
        Create Sentinel from an existing goal's scope vectors.

        Args:
            goal_id: Goal ID to get scope from
            session_id: Current session ID

        Returns:
            Configured Sentinel with loop tracking from goal scope
        """
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT scope FROM goals WHERE id = ?",
            (goal_id,)
        )
        row = cursor.fetchone()
        db.close()

        sentinel = cls(session_id=session_id)

        # goals.scope is a JSON blob: {"breadth": .., "duration": .., "coordination": ..}
        # (written by GoalRepository.create_goal). Parse defensively — legacy rows
        # may hold a float/string instead of a dict.
        scope_data: dict = {}
        if row and row[0]:
            import json
            try:
                parsed = json.loads(row[0])
                if isinstance(parsed, dict):
                    scope_data = parsed
            except (json.JSONDecodeError, TypeError):
                pass

        if scope_data:
            sentinel.init_loop_tracking(
                scope_breadth=scope_data.get('breadth') or 0.5,
                scope_duration=scope_data.get('duration') or 0.5,
                scope_coordination=scope_data.get('coordination') or 0.3
            )

        return sentinel
