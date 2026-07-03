"""Security audit — runs pip-audit, cross-references with CISA KEV.

Phase 1 scope: Python package vulnerabilities only. pip-audit (already a
dependency for the dep_audit compliance check) provides CVE/GHSA findings;
KEV cross-reference promotes actively-exploited findings to top priority.

Output is a structured report:
    rotate_priority: now (in KEV) | month (CVE only) | monitor | safe

Phases 2+ will add OSV direct (multi-ecosystem), deps.dev OpenSSF Scorecards,
and local credential enumeration. Kept narrow on purpose to avoid scope creep.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .kev_feed import KEVFeed
from .scope import get_empirica_managed_packages
from .waivers import is_waived

logger = logging.getLogger(__name__)

PIP_AUDIT_TIMEOUT_SECONDS = 180


def run_security_audit(
    project_root: Path | None = None,
    *,
    refresh_feeds: bool = False,
    kev_feed: KEVFeed | None = None,
    empirica_managed: set[str] | None = None,
) -> dict[str, Any]:
    """Run pip-audit + KEV cross-reference. Returns structured report.

    Findings are split into two scopes:
        - "empirica": package is part of empirica's managed surface
                       (empirica itself + its transitive Requires).
                       Empirica's responsibility to ship a fix.
        - "user":     package is in the active environment but outside
                       empirica's surface. The user's responsibility.

    The audit `passed` gate considers only empirica-scoped KEV-matched
    findings — user-scoped findings are reported but don't fail the gate.

    Args:
        project_root: project to audit (defaults to CWD)
        refresh_feeds: force re-download of KEV feed even if cache is fresh
        kev_feed: injectable KEVFeed (for testing)
        empirica_managed: injectable set of empirica-managed package names
            (for testing); auto-computed otherwise

    Returns:
        {
          "check": "security_audit",
          "passed": bool,                       # True iff zero empirica-scoped KEV matches
          "scanned": {"tool": "pip-audit", ...},
          "kev_metadata": {...},
          "findings": [
              {
                "package": str, "version": str,
                "vulnerability_id": str, "aliases": [...],
                "cve_ids": [...], "fix_versions": [...],
                "kev": bool, "kev_entry": dict | None,
                "rotate_priority": "now" | "month" | "monitor" | "safe",
              },
              ...
          ],
          "summary": {"now": N, "month": N, "monitor": N, "safe": N, "total": N},
          "frameworks": {...},
          "duration_seconds": float,
        }
    """
    started = time.time()
    project_root = project_root or Path.cwd()
    kev = kev_feed or KEVFeed()

    audit_payload, audit_meta = _run_pip_audit(project_root)
    try:
        kev.refresh(force=refresh_feeds)
        kev_meta = kev.catalog_metadata()
        kev_available = True
    except RuntimeError as exc:
        logger.warning("KEV unavailable: %s", exc)
        kev_meta = {"error": str(exc)}
        kev_available = False

    if empirica_managed is None:
        empirica_managed = get_empirica_managed_packages()

    findings = _classify_findings(
        audit_payload,
        kev if kev_available else None,
        empirica_managed,
    )
    summary = _summarize(findings)

    return {
        "check": "security_audit",
        # STRICT: an empirica-scoped finding blocks the gate if it is KEV (never
        # silently waivable — actively exploited) OR is not in the governed
        # waiver source. Waived non-KEV empirica CVEs pass; user-scoped CVEs are
        # informational (not gated). Shares the waiver list with release.py.
        "passed": not any(f["scope"] == "empirica" and (f.get("kev") or not f.get("waived")) for f in findings),
        "scanned": audit_meta,
        "kev_metadata": kev_meta,
        "scope_metadata": {
            "empirica_managed_count": len(empirica_managed),
            "empirica_root_present": "empirica" in empirica_managed,
        },
        "findings": findings,
        "summary": summary,
        "frameworks": {
            "eu_ai_act": "Art. 15(4) — Cybersecurity (OWASP / supply-chain vulnerability scanning)",
            "iso_42001": "8.4 — AI system development — secure coding practices",
            "gdpr": "Art. 32 — Security of processing — known-vulnerability remediation",
        },
        "duration_seconds": round(time.time() - started, 2),
    }


def _run_pip_audit(project_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run pip-audit with JSON output. Returns (parsed_payload, metadata).

    Falls back to {"dependencies": []} if pip-audit isn't installed or fails.
    """
    pip_audit = shutil.which("pip-audit")
    if not pip_audit:
        return ({"dependencies": []}, {"tool": "pip-audit", "status": "not_installed"})

    try:
        proc = subprocess.run(
            [pip_audit, "--format", "json"],
            capture_output=True,
            text=True,
            timeout=PIP_AUDIT_TIMEOUT_SECONDS,
            cwd=str(project_root),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ({"dependencies": []}, {"tool": "pip-audit", "status": "timeout"})

    if not proc.stdout.strip():
        return ({"dependencies": []}, {"tool": "pip-audit", "status": "no_output"})

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return ({"dependencies": []}, {"tool": "pip-audit", "status": "parse_error"})

    deps = payload.get("dependencies", [])
    return (payload, {"tool": "pip-audit", "status": "ok", "dependencies_scanned": len(deps)})


def _classify_findings(
    audit_payload: dict[str, Any],
    kev: KEVFeed | None,
    empirica_managed: set[str],
) -> list[dict[str, Any]]:
    """Walk pip-audit output, classify each vulnerable dep with rotate_priority + scope."""
    findings: list[dict[str, Any]] = []
    for dep in audit_payload.get("dependencies", []):
        package = dep.get("name") or dep.get("package", "")
        version = dep.get("version", "")
        scope = "empirica" if package.lower().replace("_", "-") in empirica_managed else "user"
        for vuln in dep.get("vulns", []) or []:
            vuln_id = vuln.get("id", "")
            aliases = list(vuln.get("aliases", []) or [])
            # pip-audit aliases include both GHSA-* and CVE-* — extract CVEs explicitly
            all_ids = [vuln_id, *aliases] if vuln_id else aliases
            cve_ids = sorted({i for i in all_ids if i.startswith("CVE-")})

            kev_match: dict[str, Any] | None = None
            if kev is not None:
                for cve_id in cve_ids:
                    entry = kev.lookup(cve_id)
                    if entry:
                        kev_match = entry
                        break

            findings.append(
                {
                    "package": package,
                    "version": version,
                    "scope": scope,
                    "vulnerability_id": vuln_id,
                    "aliases": aliases,
                    "cve_ids": cve_ids,
                    "fix_versions": list(vuln.get("fix_versions", []) or []),
                    "description": (vuln.get("description") or "")[:200],
                    "kev": kev_match is not None,
                    "kev_entry": _summarize_kev_entry(kev_match) if kev_match else None,
                    "rotate_priority": _priority_for(kev_match, vuln),
                    "waived": is_waived([vuln_id, *aliases, *cve_ids]),
                }
            )
    # Sort: empirica scope first, then by priority within each scope
    findings.sort(
        key=lambda f: (
            0 if f["scope"] == "empirica" else 1,
            _priority_rank(f["rotate_priority"]),
        )
    )
    return findings


def _summarize_kev_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Trim a KEV entry to fields relevant for the audit report."""
    return {
        "cve_id": entry.get("cveID"),
        "date_added": entry.get("dateAdded"),
        "due_date": entry.get("dueDate"),
        "vendor_project": entry.get("vendorProject"),
        "product": entry.get("product"),
        "vulnerability_name": entry.get("vulnerabilityName"),
        "ransomware_campaign_use": entry.get("knownRansomwareCampaignUse"),
        "required_action": entry.get("requiredAction"),
    }


def _priority_for(kev_match: dict[str, Any] | None, vuln: dict[str, Any]) -> str:
    """Derive rotate_priority for a single finding."""
    if kev_match is not None:
        return "now"
    severity = (vuln.get("severity") or "").lower()
    if severity in ("critical", "high"):
        return "month"
    if severity in ("medium", "moderate"):
        return "monitor"
    if severity:
        return "safe"
    # No severity reported by pip-audit (common) — default to month so it isn't ignored
    return "month"


_PRIORITY_RANK = {"now": 0, "month": 1, "monitor": 2, "safe": 3}


def _priority_rank(priority: str) -> int:
    return _PRIORITY_RANK.get(priority, 99)


_PRIORITY_BUCKETS = ("now", "month", "monitor", "safe")


def _empty_scope_summary() -> dict[str, int]:
    bucket: dict[str, int] = dict.fromkeys(_PRIORITY_BUCKETS, 0)
    bucket["total"] = 0
    bucket["waived"] = 0
    return bucket


def _summarize(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-scope priority counts plus overall total.

    Shape:
        {
          "total": N,
          "empirica": {"now": A, "month": B, "monitor": C, "safe": D, "total": A+B+C+D},
          "user":     {"now": E, ..., "total": E+F+G+H},
        }
    """
    summary: dict[str, Any] = {
        "total": len(findings),
        "empirica": _empty_scope_summary(),
        "user": _empty_scope_summary(),
    }
    for f in findings:
        scope = f.get("scope", "user")
        priority = f.get("rotate_priority", "safe")
        bucket = summary.get(scope) if scope in ("empirica", "user") else summary["user"]
        if priority in _PRIORITY_BUCKETS:
            bucket[priority] += 1
            bucket["total"] += 1
        if f.get("waived"):
            bucket["waived"] += 1
    return summary
