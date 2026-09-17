"""A `pass` that cannot go red must say what it does not cover.

`secret_scan` computes `passed = (verified == 0)`. "Verified" means trufflehog
called the issuing service and got a 200 — it can do that for AWS, GitHub and
Datadog, and it **cannot** for our own `ctx_empirica_*` detector. So every
custom-detector hit is permanently unverified, permanently advisory, permanently
green.

Measured: this check reported **PASS over 177 matches** of a key that was verified
live by hand (HTTP 200). Adding the detector fixed visibility and not the verdict —
0 findings before (blind), 177 after (unverifiable). The count became true while
the pass/fail stayed incapable of going red.

**The fix is scope, not threshold.** Hard-failing on any unverified finding would
fail on noise forever and train people to ignore the check — which is a worse
outcome than the bug. So `passed` is unchanged, and what changes is that the
response can no longer report `pass` without also reporting what the pass excludes.

Same principle as the rest of this family: make the partial-ness checkable from the
response itself, rather than knowable only by someone who already understands the
tool's limits.
"""

from __future__ import annotations

import json

from empirica.cli.command_handlers.compliance_report_commands import (
    _parse_trufflehog_result as _parse_secret_scan_result,
)


def _raw(findings: list[dict]) -> dict:
    return {
        "stdout": "\n".join(json.dumps(f) for f in findings),
        "duration_seconds": 0.1,
    }


def _unverified(n: int, detector: str = "ctx_empirica") -> list[dict]:
    return [{"DetectorName": detector, "Verified": False} for _ in range(n)]


def _verified(n: int, detector: str = "AWS") -> list[dict]:
    return [{"DetectorName": detector, "Verified": True} for _ in range(n)]


def test_the_177_case_reports_pass_but_cannot_report_it_alone():
    """The exact shape that went unchallenged, as an assertion."""
    r = _parse_secret_scan_result(_raw(_unverified(177)))

    assert r["passed"] is True, "unchanged on purpose — hard-failing here would fail on noise forever"
    assert r["undecidable"] == 177, "the count of what the verdict does NOT cover must be present"
    assert "neither clears nor condemns" in r["verdict_scope"]


def test_undecidable_is_never_elided():
    """A key that disappears when zero is a key a consumer cannot rely on.

    Absence and zero must not be the same reading — the defect class this whole
    area keeps producing.
    """
    clean = _parse_secret_scan_result(_raw(_verified(0)))

    assert "undecidable" in clean, "the field must be present even at zero"
    assert clean["undecidable"] == 0


def test_a_clean_scan_does_not_carry_a_scary_scope_string():
    """Positive control.

    Without it, an unconditional caveat would satisfy every assertion above while
    making a genuinely clean scan look compromised — and a warning that fires
    always is a warning nobody reads.
    """
    r = _parse_secret_scan_result(_raw(_verified(0)))

    assert r["verdict_scope"] == "verified detectors only"
    assert "neither clears nor condemns" not in r["verdict_scope"]


def test_a_verified_finding_still_fails():
    """The check must remain capable of going red on what it CAN judge.

    If this broke, the scoping change would have turned a weak check into a
    decorative one.
    """
    r = _parse_secret_scan_result(_raw(_verified(1)))

    assert r["passed"] is False
    assert r["status"] == "fail"


def test_mixed_findings_count_both_sides():
    r = _parse_secret_scan_result(_raw(_verified(2) + _unverified(5)))

    assert r["findings_verified"] == 2
    assert r["findings_unverified"] == 5
    assert r["undecidable"] == 5
    assert r["findings_total"] == 7
    assert r["passed"] is False, "a verified hit fails regardless of the undecidable ones"


def test_the_human_line_says_undecidable_not_unverified():
    """Wording is the mechanism here, not decoration.

    "unverified" reads as a weaker grade of clean — "probably fine". It means the
    check cannot form a verdict at all. That misreading is how a PASS over 177
    live-verified matches went unchallenged.

    Asserts on the RENDERED line, not on source — the string a human reads is the
    artifact, and a source grep would pass on a line that never fires.
    """
    from empirica.cli.command_handlers.compliance_report_commands import _format_check_detail

    parsed = _parse_secret_scan_result(_raw(_unverified(177)))
    line = _format_check_detail("secret_scan", parsed)

    assert "UNDECIDABLE" in line, f"the human line must not call these merely 'unverified': {line!r}"
    assert "cannot be called" in line, "and it must say WHY they are undecidable"
    assert "177" in line


def test_a_clean_scan_renders_without_the_caveat():
    """Positive control for the line above."""
    from empirica.cli.command_handlers.compliance_report_commands import _format_check_detail

    line = _format_check_detail("secret_scan", _parse_secret_scan_result(_raw(_verified(0))))

    assert "UNDECIDABLE" not in line
    assert "0 undecidable" in line
