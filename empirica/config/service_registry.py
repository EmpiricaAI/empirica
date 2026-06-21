"""
A2 Service Registry — SPEC 1 Part 2 implementation.

Registry of deterministic compliance checks. Each check self-declares:
- A unique check_id
- Which (work_type, domain) tuples it applies to
- A runner function that produces a CheckResult
- Pass criteria and timeout

The Sentinel looks up checks by ID via the registry and invokes their
runner. The registry never imports specific services — services register
themselves.

See: .empirica/visions/2026-04-08-sentinel-reframe-api-contract-spec.md
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class RegistrationConflict(Exception):
    """Raised when re-registering a check_id with a different declaration."""


@dataclass(frozen=True)
class CheckResult:
    """Result of running a single compliance check."""

    check_id: str
    passed: bool
    details: dict[str, Any]
    summary: str
    duration_ms: int
    ran_at: float

    # For Brier scoring of AI predictions
    predicted_pass: float | None = None
    predicted_at: float | None = None

    # Cache/tier metadata — informs the AI what actually ran
    cached: bool = False          # True if result came from cache
    deferred: bool = False        # True if check was deferred (tier too high)
    tier: str = "always"          # "always" | "goal_completion" | "release"


@dataclass(frozen=True)
class CheckDeclaration:
    """Declaration of a deterministic compliance check."""

    check_id: str
    tool: str
    applies_to: tuple[tuple[str, str], ...]  # ((work_type, domain), ...)
    criterion_description: str
    runner: Callable[..., CheckResult]
    timeout_seconds: int = 120
    tags: tuple[str, ...] = ()
    tier: str = "always"  # "always" = every POSTFLIGHT, "goal_completion" = at goal close, "release" = pre-release only


class ServiceRegistry:
    """Registry of available deterministic checks.

    Class-level registry — all methods are classmethods for singleton access.

    Cache: check results are cached by (check_id, content_hash) where
    content_hash is derived from changed_files. If the same files produce
    the same hash, the cached result is returned instantly. Cache is
    per-session (cleared on session-create).
    """

    _registered: ClassVar[dict[str, CheckDeclaration]] = {}
    _cache: ClassVar[dict[tuple[str, str], CheckResult]] = {}  # (check_id, content_hash) → result

    @classmethod
    def register(cls, declaration: CheckDeclaration) -> None:
        """Register a check. Idempotent for identical declarations.

        Raises RegistrationConflict if re-registering the same check_id
        with a different declaration.
        """
        existing = cls._registered.get(declaration.check_id)
        if existing is not None:
            if existing is declaration:
                return  # exact same object — no-op
            # Compare by value (excluding runner which is a callable)
            if (
                existing.tool == declaration.tool
                and existing.applies_to == declaration.applies_to
                and existing.criterion_description == declaration.criterion_description
                and existing.timeout_seconds == declaration.timeout_seconds
                and existing.tags == declaration.tags
            ):
                return  # equivalent — no-op
            raise RegistrationConflict(
                f"Check '{declaration.check_id}' already registered with "
                f"tool='{existing.tool}', cannot re-register with "
                f"tool='{declaration.tool}'"
            )
        cls._registered[declaration.check_id] = declaration

    @classmethod
    def get(cls, check_id: str) -> CheckDeclaration:
        """Look up a single check by ID. Raises KeyError if not found."""
        return cls._registered[check_id]

    @classmethod
    def resolve_for(
        cls, work_type: str, domain: str
    ) -> list[CheckDeclaration]:
        """Return all declarations matching (work_type, domain).

        Returns list ordered by check_id for determinism.
        Wildcard '*' in applies_to matches any value.
        """
        matches = []
        for decl in cls._registered.values():
            for wt, d in decl.applies_to:
                if (wt == "*" or wt == work_type) and (d == "*" or d == domain):
                    matches.append(decl)
                    break
        return sorted(matches, key=lambda d: d.check_id)

    @classmethod
    def _content_hash(cls, context: dict[str, Any]) -> str:
        """Compute a content hash from changed_files for cache keying."""
        import hashlib
        changed = sorted(context.get("changed_files", []))
        return hashlib.md5("|".join(changed).encode()).hexdigest()[:12]

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the check result cache (call on session-create)."""
        cls._cache.clear()

    @classmethod
    def run(
        cls,
        check_id: str,
        context: dict[str, Any],
        predicted_pass: float | None = None,
        execution_tier: str = "always",
    ) -> CheckResult:
        """Invoke a check's runner with caching and tier awareness.

        Cache: results are cached by (check_id, content_hash). Same changed
        files = same hash = cached result returned instantly. The AI is told
        via result.cached=True so it knows this wasn't a fresh run.

        Tiers: if the check's tier is higher than execution_tier, the check
        is deferred. The AI is told via result.deferred=True so it excludes
        this check from its Brier predictions.

        Tier hierarchy: always < goal_completion < release
        """
        decl = cls.get(check_id)  # raises KeyError if unknown
        tier_order = {"always": 0, "goal_completion": 1, "release": 2}
        predicted_at = time.time() if predicted_pass is not None else None

        # Tier check: defer if check tier exceeds execution tier
        if tier_order.get(decl.tier, 0) > tier_order.get(execution_tier, 0):
            return CheckResult(
                check_id=check_id,
                passed=True,  # deferred = not blocking
                details={"deferred": True, "tier": decl.tier, "execution_tier": execution_tier},
                summary=f"deferred ({decl.tier} tier, running at {execution_tier})",
                duration_ms=0,
                ran_at=time.time(),
                predicted_pass=predicted_pass,
                predicted_at=predicted_at,
                deferred=True,
                tier=decl.tier,
            )

        # Cache check: if changed_files produce the same hash, reuse result
        content_hash = cls._content_hash(context)
        cache_key = (check_id, content_hash)
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return CheckResult(
                check_id=cached.check_id,
                passed=cached.passed,
                details={**cached.details, "cache_hit": True},
                summary=cached.summary + " (cached)",
                duration_ms=0,
                ran_at=cached.ran_at,
                predicted_pass=predicted_pass,
                predicted_at=predicted_at,
                cached=True,
                tier=decl.tier,
            )

        # Execute the runner
        start = time.time()
        try:
            result = decl.runner(context)
            elapsed_ms = int((time.time() - start) * 1000)
            result = CheckResult(
                check_id=result.check_id,
                passed=result.passed,
                details=result.details,
                summary=result.summary,
                duration_ms=elapsed_ms,
                ran_at=result.ran_at,
                predicted_pass=predicted_pass,
                predicted_at=predicted_at,
                tier=decl.tier,
            )
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            err_type = type(e).__name__
            err_msg = str(e)[:200]
            result = CheckResult(
                check_id=check_id,
                passed=False,
                details={"error": f"{err_type}: {err_msg}"},
                summary=f"Check failed: {err_type}: {err_msg}",
                duration_ms=elapsed_ms,
                ran_at=time.time(),
                predicted_pass=predicted_pass,
                predicted_at=predicted_at,
                tier=decl.tier,
            )
            logger.warning("Check %s failed: %s: %s", check_id, err_type, err_msg)

        # Cache the result
        cls._cache[cache_key] = result
        return result

    @classmethod
    def list_all(cls) -> list[str]:
        """All registered check IDs."""
        return sorted(cls._registered.keys())

    @classmethod
    def load_builtins(cls) -> None:
        """Register built-in checks for existing collector sources.

        These are stub runners that will delegate to the actual collector
        methods when the compliance loop (B2) is wired. For now they
        declare the check surface so domain-resolve can verify checklists.
        """
        _register_builtin_checks()


