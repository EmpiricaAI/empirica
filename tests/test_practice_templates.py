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
    assert candidates["coordinator"].reason == "2 practices to coordinate"
    assert pt.route(candidates["support"]) == "confirm"


def test_a_rare_workload_alone_is_parked():
    candidates = {c.template: c for c in pt.suggest({"outreach": "rarely"})}
    assert pt.route(candidates["outreach"]) == "park" and "coordinator" not in candidates
