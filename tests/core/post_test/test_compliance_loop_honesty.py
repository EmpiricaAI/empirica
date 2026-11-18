"""The compliance loop says when it did not answer.

A. POSTFLIGHT runs the loop at the default `always` tier, so every
   goal_completion / release check is deferred with passed=True. The run used to
   report status 'complete' over checks that never ran.
B. Any exception inside the loop was logged and turned into None, which the
   caller reads as "no checklist applies", so its compliance_error path could
   never fire.
"""

from __future__ import annotations

import time

import pytest

from empirica.config.service_registry import CheckDeclaration, CheckResult, ServiceRegistry
from empirica.core.post_test.compliance_loop import run_compliance_checks


@pytest.fixture(autouse=True)
def clean_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ServiceRegistry._registered.clear()
    ServiceRegistry.clear_cache()
    yield
    ServiceRegistry._registered.clear()
    ServiceRegistry.clear_cache()


def _register(check_id: str, tier: str) -> None:
    ServiceRegistry.register(
        CheckDeclaration(
            check_id=check_id,
            tool="t",
            applies_to=(("code", "*"),),
            criterion_description="d",
            runner=lambda ctx: CheckResult(check_id, True, {}, "ok", 1, time.time()),
            tier=tier,
        )
    )


def _run(**kw):
    return run_compliance_checks(
        session_id="s1", transaction_id="t1", work_type="code", domain="default", criticality="low", **kw
    )


def test_a_deferred_goal_completion_check_does_not_report_complete():
    _register("tests", tier="goal_completion")  # default/low requires only `tests`
    result = _run()  # the POSTFLIGHT call: default execution tier
    assert result is not None
    assert result.status != "complete"
    assert result.status == "complete_with_deferred"
    assert not result.is_complete
    assert result.checks_deferred == ["tests"]
    d = result.to_dict()
    assert d["status"] == "complete_with_deferred" and d["checks_deferred"] == ["tests"]
    assert d["checks_failed"] == 0  # deferred is still not a failure


def test_the_same_check_run_at_its_tier_is_complete():
    """Positive control: nothing deferred, nothing failed -> complete, as before."""
    _register("tests", tier="goal_completion")
    result = _run(execution_tier="goal_completion")
    assert result is not None
    assert result.status == "complete" and result.is_complete
    assert result.checks_deferred == []


def test_an_exception_inside_the_loop_surfaces_instead_of_returning_none(monkeypatch):
    from empirica.config import domain_registry

    def boom(self, key):
        raise RuntimeError("domains unreadable")

    monkeypatch.setattr(domain_registry.DomainRegistry, "resolve", boom)
    with pytest.raises(RuntimeError, match="domains unreadable"):
        _run()