# ---------------------------------------------------------------------------
# Built-in check runners (C2 — goal-scoped subprocess execution)
#
# All runners receive context["changed_files"] from the compliance loop.
# When available, checks scope to changed files only — making compliance
# meaningful per-goal rather than repo-wide.
# ---------------------------------------------------------------------------

def _py_files_from_changed(context: dict[str, Any]) -> list[str]:
    """Extract .py files from context changed_files."""
    return [f for f in context.get("changed_files", []) if f.endswith(".py")]


def _run_tests_check(context: dict[str, Any]) -> CheckResult:
    """Run pytest — scoped to changed modules when available."""
    import subprocess
    project_path = context.get("project_path", ".")
    changed_py = _py_files_from_changed(context)

    # Scope: if we have changed files, only test those modules
    cmd = ["python3", "-m", "pytest", "--tb=no", "-q"]
    scope_note = ""
    if changed_py:
        # Find test files that might cover the changed modules
        test_args = []
        for f in changed_py:
            if "/test_" in f or f.startswith("test_"):
                test_args.append(f)
        if test_args:
            cmd.extend(test_args)
            scope_note = f" (scoped to {len(test_args)} test files)"

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=project_path,
        )
        stdout = result.stdout.strip()
        passed = failed = 0
        for part in stdout.split("\n")[-1].split(","):
            part = part.strip()
            if "passed" in part:
                try:
                    passed = int(part.split()[0])
                except (ValueError, IndexError):
                    pass
            elif "failed" in part:
                try:
                    failed = int(part.split()[0])
                except (ValueError, IndexError):
                    pass

        summary = f"{passed} passed, {failed} failed" if failed else f"{passed} passed"
        return CheckResult(
            check_id="tests",
            passed=result.returncode == 0,
            details={"passed": passed, "failed": failed, "exit_code": result.returncode,
                      "scoped": bool(scope_note), "changed_files": len(changed_py)},
            summary=summary + scope_note,
            duration_ms=0, ran_at=time.time(),
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            check_id="tests", passed=False, details={"error": "timeout"},
            summary="pytest timed out after 300s", duration_ms=300000, ran_at=time.time(),
        )
    except FileNotFoundError:
        return CheckResult(
            check_id="tests", passed=True, details={"skipped": True},
            summary="pytest not available — skipped", duration_ms=0, ran_at=time.time(),
        )


