"""A member's seat of a shared practice joins it instead of stopping at owner_conflict (goal 0869f94f).

Cortex models one project per practice in the team org, owned by whoever registered it first; a member joins with POST
/v1/users/me/projects, which Cortex refuses with 403 for a project in another org (prop_wc76j4om). project-register stopped
at the owner_conflict and only suggested `project-init --force`, which forks the practice (Francisco's seat, 2026-11-25).
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers import projects_commands as pc

CONFLICT = (400, {"error": "project_id abc is already registered to a different owner"})


def _cortex(monkeypatch, join):
    calls = []

    def post(url, path, payload, key, timeout):
        calls.append(path)
        return join if path == pc.CORTEX_USER_PROJECTS_PATH else CONFLICT

    monkeypatch.setattr(pc, "_post_project", post)
    return calls


def test_a_member_seat_joins_in_the_bulk_path(monkeypatch):
    calls = _cortex(monkeypatch, (200, {"ok": True, "linked": True}))
    out = pc._register_one_project({"name": "consult", "project_id": "abc"}, "https://c", "k", 5.0)
    assert out["outcome"] == "joined" and out["project_id"] == "abc"
    assert pc.CORTEX_USER_PROJECTS_PATH in calls


def test_a_project_in_another_org_is_still_a_conflict_with_the_reason(monkeypatch):
    _cortex(monkeypatch, (403, {"error": "Project is in a different org"}))
    out = pc._register_one_project({"name": "consult", "project_id": "abc"}, "https://c", "k", 5.0)
    assert out["outcome"] == "owner_conflict"
    assert "different org" in out["reason"] or "different org" in out["hint"]
    assert "project-init --force" in out["hint"], "the fork stays available for a stray copy"


@pytest.mark.parametrize(
    "join, outcome",
    [((200, {"linked": True}), "joined"), ((403, {"error": "Project is in a different org"}), "owner_conflict")],
)
def test_the_single_path_joins_or_explains(monkeypatch, join, outcome):
    _cortex(monkeypatch, join)
    base = pc._interpret_cortex_register_response(*CONFLICT, "abc")
    out = pc._join_on_owner_conflict("https://c", "abc", "k", 5.0, base)
    assert out["outcome"] == outcome
    if outcome == "joined":
        assert out["ok"] is True
