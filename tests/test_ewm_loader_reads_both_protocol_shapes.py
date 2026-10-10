"""The EWM loader reads the single-user protocol /ewm-interview writes, as well as the legacy team shape.

/ewm-interview v0.3.0 writes `user_profile`, a flat `work_preferences` and a flat `domains`. The loader read
`team`, `work_preferences[<user>]` and `domains[<user>]`, so a protocol the skill had just written loaded no role,
no preferences and no expertise, and asked the user to identify themselves (confirmed by ecodex on in-memory fixtures).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "ewm-protocol-loader.py"
)


@pytest.fixture(scope="module")
def loader():
    spec = importlib.util.spec_from_file_location("ewm_loader_shapes", HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SINGLE_USER = {
    "user_profile": {"name": "Ada", "role": "founder"},
    "work_preferences": {
        "ai_autonomy_level": "assistant_mode",
        "uncertainty_surfacing": "always_explicit",
        "pushback_style": "socratic",
        "task_splitting": {"ai_autonomous": ["tests"], "human_only": ["pricing"]},
    },
    "domains": {"expert": ["compilers"], "learning": ["sales"]},
}

LEGACY_TEAM = {
    "team": [{"name": "David", "role": "co-founder", "focus": "architecture"}],
    "work_preferences": {"david": {"ai_autonomy_level": "full_autonomy", "pushback_style": "direct_and_factual"}},
    "domains": {"david": {"expert": ["architecture"]}},
}


def test_the_single_user_shape_loads_role_preferences_and_expertise(loader):
    out = loader.format_user_preferences(SINGLE_USER, "ada")
    assert "**Role:** founder" in out
    assert "Assistant — wait for direction" in out
    assert "Always surface uncertainty" in out and "Socratic" in out
    assert "**AI autonomous:** tests" in out and "**Human only:** pricing" in out
    assert "**Expert in:** compilers" in out and "**Learning:** sales" in out


def test_the_legacy_team_shape_is_unchanged(loader):
    out = loader.format_user_preferences(LEGACY_TEAM, "david")
    assert "**Role:** co-founder" in out and "**Focus:** architecture" in out
    assert "Full autonomy" in out and "Direct and factual" in out
    assert "**Expert in:** architecture" in out


def test_the_single_user_protocol_names_its_own_user(loader):
    assert loader.resolve_user(SINGLE_USER, None) == "ada"


def test_an_explicit_user_still_wins_and_a_team_protocol_still_asks(loader):
    assert loader.resolve_user(SINGLE_USER, "grace") == "grace"
    assert loader.resolve_user(LEGACY_TEAM, None) is None


def test_another_users_name_does_not_pick_up_the_single_users_preferences(loader):
    out = loader.format_user_preferences(SINGLE_USER, "grace")
    assert "Assistant — wait for direction" not in out and "**Role:** founder" not in out