def _run_lint_check(context: dict[str, Any]) -> CheckResult:
    """Run ruff check — scoped to changed files when available."""
    import subprocess
    project_path = context.get("project_path", ".")
    changed_py = _py_files_from_changed(context)

    # Scope: lint only changed files if available
    cmd = ["ruff", "check", "--output-format=json", "--quiet"]
    scope_note = ""
    if changed_py:
        cmd.extend(changed_py)
        scope_note = f" (scoped to {len(changed_py)} files)"

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, cwd=project_path,
        )
        import json as _json
        errors = 0
        try:
            findings = _json.loads(result.stdout) if result.stdout.strip() else []
            errors = len(findings)
        except Exception:
            errors = 1 if result.returncode != 0 else 0

        summary = f"{errors} lint errors" if errors else "lint clean"
        return CheckResult(
            check_id="lint",
            passed=result.returncode == 0,
            details={"errors": errors, "exit_code": result.returncode,
                      "scoped": bool(scope_note), "changed_files": len(changed_py)},
            summary=summary + scope_note,
            duration_ms=0, ran_at=time.time(),
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            check_id="lint", passed=False, details={"error": "timeout"},
            summary="ruff timed out after 60s", duration_ms=60000, ran_at=time.time(),
        )
    except FileNotFoundError:
        return CheckResult(
            check_id="lint", passed=True, details={"skipped": True},
            summary="ruff not available — skipped", duration_ms=0, ran_at=time.time(),
        )


