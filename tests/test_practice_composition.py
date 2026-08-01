"""`null` means ABSENT. `[]` means genuinely zero. They must never be confused.

Extension's contract for the practice record (prop_od4esudv), and the property they
were right to lead with. A practice holding fifteen skills that renders "skills: none"
because the reader could not attest them is a failure wearing a true negative's
clothes — the same shape found in six other places across this session.

The distinction is cheap to honour at write time and impossible to reconstruct
downstream: once the wire says `[]`, no consumer can recover whether that meant "I
looked and there was nothing" or "I could not look".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.api.routes import practice

# ── the load-bearing distinction ──────────────────────────────────────


def test_an_unreadable_directory_yields_null_not_empty(tmp_path):
    """POSITIVE CONTROL. A directory that does not exist cannot be attested."""
    assert practice._list_markdown_units(tmp_path / "nope", "plugin") is None


def test_an_existing_but_empty_directory_yields_empty_not_null(tmp_path):
    """NEGATIVE CONTROL, and the half that makes the first one meaningful. Without
    this, a collector returning None for everything would pass the test above."""
    d = tmp_path / "skills"
    d.mkdir()

    assert practice._list_markdown_units(d, "plugin") == []


def test_units_are_found_in_both_layouts(tmp_path):
    """Skills are directories carrying SKILL.md; agents are flat .md files."""
    d = tmp_path / "mixed"
    (d / "a-skill").mkdir(parents=True)
    (d / "a-skill" / "SKILL.md").write_text("x", encoding="utf-8")
    (d / "an-agent.md").write_text("y", encoding="utf-8")

    names = [u["name"] for u in practice._list_markdown_units(d, "plugin") or []]

    assert names == ["a-skill", "an-agent"]


def test_a_missing_module_is_null(tmp_path):
    assert practice._read_module(tmp_path) is None


def test_a_present_module_reports_name_and_version(tmp_path):
    (tmp_path / "module.yaml").write_text("name: booking\nversion: 1.2.3\n", encoding="utf-8")

    assert practice._read_module(tmp_path) == {"name": "booking", "version": "1.2.3"}


def test_an_unparseable_module_is_null_not_a_half_filled_object(tmp_path):
    """A module with empty fields would render as a module that exists and is broken.
    Absent is the honest answer when the file cannot be understood."""
    (tmp_path / "module.yaml").write_text("{{{ not yaml", encoding="utf-8")

    assert practice._read_module(tmp_path) is None


# ── the prompt is attested, not shipped ───────────────────────────────


def test_the_project_prompt_is_fingerprinted_rather_than_returned(tmp_path):
    """The UI needs to know a prompt exists and whether it changed. It does not need
    the text, and the daemon must not become a way to pull a practice's instructions
    over HTTP."""
    (tmp_path / "CLAUDE.md").write_text("secret operating instructions", encoding="utf-8")

    got = practice._read_project_prompt(tmp_path)

    assert got["present"] is True
    assert got["bytes"] == len("secret operating instructions")
    assert len(got["sha256"]) == 64
    assert "secret" not in str(got), "the prompt body leaked into the response"


def test_a_missing_prompt_is_null(tmp_path):
    assert practice._read_project_prompt(tmp_path) is None


# ── route shape ───────────────────────────────────────────────────────


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from empirica.api.serve_app import create_serve_app

    return TestClient(create_serve_app())


def test_the_route_is_mounted_and_returns_every_contract_field(client, tmp_path):
    r = client.get("/api/v1/practice/composition", params={"path": str(tmp_path)})

    assert r.status_code == 200
    body = r.json()
    for field in (
        "module",
        "project_prompt",
        "agents",
        "skills",
        "mcp_servers",
        "observed_at",
        "config_watermark",
    ):
        assert field in body, f"contract field {field} missing"


def test_observed_at_is_always_present_so_staleness_is_visible(client, tmp_path):
    body = client.get("/api/v1/practice/composition", params={"path": str(tmp_path)}).json()

    assert body["observed_at"], "no observed_at — a consumer cannot tell how fresh this is"


def test_an_unresolvable_project_id_is_404_not_an_empty_composition(client):
    """Returning a composition of nulls for an unknown practice would be the same
    confusion one level up: the caller could not tell 'no such practice' from 'that
    practice has nothing'."""
    r = client.get("/api/v1/practice/composition", params={"project_id": "definitely-not-a-project"})

    assert r.status_code == 404


def test_a_real_tree_reports_lists_rather_than_nulls(client):
    """Against this repo the collectors must actually find things — otherwise every
    null-vs-empty test above passes while the route reports nothing useful."""
    body = client.get("/api/v1/practice/composition", params={"path": str(Path.cwd())}).json()

    assert isinstance(body["skills"], list) and body["skills"], "skills came back empty on a real tree"
    assert isinstance(body["agents"], list) and body["agents"]


# ── source is a discriminator, not a constant ─────────────────────────
#
# Extension's contract specified `source: project|plugin|user`. The first version of
# this route passed the constant "plugin", so three practices probed live returned
# identical compositions — truthful (this fleet has no project-scoped config) and
# zero-signal. `source` is what makes a practice record mean anything.


def test_project_scoped_units_are_labelled_project(tmp_path, monkeypatch):
    """POSITIVE CONTROL — the label that was hardcoded."""
    plugin = tmp_path / "plugin"
    (plugin / "agents").mkdir(parents=True)
    (plugin / "agents" / "inherited.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(practice, "_PLUGIN_ROOT", plugin)

    root = tmp_path / "proj"
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "agents" / "local.md").write_text("y", encoding="utf-8")

    by_name = {u["name"]: u["source"] for u in practice._merge_units(root, "agents") or []}

    assert by_name == {"inherited": "plugin", "local": "project"}


def test_a_project_unit_overrides_an_inherited_one_of_the_same_name(tmp_path, monkeypatch):
    """A practice that overrides an inherited agent has deliberately replaced it.
    Reporting both would misstate what actually runs."""
    plugin = tmp_path / "plugin"
    (plugin / "agents").mkdir(parents=True)
    (plugin / "agents" / "security.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(practice, "_PLUGIN_ROOT", plugin)

    root = tmp_path / "proj"
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "agents" / "security.md").write_text("y", encoding="utf-8")

    units = practice._merge_units(root, "agents") or []

    assert len(units) == 1
    assert units[0]["source"] == "project"


def test_no_units_anywhere_is_null_not_empty(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: the null-vs-empty rule must survive the merge."""
    monkeypatch.setattr(practice, "_PLUGIN_ROOT", tmp_path / "absent")

    assert practice._merge_units(tmp_path / "also-absent", "agents") is None
