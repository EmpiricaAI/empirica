"""
Compliance status enum for the Sentinel reframe (SPEC 1 Part 3).

Tracks the state of domain-specific compliance checks across the
iterative compliance loop.
"""

from enum import Enum


class ComplianceStatus(str, Enum):
    # Phase 1 (SPEC 0) carry-overs — still used, mapped into new taxonomy
    GROUNDED = "grounded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNGROUNDED_REMOTE_OPS = "ungrounded_remote_ops"

    # Phase 2+ compliance loop states
    COMPLETE = "complete"
    # Nothing failed, but checks above the run's tier were deferred and never ran
    COMPLETE_WITH_DEFERRED = "complete_with_deferred"
    ITERATION_NEEDED = "iteration_needed"
    ITERATION_IN_PROGRESS = "iteration_in_progress"
    MAX_ITERATIONS_EXCEEDED = "max_iterations_exceeded"
    MANUAL_OVERRIDE = "manual_override"

    @property
    def is_complete(self) -> bool:
        """Nothing failed, so the loop does not iterate. True with deferred checks too: see fully_verified."""
        return self in (ComplianceStatus.COMPLETE, ComplianceStatus.COMPLETE_WITH_DEFERRED)

    @property
    def fully_verified(self) -> bool:
        """Complete AND every required check ran. complete_with_deferred is not: its higher-tier checks never ran."""
        return self is ComplianceStatus.COMPLETE

    @property
    def writes_to_trajectory(self) -> bool:
        """Grounded and complete statuses write to the learning trajectory.

        complete_with_deferred included: POSTFLIGHT runs only the `always` tier, so any checklist with a goal_completion
        or release check lands here; excluding it would stop calibration learning for those domains. What ran passed."""
        return self in (ComplianceStatus.GROUNDED, ComplianceStatus.COMPLETE, ComplianceStatus.COMPLETE_WITH_DEFERRED)

    @property
    def feeds_feedback(self) -> bool:
        """Grounded and complete statuses (deferred included, as for writes_to_trajectory) feed previous_transaction_feedback."""
        return self in (ComplianceStatus.GROUNDED, ComplianceStatus.COMPLETE, ComplianceStatus.COMPLETE_WITH_DEFERRED)
