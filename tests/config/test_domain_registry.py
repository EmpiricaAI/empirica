"""
Tests for A1 Domain Registry (SPEC 1 Part 1).

Verifies: YAML loading, precedence resolution, fallback logic,
backwards compatibility (empty registry returns legacy baseline).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from empirica.config.domain_registry import (
    Checklist,
    DomainKey,
    DomainRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path


def _make_domain_yaml(
    domain: str,
    work_types: list[str] | None = None,
    criticalities: dict | None = None,
) -> dict:
    """Build a minimal domain YAML dict."""
    return {
        "domain": domain,
        "version": "1",
        "description": f"Test domain: {domain}",
        "applies_to_work_types": work_types or [],
        "criticalities": criticalities
        or {
            "low": {
                "description": "Low risk",
                "required_checks": ["tests"],
                "thresholds": {"coverage_min": 0.3, "check_pass_ratio": 1.0},
                "max_iterations": 3,
            },
        },
    }


# ---------------------------------------------------------------------------
# Core dataclass tests
# ---------------------------------------------------------------------------


class TestDomainKey:
    def test_frozen(self):
        key = DomainKey(work_type="code", domain="cybersec", criticality="high")
        with pytest.raises(AttributeError):
            key.work_type = "infra"  # type: ignore[misc]

    def test_hashable(self):
        a = DomainKey("code", "cybersec", "high")
        b = DomainKey("code", "cybersec", "high")
        assert a == b
        assert hash(a) == hash(b)
        assert len({a, b}) == 1

    def test_defaults(self):
        key = DomainKey("code")
        assert key.domain == "default"
        assert key.criticality == "medium"


class TestChecklist:
    def test_empty_checklist(self):
        cl = Checklist.empty()
        assert cl.required == ()
        assert cl.optional == ()
        assert cl.max_iterations == 1
        assert cl.hints_to_ai == ()

    def test_has_checks(self):
        cl = Checklist(required=("tests", "lint"), optional=(), thresholds={}, max_iterations=3, hints_to_ai=())
        assert cl.has_checks
        assert not Checklist.empty().has_checks


# ---------------------------------------------------------------------------
# Registry loading tests
# ---------------------------------------------------------------------------


class TestBuiltinDomains:
    """The 4 built-in domains must load from the package."""

    def test_builtin_domains_load(self):
        reg = DomainRegistry()
        domains = reg.list_domains()
        assert "default" in domains
        assert "remote-ops" in domains

    def test_remote_ops_is_empty_checklist(self):
        """remote-ops resolves to an empty checklist — self-assessment stands."""
        reg = DomainRegistry()
        cl = reg.resolve(DomainKey("remote-ops", "remote-ops", "low"))
        assert not cl.has_checks

    def test_code_default_has_baseline_checks(self):
        """code/default resolves to at least tests + lint."""
        reg = DomainRegistry()
        cl = reg.resolve(DomainKey("code", "default", "medium"))
        assert "tests" in cl.required
        assert "lint" in cl.required


# ---------------------------------------------------------------------------
# YAML file loading
# ---------------------------------------------------------------------------


class TestYAMLLoading:
    def test_single_domain_file(self, tmp_path):
        data = _make_domain_yaml(
            "payments",
            criticalities={
                "high": {
                    "description": "PCI-DSS scope",
                    "required_checks": ["tests", "lint", "trivy_deps"],
                    "thresholds": {"coverage_min": 0.7, "check_pass_ratio": 1.0},
                    "max_iterations": 5,
                },
            },
        )
        _write_yaml(tmp_path / "payments.yaml", data)

        reg = DomainRegistry(user_dir=tmp_path)
        cl = reg.resolve(DomainKey("code", "payments", "high"))
        assert "trivy_deps" in cl.required
        assert cl.max_iterations == 5

    def test_project_aggregator_file(self, tmp_path):
        project_dir = tmp_path / "project" / ".empirica"
        project_dir.mkdir(parents=True)
        data = {
            "version": "1",
            "domains": {
                "internal_api": _make_domain_yaml("internal_api"),
            },
        }
        _write_yaml(project_dir / "domains.yaml", data)

        reg = DomainRegistry(project_path=tmp_path / "project")
        assert "internal_api" in reg.list_domains()


# ---------------------------------------------------------------------------
# Precedence tests
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_project_overrides_user(self, tmp_path):
        """Project domain file wins over user-global."""
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"

        # User defines cybersec/low with max_iterations=3
        user_data = _make_domain_yaml(
            "cybersec",
            criticalities={
                "low": {
                    "description": "User low",
                    "required_checks": ["tests"],
                    "thresholds": {"coverage_min": 0.3},
                    "max_iterations": 3,
                },
            },
        )
        _write_yaml(user_dir / "cybersec.yaml", user_data)

        # Project overrides cybersec/low with max_iterations=10
        proj_empirica = project_dir / ".empirica"
        proj_empirica.mkdir(parents=True)
        proj_data = {
            "version": "1",
            "domains": {
                "cybersec": _make_domain_yaml(
                    "cybersec",
                    criticalities={
                        "low": {
                            "description": "Project low",
                            "required_checks": ["tests", "lint"],
                            "thresholds": {"coverage_min": 0.5},
                            "max_iterations": 10,
                        },
                    },
                ),
            },
        }
        _write_yaml(proj_empirica / "domains.yaml", proj_data)

        reg = DomainRegistry(project_path=project_dir, user_dir=user_dir)
        cl = reg.resolve(DomainKey("code", "cybersec", "low"))
        assert cl.max_iterations == 10
        assert "lint" in cl.required

    def test_user_overrides_builtin(self, tmp_path):
        """User-global domain files override built-in defaults."""
        user_dir = tmp_path / "user"
        user_data = _make_domain_yaml(
            "default",
            criticalities={
                "medium": {
                    "description": "Custom default",
                    "required_checks": ["tests", "lint", "semgrep_basic"],
                    "thresholds": {"coverage_min": 0.6},
                    "max_iterations": 7,
                },
            },
        )
        _write_yaml(user_dir / "default.yaml", user_data)

        reg = DomainRegistry(user_dir=user_dir)
        cl = reg.resolve(DomainKey("code", "default", "medium"))
        assert "semgrep_basic" in cl.required
        assert cl.max_iterations == 7


# ---------------------------------------------------------------------------
# Fallback resolution
# ---------------------------------------------------------------------------


class TestFallbackResolution:
    def test_unknown_domain_falls_back_to_default(self):
        """Unregistered domain resolves to default domain baseline."""
        reg = DomainRegistry()
        cl = reg.resolve(DomainKey("code", "nonexistent_domain", "medium"))
        # Should fall back to default domain
        assert cl.required  # not empty

    def test_unknown_criticality_falls_back_to_lower(self, tmp_path):
        """If exact criticality not found, fall to next lower."""
        user_dir = tmp_path / "user"
        data = _make_domain_yaml(
            "custom",
            criticalities={
                "low": {
                    "description": "Only low defined",
                    "required_checks": ["tests"],
                    "thresholds": {"coverage_min": 0.3},
                    "max_iterations": 3,
                },
            },
        )
        _write_yaml(user_dir / "custom.yaml", data)

        reg = DomainRegistry(user_dir=user_dir)
        # Request "high" but only "low" exists → fallback to "low"
        cl = reg.resolve(DomainKey("code", "custom", "high"))
        assert cl.max_iterations == 3

    def test_completely_empty_registry(self, tmp_path):
        """Empty user dir + no project + no builtins → empty checklist."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        reg = DomainRegistry(
            project_path=tmp_path / "no_project",
            user_dir=empty_dir,
            builtin_dir=empty_dir,
        )
        cl = reg.resolve(DomainKey("code", "anything", "high"))
        assert not cl.has_checks


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_yaml_is_skipped(self, tmp_path):
        """Malformed YAML files are skipped with a warning, not crashes."""
        user_dir = tmp_path / "user"
        (user_dir).mkdir(parents=True)
        (user_dir / "bad.yaml").write_text("not: valid: yaml: [")

        # Should not raise
        reg = DomainRegistry(user_dir=user_dir)
        assert isinstance(reg.list_domains(), list)

    def test_missing_required_fields_skipped(self, tmp_path):
        """Domain file without 'domain' key is skipped."""
        user_dir = tmp_path / "user"
        _write_yaml(user_dir / "broken.yaml", {"version": "1"})  # no 'domain' key

        reg = DomainRegistry(user_dir=user_dir)
        assert "broken" not in reg.list_domains()


