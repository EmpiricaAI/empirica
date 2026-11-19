"""The Sentinel's orchestration and profile verbs say what actually happened (4938f99e, re-verified at HEAD 2026-11-18).

sentinel-orchestrate reported ok with 0 agents when no agent could run, and exited 0 on a failure; an unknown profile name
loaded 'general' silently; a named profile ignored its shipped YAML; audit settings were reported as if enforced.
The Sentinel is a discipline tool (David, 2026-11-11): the fix is honest reporting, not new enforcement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from empirica.cli.command_handlers import sentinel_commands as sc
from empirica.core.sentinel import orchestrator as orch

_PROFILES = Path(orch.__file__).resolve().parent / "profiles"


@pytest.fixture
def sentinel(monkeypatch):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:9")
    s = orch.Sentinel(session_id="s-test")
    persona = SimpleNamespace(persona_id="security", to_dict=lambda: {"persona_id": "security"})
    monkeypatch.setattr(s, "select_personas", lambda task, max_personas=3: [persona])
    return s


def test_orchestrating_with_no_agent_able_to_run_is_a_failure(sentinel):
    result = sentinel.auto_orchestrate("audit the login flow")
    assert result.ok is False
    assert "no agent" in (result.error or "").lower()


def test_every_spawn_failing_is_a_failure_that_names_the_cause(sentinel):
    def boom(**kw):
        raise RuntimeError("spawn exploded")

    sentinel.register_spawn_function(boom)
    result = sentinel.orchestrate("t", execute_agents=True)
    assert result.ok is False and "spawn exploded" in (result.error or "")


def test_selecting_personas_without_running_agents_still_succeeds(sentinel):
    assert sentinel.orchestrate("t", execute_agents=False).ok is True


def test_the_orchestrate_verb_exits_1_and_prints_the_error(monkeypatch, capsys):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:9")
    failed = orch.OrchestrationResult(ok=False, task="t", personas_selected=[], agents_spawned=[], error="boom")
    monkeypatch.setattr(orch.Sentinel, "auto_orchestrate", lambda self, **kw: failed)
    for fmt in ("human", "json"):
        args = argparse.Namespace(session_id="s", task="t", max_agents=1, profile=None, scope_breadth=0.5,
                                  scope_duration=0.5, merge="union", dry_run=False, output=fmt)  # fmt: skip
        assert sc.handle_sentinel_orchestrate_command(args) == 1, fmt
        assert "boom" in capsys.readouterr().out, fmt


def test_an_unknown_profile_name_is_refused(sentinel):
    with pytest.raises(ValueError, match="nosuch"):
        sentinel.load_domain_profile("nosuch")


@pytest.mark.parametrize("name", sorted(p.stem for p in _PROFILES.glob("*.yaml")))
def test_a_named_profile_is_its_shipped_yaml(sentinel, name):
    shipped = yaml.safe_load((_PROFILES / f"{name}.yaml").read_text())
    loaded = sentinel.load_domain_profile(name)
    assert [g.gate_id for g in loaded.gates] == [g["gate_id"] for g in shipped.get("gates", [])]


def test_an_unknowns_count_gate_can_fire(sentinel):
    sentinel.load_domain_profile("healthcare")
    check = sentinel.check_compliance(vectors={"uncertainty": 0.1, "know": 0.9}, findings=[], unknowns=list("abcd"))
    assert "too_many_unknowns" in check["triggered_gates"]  # unknowns_count > 3 (healthcare.yaml)
    calm = sentinel.check_compliance(vectors={"uncertainty": 0.1, "know": 0.9}, findings=[], unknowns=["a"])
    assert "too_many_unknowns" not in calm["triggered_gates"]


def test_load_profile_does_not_claim_an_audit_that_nothing_writes(monkeypatch, capsys):
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:9")
    args = argparse.Namespace(session_id="s", profile="healthcare", file=None, output="json")
    sc.handle_sentinel_load_profile_command(args)
    out = json.loads(capsys.readouterr().out)
    assert "audit_enabled" not in out
    assert out["audit_configured"] is True and out["enforced"] is False


def test_a_gate_on_an_input_nobody_supplied_is_reported_not_evaluated(sentinel):
    """1.17.9 sweep leftover (c712252a): `amount > 10000` read a missing amount as 0.5, and `requires_calculation` a
    flag nothing passes; such gates now say they were not evaluated instead of looking checked."""
    sentinel.load_domain_profile("finance")
    names = {g.gate_id: g.condition for g in sentinel.domain_profile.gates}
    out = sentinel.check_compliance(vectors={"uncertainty": 0.1, "know": 0.9}, findings=[], unknowns=[])
    unevaluated = {names[g] for g in out["not_evaluated"]}
    assert {"amount > 10000", "requires_calculation", "anomaly_score > 0.7"} <= unevaluated
    assert not any(c.startswith(("uncertainty", "know")) for c in unevaluated)  # epistemic vectors keep their default
    supplied = sentinel.check_compliance(
        vectors={"uncertainty": 0.1, "know": 0.9, "amount": 50000},
        findings=[],
        unknowns=[],
        flags={"requires_calculation": False},
    )
    assert "amount > 10000" not in {names[g] for g in supplied["not_evaluated"]}
    assert "requires_calculation" not in {names[g] for g in supplied["not_evaluated"]}
    assert "amount > 10000" in {names[g] for g in supplied["triggered_gates"]}


def test_status_lists_the_shipped_profiles(monkeypatch, capsys):
    import argparse as _a

    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:9")
    sc.handle_sentinel_status_command(_a.Namespace(session_id="s", output="json"))
    listed = set(json.loads(capsys.readouterr().out)["available_profiles"])
    assert {p.stem for p in _PROFILES.glob("*.yaml")} <= listed


@pytest.mark.parametrize("name", sorted(p.stem for p in _PROFILES.glob("*.yaml")))
def test_the_built_in_fallback_profiles_agree_with_the_shipped_files(sentinel, name):
    """1.17.9 sweep leftover: general's confidence_to_proceed was 0.75 in DEFAULT_PROFILES and 0.7 in general.yaml."""
    shipped = sentinel.load_domain_profile(name)
    fallback = orch.Sentinel.DEFAULT_PROFILES[name]
    assert (fallback.uncertainty_trigger, fallback.confidence_to_proceed) == (
        shipped.uncertainty_trigger,
        shipped.confidence_to_proceed,
    )
