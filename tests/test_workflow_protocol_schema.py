"""The workflow protocol schema v0.3.0 (goal 6e349146, David's rulings 2026-11-17): one person's protocol, closed, values
fixed to what the loader can describe, a required schema_version. Cortex vendors the file; these tests pin the contract."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest
import tomllib
import yaml
from jsonschema import Draft202012Validator

from empirica.core import workflow_protocol as wp
from empirica.core.project_types import PROJECT_TYPES

_ROOT = Path(__file__).resolve().parent.parent

FULL = {
    "schema_version": "0.3.0",
    "user_profile": {"name": "Ada", "role": "founder", "focus": "product", "created": "2026-11-17"},
    "goals": {
        "primary": [{"description": "Ship the beta", "success_criteria": ["10 users"], "timeline": "This quarter"}],
        "secondary": ["Write the docs"],
    },
    "domains": {"expert": ["python"], "learning": ["marketing"], "novice": ["legal"]},
    "tools": {"code_hosting": ["github"], "chat": ["slack"]},
    "mesh": {"cortex": True, "tenant": "ada", "org": "acme", "forgejo": {"owner": "ada", "host": "ssh://git@h:2222"}},
    "practices": [
        {"name": "ada-coordinator", "type": "operations", "harness": "claude-code", "domain": "operations"},
        {
            "name": "acme-app",
            "type": "web-app",
            "description": "The product",
            "harness": "both",
            "model": "claude-opus-5-5",
            "skills": ["empirica:pre-publish"],
            "repository": "ssh://git@h:2222/acme/acme-app.git",
        },
    ],
    "cockpit_profile": "ada",
    "work_preferences": {
        "ai_autonomy_level": "collaborative_equal_partner",
        "uncertainty_surfacing": "always_explicit",
        "pushback_style": "direct_and_factual",
        "calibration_mode": "multi_round",
        "task_splitting": {"ai_autonomous": ["research"], "ai_with_checkpoint": ["code"], "human_only": ["legal"]},
    },
    "trust_building": {"current_level": "establishing", "non_negotiables": ["never hide uncertainty"]},
    "anti_agreement_protocol": {"enabled": True, "mode": "direct", "ai_self_regulation": {"quantify_confidence": True}},
}


def test_the_schema_is_a_valid_2020_12_schema_with_the_published_id():
    Draft202012Validator.check_schema(wp.schema())
    assert wp.schema()["$id"] == wp.SCHEMA_ID
    assert wp.schema()["properties"]["schema_version"]["const"] == wp.SCHEMA_VERSION


def test_a_full_interview_protocol_is_valid():
    assert wp.validate(FULL) == []


def test_the_minimum_is_a_version_and_a_name():
    assert wp.validate({"schema_version": "0.3.0", "user_profile": {"name": "Ada"}}) == []
    assert wp.validate({"user_profile": {"name": "Ada"}})  # no version


@pytest.mark.parametrize(
    "path, value",
    [
        (("extra",), 1),  # closed at the top
        (("user_profile", "email"), "a@b.c"),  # closed inside
        (("practices", 0, "token"), "x"),
        (("work_preferences", "ai_autonomy_level"), "autonomous"),  # the value the 0.2.0 skill wrote
        (("work_preferences", "pushback_style"), "rude"),
        (("practices", 0, "type"), "website"),
        (("practices", 0, "harness"), "cursor"),
        (("practices", 0, "name"), "Bad Name"),
        (("schema_version",), "0.2.0"),
        (("user_profile", "created"), "17/11/2026"),
    ],
)
def test_unknown_fields_and_values_are_refused(path, value):
    doc = copy.deepcopy(FULL)
    target = doc
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert wp.validate(doc), path


def test_the_team_shape_is_not_a_profile():
    team = {"schema_version": "0.3.0", "user_profile": {"name": "Ada"}, "team": [{"name": "ada"}]}
    assert wp.validate(team)


def test_practice_types_are_the_project_types():
    item = wp.schema()["properties"]["practices"]["items"]["properties"]["type"]
    assert tuple(item["enum"]) == PROJECT_TYPES


def _loader():
    path = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "ewm-protocol-loader.py"
    spec = importlib.util.spec_from_file_location("_ewm_loader_schema", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("field", ["ai_autonomy_level", "uncertainty_surfacing", "pushback_style"])
def test_every_allowed_behaviour_value_is_one_the_loader_describes(field):
    """The values are fixed to what the loader can describe: a value it does not know reaches the AI as a bare word."""
    loader = _loader()
    for value in wp.schema()["properties"]["work_preferences"]["properties"][field]["enum"]:
        doc = copy.deepcopy(FULL)
        doc["work_preferences"][field] = value
        text = loader.format_user_preferences(doc, "ada")
        line = next(t for t in text.splitlines() if t.startswith({"ai_autonomy_level": "- **Autonomy:**", "uncertainty_surfacing": "- **Uncertainty:**", "pushback_style": "- **Pushback:**"}[field]))  # fmt: skip
        assert value not in line, (field, value, line)


def test_a_yaml_file_with_unquoted_dates_loads_and_validates(tmp_path):
    path = tmp_path / "workflow-protocol.yaml"
    path.write_text(yaml.safe_dump(FULL).replace("'2026-11-17'", "2026-11-17"))
    document, problems = wp.load(path)
    assert problems == [] and document is not None and document["user_profile"]["created"] == "2026-11-17"


def test_an_unreadable_file_is_a_problem_not_a_crash(tmp_path):
    assert wp.load(tmp_path / "missing.yaml")[0] is None
    bad = tmp_path / "p.yaml"
    bad.write_text("- a list\n")
    assert wp.load(bad) == (None, [f"{bad} does not hold a protocol (expected a mapping)"])


def test_the_schema_ships_in_the_package():
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    assert "schemas/*.json" in data["tool"]["setuptools"]["package-data"]["empirica"]


@pytest.mark.parametrize(
    "url, ok",
    [
        ("https://git.example.com/acme/app.git", True),
        ("ssh://git@git.example.com:2222/acme/app.git", True),
        ("git@github.com:acme/app.git", True),
        ("https://user:secret@git.example.com/acme/app.git", False),  # a password in the URL
        ("https://git.example.com/acme/app.git?x=1", False),
        ("https://git.example.com/acme/app.git#frag", False),
        ("ssh://-oProxyCommand=evil/x", False),  # a leading-hyphen host
        ("https://git.example.com/acme/$(id).git", False),
        ("git@github.com:acme/app.git;rm -rf ~", False),
        ("file:///etc/passwd", False),
    ],
)
def test_a_repository_is_a_plain_url_of_inert_characters(url, ok):
    """Cortex's rule, mirrored so the terminal path and cortex agree (prop_npmgug5zcja6pgvpco5vaiqunm)."""
    doc = copy.deepcopy(FULL)
    doc["practices"][1]["repository"] = url
    assert (wp.validate(doc) == []) is ok, url
