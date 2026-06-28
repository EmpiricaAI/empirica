"""Governance-layer integrity — "controls on the controls" (T8, goal fca113ac).

``_build_governance_integrity_check`` audits the audit layer itself: every
assembled compliance check must be regulatory-mapped (an unmapped check is a
silent compliance gap — the null/empty-masking class), and REGULATORY_MAP must
be well-formed. This is the oversight-of-the-oversight control the EU AI Act QMS
article (Art. 17) expects.
"""

from __future__ import annotations

import empirica.cli.command_handlers.compliance_report_commands as crc
from empirica.cli.command_handlers.compliance_report_commands import (
    REGULATORY_MAP,
    _build_governance_integrity_check,
)


def test_passes_when_all_checks_mapped_and_map_wellformed():
    prior = [{"check": "lint", "status": "pass"}, {"check": "tests", "status": "pass"}]
    r = _build_governance_integrity_check(prior)
    assert r["check"] == "governance_integrity"
    assert r["status"] == "pass" and r["passed"] is True
    assert r["unmapped_checks"] == [] and r["malformed_entries"] == []
    assert r["checks_audited"] == 2


def test_detects_unmapped_check():
    # The core value: a check that runs but has no regulatory mapping is a silent
    # audit gap — the check must surface it loudly.
    prior = [{"check": "lint"}, {"check": "totally_new_check_no_map"}]
    r = _build_governance_integrity_check(prior)
    assert r["status"] == "fail" and r["passed"] is False
    assert "totally_new_check_no_map" in r["unmapped_checks"]


def test_governance_integrity_is_self_mapped():
    # It must carry its OWN regulatory entry — else it flags itself once appended
    # to results and run through _add_regulatory_mapping.
    assert "governance_integrity" in REGULATORY_MAP
    fw = REGULATORY_MAP["governance_integrity"]["frameworks"]
    assert fw and all(("article" in b or "clause" in b) and b.get("requirement") for b in fw.values())


def test_real_regulatory_map_is_wellformed():
    # Live regression guard: the actual shipped REGULATORY_MAP must be well-formed
    # — every framework carries an article/clause locator AND a requirement.
    r = _build_governance_integrity_check([])
    assert r["malformed_entries"] == [], f"malformed REGULATORY_MAP entries: {r['malformed_entries']}"
    assert r["status"] == "pass"
    assert r["map_entries"] == len(REGULATORY_MAP)


def test_detects_malformed_entry(monkeypatch):
    bad = dict(crc.REGULATORY_MAP)
    bad["_bad_missing_req"] = {"check": "x", "frameworks": {"eu_ai_act": {"article": "Art. 9"}}}  # no requirement
    bad["_bad_no_locator"] = {"check": "y", "frameworks": {"iso_42001": {"requirement": "something"}}}  # no clause
    bad["_bad_no_frameworks"] = {"check": "z", "frameworks": {}}
    monkeypatch.setattr(crc, "REGULATORY_MAP", bad)

    r = crc._build_governance_integrity_check([])
    assert r["status"] == "fail"
    flagged = "\n".join(r["malformed_entries"])
    assert "_bad_missing_req.eu_ai_act: missing requirement" in flagged
    assert "_bad_no_locator.iso_42001: missing article/clause" in flagged
    assert "_bad_no_frameworks: no frameworks" in flagged
