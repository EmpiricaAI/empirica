"""Findings from the 1.17.18 pre-publish sweep, register and provision area (goal c42e1294)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from empirica.cli.command_handlers import projects_commands as pc
from empirica.cli.command_handlers import provision_practice_commands as ppc


def _root(tmp_path, project_yaml="ai_id: x\n", module_yaml=None):
    (tmp_path / ".empirica").mkdir(exist_ok=True)
    (tmp_path / ".empirica" / "project.yaml").write_text(project_yaml)
    if module_yaml is not None:
        (tmp_path / "module.yaml").write_text(module_yaml)
    return tmp_path


MODULE = "empirica_module:\n  name: m\n  seat_name: m\n  version: '1'\n"


def test_a_module_yaml_without_domains_declares_nothing(tmp_path):
    """The manifest model defaults domains to [], so every such practice sent [] and wiped Cortex's stored list."""
    assert pc._declared_provides_domains(_root(tmp_path, module_yaml=MODULE)) is None
    assert pc._declared_provides_domains(_root(tmp_path, module_yaml=MODULE + "  provides:\n    skills: {}\n")) is None


def test_an_explicit_empty_module_list_is_a_declaration(tmp_path):
    assert pc._declared_provides_domains(_root(tmp_path, module_yaml=MODULE + "  provides:\n    domains: []\n")) == []


def test_a_scalar_declaration_is_one_domain(tmp_path):
    assert pc._declared_provides_domains(_root(tmp_path, "provides_domains: client-documents\n")) == [
        "client-documents"
    ]


@pytest.mark.parametrize("status", [200, 409])
def test_the_bulk_path_passes_on_cortexs_answer(tmp_path, monkeypatch, status):
    root = _root(tmp_path, "provides_domains: [client-documents]\n")
    sent = {}

    def post(url, path, payload, key, timeout):
        sent.update(payload)
        return status, {"project_id": "pid", "provides_domains_stored": False, "provides_domains_error": "bad"}

    monkeypatch.setattr(pc, "_post_project", post)
    monkeypatch.setattr(pc, "_link_user_to_project", lambda *a, **k: {"linked": True, "status": 200}, raising=False)
    out = pc._register_one_project({"name": "x", "path": str(root)}, "https://c", "k", 5.0)
    assert sent["provides_domains"] == ["client-documents"]
    assert out["provides_domains_stored"] is False and out["provides_domains_error"] == "bad"


def test_the_single_path_passes_it_on_when_already_registered():
    out = pc._interpret_cortex_register_response(409, {"project_id": "pid", "provides_domains_stored": True}, "pid")
    assert out["outcome"] == "already_registered" and out["provides_domains_stored"] is True


def test_provisioning_uses_the_stored_type_not_the_flags(tmp_path):
    """Re-provisioning with --type content --domain marketing did not change the stored type, yet wrote outreach's roles."""
    p = tmp_path / "project.yaml"
    p.write_text("ai_id: x\ntype: service\n")
    step = ppc._provides_step(SimpleNamespace(type="content", domain="marketing"), p, dry_run=False)
    assert step["changed"] is False and "provides_domains" not in yaml.safe_load(p.read_text())
