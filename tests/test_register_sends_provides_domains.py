"""project-register sends what a practice provides, so the roster can route by role (goal b15f79b9).

David, 2026-11-25: client- and prospect-facing write-ups go to the practice that provides client-documents. Cortex's register
already stores body.provides_domains; core never sent it, so the roster stayed empty even for a practice that declared its
domains (outreach, prop_taqxbe6f). A practice declares them in .empirica/project.yaml; a module.yaml's provides.domains is
honoured when project.yaml says nothing.
"""

from __future__ import annotations

from empirica.cli.command_handlers import projects_commands as pc


def _practice(tmp_path, project_yaml: str):
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(project_yaml)
    return tmp_path


def test_project_yaml_declaration_is_read(tmp_path):
    root = _practice(tmp_path, "ai_id: outreach\nprovides_domains: [client-documents, publishing]\n")
    assert pc._declared_provides_domains(root) == ["client-documents", "publishing"]


def test_module_yaml_is_honoured_when_project_yaml_is_silent(tmp_path):
    root = _practice(tmp_path, "ai_id: autonomy\n")
    (root / "module.yaml").write_text(
        "empirica_module:\n  name: autonomy\n  seat_name: autonomy\n  version: '1'\n"
        "  provides:\n    domains: [governance, audit]\n"
    )
    assert pc._declared_provides_domains(root) == ["governance", "audit"]


def test_undeclared_is_none_so_cortex_keeps_what_it_has(tmp_path):
    assert pc._declared_provides_domains(_practice(tmp_path, "ai_id: x\n")) is None
    assert pc._declared_provides_domains(None) is None


def test_bulk_payload_carries_it(tmp_path):
    root = _practice(tmp_path, "provides_domains: [client-documents]\n")
    assert pc._register_payload({"name": "outreach", "path": str(root)}, False)["provides_domains"] == [
        "client-documents"
    ]


def test_bulk_payload_omits_it_when_undeclared(tmp_path):
    root = _practice(tmp_path, "ai_id: x\n")
    assert "provides_domains" not in pc._register_payload({"name": "x", "path": str(root)}, False)


def test_single_payload_carries_it():
    payload = pc._single_register_payload("pid", "outreach", None, False, ["client-documents"])
    assert payload["provides_domains"] == ["client-documents"] and payload["project_id"] == "pid"
    assert "provides_domains" not in pc._single_register_payload("pid", "x", None, False, None)


def test_cortex_acknowledgement_is_passed_on():
    fn = next(getattr(pc, n) for n in dir(pc) if n.startswith("_") and "register_response" in n)
    out = fn(200, {"project_id": "pid", "provides_domains_stored": True}, "pid")
    assert out["provides_domains_stored"] is True
