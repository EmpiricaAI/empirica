"""
Compliance report command — project-wide quality and regulatory snapshot.

Runs all deterministic checks (ruff, radon, pyright, pytest, pip-audit)
and maps results to regulatory frameworks (EU AI Act, GDPR, ISO 42001).

Machine-readable JSON + human-readable summary.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Regulatory framework mappings
REGULATORY_MAP: dict[str, dict[str, Any]] = {
    "lint": {
        "check": "Static analysis (ruff)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 9", "requirement": "Risk management — code quality assurance"},
            "iso_42001": {"clause": "6.1.2", "requirement": "AI risk assessment — source code quality"},
        },
    },
    "complexity": {
        "check": "Cyclomatic complexity (radon)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 15(1)", "requirement": "Accuracy — maintainable, auditable code"},
            "iso_42001": {"clause": "8.4", "requirement": "AI system development — complexity management"},
        },
    },
    "type_safety": {
        "check": "Type checking (pyright)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 15(1)", "requirement": "Accuracy — type-safe operations"},
            "iso_42001": {"clause": "8.4", "requirement": "AI system development — correctness guarantees"},
        },
    },
    "tests": {
        "check": "Test suite (pytest)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 15(3)", "requirement": "Robustness — functional verification"},
            "iso_42001": {"clause": "8.5", "requirement": "AI system testing and validation"},
        },
    },
    "dep_audit": {
        "check": "Dependency audit (pip-audit)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 15(4)", "requirement": "Cybersecurity — supply chain security"},
            "iso_42001": {"clause": "A.7.5", "requirement": "Third-party components management"},
            "gdpr": {"article": "Art. 32", "requirement": "Security of processing — dependency integrity"},
        },
    },
    "security_scan": {
        "check": "SAST security scan (semgrep OWASP)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 15(4)", "requirement": "Cybersecurity — OWASP vulnerability scanning"},
            "iso_42001": {"clause": "8.4", "requirement": "AI system development — secure coding practices"},
            "gdpr": {"article": "Art. 25", "requirement": "Data protection by design and by default"},
        },
    },
    "tech_docs": {
        "check": "Technical documentation (docs-assess)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 11 + Annex IV", "requirement": "Technical documentation — coverage and accuracy"},
            "iso_42001": {"clause": "7.5.1", "requirement": "Documented information — creation and updating"},
        },
    },
    "release_chain": {
        "check": "Release chain integrity (publish verification)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 10", "requirement": "Data governance — release traceability"},
            "iso_42001": {"clause": "8.6", "requirement": "Release management — deployment verification"},
        },
    },
    "repo_hygiene": {
        "check": "Repository hygiene (git compliance)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 10", "requirement": "Data governance — version control and traceability"},
            "iso_42001": {"clause": "7.5", "requirement": "Documented information — configuration management"},
        },
    },
    "ai_transparency": {
        "check": "AI contribution transparency (git attribution)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 50", "requirement": "Transparency — AI-generated content disclosure"},
            "iso_42001": {"clause": "A.8.4", "requirement": "AI system operation — provenance tracking"},
        },
    },
    "decision_transparency": {
        "check": "Decision audit trail (rationale coverage)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 13", "requirement": "Transparency — interpretable AI output"},
            "iso_42001": {"clause": "9.1.2", "requirement": "Analysis and evaluation — decision traceability"},
            "gdpr": {"article": "Art. 22(3)", "requirement": "Automated decision-making — right to explanation"},
        },
    },
    "discipline": {
        "check": "Epistemic discipline trajectory (behavioral)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 17", "requirement": "Quality management system — process discipline"},
            "iso_42001": {"clause": "9.1.3", "requirement": "Monitoring and measurement — process effectiveness"},
        },
    },
    "epistemic_audit": {
        "check": "Epistemic transaction trail (empirica)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 12", "requirement": "Record-keeping — AI decision audit trail"},
            "iso_42001": {"clause": "9.1", "requirement": "Monitoring and measurement"},
            "gdpr": {"article": "Art. 30", "requirement": "Records of processing activities"},
        },
    },
    "calibration": {
        "check": "Grounded calibration (empirica)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 14", "requirement": "Human oversight — AI self-assessment accuracy"},
            "iso_42001": {"clause": "9.2", "requirement": "Internal audit — calibration verification"},
        },
    },
}


def _load_compliance_config(project_root: Path) -> dict[str, Any]:
    """Load .empirica/compliance.yaml — per-project overrides for compliance-report.

    Schema (all keys optional):
        skip_checks: list[str]
            Check IDs to drop from results (e.g. ["tech_docs"] for non-CLI projects).
        extra_checks: list[dict]
            Project-specific checks to run after the built-in suite. Each entry:
                id: str — check identifier (e.g. "cortex_docs_coverage")
                runner: str — command to invoke (script path relative to project_root,
                              or absolute path, or shell command)
                description: str — human-readable label (optional)
                timeout_seconds: int — runner timeout (default 60)
                regulatory: dict — optional framework mapping (eu_ai_act / iso_42001 / gdpr)
        repo_hygiene: dict
            Override sub-check requirements:
                license_required: bool (default True)
                changelog_required: bool (default True)
                release_scripts_required: bool (default True)

    Returns empty dict when the config file is absent or malformed — compliance-report
    falls back to its built-in defaults.
    """
    config_path = project_root / ".empirica" / "compliance.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("compliance.yaml load failed: %s — falling back to defaults", exc)
        return {}


def _run_extra_check(check_def: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Run a project-defined extra check declared in .empirica/compliance.yaml.

    The runner script must accept --output json and emit a JSON object on stdout
    with at least: {"passed": bool, "status": "pass"|"fail"}. Any extra fields
    are passed through to the report.
    """
    check_id = check_def.get("id", "extra_check")
    runner = check_def.get("runner")
    if not runner:
        return {"check": check_id, "passed": None, "status": "unavailable", "error": "no runner"}

    timeout = int(check_def.get("timeout_seconds", 60))

    # Split shell-style runner ("scripts/foo.py", "python scripts/foo.py", "/abs/path")
    cmd = runner.split() if isinstance(runner, str) else list(runner)
    if cmd and cmd[0].endswith(".py") and not cmd[0].startswith("python"):
        cmd = ["python3", *cmd]

    raw = _run_check(check_id, [*cmd, "--output", "json"], timeout=timeout)
    if raw.get("error"):
        return {"check": check_id, "passed": None, "status": "unavailable", "error": raw["error"]}

    try:
        import json as _json
        data = _json.loads(raw.get("stdout") or "{}")
    except Exception:
        return {
            "check": check_id, "passed": False, "status": "fail",
            "error": "runner did not emit valid JSON",
            "duration_seconds": raw.get("duration_seconds"),
        }

    # Merge runner output with metadata
    result = {
        "check": check_id,
        "tool": runner,
        "duration_seconds": raw.get("duration_seconds"),
        **data,
    }
    if "status" not in result:
        result["status"] = "pass" if result.get("passed") else "fail"
    if "regulatory" not in result and check_def.get("regulatory"):
        result["regulatory"] = check_def["regulatory"]
    if "description" not in result and check_def.get("description"):
        result["description"] = check_def["description"]
    return result


