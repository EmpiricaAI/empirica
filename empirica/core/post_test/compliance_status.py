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
    def writes_to_trajectory(self) -> bool:
        """Only grounded and complete statuses write to learning trajectory."""
        return self in (ComplianceStatus.GROUNDED, ComplianceStatus.COMPLETE)

    @property
    def feeds_feedback(self) -> bool:
        """Only grounded and complete feed previous_transaction_feedback."""
        return self in (ComplianceStatus.GROUNDED, ComplianceStatus.COMPLETE)