# ---------------------------------------------------------------------------
# work_type filtering
# ---------------------------------------------------------------------------


class TestWorkTypeFiltering:
    def test_applies_to_work_types_filters(self, tmp_path):
        """Domain with applies_to_work_types only matches those types."""
        user_dir = tmp_path / "user"
        data = _make_domain_yaml("infra_only", work_types=["infra"])
        data["criticalities"]["low"]["required_checks"] = ["terraform_validate"]
        _write_yaml(user_dir / "infra_only.yaml", data)

        reg = DomainRegistry(user_dir=user_dir)
        # "infra" work_type matches
        cl = reg.resolve(DomainKey("infra", "infra_only", "low"))
        assert "terraform_validate" in cl.required

        # "code" work_type does NOT match → falls back to default
        cl = reg.resolve(DomainKey("code", "infra_only", "low"))
        # Should not contain the infra-specific check
        assert "terraform_validate" not in cl.required


# ---------------------------------------------------------------------------
# List/query API
# ---------------------------------------------------------------------------


class TestListAPI:
    def test_list_domains(self):
        reg = DomainRegistry()
        domains = reg.list_domains()
        assert isinstance(domains, list)
        assert len(domains) >= 2  # at least default + remote-ops

    def test_list_criticalities(self, tmp_path):
        user_dir = tmp_path / "user"
        data = _make_domain_yaml(
            "multi",
            criticalities={
                "low": {"description": "L", "required_checks": [], "thresholds": {}, "max_iterations": 1},
                "high": {"description": "H", "required_checks": ["tests"], "thresholds": {}, "max_iterations": 5},
            },
        )
        _write_yaml(user_dir / "multi.yaml", data)

        reg = DomainRegistry(user_dir=user_dir)
        crits = reg.list_criticalities("multi")
        assert "low" in crits
        assert "high" in crits


