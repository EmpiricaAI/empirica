"""Strict security-audit gate + shared governed waiver source (goal 15a4034b).

`empirica security-audit` now fails on ANY un-waived empirica-scoped CVE (was:
only CISA-KEV matches), sharing one governed waiver list with the release gate.
KEV matches are never silently waivable. Tests inject a synthetic pip-audit
payload + a fake KEV feed so no network/pip-audit is needed.
"""

from __future__ import annotations

from pathlib import Path

from empirica.core.security import audit, waivers


class _FakeKev:
    """Minimal KEVFeed stand-in. ``kev_hits`` = set of CVE ids that are in KEV."""

    def __init__(self, kev_hits: set[str] | None = None):
        self._hits = kev_hits or set()

    def refresh(self, force: bool = False) -> None:
        pass

    def catalog_metadata(self) -> dict:
        return {"catalog_version": "test", "total_entries": len(self._hits)}

    def lookup(self, cve_id: str):
        return {"cveID": cve_id} if cve_id in self._hits else None


def _payload(package: str, vuln_id: str, cve: str, severity: str = "high") -> dict:
    return {
        "dependencies": [
            {
                "name": package,
                "version": "1.0.0",
                "vulns": [{"id": vuln_id, "aliases": [cve], "severity": severity, "fix_versions": []}],
            }
        ]
    }


def _run(monkeypatch, payload, kev_hits=None, managed=frozenset({"pydantic"})):
    monkeypatch.setattr(audit, "_run_pip_audit", lambda _root: (payload, {"tool": "pip-audit", "status": "ok"}))
    return audit.run_security_audit(Path("."), kev_feed=_FakeKev(kev_hits), empirica_managed=set(managed))


# ── strict gate ───────────────────────────────────────────────────────────────


def test_unwaived_empirica_cve_now_fails(monkeypatch):
    # Non-KEV, empirica-scoped, not waived → STRICT fail (old behavior: passed).
    r = _run(monkeypatch, _payload("pydantic", "PYSEC-1", "CVE-2026-1"))
    assert r["passed"] is False
    assert r["summary"]["empirica"]["month"] == 1


def test_user_scoped_cve_does_not_gate(monkeypatch):
    # A vuln in a non-empirica-managed package is informational, not a blocker.
    r = _run(monkeypatch, _payload("some-user-pkg", "PYSEC-2", "CVE-2026-2"), managed={"pydantic"})
    assert r["passed"] is True
    assert r["summary"]["user"]["total"] == 1


def test_no_cves_passes(monkeypatch):
    r = _run(monkeypatch, {"dependencies": []})
    assert r["passed"] is True


# ── waivers ───────────────────────────────────────────────────────────────────


def test_waived_empirica_cve_passes(monkeypatch):
    monkeypatch.setattr(audit, "is_waived", lambda ids: "CVE-2026-3" in ids)
    r = _run(monkeypatch, _payload("pydantic", "PYSEC-3", "CVE-2026-3"))
    assert r["passed"] is True
    assert r["summary"]["empirica"]["waived"] == 1


def test_kev_empirica_cve_fails_even_if_waived(monkeypatch):
    # KEV = actively exploited → never silently waivable, even with a waiver.
    monkeypatch.setattr(audit, "is_waived", lambda ids: True)
    r = _run(monkeypatch, _payload("pydantic", "PYSEC-4", "CVE-2026-4"), kev_hits={"CVE-2026-4"})
    assert r["passed"] is False
    assert r["summary"]["empirica"]["now"] == 1


# ── shared waiver source ──────────────────────────────────────────────────────


def test_waiver_source_empty_by_default():
    assert waivers.CVE_WAIVERS == []
    assert waivers.waived_ids() == set()
    assert waivers.is_waived(["CVE-2026-9999"]) is False


def test_is_waived_matches_id_or_alias():
    wl = [{"id": "PYSEC-X", "package": "p", "rationale": "r", "retire_when": "w", "aliases": ["CVE-2026-X"]}]
    import unittest.mock

    with unittest.mock.patch.object(waivers, "CVE_WAIVERS", wl):
        assert waivers.is_waived(["CVE-2026-X"]) is True
        assert waivers.is_waived(["PYSEC-X"]) is True
        assert waivers.is_waived(["CVE-2026-OTHER"]) is False
