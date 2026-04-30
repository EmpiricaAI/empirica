"""Tests for per-project compliance.yaml config layer.

The config layer lets projects override compliance-report defaults via
.empirica/compliance.yaml — skip_checks, extra_checks, repo_hygiene
relaxations. Required so compliance-report works on non-empirica project
shapes (servers, libraries, services) without forcing the empirica-CLI
docs metric on every project.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from empirica.cli.command_handlers.compliance_report_commands import (
    _build_repo_hygiene_check,
    _load_compliance_config,
    _run_extra_check,
)


# ── _load_compliance_config ──────────────────────────────────────────────


def test_load_compliance_config_missing_returns_empty(tmp_path):
    assert _load_compliance_config(tmp_path) == {}


def test_load_compliance_config_reads_yaml(tmp_path):
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "compliance.yaml").write_text(yaml.safe_dump({
        "skip_checks": ["tech_docs"],
        "extra_checks": [{"id": "foo", "runner": "scripts/foo.py"}],
    }))
    cfg = _load_compliance_config(tmp_path)
    assert cfg["skip_checks"] == ["tech_docs"]
    assert cfg["extra_checks"][0]["id"] == "foo"


def test_load_compliance_config_malformed_returns_empty(tmp_path):
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "compliance.yaml").write_text("::: not valid yaml :::")
    assert _load_compliance_config(tmp_path) == {}


# ── _build_repo_hygiene_check overrides ─────────────────────────────────


def _bare_project(tmp_path: Path) -> Path:
    """Project with .gitignore + pyproject.toml only (license/changelog missing)."""
    (tmp_path / ".gitignore").write_text("*.pyc\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n')
    return tmp_path


def test_repo_hygiene_default_fails_on_missing_license(tmp_path):
    proj = _bare_project(tmp_path)
    result = _build_repo_hygiene_check(proj)
    assert result["passed"] is False
    assert result["details"]["license"] == "MISSING"
    assert result["details"]["changelog"] == "MISSING"


def test_repo_hygiene_license_required_false_relaxes_check(tmp_path):
    proj = _bare_project(tmp_path)
    result = _build_repo_hygiene_check(proj, overrides={"license_required": False})
    assert result["details"]["license"] == "skipped"
    # license no longer counts toward total → checks_total drops by 1
    assert result["checks_total"] == 5


def test_repo_hygiene_all_optional_passes_with_minimal_project(tmp_path):
    proj = _bare_project(tmp_path)
    result = _build_repo_hygiene_check(proj, overrides={
        "license_required": False,
        "changelog_required": False,
        "release_scripts_required": False,
    })
    # Only gitignore + no_tracked_secrets + version_file remain — all present
    assert result["passed"] is True
    assert result["checks_total"] == 3


# ── _run_extra_check ────────────────────────────────────────────────────


def _make_runner(tmp_path: Path, body: str) -> Path:
    runner = tmp_path / "runner.py"
    runner.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"{body}\n"
    )
    runner.chmod(0o755)
    return runner


def test_run_extra_check_passes_through_runner_json(tmp_path):
    _make_runner(tmp_path, 'print(json.dumps({"passed": True, "score": 0.95, "extra": "metadata"}))')
    result = _run_extra_check(
        {"id": "my_check", "runner": str(tmp_path / "runner.py")},
        tmp_path,
    )
    assert result["check"] == "my_check"
    assert result["passed"] is True
    assert result["score"] == 0.95
    assert result["extra"] == "metadata"
    assert result["status"] == "pass"


def test_run_extra_check_fail_status(tmp_path):
    _make_runner(tmp_path, 'print(json.dumps({"passed": False}))')
    result = _run_extra_check(
        {"id": "my_check", "runner": str(tmp_path / "runner.py")},
        tmp_path,
    )
    assert result["passed"] is False
    assert result["status"] == "fail"


def test_run_extra_check_invalid_json_marks_fail(tmp_path):
    _make_runner(tmp_path, 'print("not json")')
    result = _run_extra_check(
        {"id": "my_check", "runner": str(tmp_path / "runner.py")},
        tmp_path,
    )
    assert result["passed"] is False
    assert "valid JSON" in result["error"]


def test_run_extra_check_missing_runner_returns_unavailable(tmp_path):
    result = _run_extra_check({"id": "my_check"}, tmp_path)
    assert result["status"] == "unavailable"


def test_run_extra_check_attaches_regulatory_mapping(tmp_path):
    _make_runner(tmp_path, 'print(json.dumps({"passed": True}))')
    result = _run_extra_check(
        {
            "id": "my_check",
            "runner": str(tmp_path / "runner.py"),
            "regulatory": {"eu_ai_act": {"article": "Art. 11", "requirement": "X"}},
        },
        tmp_path,
    )
    assert result["regulatory"]["eu_ai_act"]["article"] == "Art. 11"


def test_run_extra_check_attaches_description(tmp_path):
    _make_runner(tmp_path, 'print(json.dumps({"passed": True}))')
    result = _run_extra_check(
        {"id": "my_check", "runner": str(tmp_path / "runner.py"), "description": "Cortex prod /health"},
        tmp_path,
    )
    assert result["description"] == "Cortex prod /health"
