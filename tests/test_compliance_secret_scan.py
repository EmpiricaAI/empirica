"""Tests for the trufflehog secret-scan compliance check parser."""

from __future__ import annotations

import json

from empirica.cli.command_handlers.compliance_report_commands import (
    _parse_trufflehog_result,
)


def _raw(stdout: str = "", error: str | None = None,
         duration: float = 0.5) -> dict:
    out: dict = {"duration_seconds": duration, "stdout": stdout, "stderr": ""}
    if error:
        out["error"] = error
    return out


def test_unavailable_when_tool_missing():
    """No trufflehog binary on PATH → parser surfaces 'unavailable'
    instead of crashing the compliance report."""
    raw = _raw(error="tool not installed")
    result = _parse_trufflehog_result(raw)
    assert result["check"] == "secret_scan"
    assert result["status"] == "unavailable"
    assert result.get("findings") is None


def test_clean_run_passes():
    """Trufflehog ran fine, found nothing — passed=True, all counts zero."""
    raw = _raw(stdout="")
    result = _parse_trufflehog_result(raw)
    assert result["passed"] is True
    assert result["findings_total"] == 0
    assert result["findings_verified"] == 0
    assert result["findings_unverified"] == 0
    assert result["status"] == "pass"


def test_unverified_findings_warn_but_pass():
    """Pattern-only matches (Verified=false) are advisory — they don't
    fail the check because trufflehog regex matches have a real FP rate
    when the verifier can't reach the issuing service."""
    findings = [
        {"DetectorName": "Generic", "Verified": False, "Raw": "abc"},
        {"DetectorName": "AWS", "Verified": False, "Raw": "AKIA..."},
    ]
    stdout = "\n".join(json.dumps(f) for f in findings)
    result = _parse_trufflehog_result(_raw(stdout=stdout))
    assert result["passed"] is True
    assert result["findings_verified"] == 0
    assert result["findings_unverified"] == 2
    assert result["findings_total"] == 2


def test_verified_finding_fails():
    """A single Verified=true finding hard-fails the check — the verifier
    confirmed the credential is active. This is the case that matters."""
    findings = [
        {"DetectorName": "Anthropic", "Verified": True, "Raw": "sk-ant-..."},
        {"DetectorName": "Generic", "Verified": False, "Raw": "..."},
    ]
    stdout = "\n".join(json.dumps(f) for f in findings)
    result = _parse_trufflehog_result(_raw(stdout=stdout))
    assert result["passed"] is False
    assert result["findings_verified"] == 1
    assert result["findings_unverified"] == 1
    assert result["findings_total"] == 2
    assert result["verified_detectors"] == {"Anthropic": 1}


def test_verified_breakdown_groups_by_detector():
    """Multiple verified findings of the same detector aggregate; mixed
    detectors produce a per-detector tally for human-readable summaries."""
    findings = [
        {"DetectorName": "OpenAI", "Verified": True},
        {"DetectorName": "OpenAI", "Verified": True},
        {"DetectorName": "AWS", "Verified": True},
    ]
    stdout = "\n".join(json.dumps(f) for f in findings)
    result = _parse_trufflehog_result(_raw(stdout=stdout))
    assert result["findings_verified"] == 3
    assert result["verified_detectors"] == {"OpenAI": 2, "AWS": 1}


def test_malformed_lines_are_skipped():
    """Trufflehog emits one JSON object per line; a malformed line
    must not abort parsing (worst case = miss a finding, not crash)."""
    findings_line = json.dumps({"DetectorName": "AWS", "Verified": True})
    stdout = "\n".join([findings_line, "not-json-at-all", findings_line])
    result = _parse_trufflehog_result(_raw(stdout=stdout))
    assert result["findings_verified"] == 2
    assert result["findings_total"] == 2


def test_lowercase_keys_are_supported():
    """Some trufflehog output formats lowercase the field names; the
    parser tolerates both (DetectorName/detector, Verified/verified)."""
    findings = [
        {"detector": "anthropic", "verified": True},
    ]
    stdout = "\n".join(json.dumps(f) for f in findings)
    result = _parse_trufflehog_result(_raw(stdout=stdout))
    assert result["findings_verified"] == 1
    assert result["verified_detectors"] == {"anthropic": 1}
