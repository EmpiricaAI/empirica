"""Tests for empirica.core.security.audit.

The audit module wraps pip-audit and cross-references findings with the CISA
KEV catalog. Tests inject a fake pip-audit payload and a stub KEVFeed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.core.security import audit as audit_module


def _fake_audit_payload() -> dict:
    """Mimics pip-audit --format json output."""
    return {
        "dependencies": [
            {
                "name": "vulnpkg",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "GHSA-aaaa-bbbb-cccc",
                        "aliases": ["CVE-2025-12345"],
                        "fix_versions": ["1.0.1"],
                        "description": "RCE in vulnpkg.",
                        "severity": "critical",
                    },
                ],
            },
            {
                "name": "minorpkg",
                "version": "0.5.0",
                "vulns": [
                    {
                        "id": "GHSA-zzzz-yyyy-xxxx",
                        "aliases": ["CVE-2024-44444"],
                        "fix_versions": ["0.5.1"],
                        "description": "Minor leak.",
                        "severity": "low",
                    },
                ],
            },
            {
                "name": "cleanpkg",
                "version": "2.0.0",
                "vulns": [],
            },
        ],
    }


class _StubKEVFeed:
    """Test double — never hits the network, exposes a fixed catalog."""

    def __init__(self, kev_cves: set[str]) -> None:
        self._index = {
            cve: {
                "cveID": cve,
                "dateAdded": "2026-04-15",
                "dueDate": "2026-05-06",
                "vendorProject": "TestVendor",
                "product": "TestProduct",
                "vulnerabilityName": f"{cve} test",
                "knownRansomwareCampaignUse": "Known" if cve.startswith("CVE-2025") else "Unknown",
                "requiredAction": "Patch",
            }
            for cve in kev_cves
        }

    def refresh(self, force: bool = False) -> dict:
        return {"vulnerabilities": list(self._index.values())}

    def lookup(self, cve_id: str):
        return self._index.get(cve_id)

    def lookup_many(self, cve_ids):
        return {c: self._index[c] for c in cve_ids if c in self._index}

    def catalog_metadata(self) -> dict:
        return {
            "catalog_version": "test",
            "date_released": "2026-04-24",
            "total_entries": len(self._index),
            "cache_path": "<test>",
            "cache_age_hours": 0.0,
        }


@pytest.fixture
def fake_pip_audit(monkeypatch):
    """Patch _run_pip_audit to return a fixed payload."""
    payload = _fake_audit_payload()
    meta = {"tool": "pip-audit", "status": "ok", "dependencies_scanned": 3}
    monkeypatch.setattr(audit_module, "_run_pip_audit", lambda _: (payload, meta))


# Default empirica_managed for tests that exercise the empirica scope path.
_TEST_EMPIRICA_MANAGED: set[str] = {"vulnpkg"}  # vulnpkg is "empirica-managed"; minorpkg/cleanpkg are "user"


def _audit(kev, *, managed=_TEST_EMPIRICA_MANAGED):
    return audit_module.run_security_audit(
        Path("."),
        kev_feed=kev,
        empirica_managed=managed,
    )


def test_audit_returns_structured_report(fake_pip_audit):
    kev = _StubKEVFeed({"CVE-2025-12345"})
    report = _audit(kev)

    assert report["check"] == "security_audit"
    assert "findings" in report
    assert "summary" in report
    assert "frameworks" in report


def test_kev_match_promotes_to_now(fake_pip_audit):
    kev = _StubKEVFeed({"CVE-2025-12345"})
    report = _audit(kev)
    findings_by_pkg = {f["package"]: f for f in report["findings"]}

    vp = findings_by_pkg["vulnpkg"]
    assert vp["kev"] is True
    assert vp["rotate_priority"] == "now"
    assert vp["kev_entry"]["cve_id"] == "CVE-2025-12345"
    assert vp["kev_entry"]["ransomware_campaign_use"] == "Known"


def test_scope_classification(fake_pip_audit):
    """vulnpkg is in empirica_managed → scope=empirica; minorpkg is not → scope=user."""
    kev = _StubKEVFeed(set())
    report = _audit(kev)
    findings_by_pkg = {f["package"]: f for f in report["findings"]}
    assert findings_by_pkg["vulnpkg"]["scope"] == "empirica"
    assert findings_by_pkg["minorpkg"]["scope"] == "user"


def test_no_kev_match_falls_back_to_severity(fake_pip_audit):
    """A finding not in KEV with severity=critical falls to 'month'."""
    kev = _StubKEVFeed(set())  # empty KEV
    report = _audit(kev)
    findings_by_pkg = {f["package"]: f for f in report["findings"]}
    assert findings_by_pkg["vulnpkg"]["rotate_priority"] == "month"


def test_severity_low_classified_as_safe(fake_pip_audit):
    kev = _StubKEVFeed(set())
    report = _audit(kev)
    findings_by_pkg = {f["package"]: f for f in report["findings"]}
    assert findings_by_pkg["minorpkg"]["rotate_priority"] == "safe"


def test_summary_split_by_scope(fake_pip_audit):
    kev = _StubKEVFeed({"CVE-2025-12345"})
    report = _audit(kev)
    summary = report["summary"]
    assert summary["total"] == 2  # cleanpkg has no vulns
    # vulnpkg (empirica) is in KEV → empirica.now = 1
    assert summary["empirica"]["now"] == 1
    assert summary["empirica"]["total"] == 1
    # minorpkg (user) is severity=low → user.safe = 1
    assert summary["user"]["safe"] == 1
    assert summary["user"]["total"] == 1


def test_passed_false_when_empirica_kev_match(fake_pip_audit):
    """vulnpkg in empirica scope + KEV match → passed=False."""
    kev = _StubKEVFeed({"CVE-2025-12345"})
    report = _audit(kev)
    assert report["passed"] is False


def test_user_kev_does_not_gate_but_empirica_cve_does(fake_pip_audit):
    """User-scoped KEV never contributes to the empirica verdict; but a non-KEV
    empirica CVE now DOES fail the strict gate (was: only empirica-KEV failed)."""
    kev = _StubKEVFeed({"CVE-2024-44444"})  # only minorpkg's CVE (user scope)
    report = _audit(kev, managed={"vulnpkg"})  # minorpkg is user
    findings_by_pkg = {f["package"]: f for f in report["findings"]}
    assert findings_by_pkg["minorpkg"]["rotate_priority"] == "now"
    assert findings_by_pkg["minorpkg"]["scope"] == "user"
    assert report["summary"]["empirica"]["now"] == 0  # user KEV doesn't add here
    assert report["summary"]["user"]["now"] == 1
    # STRICT: vulnpkg (empirica, critical CVE, non-KEV, un-waived) blocks the gate.
    assert report["passed"] is False


def test_strict_fails_on_empirica_cve_without_kev(fake_pip_audit):
    """STRICT gate: an un-waived empirica-scoped CVE fails even with no KEV match
    (was: passed unless there was an empirica-KEV match)."""
    kev = _StubKEVFeed(set())
    report = _audit(kev)  # default managed includes vulnpkg (critical CVE)
    assert report["passed"] is False
    assert report["summary"]["empirica"]["month"] == 1


def test_findings_sorted_empirica_first(fake_pip_audit):
    """Empirica-scope findings come before user-scope, then by priority within scope."""
    kev = _StubKEVFeed(set())
    report = _audit(kev, managed={"vulnpkg"})
    scopes = [f["scope"] for f in report["findings"]]
    # empirica-scoped vulnpkg should appear before user-scoped minorpkg
    assert scopes == sorted(scopes, key=lambda s: 0 if s == "empirica" else 1)


def test_pip_audit_not_installed(monkeypatch):
    monkeypatch.setattr(audit_module.shutil, "which", lambda _: None)
    kev = _StubKEVFeed(set())
    report = _audit(kev)
    assert report["scanned"]["status"] == "not_installed"
    assert report["findings"] == []
    assert report["passed"] is True


def test_kev_unavailable_marks_findings_without_kev(fake_pip_audit):
    """If KEV refresh fails completely, findings should still report (no KEV match)."""

    class _FailingKEV:
        def refresh(self, force=False):
            raise RuntimeError("kev unavailable")

        def lookup(self, cve_id):
            return None

        def catalog_metadata(self):
            return {}

    report = _audit(_FailingKEV())
    assert "error" in report["kev_metadata"]
    # findings still emitted, none marked KEV
    for f in report["findings"]:
        assert f["kev"] is False


def test_frameworks_present(fake_pip_audit):
    kev = _StubKEVFeed(set())
    report = _audit(kev)
    assert "eu_ai_act" in report["frameworks"]
    assert "iso_42001" in report["frameworks"]
    assert "gdpr" in report["frameworks"]


def test_cve_extraction_from_aliases(fake_pip_audit):
    kev = _StubKEVFeed(set())
    report = _audit(kev)
    findings_by_pkg = {f["package"]: f for f in report["findings"]}
    # vulnpkg vuln id is GHSA, alias is CVE — cve_ids should pull only the CVE
    assert findings_by_pkg["vulnpkg"]["cve_ids"] == ["CVE-2025-12345"]


def test_scope_metadata_in_report(fake_pip_audit):
    """Report includes scope_metadata explaining the empirica-managed surface."""
    kev = _StubKEVFeed(set())
    report = _audit(kev, managed={"vulnpkg", "empirica"})
    assert "scope_metadata" in report
    assert report["scope_metadata"]["empirica_managed_count"] == 2
    assert report["scope_metadata"]["empirica_root_present"] is True