# ---------------------------------------------------------------------------
# Fail closed: a missing criticality never falls back to a WEAKER checklist
# (deep sweep 2026-10-05; David: fail closed and fix the index)
# ---------------------------------------------------------------------------


def _crit(iterations: int) -> dict:
    return {
        "description": f"iterations {iterations}",
        "required_checks": ["tests"],
        "thresholds": {"coverage_min": 0.3},
        "max_iterations": iterations,
    }


def _custom_registry(tmp_path, levels: dict[str, int]) -> DomainRegistry:
    user_dir = tmp_path / "user"
    _write_yaml(
        user_dir / "custom.yaml",
        _make_domain_yaml("custom", criticalities={name: _crit(n) for name, n in levels.items()}),
    )
    return DomainRegistry(user_dir=user_dir)


class TestFailClosedCriticality:
    def test_a_missing_level_falls_to_the_nearest_stricter_one_not_a_weaker_one(self, tmp_path):
        reg = _custom_registry(tmp_path, {"high": 9, "low": 1})
        # medium is absent: the old walk-down chose low (1); the stricter neighbour is high (9)
        assert reg.resolve(DomainKey("code", "custom", "medium")).max_iterations == 9

    def test_the_exact_level_still_wins(self, tmp_path):
        reg = _custom_registry(tmp_path, {"high": 9, "medium": 5, "low": 1})
        assert reg.resolve(DomainKey("code", "custom", "medium")).max_iterations == 5

    def test_a_weaker_level_is_chosen_only_when_nothing_stricter_exists_and_it_says_so(self, tmp_path, caplog):
        reg = _custom_registry(tmp_path, {"medium": 5, "low": 1})
        with caplog.at_level("WARNING"):
            cl = reg.resolve(DomainKey("code", "custom", "critical"))
        assert cl.max_iterations == 5  # the strictest the domain defines
        assert any("critical" in r.message and "custom" in r.message for r in caplog.records)

    def test_an_unknown_criticality_is_treated_as_the_strictest_not_skipped(self, tmp_path, caplog):
        reg = _custom_registry(tmp_path, {"critical": 20, "low": 1})
        with caplog.at_level("WARNING"):
            cl = reg.resolve(DomainKey("code", "custom", "hihg"))
        # the old code started its walk at index 0 and skipped "critical" entirely
        assert cl.max_iterations == 20
        assert any("hihg" in r.message for r in caplog.records)
