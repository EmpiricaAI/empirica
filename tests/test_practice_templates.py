"""Practice templates and presets (goal e8c47635): every template names a real project type, domain file and skill, every
preset uses real templates, and suggest() routes candidates the way the forecast skill routes directions."""

from __future__ import annotations

from pathlib import Path

from empirica.core import practice_templates as pt
from empirica.core.project_types import PROJECT_TYPES

_ROOT = Path(__file__).resolve().parent.parent


def test_every_template_names_a_real_type_domain_and_skill():
    domains = {p.stem for p in (_ROOT / "empirica" / "config" / "domains").glob("*.yaml")}
    skills = {
        p.parent.name
        for p in (_ROOT / "empirica" / "plugins" / "claude-code-integration" / "skills").glob("*/SKILL.md")
    }
    workloads = set(pt.catalog()["workloads"])
    for name, spec in pt.templates().items():
        assert spec["type"] in PROJECT_TYPES, name
        assert spec["domain"] in domains, name
        assert {s.split(":", 1)[1] for s in spec["skills"]} <= skills, name
        assert set(spec["suggested_by"]) <= workloads, name


def test_every_preset_is_made_of_templates():
    for name, spec in pt.presets().items():
        assert spec["templates"] and set(spec["templates"]) <= set(pt.templates()), name


def test_suggest_grounds_each_practice_in_an_answer_and_routes_it():
    candidates = {c.template: c for c in pt.suggest({"research": "often", "writing": "sometimes", "data": "rarely"})}
    assert pt.route(candidates["research"]) == "confirm" and "research" in candidates["research"].reason
    assert pt.route(candidates["writer"]) == "ask"
    assert pt.route(candidates["analyst"]) == "park"  # data, rarely: not enough to propose it now
    assert "coordinator" not in candidates  # one confirmed practice: nothing to coordinate until the writer is a yes
    assert pt.route(candidates["support"]) == "confirm"


def test_a_rare_workload_alone_is_parked():
    candidates = {c.template: c for c in pt.suggest({"outreach": "rarely"})}
    assert pt.route(candidates["outreach"]) == "park" and "coordinator" not in candidates


def test_a_quick_interview_yields_a_protocol_that_validates_and_provisions(tmp_path, capsys):
    """End to end for the Quick tier: workload answers -> routed practices -> a 0.4.0 protocol -> --from-protocol."""
    import argparse
    import json

    import yaml

    from empirica.cli.command_handlers import provision_practice_commands as pp
    from empirica.core import workflow_protocol as wp

    confirmed = [c for c in pt.suggest({"client_work": "often", "research": "often"}) if pt.route(c) == "confirm"]
    spec = pt.templates()
    protocol = {
        "schema_version": "0.4.0",
        "user_profile": {"name": "Ada"},
        "practices": [
            {"name": f"ada-{c.template}", "template": c.template, "reason": c.reason, "type": spec[c.template]["type"],
             "domain": spec[c.template]["domain"], "description": spec[c.template]["for"][:300], "harness": "claude-code"}
            for c in confirmed
        ],
        "work_preferences": {"ai_autonomy_level": "collaborative_equal_partner"},
    }  # fmt: skip
    assert wp.validate(protocol) == []
    assert {p["template"] for p in protocol["practices"]} == {"engagement", "research", "coordinator", "support"}
    path = tmp_path / "workflow-protocol.yaml"
    path.write_text(yaml.safe_dump(protocol))
    seen = []

    def provision(ns):
        seen.append(ns.name)
        print(json.dumps({"ok": True, "name": ns.name, "steps": []}))
        return 0

    args = argparse.Namespace(
        name=None, from_protocol=str(path), only=None, base_path=str(tmp_path / "p"), type=None, description=None,
        domain=None, tenant=None, org=None, substrate=None, forgejo_owner=None, forgejo_host=None, no_cortex=True,
        cockpit_profile=None, launch=None, resume=None, second_pane=None, no_local_server=True, dry_run=True, output="json",
    )  # fmt: skip
    assert pp._provision_from_protocol(args, provision=provision) == 0
    assert sorted(seen) == sorted(p["name"] for p in protocol["practices"])


def test_every_template_has_a_starter_plan_and_focus_fills_in():
    for name in pt.templates():
        plan = pt.starter_plan(name, "acme-app")
        assert plan and plan.objective and 2 <= len(plan.tasks) <= 3, name
        assert "{focus}" not in plan.objective + " ".join(plan.tasks), name
    assert pt.starter_plan("builder", "acme-app").objective == "Get to know acme-app: map it and pick the first tasks"
    assert "your first piece of work" in pt.starter_plan("research").objective


def test_the_first_plan_goes_to_the_most_confident_non_support_practice():
    candidates = pt.suggest({"build_software": "often", "writing": "sometimes"})
    assert pt.main_practice(candidates).template == "builder"
    assert pt.main_practice(pt.suggest({"outreach": "rarely"})) is None  # only support confirmed: no first plan


def test_a_coordinator_needs_two_confirmed_practices_from_two_answers():
    # two 'sometimes' answers are asked about, not confirmed: nothing confirmed to coordinate (1.17.8 sweep)
    assert "coordinator" not in {c.template for c in pt.suggest({"research": "sometimes", "writing": "sometimes"})}
    # one answer that suggests two templates does not justify a coordinator by itself
    assert "coordinator" not in {c.template for c in pt.suggest({"data": "often"})}
    assert "coordinator" in {c.template for c in pt.suggest({"build_software": "often", "writing": "often"})}


def test_unknown_work_kinds_and_frequencies_are_refused_not_absorbed():
    import pytest

    with pytest.raises(ValueError, match="build_softwar"):
        pt.suggest({"build_softwar": "often"})
    with pytest.raises(ValueError, match="weekly"):
        pt.suggest({"research": "weekly"})


def test_a_tie_gives_the_first_plan_to_the_persons_own_work_not_research():
    assert pt.main_practice(pt.suggest({"build_software": "often", "research": "often"})).template == "builder"
    assert pt.main_practice(pt.suggest({"data": "often"})).template == "analyst"
