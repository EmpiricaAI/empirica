"""
B2 Iterative Compliance Loop — SPEC 1 Wave 3 implementation.

At POSTFLIGHT, runs the domain checklist against registered service checks.
Reports compliance status and advisories for failed checks. The AI
uses the advisory to scope the next transaction.

The loop does NOT auto-create transactions — it reports what needs doing.
The AI (or an autonomous orchestrator) decides whether to act on it.

See: .empirica/visions/2026-04-08-sentinel-as-compliance-loop.md
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ComplianceResult:
    """Result of running a domain compliance check loop."""

    status: str  # ComplianceStatus value
    domain: str | None
    criticality: str | None
    checks_run: int
    checks_passed: int
    checks_failed: int
    check_results: list[dict[str, Any]]
    next_transaction: dict[str, Any] | None = None  # advisory for iteration
    iteration_number: int = 1
    #: Checks that did not answer: skipped (tool absent), deferred to a later
    #: tier, or errored non-blocking. They are NOT failures and do not block,
    #: but they are not passes either. Before this field every one of them was
    #: counted in checks_passed, so "3 run, 3 passed" was the rendering of one
    #: pass, one skip and one error.
    checks_skipped: int = 0

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "status": self.status,
            "domain": self.domain,
            "criticality": self.criticality,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "checks_skipped": self.checks_skipped,
            "check_results": self.check_results,
        }
        if self.next_transaction:
            d["next_transaction"] = self.next_transaction
        return d


def run_compliance_checks(
    session_id: str,
    transaction_id: str | None,
    work_type: str | None,
    domain: str | None,
    criticality: str | None,
    project_path: str | None = None,
    db=None,
    iteration_number: int = 1,
    changed_files: list[str] | None = None,
    execution_tier: str = "always",
) -> ComplianceResult | None:
    """Run the domain compliance checklist and return results.

    Returns None if no domain/checklist applies (legacy behavior).
    Returns ComplianceResult with status and check results otherwise.
    """
    if not domain and not work_type:
        return None

    try:
        from empirica.config.domain_registry import DomainKey, DomainRegistry
        from empirica.config.service_registry import ServiceRegistry

        # Resolve the checklist
        reg = DomainRegistry(
            project_path=Path(project_path) if project_path else None,
        )
        key = DomainKey(
            work_type=work_type or "code",
            domain=domain or "default",
            criticality=criticality or "medium",
        )
        checklist = reg.resolve(key)

        if not checklist.has_checks:
            # Empty checklist — self-assessment stands (e.g., remote-ops)
            return ComplianceResult(
                status="complete",
                domain=key.domain,
                criticality=key.criticality,
                checks_run=0,
                checks_passed=0,
                checks_failed=0,
                check_results=[],
                iteration_number=iteration_number,
            )

        # Run each required check
        context = {
            "session_id": session_id,
            "transaction_id": transaction_id,
            "work_type": work_type,
            "domain": domain,
            "criticality": criticality,
            "project_path": project_path,
            "changed_files": changed_files or [],
        }

        check_results = []
        passed_count = 0
        skipped_count = 0
        failed_checks = []

        for check_id in checklist.required:
            try:
                result = ServiceRegistry.run(check_id, context, execution_tier=execution_tier)
                result_dict = {
                    "check_id": result.check_id,
                    "passed": result.passed,
                    "summary": result.summary,
                    "duration_ms": result.duration_ms,
                }
                if result.predicted_pass is not None:
                    result_dict["predicted_pass"] = result.predicted_pass
                if result.cached:
                    result_dict["cached"] = True
                if result.deferred:
                    result_dict["deferred"] = True
                    result_dict["tier"] = result.tier
                # A check that could not answer says so in its details:
                # {"skipped": True} when the tool is absent, {"error": ...} when
                # it raised and chose not to block. Neither is a pass.
                details = result.details if isinstance(result.details, dict) else {}
                did_not_answer = bool(result.deferred or details.get("skipped") or details.get("error"))
                if did_not_answer and result.passed:
                    result_dict["skipped"] = True
                check_results.append(result_dict)

                if result.passed and did_not_answer:
                    skipped_count += 1
                elif result.passed:
                    passed_count += 1
                else:
                    failed_checks.append(result)

                # Store in compliance_checks table if DB available
                if db and transaction_id:
                    _store_check_result(
                        db,
                        transaction_id,
                        session_id,
                        result,
                        iteration_number,
                    )
            except KeyError:
                # Check not registered — skip with warning
                logger.warning("Check '%s' not registered, skipping", check_id)
                check_results.append(
                    {
                        "check_id": check_id,
                        "passed": False,
                        "summary": f"Check '{check_id}' not registered in ServiceRegistry",
                        "duration_ms": 0,
                    }
                )
                failed_checks.append(None)

        checks_run = len(check_results)
        checks_failed = checks_run - passed_count - skipped_count

        # Determine status
        if checks_failed == 0:
            status = "complete"
            next_tx = None
        elif iteration_number >= checklist.max_iterations:
            status = "max_iterations_exceeded"
            next_tx = None
        else:
            status = "iteration_needed"
            failed_names = [r["check_id"] for r in check_results if not r["passed"]]
            next_tx = {
                "intent": f"address failures: {', '.join(failed_names)}",
                "inherited_domain": key.domain,
                "inherited_criticality": key.criticality,
                "iteration_number": iteration_number + 1,
                "parent_transaction_id": transaction_id,
            }

        return ComplianceResult(
            status=status,
            domain=key.domain,
            criticality=key.criticality,
            checks_run=checks_run,
            checks_passed=passed_count,
            checks_failed=checks_failed,
            check_results=check_results,
            next_transaction=next_tx,
            iteration_number=iteration_number,
            checks_skipped=skipped_count,
        )

    except Exception as e:
        logger.warning("Compliance check failed: %s", e)
        return None


def _store_check_result(
    db,
    transaction_id: str,
    session_id: str,
    result,
    iteration_number: int,
) -> None:
    """Store a check result in the compliance_checks table."""
    try:
        cursor = db.conn.cursor()
        cursor.execute(
            """
            INSERT INTO compliance_checks (
                check_record_id, transaction_id, session_id,
                check_id, tool, passed, details, summary,
                duration_ms, ran_at, predicted_pass, predicted_at,
                iteration_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                str(uuid.uuid4()),
                transaction_id,
                session_id,
                result.check_id,
                getattr(result, "tool", "unknown"),
                1 if result.passed else 0,
                None,  # details JSON — populated by real runners
                result.summary,
                result.duration_ms,
                result.ran_at,
                result.predicted_pass,
                result.predicted_at,
                iteration_number,
            ),
        )
        db.conn.commit()
    except Exception as e:
        logger.debug("Failed to store check result: %s", e)
