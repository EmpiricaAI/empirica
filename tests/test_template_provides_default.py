"""A practice provisioned from the outreach template declares what it provides (goal b15f79b9).

David, 2026-11-25: client- and prospect-facing write-ups go to the practice that provides client-documents. Templates carry a
default provides list; provisioning writes it into project.yaml when the practice's (type, domain) is exactly a template's
and project.yaml declares nothing yet. Only outreach has a default: no role was ruled for the others.
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from empirica.cli.command_handlers import provision_practice_commands as ppc
from empirica.core import practice_templates as pt


def test_outreach_template_provides_client_documents():
    assert "client-documents" in pt.template_provides("content", "marketing")


def test_no_default_without_an_exact_template_match():
    assert pt.template_provides("content", "docs") == []  # writer: no role ruled
    assert pt.template_provides("content", None) == []


def test_type_domain_pairs_are_unique_so_the_match_is_exact():
    pairs = [(v.get("type"), v.get("domain")) for v in pt.templates().values() if isinstance(v, dict) and v.get("type")]
    assert len(pairs) == len(set(pairs))


def _yaml(tmp_path, body):
    p = tmp_path / "project.yaml"
    p.write_text(body)
    return p


def test_provides_step_writes_the_default(tmp_path):
    p = _yaml(tmp_path, "ai_id: outreach\n")
    step = ppc._provides_step(SimpleNamespace(type="content", domain="marketing"), p, dry_run=False)
    assert step["changed"] is True
    assert "client-documents" in yaml.safe_load(p.read_text())["provides_domains"]


def test_provides_step_keeps_a_declaration(tmp_path):
    p = _yaml(tmp_path, "ai_id: outreach\nprovides_domains: [publishing]\n")
    step = ppc._provides_step(SimpleNamespace(type="content", domain="marketing"), p, dry_run=False)
    assert step["changed"] is False and yaml.safe_load(p.read_text())["provides_domains"] == ["publishing"]


def test_provides_step_does_nothing_without_a_default(tmp_path):
    p = _yaml(tmp_path, "ai_id: x\n")
    assert ppc._provides_step(SimpleNamespace(type="service", domain="default"), p, dry_run=False)["changed"] is False
    assert "provides_domains" not in yaml.safe_load(p.read_text())