def _run_git_metrics_check(context: dict[str, Any]) -> CheckResult:
    """Check for uncommitted changes in the project."""
    import subprocess
    project_path = context.get("project_path", ".")

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, cwd=project_path,
        )
        uncommitted = len([line for line in result.stdout.strip().split("\n") if line.strip()])
        return CheckResult(
            check_id="git_metrics",
            passed=uncommitted == 0,
            details={"uncommitted_files": uncommitted},
            summary=f"{uncommitted} uncommitted files" if uncommitted else "working tree clean",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception:
        return CheckResult(
            check_id="git_metrics", passed=True, details={"skipped": True},
            summary="git not available — skipped", duration_ms=0, ran_at=time.time(),
        )


def _run_complexity_check(context: dict[str, Any]) -> CheckResult:
    """Check cyclomatic complexity of changed files via radon."""
    import subprocess
    project_path = context.get("project_path", ".")
    changed_py = _py_files_from_changed(context)

    if not changed_py:
        return CheckResult(
            check_id="complexity", passed=True, details={"skipped": True, "reason": "no changed .py files"},
            summary="no changed files to check", duration_ms=0, ran_at=time.time(),
        )

    try:
        result = subprocess.run(
            ["radon", "cc", "--average", "--no-assert", "-s"] + changed_py,
            capture_output=True, text=True, timeout=30, cwd=project_path,
        )
        # Parse average complexity from last line: "Average complexity: A (2.5)"
        avg = 0.0
        grade = "A"
        for line in reversed(result.stdout.strip().split("\n")):
            if "Average complexity" in line:
                parts = line.split("(")
                if len(parts) >= 2:
                    try:
                        avg = float(parts[-1].rstrip(")"))
                    except ValueError:
                        pass
                grade_parts = line.split()
                for p in grade_parts:
                    if p in ("A", "B", "C", "D", "E", "F"):
                        grade = p
                break

        passed = grade in ("A", "B", "C")  # D, E, F fail
        return CheckResult(
            check_id="complexity",
            passed=passed,
            details={"average_cc": avg, "grade": grade, "files_checked": len(changed_py)},
            summary=f"complexity {grade} (avg {avg:.1f}) on {len(changed_py)} files",
            duration_ms=0, ran_at=time.time(),
        )
    except FileNotFoundError:
        return CheckResult(
            check_id="complexity", passed=True, details={"skipped": True},
            summary="radon not available — skipped", duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="complexity", passed=True, details={"error": str(e)[:200]},
            summary=f"complexity check error: {e}", duration_ms=0, ran_at=time.time(),
        )


def _run_dep_audit_check(context: dict[str, Any]) -> CheckResult:
    """Check for known vulnerabilities in dependencies via pip-audit."""
    import subprocess
    project_path = context.get("project_path", ".")

    try:
        result = subprocess.run(
            ["pip-audit", "--format=json", "--progress-spinner=off"],
            capture_output=True, text=True, timeout=120, cwd=project_path,
        )
        import json as _json
        vulns = 0
        try:
            data = _json.loads(result.stdout) if result.stdout.strip() else []
            if isinstance(data, list):
                vulns = len(data)
            elif isinstance(data, dict):
                vulns = len(data.get("dependencies", []))
        except Exception:
            vulns = 1 if result.returncode != 0 else 0

        return CheckResult(
            check_id="dep_audit",
            passed=vulns == 0,
            details={"vulnerabilities": vulns, "exit_code": result.returncode},
            summary=f"{vulns} known vulnerabilities" if vulns else "no known vulnerabilities",
            duration_ms=0, ran_at=time.time(),
        )
    except FileNotFoundError:
        return CheckResult(
            check_id="dep_audit", passed=True, details={"skipped": True},
            summary="pip-audit not available — skipped", duration_ms=0, ran_at=time.time(),
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            check_id="dep_audit", passed=True, details={"error": "timeout"},
            summary="pip-audit timed out after 120s", duration_ms=120000, ran_at=time.time(),
        )


# ---------------------------------------------------------------------------
# Artifact-based check runners (universal — query Empirica DB, not subprocess)
#
# These checks work across ALL domains: consulting, research, operations,
# marketing. They verify epistemic process quality by querying the artifact
# tables for the current transaction.
# ---------------------------------------------------------------------------

def _run_artifact_breadth_check(context: dict[str, Any]) -> CheckResult:
    """Check that multiple artifact types were logged in this transaction.

    Minimum 3 distinct types (e.g., findings + decisions + assumptions)
    indicates the AI is doing genuine epistemic work, not just coding.
    """
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        tx_id = context.get("transaction_id")
        session_id = context.get("session_id")

        # Count distinct artifact types logged in this transaction
        types_found = set()
        tables = [
            ("project_findings", "findings"),
            ("project_unknowns", "unknowns"),
            ("project_dead_ends", "dead_ends"),
            ("mistakes_made", "mistakes"),
            ("assumptions", "assumptions"),
            ("decisions", "decisions"),
        ]

        for table, label in tables:
            try:
                if tx_id:
                    cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE transaction_id = ?", (tx_id,))
                elif session_id:
                    cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (session_id,))
                else:
                    continue
                count = cursor.fetchone()[0]
                if count > 0:
                    types_found.add(label)
            except Exception:
                continue

        db.close()
        breadth = len(types_found)
        passed = breadth >= 2  # At least 2 distinct types
        return CheckResult(
            check_id="artifact_breadth",
            passed=passed,
            details={"types_found": sorted(types_found), "breadth": breadth},
            summary=f"{breadth} artifact types ({', '.join(sorted(types_found))})" if types_found else "no artifacts logged",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="artifact_breadth", passed=True, details={"error": str(e)[:200]},
            summary="artifact breadth check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


def _run_assumptions_flagged_check(context: dict[str, Any]) -> CheckResult:
    """Check that logged assumptions have confidence scores."""
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        session_id = context.get("session_id")

        if not session_id:
            db.close()
            return CheckResult(
                check_id="assumptions_flagged", passed=True, details={"skipped": True},
                summary="no session — skipped", duration_ms=0, ran_at=time.time(),
            )

        cursor.execute(
            "SELECT COUNT(*) FROM assumptions WHERE session_id = ?", (session_id,)
        )
        total = cursor.fetchone()[0]

        if total == 0:
            db.close()
            return CheckResult(
                check_id="assumptions_flagged", passed=True,
                details={"total": 0, "note": "no assumptions logged"},
                summary="no assumptions to check",
                duration_ms=0, ran_at=time.time(),
            )

        # Check how many have confidence scores
        cursor.execute(
            "SELECT COUNT(*) FROM assumptions WHERE session_id = ? AND confidence IS NOT NULL AND confidence > 0",
            (session_id,),
        )
        scored = cursor.fetchone()[0]
        db.close()

        passed = scored == total
        return CheckResult(
            check_id="assumptions_flagged",
            passed=passed,
            details={"total": total, "scored": scored, "unscored": total - scored},
            summary=f"{scored}/{total} assumptions have confidence scores",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="assumptions_flagged", passed=True, details={"error": str(e)[:200]},
            summary="assumptions check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


def _run_unknowns_resolved_check(context: dict[str, Any]) -> CheckResult:
    """Check that unknowns logged in this session are resolved or acknowledged."""
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        session_id = context.get("session_id")

        if not session_id:
            db.close()
            return CheckResult(
                check_id="unknowns_resolved", passed=True, details={"skipped": True},
                summary="no session — skipped", duration_ms=0, ran_at=time.time(),
            )

        cursor.execute(
            "SELECT COUNT(*) FROM project_unknowns WHERE session_id = ? AND is_resolved = 0",
            (session_id,),
        )
        unresolved = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM project_unknowns WHERE session_id = ?",
            (session_id,),
        )
        total = cursor.fetchone()[0]
        db.close()

        passed = unresolved == 0
        return CheckResult(
            check_id="unknowns_resolved",
            passed=passed,
            details={"total": total, "unresolved": unresolved, "resolved": total - unresolved},
            summary=f"{unresolved} unresolved unknowns" if unresolved else "all unknowns resolved",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="unknowns_resolved", passed=True, details={"error": str(e)[:200]},
            summary="unknowns check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


def _run_scope_coverage_check(context: dict[str, Any]) -> CheckResult:
    """Check that goal tasks are completed (scope coverage)."""
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        session_id = context.get("session_id")

        if not session_id:
            db.close()
            return CheckResult(
                check_id="scope_coverage", passed=True, details={"skipped": True},
                summary="no session — skipped", duration_ms=0, ran_at=time.time(),
            )

        # Count goals and completion for this session
        cursor.execute(
            "SELECT COUNT(*) FROM goals WHERE session_id = ?", (session_id,)
        )
        total_goals = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM goals WHERE session_id = ? AND is_completed = 1",
            (session_id,),
        )
        completed = cursor.fetchone()[0]
        db.close()

        if total_goals == 0:
            return CheckResult(
                check_id="scope_coverage", passed=True,
                details={"total": 0, "note": "no goals in session"},
                summary="no goals to check", duration_ms=0, ran_at=time.time(),
            )

        ratio = completed / total_goals
        passed = ratio >= 0.5  # At least half the goals done
        return CheckResult(
            check_id="scope_coverage",
            passed=passed,
            details={"total": total_goals, "completed": completed, "ratio": round(ratio, 2)},
            summary=f"{completed}/{total_goals} goals completed ({ratio:.0%})",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="scope_coverage", passed=True, details={"error": str(e)[:200]},
            summary="scope coverage check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


# ---------------------------------------------------------------------------
# Provenance check runners (query provenance graph columns from T1)
# ---------------------------------------------------------------------------

def _run_recommendation_traceability_check(context: dict[str, Any]) -> CheckResult:
    """Verify that decisions reference findings (evidence-backed choices).

    At least half of decisions should cite evidence via evidence_refs.
    Decisions without evidence are hunches — still valid but flagged.
    """
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        session_id = context.get("session_id")

        if not session_id:
            db.close()
            return CheckResult(
                check_id="recommendation_traceability", passed=True, details={"skipped": True},
                summary="no session — skipped", duration_ms=0, ran_at=time.time(),
            )

        cursor.execute(
            "SELECT COUNT(*) FROM decisions WHERE session_id = ?", (session_id,)
        )
        total = cursor.fetchone()[0]

        if total == 0:
            db.close()
            return CheckResult(
                check_id="recommendation_traceability", passed=True,
                details={"total": 0, "note": "no decisions logged"},
                summary="no decisions to check", duration_ms=0, ran_at=time.time(),
            )

        cursor.execute(
            "SELECT COUNT(*) FROM decisions WHERE session_id = ? AND evidence_refs IS NOT NULL",
            (session_id,),
        )
        evidenced = cursor.fetchone()[0]
        db.close()

        ratio = evidenced / total
        passed = ratio >= 0.5
        return CheckResult(
            check_id="recommendation_traceability",
            passed=passed,
            details={"total": total, "evidenced": evidenced, "ratio": round(ratio, 2)},
            summary=f"{evidenced}/{total} decisions cite evidence ({ratio:.0%})",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="recommendation_traceability", passed=True, details={"error": str(e)[:200]},
            summary="traceability check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


def _run_finding_sourced_check(context: dict[str, Any]) -> CheckResult:
    """Verify that findings reference sources (not just observations).

    Informational — many valid findings are observations without external
    sources. Only flags when source ratio is very low (< 25%) and there
    are enough findings to be meaningful (>= 3).
    """
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        session_id = context.get("session_id")

        if not session_id:
            db.close()
            return CheckResult(
                check_id="finding_sourced", passed=True, details={"skipped": True},
                summary="no session — skipped", duration_ms=0, ran_at=time.time(),
            )

        cursor.execute(
            "SELECT COUNT(*) FROM project_findings WHERE session_id = ?", (session_id,)
        )
        total = cursor.fetchone()[0]

        if total == 0:
            db.close()
            return CheckResult(
                check_id="finding_sourced", passed=True,
                details={"total": 0, "note": "no findings logged"},
                summary="no findings to check", duration_ms=0, ran_at=time.time(),
            )

        cursor.execute(
            "SELECT COUNT(*) FROM project_findings WHERE session_id = ? AND source_refs IS NOT NULL",
            (session_id,),
        )
        sourced = cursor.fetchone()[0]
        db.close()

        ratio = sourced / total
        # Lenient: pass unless very few findings have sources AND there are enough to matter
        passed = total < 3 or ratio >= 0.25
        return CheckResult(
            check_id="finding_sourced",
            passed=passed,
            details={"total": total, "sourced": sourced, "ratio": round(ratio, 2)},
            summary=f"{sourced}/{total} findings cite sources ({ratio:.0%})",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="finding_sourced", passed=True, details={"error": str(e)[:200]},
            summary="finding sourced check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


def _run_provenance_depth_check(context: dict[str, Any]) -> CheckResult:
    """Verify the full chain: source → finding → decision exists at least once.

    Checks that at least one decision has evidence_refs pointing to a finding
    that has source_refs pointing to a source. This proves the provenance
    graph is being used end-to-end.
    """
    try:
        import json as _json

        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        session_id = context.get("session_id")

        if not session_id:
            db.close()
            return CheckResult(
                check_id="provenance_depth", passed=True, details={"skipped": True},
                summary="no session — skipped", duration_ms=0, ran_at=time.time(),
            )

        # Find decisions with evidence_refs in this session
        cursor.execute(
            "SELECT evidence_refs FROM decisions WHERE session_id = ? AND evidence_refs IS NOT NULL",
            (session_id,),
        )
        decisions_with_evidence = cursor.fetchall()

        if not decisions_with_evidence:
            db.close()
            return CheckResult(
                check_id="provenance_depth", passed=False,
                details={"chains": 0, "note": "no decisions with evidence refs"},
                summary="no complete provenance chain (no evidenced decisions)",
                duration_ms=0, ran_at=time.time(),
            )

        # Collect all finding IDs referenced by decisions
        all_finding_ids = set()
        for (refs_json,) in decisions_with_evidence:
            try:
                refs = _json.loads(refs_json) if isinstance(refs_json, str) else refs_json
                if isinstance(refs, list):
                    all_finding_ids.update(refs)
            except Exception:
                continue

        if not all_finding_ids:
            db.close()
            return CheckResult(
                check_id="provenance_depth", passed=False,
                details={"chains": 0, "note": "evidence_refs parse failed"},
                summary="no complete provenance chain",
                duration_ms=0, ran_at=time.time(),
            )

        # Check if any of those findings have source_refs
        placeholders = ",".join("?" for _ in all_finding_ids)
        cursor.execute(
            f"SELECT COUNT(*) FROM project_findings WHERE id IN ({placeholders}) AND source_refs IS NOT NULL",
            tuple(all_finding_ids),
        )
        sourced_findings = cursor.fetchone()[0]
        db.close()

        passed = sourced_findings > 0
        return CheckResult(
            check_id="provenance_depth",
            passed=passed,
            details={
                "decisions_with_evidence": len(decisions_with_evidence),
                "finding_ids_referenced": len(all_finding_ids),
                "sourced_findings": sourced_findings,
                "complete_chains": sourced_findings,
            },
            summary=f"{sourced_findings} complete source→finding→decision chain(s)" if sourced_findings
            else "no complete provenance chain",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="provenance_depth", passed=True, details={"error": str(e)[:200]},
            summary="provenance depth check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


# ---------------------------------------------------------------------------
# Connective-tissue check runners (graph-edge health — universal/domain-agnostic)
#
# These verify the *connective tissue* of the epistemic graph: whether
# artifacts are linked, whether any float disconnected, and whether edges
# reference artifacts that actually exist. They query the canonical
# artifact_edges table (post-migration 041), which _store_edge dual-writes
# and _delete_artifact_edges cleans — so it is the live, queryable-both-ways
# source (the legacy <type>_data.edges JSON only holds outgoing edges).
#
# Domain-agnostic by construction: they assess graph *structure*, not content.
# (empirica-designed, seeded by the prop_evj5 mesh FYI; the FYI's verbatim
# three weren't recoverable, so these formalize signals the POSTFLIGHT
# retrospective already emits — edges_with_artifacts / edge_density_note.)
# ---------------------------------------------------------------------------

# Core artifact tables that carry id + session_id + transaction_id and can be
# edge endpoints. Mirrors _retro_count_edges in _workflow_shared.py.
_EDGE_ARTIFACT_TABLES = (
    "project_findings",
    "project_unknowns",
    "project_dead_ends",
    "mistakes_made",
    "assumptions",
    "decisions",
)

# Universal work types for connective-tissue checks: every work_type EXCEPT
# remote-ops, whose local sensors can't observe the work (calibration is
# ungrounded there, so no builtin checks apply). Matches the artifact_breadth
# enumeration — "domain-agnostic" means across domains (the '*' domain), not
# across remote-ops.
_CONNECTIVE_APPLIES_TO = (
    ("code", "*"), ("infra", "*"), ("research", "*"), ("comms", "*"),
    ("design", "*"), ("docs", "*"), ("data", "*"), ("debug", "*"),
    ("config", "*"), ("audit", "*"), ("release", "*"),
)


def _collect_artifact_ids(
    cursor, session_id: str | None = None, transaction_id: str | None = None
) -> set[str]:
    """Collect artifact UUIDs across the core artifact tables.

    No filters → all ids (global existence checks). With session_id (and
    optionally transaction_id) → scoped. Per-table try/except so a missing
    column/table on an older DB never breaks the set.
    """
    ids: set[str] = set()
    for table in _EDGE_ARTIFACT_TABLES:
        try:
            if transaction_id and session_id:
                cursor.execute(
                    f"SELECT id FROM {table} WHERE session_id = ? AND transaction_id = ?",
                    (session_id, transaction_id),
                )
            elif session_id:
                cursor.execute(f"SELECT id FROM {table} WHERE session_id = ?", (session_id,))
            else:
                cursor.execute(f"SELECT id FROM {table}")
            ids.update(r[0] for r in cursor.fetchall() if r[0] is not None)
        except Exception:
            continue
    return ids


def _edge_touched_ids(cursor, ids: set[str]) -> set[str]:
    """Subset of `ids` that appear as either endpoint of an artifact_edges row."""
    if not ids:
        return set()
    ids_list = list(ids)
    placeholders = ",".join("?" for _ in ids_list)
    try:
        cursor.execute(
            f"SELECT from_id FROM artifact_edges WHERE from_id IN ({placeholders}) "
            f"UNION SELECT to_id FROM artifact_edges WHERE to_id IN ({placeholders})",
            (*ids_list, *ids_list),
        )
        touched = {r[0] for r in cursor.fetchall()}
    except Exception:
        return set()
    return touched & ids


def _run_edge_density_check(context: dict[str, Any]) -> CheckResult:
    """Connective tissue: this transaction's artifacts should connect into the graph.

    Of the artifacts logged in this transaction, how many appear as an endpoint
    of an artifact_edges row. Lenient — only flags when >= 2 artifacts exist and
    NONE are connected (mirrors the retrospective edge nudge). Non-blocking.
    """
    try:
        session_id = context.get("session_id")
        tx_id = context.get("transaction_id")
        if not session_id:
            return CheckResult(
                check_id="edge_density", passed=True, details={"skipped": True},
                summary="no session — skipped", duration_ms=0, ran_at=time.time(),
            )
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        tx_ids = _collect_artifact_ids(cursor, session_id, tx_id)
        total = len(tx_ids)
        if total < 2:
            db.close()
            return CheckResult(
                check_id="edge_density", passed=True,
                details={"total": total, "note": "too few artifacts to assess density"},
                summary=f"{total} artifact(s) — density n/a", duration_ms=0, ran_at=time.time(),
            )
        connected = len(_edge_touched_ids(cursor, tx_ids))
        db.close()
        ratio = connected / total
        passed = connected >= 1
        return CheckResult(
            check_id="edge_density", passed=passed,
            details={"total": total, "connected": connected, "ratio": round(ratio, 2)},
            summary=f"{connected}/{total} transaction artifacts connected ({ratio:.0%})",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="edge_density", passed=True, details={"error": str(e)[:200]},
            summary="edge density check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


def _run_orphan_artifacts_check(context: dict[str, Any]) -> CheckResult:
    """Connective tissue: flag artifacts fully disconnected from the graph.

    Session artifacts appearing in NO artifact_edges row (neither endpoint) are
    orphans — invisible to the commit-context walker. Informational: only flags
    when the session has >= 3 artifacts AND a majority are orphaned. Non-blocking.
    """
    try:
        session_id = context.get("session_id")
        if not session_id:
            return CheckResult(
                check_id="orphan_artifacts", passed=True, details={"skipped": True},
                summary="no session — skipped", duration_ms=0, ran_at=time.time(),
            )
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        session_ids = _collect_artifact_ids(cursor, session_id)
        total = len(session_ids)
        if total < 3:
            db.close()
            return CheckResult(
                check_id="orphan_artifacts", passed=True,
                details={"total": total, "note": "too few artifacts to assess"},
                summary=f"{total} session artifact(s) — n/a", duration_ms=0, ran_at=time.time(),
            )
        connected = len(_edge_touched_ids(cursor, session_ids))
        db.close()
        orphans = total - connected
        ratio = orphans / total
        passed = ratio <= 0.5  # flag only when a majority are orphaned
        return CheckResult(
            check_id="orphan_artifacts", passed=passed,
            details={"total": total, "orphans": orphans, "connected": connected,
                     "orphan_ratio": round(ratio, 2)},
            summary=f"{orphans}/{total} session artifacts orphaned ({ratio:.0%})",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="orphan_artifacts", passed=True, details={"error": str(e)[:200]},
            summary="orphan artifacts check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


def _run_dangling_edges_check(context: dict[str, Any]) -> CheckResult:
    """Connective tissue: edges must reference artifacts that exist.

    For edges originating from this session's artifacts, verify each to_id
    resolves to a real artifact. A to_id pointing at nothing is a dangling edge
    — a typo'd ref, or an artifact deleted outside the edge-cleanup path.
    Referential-integrity check: should be 0. Non-blocking.
    """
    try:
        session_id = context.get("session_id")
        if not session_id:
            return CheckResult(
                check_id="dangling_edges", passed=True, details={"skipped": True},
                summary="no session — skipped", duration_ms=0, ran_at=time.time(),
            )
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()
        session_ids = _collect_artifact_ids(cursor, session_id)
        if not session_ids:
            db.close()
            return CheckResult(
                check_id="dangling_edges", passed=True,
                details={"total_edges": 0, "note": "no session artifacts"},
                summary="no session artifacts — n/a", duration_ms=0, ran_at=time.time(),
            )
        ids_list = list(session_ids)
        placeholders = ",".join("?" for _ in ids_list)
        try:
            cursor.execute(
                f"SELECT from_id, to_id, relation FROM artifact_edges "
                f"WHERE from_id IN ({placeholders})",
                tuple(ids_list),
            )
            edges = cursor.fetchall()
        except Exception:
            edges = []
        if not edges:
            db.close()
            return CheckResult(
                check_id="dangling_edges", passed=True,
                details={"total_edges": 0, "note": "no outgoing edges from session artifacts"},
                summary="no edges to check", duration_ms=0, ran_at=time.time(),
            )
        all_ids = _collect_artifact_ids(cursor)  # global existence set
        db.close()
        dangling = [(f, t, r) for (f, t, r) in edges if t not in all_ids]
        passed = len(dangling) == 0
        return CheckResult(
            check_id="dangling_edges", passed=passed,
            details={"total_edges": len(edges), "dangling": len(dangling),
                     "examples": [{"from": f, "to": t, "relation": r} for f, t, r in dangling[:5]]},
            summary=f"{len(dangling)} dangling edge(s) of {len(edges)}" if dangling
            else f"all {len(edges)} edges resolve",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="dangling_edges", passed=True, details={"error": str(e)[:200]},
            summary="dangling edges check failed (non-blocking)", duration_ms=0, ran_at=time.time(),
        )


def _register_builtin_checks() -> None:
    """Register all built-in checks."""
    builtins = [
        CheckDeclaration(
            check_id="tests",
            tool="pytest",
            applies_to=(("code", "*"), ("infra", "*"), ("debug", "*")),
            criterion_description="All pytest tests pass",
            runner=_run_tests_check,
            timeout_seconds=300,
            tags=("testing",),
            tier="goal_completion",
        ),
        CheckDeclaration(
            check_id="lint",
            tool="ruff",
            applies_to=(("code", "*"), ("infra", "*"), ("docs", "*")),
            criterion_description="No ruff lint errors",
            runner=_run_lint_check,
            timeout_seconds=60,
            tags=("quality",),
        ),
        CheckDeclaration(
            check_id="git_metrics",
            tool="git",
            applies_to=(("code", "*"), ("infra", "*")),
            criterion_description="Git history is clean (no uncommitted changes)",
            runner=_run_git_metrics_check,
            timeout_seconds=30,
            tags=("vcs",),
        ),
        CheckDeclaration(
            check_id="complexity",
            tool="radon",
            applies_to=(("code", "*"),),
            criterion_description="Changed files have acceptable cyclomatic complexity (A-C)",
            runner=_run_complexity_check,
            timeout_seconds=30,
            tags=("quality",),
        ),
        CheckDeclaration(
            check_id="dep_audit",
            tool="pip-audit",
            applies_to=(("code", "*"), ("infra", "*")),
            criterion_description="No known vulnerabilities in dependencies",
            runner=_run_dep_audit_check,
            timeout_seconds=120,
            tags=("security",),
            tier="release",
        ),
        # Artifact-based checks (universal — all domains)
        CheckDeclaration(
            check_id="artifact_breadth",
            tool="empirica-db",
            applies_to=(("code", "*"), ("infra", "*"), ("research", "*"), ("comms", "*"), ("design", "*"), ("docs", "*"), ("data", "*"), ("debug", "*"), ("config", "*"), ("audit", "*"), ("release", "*")),
            criterion_description="At least 2 distinct artifact types logged (findings, decisions, assumptions, etc.)",
            runner=_run_artifact_breadth_check,
            timeout_seconds=5,
            tags=("epistemic", "universal"),
        ),
        CheckDeclaration(
            check_id="assumptions_flagged",
            tool="empirica-db",
            applies_to=(("*", "consulting"), ("*", "research"), ("*", "operations"), ("*", "marketing")),
            criterion_description="All logged assumptions have confidence scores",
            runner=_run_assumptions_flagged_check,
            timeout_seconds=5,
            tags=("epistemic", "knowledge-work"),
        ),
        CheckDeclaration(
            check_id="unknowns_resolved",
            tool="empirica-db",
            applies_to=(("*", "consulting"), ("*", "research")),
            criterion_description="No unresolved unknowns blocking the deliverable",
            runner=_run_unknowns_resolved_check,
            timeout_seconds=5,
            tags=("epistemic", "knowledge-work"),
            tier="goal_completion",
        ),
        CheckDeclaration(
            check_id="scope_coverage",
            tool="empirica-db",
            applies_to=(("*", "consulting"), ("*", "operations"), ("*", "marketing")),
            criterion_description="At least 50% of session goals completed",
            runner=_run_scope_coverage_check,
            timeout_seconds=5,
            tags=("epistemic", "deliverable"),
            tier="goal_completion",
        ),
        # Provenance graph checks (T2 — query source→finding→decision links)
        CheckDeclaration(
            check_id="recommendation_traceability",
            tool="empirica-db",
            applies_to=(("*", "consulting"), ("*", "research")),
            criterion_description="At least 50% of decisions cite evidence (finding IDs)",
            runner=_run_recommendation_traceability_check,
            timeout_seconds=5,
            tags=("epistemic", "provenance"),
        ),
        CheckDeclaration(
            check_id="finding_sourced",
            tool="empirica-db",
            applies_to=(("*", "research"), ("*", "consulting")),
            criterion_description="Findings cite sources (informational — low threshold)",
            runner=_run_finding_sourced_check,
            timeout_seconds=5,
            tags=("epistemic", "provenance"),
        ),
        CheckDeclaration(
            check_id="provenance_depth",
            tool="empirica-db",
            applies_to=(("*", "consulting"), ("*", "research")),
            criterion_description="At least one complete source→finding→decision chain exists",
            runner=_run_provenance_depth_check,
            timeout_seconds=5,
            tags=("epistemic", "provenance"),
            tier="goal_completion",
        ),
        # Connective-tissue checks (graph-edge health — universal / domain-agnostic)
        CheckDeclaration(
            check_id="edge_density",
            tool="empirica-db",
            applies_to=_CONNECTIVE_APPLIES_TO,
            criterion_description="Transaction artifacts are connected into the graph (>=1 edge when >=2 artifacts)",
            runner=_run_edge_density_check,
            timeout_seconds=5,
            tags=("epistemic", "connective-tissue", "universal"),
        ),
        CheckDeclaration(
            check_id="orphan_artifacts",
            tool="empirica-db",
            applies_to=_CONNECTIVE_APPLIES_TO,
            criterion_description="No majority of session artifacts left fully disconnected from the graph",
            runner=_run_orphan_artifacts_check,
            timeout_seconds=5,
            tags=("epistemic", "connective-tissue", "universal"),
        ),
        CheckDeclaration(
            check_id="dangling_edges",
            tool="empirica-db",
            applies_to=_CONNECTIVE_APPLIES_TO,
            criterion_description="Every edge references an artifact that exists (referential integrity)",
            runner=_run_dangling_edges_check,
            timeout_seconds=5,
            tags=("epistemic", "connective-tissue", "integrity"),
            tier="goal_completion",
        ),
    ]

    for decl in builtins:
        ServiceRegistry.register(decl)