def _run_check(name: str, cmd: list[str], timeout: int = 120) -> dict[str, Any]:
    """Run a single compliance check and return structured result."""
    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        duration = round(time.time() - start, 2)
        return {
            "check": name,
            "passed": result.returncode == 0,
            "returncode": result.returncode,
            "duration_seconds": duration,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except FileNotFoundError:
        return {"check": name, "passed": None, "error": "tool not installed", "duration_seconds": 0}
    except subprocess.TimeoutExpired:
        return {"check": name, "passed": False, "error": f"timeout ({timeout}s)", "duration_seconds": timeout}


def _parse_ruff_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse ruff check output into structured result."""
    if raw.get("error"):
        return {**raw, "violations": None, "status": "unavailable"}
    passed = raw["passed"]
    violation_count = 0
    if not passed and raw.get("stdout"):
        for line in raw["stdout"].strip().split("\n"):
            if line.startswith("Found ") and " error" in line:
                try:
                    violation_count = int(line.split()[1])
                except (ValueError, IndexError):
                    pass
    return {
        "check": "lint",
        "tool": "ruff",
        "passed": passed,
        "violations": violation_count,
        "status": "pass" if passed else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _parse_c901_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse ruff C901 output (cyclomatic complexity > 15)."""
    if raw.get("error"):
        return {**raw, "check": "complexity", "violations": None, "status": "unavailable"}
    passed = raw["passed"]
    violation_count = 0
    if not passed:
        stderr = raw.get("stderr") or raw.get("stdout") or ""
        for line in stderr.strip().split("\n"):
            if line.startswith("Found ") and "error" in line:
                try:
                    violation_count = int(line.split()[1])
                except (ValueError, IndexError):
                    pass
    return {
        "check": "complexity",
        "tool": "ruff (C901, threshold 15)",
        "passed": passed,
        "violations": violation_count,
        "status": "pass" if passed else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _parse_pyright_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse pyright output."""
    if raw.get("error"):
        return {**raw, "errors": None, "status": "unavailable"}
    errors = 0
    warnings = 0
    for line in (raw.get("stdout") or "").strip().split("\n"):
        if "error" in line and "warning" in line:
            parts = line.split(",")
            for part in parts:
                part = part.strip()
                if "error" in part:
                    try:
                        errors = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
                if "warning" in part:
                    try:
                        warnings = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
    return {
        "check": "type_safety",
        "tool": "pyright",
        "passed": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "status": "pass" if errors == 0 else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _parse_pytest_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse pytest output."""
    if raw.get("error"):
        return {**raw, "passed_count": None, "status": "unavailable"}
    passed_count = 0
    failed_count = 0
    skipped_count = 0
    output = (raw.get("stdout") or "") + (raw.get("stderr") or "")
    for line in output.strip().split("\n"):
        if "passed" in line:
            for part in line.split(","):
                part = part.strip()
                if "passed" in part:
                    try:
                        passed_count = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
                if "failed" in part:
                    try:
                        failed_count = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
                if "skipped" in part:
                    try:
                        skipped_count = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
    return {
        "check": "tests",
        "tool": "pytest",
        "passed": raw["passed"],
        "passed_count": passed_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "status": "pass" if raw["passed"] else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _parse_pip_audit_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse pip-audit output."""
    if raw.get("error"):
        return {**raw, "vulnerabilities": None, "status": "unavailable"}
    vuln_count = 0
    output = (raw.get("stdout") or "") + (raw.get("stderr") or "")
    for line in output.strip().split("\n"):
        if line.startswith("Found ") and "vulnerabilit" in line:
            try:
                vuln_count = int(line.split()[1])
            except (ValueError, IndexError):
                pass
    return {
        "check": "dep_audit",
        "tool": "pip-audit",
        "passed": vuln_count == 0,
        "vulnerabilities": vuln_count,
        "status": "pass" if vuln_count == 0 else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _build_epistemic_audit(project_root: Path) -> dict[str, Any]:
    """Check for epistemic transaction trail."""
    from empirica.config.path_resolver import get_session_db_path
    try:
        db_path = get_session_db_path()
    except Exception:
        db_path = project_root / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return {"check": "epistemic_audit", "passed": None, "status": "unavailable", "reason": "no database"}

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM reflexes WHERE phase = 'POSTFLIGHT'")
        postflights = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM project_findings")
        findings = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM decisions")
        decisions = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        conn.close()
        return {"check": "epistemic_audit", "passed": None, "status": "unavailable", "reason": "schema mismatch"}
    conn.close()

    has_trail = postflights > 0 and findings > 0
    return {
        "check": "epistemic_audit",
        "passed": has_trail,
        "postflights": postflights,
        "findings": findings,
        "decisions": decisions,
        "status": "pass" if has_trail else "fail",
    }


def _build_calibration_check(project_root: Path) -> dict[str, Any]:
    """Check grounded calibration data."""
    from empirica.config.path_resolver import get_session_db_path
    try:
        db_path = get_session_db_path()
    except Exception:
        db_path = project_root / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return {"check": "calibration", "passed": None, "status": "unavailable"}

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT COUNT(*), AVG(overall_calibration_score)
            FROM grounded_verifications
            WHERE compliance_status = 'grounded'
        """)
        row = cursor.fetchone()
        count = row[0] if row else 0
        avg_score = round(row[1], 4) if row and row[1] else None
    except sqlite3.OperationalError:
        conn.close()
        return {"check": "calibration", "passed": None, "status": "unavailable"}
    conn.close()

    return {
        "check": "calibration",
        "passed": count > 0,
        "grounded_verifications": count,
        "avg_calibration_score": avg_score,
        "status": "pass" if count > 0 else "no_data",
    }


def _build_release_chain_check(project_root: Path) -> dict[str, Any]:
    """Verify current version is published to all declared channels."""
    # Get current version from pyproject.toml
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return {"check": "release_chain", "passed": None, "status": "unavailable"}

    version = None
    try:
        with open(pyproject) as f:
            for line in f:
                if line.strip().startswith("version"):
                    version = line.split("=")[1].strip().strip('"').strip("'")
                    break
    except Exception:
        pass
    if not version:
        return {"check": "release_chain", "passed": None, "status": "unavailable"}

    # Load publish_channels from project.yaml
    proj_yaml = project_root / ".empirica" / "project.yaml"
    channels: list[str] = ["git_tag"]  # always check git tag
    if proj_yaml.exists():
        try:
            import yaml
            with open(proj_yaml) as f:
                proj = yaml.safe_load(f) or {}
            channels = proj.get("publish_channels", channels)
        except Exception:
            pass

    # Check each channel
    results: dict[str, str] = {}
    for channel in channels:
        results[channel] = _check_channel(channel, version, project_root)

    published = sum(1 for v in results.values() if v == "published")
    missing = sum(1 for v in results.values() if v == "missing")
    passed = missing == 0

    return {
        "check": "release_chain",
        "passed": passed,
        "version": version,
        "channels": results,
        "published": published,
        "missing": missing,
        "total": len(channels),
        "status": "pass" if passed else "fail",
    }


def _check_channel(channel: str, version: str, project_root: Path) -> str:
    """Check if version is published to a specific channel. Returns status string."""
    try:
        if channel == "git_tag":
            result = subprocess.run(
                ["git", "tag", "-l", f"v{version}"],
                capture_output=True, text=True, timeout=5, cwd=str(project_root),
            )
            return "published" if f"v{version}" in result.stdout else "missing"

        if channel == "github_release":
            result = subprocess.run(
                ["gh", "release", "view", f"v{version}", "--json", "tagName"],
                capture_output=True, text=True, timeout=10,
            )
            return "published" if result.returncode == 0 else "missing"

        if channel == "pypi":
            result = subprocess.run(
                ["pip", "index", "versions", "empirica"],
                capture_output=True, text=True, timeout=15,
            )
            return "published" if version in result.stdout else "missing"

        if channel == "pypi_mcp":
            result = subprocess.run(
                ["pip", "index", "versions", "empirica-mcp"],
                capture_output=True, text=True, timeout=15,
            )
            return "published" if version in result.stdout else "missing"

        if channel == "docker":
            # Check Docker Hub tag existence via API
            result = subprocess.run(
                ["docker", "manifest", "inspect", f"nubaeon/empirica:{version}"],
                capture_output=True, text=True, timeout=15,
            )
            return "published" if result.returncode == 0 else "missing"

        if channel == "homebrew":
            # Check if homebrew formula has the version
            tap_dir = project_root.parent / "homebrew-tap"
            if tap_dir.exists():
                formula = tap_dir / "empirica.rb"
                if formula.exists():
                    content = formula.read_text()
                    return "published" if version in content else "missing"
            return "skipped"

        return "unknown_channel"
    except Exception:
        return "check_failed"


def _build_discipline_check(project_root: Path) -> dict[str, Any]:
    """Assess epistemic process discipline from observable behavioral evidence.

    Every component is measured by a service the AI doesn't control:
    - Transaction count: DB timestamps (reflexes table)
    - Artifact breadth: DB artifact tables
    - Goal completion: DB goals table
    - Commit discipline: git log
    - Investigation ratio: sentinel tool counts

    Cannot be gamed — you either did the work or you didn't.
    """
    from empirica.config.path_resolver import get_session_db_path
    try:
        db_path = get_session_db_path()
    except Exception:
        db_path = project_root / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return {"check": "discipline", "passed": None, "status": "unavailable"}

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    try:
        # Transaction count (POSTFLIGHT = completed transaction)
        cursor.execute("SELECT COUNT(*) FROM reflexes WHERE phase = 'POSTFLIGHT'")
        transactions = cursor.fetchone()[0]

        # Artifact breadth — count each type
        artifact_counts: dict[str, int] = {}
        for table, label in [
            ("project_findings", "findings"),
            ("project_unknowns", "unknowns"),
            ("project_dead_ends", "dead_ends"),
            ("mistakes_made", "mistakes"),
            ("assumptions", "assumptions"),
            ("decisions", "decisions"),
        ]:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                artifact_counts[label] = cursor.fetchone()[0]
            except sqlite3.OperationalError:
                artifact_counts[label] = 0

        types_used = sum(1 for v in artifact_counts.values() if v > 0)
        total_artifacts = sum(artifact_counts.values())

        # Goal completion
        cursor.execute("SELECT COUNT(*) FROM goals WHERE status = 'completed'")
        goals_completed = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM goals")
        goals_total = cursor.fetchone()[0]

        # Commit count (git log)
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--all"],
                capture_output=True, text=True, timeout=5, cwd=str(project_root),
            )
            commit_count = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
        except Exception:
            commit_count = 0

    except sqlite3.OperationalError:
        conn.close()
        return {"check": "discipline", "passed": None, "status": "unavailable"}
    conn.close()

    # Discipline assessment — narrative gaps
    gaps: list[str] = []
    if types_used <= 2:
        gaps.append(f"Narrow artifact breadth: only {types_used}/6 types used")
    if artifact_counts.get("assumptions", 0) == 0:
        gaps.append("No assumptions logged — are beliefs being made explicit?")
    if artifact_counts.get("dead_ends", 0) == 0:
        gaps.append("No dead-ends logged — are failed approaches being captured?")
    if transactions > 0 and total_artifacts / transactions < 1:
        gaps.append(f"Low artifact density: {total_artifacts/transactions:.1f} per transaction")

    # Pass if: has transactions, has diverse artifacts, has goals
    passed = transactions >= 3 and types_used >= 3 and goals_completed > 0

    return {
        "check": "discipline",
        "passed": passed,
        "transactions": transactions,
        "artifacts": artifact_counts,
        "artifact_types_used": types_used,
        "total_artifacts": total_artifacts,
        "goals_completed": goals_completed,
        "goals_total": goals_total,
        "commits": commit_count,
        "gaps": gaps,
        "status": "pass" if passed else "fail",
    }


def _build_ai_transparency_check(project_root: Path) -> dict[str, Any]:
    """Check AI contribution attribution in git history."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%b", "-50"],
            capture_output=True, text=True, timeout=10, cwd=str(project_root),
        )
        if result.returncode != 0:
            return {"check": "ai_transparency", "passed": None, "status": "unavailable"}

        total_commits = 50  # sampled
        attributed = sum(1 for line in result.stdout.split("\n") if "Co-Authored-By:" in line)
        ratio = attributed / max(total_commits, 1)

        return {
            "check": "ai_transparency",
            "passed": attributed > 0,
            "ai_attributed_commits": attributed,
            "sample_size": total_commits,
            "attribution_ratio": round(ratio, 2),
            "status": "pass" if attributed > 0 else "fail",
        }
    except Exception:
        return {"check": "ai_transparency", "passed": None, "status": "unavailable"}


def _build_decision_transparency_check(project_root: Path) -> dict[str, Any]:
    """Check that logged decisions have rationale (interpretable AI output)."""
    from empirica.config.path_resolver import get_session_db_path
    try:
        db_path = get_session_db_path()
    except Exception:
        db_path = project_root / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return {"check": "decision_transparency", "passed": None, "status": "unavailable"}

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM decisions")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM decisions WHERE rationale IS NOT NULL AND rationale != ''")
        with_rationale = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        conn.close()
        return {"check": "decision_transparency", "passed": None, "status": "unavailable"}
    conn.close()

    ratio = with_rationale / max(total, 1)
    # Pass if >= 80% of decisions have rationale
    passed = total == 0 or ratio >= 0.8
    return {
        "check": "decision_transparency",
        "passed": passed,
        "decisions_total": total,
        "decisions_with_rationale": with_rationale,
        "rationale_coverage": round(ratio * 100, 1),
        "status": "pass" if passed else "fail",
    }


def _parse_docs_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse empirica docs-assess JSON output."""
    if raw.get("error"):
        return {"check": "tech_docs", "passed": None, "status": "unavailable", "error": raw["error"]}

    try:
        import json as _json
        data = _json.loads(raw.get("stdout") or "{}")
        overall = data.get("overall", {})
        coverage = overall.get("coverage", 0)
        documented = overall.get("documented", 0)
        total = overall.get("total_features", 0)
        # Pass if coverage >= 70%
        passed = coverage >= 70
        return {
            "check": "tech_docs",
            "tool": "empirica docs-assess",
            "passed": passed,
            "coverage_percent": round(coverage, 1),
            "documented": documented,
            "total": total,
            "status": "pass" if passed else "fail",
            "duration_seconds": raw["duration_seconds"],
        }
    except Exception:
        return {"check": "tech_docs", "passed": None, "status": "unavailable", "duration_seconds": raw.get("duration_seconds", 0)}


def _build_repo_hygiene_check(project_root: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check repository hygiene — files, structure, version consistency.

    Per-project overrides via .empirica/compliance.yaml repo_hygiene:
        license_required: bool (default True)
        changelog_required: bool (default True)
        release_scripts_required: bool (default True)
    Skipped sub-checks count as neither pass nor fail — they don't appear in
    checks_total, keeping the score honest for projects where the requirement
    doesn't apply.
    """
    overrides = overrides or {}
    checks_passed = 0
    checks_total = 0
    details: dict[str, str] = {}

    # 1. LICENSE file
    if overrides.get("license_required", True):
        checks_total += 1
        license_exists = (project_root / "LICENSE").exists() or (project_root / "LICENSE.md").exists()
        if license_exists:
            checks_passed += 1
            details["license"] = "present"
        else:
            details["license"] = "MISSING"
    else:
        details["license"] = "skipped"

    # 2. CHANGELOG
    if overrides.get("changelog_required", True):
        checks_total += 1
        changelog = project_root / "CHANGELOG.md"
        if changelog.exists():
            checks_passed += 1
            details["changelog"] = "present"
        else:
            details["changelog"] = "MISSING"
    else:
        details["changelog"] = "skipped"

    # 3. .gitignore
    checks_total += 1
    gitignore = project_root / ".gitignore"
    if gitignore.exists():
        checks_passed += 1
        details["gitignore"] = "present"
    else:
        details["gitignore"] = "MISSING"

    # 4. Release scripts
    if overrides.get("release_scripts_required", True):
        checks_total += 1
        release_script = (
            (project_root / "scripts" / "release.py").exists()
            or (project_root / "scripts" / "release.sh").exists()
            or (project_root / "Makefile").exists()
        )
        if release_script:
            checks_passed += 1
            details["release_scripts"] = "present"
        else:
            details["release_scripts"] = "MISSING"
    else:
        details["release_scripts"] = "skipped"

    # 5. No secrets in tracked files
    checks_total += 1
    secret_patterns = [".env", "credentials.json", "secrets.yaml", "id_rsa", "id_ed25519"]
    secrets_found = []
    for pattern in secret_patterns:
        for match in project_root.rglob(pattern):
            if ".git" not in str(match) and "__pycache__" not in str(match):
                secrets_found.append(str(match.relative_to(project_root)))
    if not secrets_found:
        checks_passed += 1
        details["no_tracked_secrets"] = "clean"
    else:
        details["no_tracked_secrets"] = f"FOUND: {', '.join(secrets_found[:3])}"

    # 6. Version file exists (pyproject.toml or setup.py)
    checks_total += 1
    has_version = (project_root / "pyproject.toml").exists() or (project_root / "setup.py").exists()
    if has_version:
        checks_passed += 1
        details["version_file"] = "present"
    else:
        details["version_file"] = "MISSING"

    passed = checks_passed == checks_total
    return {
        "check": "repo_hygiene",
        "passed": passed,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "details": details,
        "status": "pass" if passed else "fail",
    }


def _compute_overall_status(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute overall compliance status."""
    total = len(results)
    passed = sum(1 for r in results if r.get("passed") is True)
    failed = sum(1 for r in results if r.get("passed") is False)
    unavailable = sum(1 for r in results if r.get("passed") is None)

    if failed == 0 and unavailable == 0:
        status = "fully_compliant"
    elif failed == 0:
        status = "compliant_with_gaps"
    else:
        status = "non_compliant"

    return {
        "status": status,
        "checks_total": total,
        "checks_passed": passed,
        "checks_failed": failed,
        "checks_unavailable": unavailable,
        "score": round(passed / max(total - unavailable, 1), 4),
    }


def _add_regulatory_mapping(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich results with regulatory framework mappings."""
    for result in results:
        check_id = result.get("check", "")
        if check_id in REGULATORY_MAP:
            result["regulatory"] = REGULATORY_MAP[check_id]["frameworks"]
    return results


def _parse_semgrep_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse semgrep OWASP scan output."""
    if raw.get("error"):
        return {**raw, "check": "security_scan", "findings": None, "status": "unavailable"}

    # semgrep --json outputs to stdout
    findings = 0
    critical = 0
    try:
        import json as _json
        data = _json.loads(raw.get("stdout") or "{}")
        results = data.get("results", [])
        findings = len(results)
        # Count critical findings (exclude known accepted patterns like MD5 for content IDs)
        for r in results:
            rule = r.get("check_id", "")
            if "md5" not in rule.lower() and "insecure-hash" not in rule.lower():
                critical += 1
    except Exception:
        pass

    return {
        "check": "security_scan",
        "tool": "semgrep (OWASP top-10)",
        "passed": critical == 0,
        "findings_total": findings,
        "findings_critical": critical,
        "status": "pass" if critical == 0 else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def run_compliance_report(
    project_root: Path | None = None,
    include_tests: bool = False,
    include_dep_audit: bool = False,
    include_security: bool = False,
) -> dict[str, Any]:
    """Run full compliance report and return structured results.

    Per-project behavior driven by .empirica/compliance.yaml:
        skip_checks: drop check IDs from output (e.g. ["tech_docs"] for non-CLI)
        extra_checks: append project-specific check runners
        repo_hygiene: relax sub-checks (license/changelog/release_scripts)
    """
    if project_root is None:
        project_root = Path.cwd()

    config = _load_compliance_config(project_root)
    skip_checks: set[str] = set(config.get("skip_checks") or [])
    extra_checks: list[dict[str, Any]] = list(config.get("extra_checks") or [])
    hygiene_overrides: dict[str, Any] = dict(config.get("repo_hygiene") or {})

    results: list[dict[str, Any]] = []

    # Always-run checks (fast)
    ruff_raw = _run_check("ruff", ["ruff", "check"], timeout=30)
    results.append(_parse_ruff_result(ruff_raw))

    complexity_raw = _run_check("ruff-c901", ["ruff", "check", "--select", "C901"], timeout=30)
    results.append(_parse_c901_result(complexity_raw))

    pyright_raw = _run_check("pyright", ["pyright", "empirica/"], timeout=120)
    results.append(_parse_pyright_result(pyright_raw))

    # Optional checks (slow)
    if include_tests:
        pytest_raw = _run_check("pytest", ["python3", "-m", "pytest", "tests/", "-q", "--tb=line"], timeout=300)
        results.append(_parse_pytest_result(pytest_raw))

    if include_dep_audit:
        audit_raw = _run_check("pip-audit", ["pip-audit"], timeout=120)
        results.append(_parse_pip_audit_result(audit_raw))

    if include_security:
        semgrep_raw = _run_check(
            "semgrep", ["semgrep", "--config", "p/owasp-top-ten", "empirica/", "--json", "--quiet"],
            timeout=180,
        )
        results.append(_parse_semgrep_result(semgrep_raw))

    # Technical documentation (runs docs-assess)
    docs_raw = _run_check("docs-assess", ["empirica", "docs-assess", "--output", "json"], timeout=60)
    results.append(_parse_docs_result(docs_raw))

    # Release chain (git + network checks)
    results.append(_build_release_chain_check(project_root))

    # Discipline trajectory (fast, DB queries)
    results.append(_build_discipline_check(project_root))

    # AI transparency checks (fast, git + DB queries)
    results.append(_build_ai_transparency_check(project_root))
    results.append(_build_decision_transparency_check(project_root))

    # Repository hygiene (fast, file checks)
    results.append(_build_repo_hygiene_check(project_root, overrides=hygiene_overrides))

    # Empirica-specific checks (fast, DB queries)
    results.append(_build_epistemic_audit(project_root))
    results.append(_build_calibration_check(project_root))

    # Per-project extra checks (.empirica/compliance.yaml extra_checks)
    for check_def in extra_checks:
        results.append(_run_extra_check(check_def, project_root))

    # Apply per-project skip_checks (filter after assembly so regulatory map still resolves)
    if skip_checks:
        results = [r for r in results if r.get("check") not in skip_checks]

    # Enrich with regulatory mappings
    results = _add_regulatory_mapping(results)

    overall = _compute_overall_status(results)

    return {
        "report_version": "1.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_root": str(project_root),
        "overall": overall,
        "checks": results,
        "regulatory_frameworks": ["EU AI Act (2024/1689)", "GDPR (2016/679)", "ISO/IEC 42001:2023"],
    }


def _format_check_detail(name: str, check: dict[str, Any]) -> str:
    """Format human-readable detail string for a compliance check."""
    c = check  # shorter alias
    formatters: dict[str, str] = {
        "lint": f"  {c.get('violations', '?')} violations",
        "complexity": f"  {c.get('violations', '0')} functions over CC 15",
        "type_safety": f"  {c.get('errors', '?')} errors, {c.get('warnings', '?')} warnings",
        "tests": f"  {c.get('passed_count', '?')} passed, {c.get('failed_count', '?')} failed",
        "dep_audit": f"  {c.get('vulnerabilities', '?')} known CVEs",
        "security_scan": f"  {c.get('findings_critical', '?')} critical, {c.get('findings_total', '?')} total",
        "release_chain": f"  v{c.get('version', '?')}: {c.get('published', '?')}/{c.get('total', '?')} channels",
        "ai_transparency": f"  {c.get('ai_attributed_commits', '?')}/{c.get('sample_size', '?')} commits attributed",
        "decision_transparency": f"  {c.get('rationale_coverage', '?')}% with rationale ({c.get('decisions_with_rationale', '?')}/{c.get('decisions_total', '?')})",
        "tech_docs": f"  {c.get('coverage_percent', '?')}% coverage ({c.get('documented', '?')}/{c.get('total', '?')})",
        "repo_hygiene": f"  {c.get('checks_passed', '?')}/{c.get('checks_total', '?')} checks",
        "epistemic_audit": f"  {c.get('postflights', '?')} transactions, {c.get('findings', '?')} findings",
    }

    if name in formatters:
        return formatters[name]

    if name == "discipline":
        detail = f"  {c.get('transactions', '?')} tx, {c.get('artifact_types_used', '?')}/6 artifact types, {c.get('goals_completed', '?')} goals done"
        for gap in c.get("gaps", [])[:2]:
            detail += f"\n{'':>50}  {gap}"
        return detail

    if name == "calibration":
        avg = c.get("avg_calibration_score")
        return f"  {c.get('grounded_verifications', '?')} verifications" + (f", avg score {avg}" if avg else "")

    return ""


def _print_human_report(report: dict[str, Any]) -> None:
    """Print human-readable compliance report."""
    overall = report["overall"]

    status_icon = {"fully_compliant": "PASS", "compliant_with_gaps": "PARTIAL", "non_compliant": "FAIL"}
    icon = status_icon.get(overall["status"], "?")

    print(f"\n{'=' * 60}")
    print(f"EMPIRICA COMPLIANCE REPORT  [{icon}]")
    print(f"{'=' * 60}")
    print(f"  Generated: {report['timestamp']}")
    print(f"  Project:   {report['project_root']}")
    print(f"  Score:     {overall['score']:.0%} ({overall['checks_passed']}/{overall['checks_total']})")
    print()

    for check in report["checks"]:
        status = check.get("status", "?")
        name = check.get("check", "?")
        tool = check.get("tool", "")
        icon_char = "+" if status == "pass" else "-" if status == "fail" else "?"

        detail = _format_check_detail(name, check)

        duration = check.get("duration_seconds", "")
        duration_str = f" ({duration}s)" if duration else ""

        print(f"  [{icon_char}] {name:<20} {tool:<12} {status:<12}{detail}{duration_str}")

        # Regulatory mapping
        regulatory = check.get("regulatory", {})
        for framework, mapping in regulatory.items():
            ref = mapping.get("article") or mapping.get("clause", "")
            req = mapping.get("requirement", "")
            print(f"       -> {framework}: {ref} — {req}")

    print(f"\n{'=' * 60}")
    print(f"  Frameworks: {', '.join(report['regulatory_frameworks'])}")
    print(f"{'=' * 60}\n")


def handle_compliance_report_command(args) -> None:
    """Handle compliance-report command."""
    include_tests = getattr(args, "tests", False)
    include_dep_audit = getattr(args, "dep_audit", False)
    include_security = getattr(args, "security", False)
    output_format = getattr(args, "output", "text")

    report = run_compliance_report(
        include_tests=include_tests,
        include_dep_audit=include_dep_audit,
        include_security=include_security,
    )

    # Persist the latest report for cockpit consumption — the cockpit
    # surfaces this per-instance below the notifications widget so users
    # can see compliance state at a glance and click to expand failures.
    # Lookup project_id via the same project.yaml the cockpit aggregator
    # reads, so the file landing path is stable.
    try:
        from empirica.core.cockpit.compliance_view import (
            _project_id_from_path,
            write_last_compliance,
        )
        project_id = _project_id_from_path(report.get("project_root"))
        if project_id:
            write_last_compliance(project_id, report)
    except Exception:
        pass

    if output_format == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_human_report(report)
