"""R5/R6 (sweep, goal b78396ad): a shipped domain must be satisfiable, and one bad domain file must not break the registry.

R5: cybersec required checks (semgrep_*, trivy_deps, gitleaks, sbom_audit, ...) that no runner registers, so every cybersec
medium/high/critical compliance run failed with "not registered" and could never complete. The test below pins the property for
every shipped domain, so the next domain file cannot reintroduce it.

R6: DomainRegistry parsed a file outside its try/except, so `required_checks: null` raised out of the constructor, and a scalar
`required_checks: tests` became ('t','e','s','t','s').
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from empirica.config.domain_registry import CRITICALITY_ORDER, DomainKey, DomainRegistry
from empirica.config.service_registry import ServiceRegistry

BUILTIN_DIR = Path(__file__).resolve().parents[2] / "empirica" / "config" / "domains"


@pytest.fixture
def builtins_loaded():
    ServiceRegistry._registered.clear()
    ServiceRegistry.clear_cache()
    ServiceRegistry.load_builtins()
    yield set(ServiceRegistry.list_all())
    ServiceRegistry._registered.clear()
    ServiceRegistry.clear_cache()


def test_the_shipped_domains_are_found():
    names = {p.stem for p in BUILTIN_DIR.glob("*.yaml")}
    assert {"cybersec", "default"} <= names, "positive control: the glob must see the shipped files"


def test_every_shipped_domain_requires_only_registered_checks(builtins_loaded, tmp_path):
    reg = DomainRegistry(user_dir=tmp_path / "empty-user-dir")
    walked = 0
    unsatisfiable = []
    for domain in reg.list_domains():
        for level in reg.list_criticalities(domain):
            walked += 1
            required = reg.resolve(DomainKey("code", domain, level)).required
            unsatisfiable += [f"{domain}/{level}: {c}" for c in required if c not in builtins_loaded]
    assert walked >= len(CRITICALITY_ORDER), f"walked {walked} checklists; the enumerator is empty"
    assert not unsatisfiable, f"required checks no runner registers: {unsatisfiable}"


def test_a_required_check_that_is_not_registered_still_fails_the_run(builtins_loaded, tmp_path, monkeypatch):
    """The loop's behaviour is unchanged: an unregistered REQUIRED check is a failure. Pinned on a synthetic domain."""
    from empirica.core.post_test.compliance_loop import run_compliance_checks

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "domains.yaml").write_text(
        yaml.dump({"domains": {"synthetic": {"criticalities": {"high": {"required_checks": ["no_such_check"]}}}}})
    )
    result = run_compliance_checks(
        session_id="s", transaction_id="t", work_type="code", domain="synthetic", criticality="high",
        project_path=str(tmp_path),
    )  # fmt: skip
    assert result is not None and result.checks_failed == 1
    assert any("not registered" in r["summary"] for r in result.check_results)


def _registry_with(tmp_path, text: str) -> DomainRegistry:
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    (user / "bad.yaml").write_text(text)
    return DomainRegistry(user_dir=user)


def test_null_required_checks_does_not_raise_and_means_no_checks(tmp_path):
    reg = _registry_with(tmp_path, "domain: bad\ncriticalities:\n  high:\n    required_checks:\n")
    assert reg.resolve(DomainKey("code", "bad", "high")).required == ()


def test_a_scalar_required_check_is_one_check_not_its_characters(tmp_path):
    reg = _registry_with(tmp_path, "domain: bad\ncriticalities:\n  high:\n    required_checks: tests\n")
    assert reg.resolve(DomainKey("code", "bad", "high")).required == ("tests",)


def test_a_scalar_applies_to_work_types_is_one_work_type_not_a_substring_test(tmp_path):
    reg = _registry_with(
        tmp_path, "domain: bad\napplies_to_work_types: code\ncriticalities:\n  high:\n    required_checks: [tests]\n"
    )
    assert reg.resolve(DomainKey("code", "bad", "high")).required == ("tests",)
    assert reg.get_domain_entry("bad").applies_to_work_types == ["code"]
    assert reg._try_resolve("cod", "bad", "high") is None, "a substring of the scalar must not match"


@pytest.mark.parametrize(
    "body",
    [
        "domain: bad\ncriticalities:\n  high:\n    required_checks: [tests]\n    max_iterations: lots\n",
        "domain: bad\ncriticalities:\n  high:\n    required_checks: [tests]\n    thresholds: [1, 2]\n",
        "domain: bad\nhints_to_ai:\ncriticalities:\n  high:\n    required_checks: [tests]\n",
    ],
)
def test_other_malformed_values_never_raise_and_fall_back_to_the_defaults(tmp_path, body):
    checklist = _registry_with(tmp_path, body).resolve(DomainKey("code", "bad", "high"))
    assert checklist.required == ("tests",) and checklist.max_iterations == 5 and isinstance(checklist.thresholds, dict)


def test_a_bad_file_is_logged_and_skipped_and_its_neighbour_still_loads(tmp_path, caplog):
    user = tmp_path / "user"
    user.mkdir()
    (user / "a_bad.yaml").write_text("domain: bad\ncriticalities: [not, a, mapping]\n")
    (user / "b_good.yaml").write_text("domain: good\ncriticalities:\n  high:\n    required_checks: [lint]\n")
    with caplog.at_level(logging.WARNING):
        reg = DomainRegistry(user_dir=user)
    assert reg.resolve(DomainKey("code", "good", "high")).required == ("lint",), "one bad file took its neighbour down"
    assert "bad" not in reg.list_domains()
