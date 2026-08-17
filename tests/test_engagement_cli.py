"""Integration tests for the engagement CLI verbs (E2 / A4).

Each handler runs against a throwaway workspace.db (via the
EMPIRICA_WORKSPACE_DB override) so the real workspace is never touched.

engagement-create was HARD-REMOVED from core (David's ruling 2026-08-17):
engagement authoring lives in the empirica-workspace CLI, and core's verb was a
bypass of that proprietary layer. Tests seed via repo.create_engagement (which
registers atomically) + upsert_entity_membership — the same writes the removed
handler performed — and pin the removal itself.
"""

from __future__ import annotations

import json
import types

import pytest

from empirica.cli.command_handlers.engagement_commands import (
    handle_engagement_list_command,
    handle_engagement_show_command,
    handle_engagement_walk_command,
)
from empirica.data.repositories.workspace_db import WorkspaceDBRepository


@pytest.fixture(autouse=True)
def ws_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(tmp_path / "workspace.db"))


def _seed(eid, title, domain=None, stage=None, org=None):
    """Seed an engagement the way authoring does: sidecar + registry (atomic in
    create_engagement) + optional org membership edge."""
    with WorkspaceDBRepository.open() as repo:
        repo.create_engagement(eid, title, domain=domain, stage=stage)
        if org:
            repo.upsert_entity_membership("engagement", eid, "organization", org, role="ticket_of")


def _args(**kw):
    base = {"output": "json", "verbose": False}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _run(handler, args, capsys) -> tuple[int, dict]:
    code = 0
    try:
        handler(args)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else {})


# ── the removal itself ───────────────────────────────────────────────────────


def test_engagement_create_is_removed():
    """Core must not expose engagement authoring — the workspace CLI owns it.
    Both the handler symbol and the CLI wiring are gone."""
    import empirica.cli.command_handlers.engagement_commands as ec

    assert not hasattr(ec, "handle_engagement_create_command")

    import empirica.cli.command_handlers as ch

    assert "handle_engagement_create_command" not in getattr(ch, "__all__", ())


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_filters(capsys):
    _seed("e-s", "s", domain="support")
    _seed("e-o", "o", domain="outreach")
    code, payload = _run(handle_engagement_list_command, _args(domain="support"), capsys)
    assert code == 0
    assert {e["engagement_id"] for e in payload["engagements"]} == {"e-s"}


def test_list_invalid_lifecycle_errors(capsys):
    code, payload = _run(handle_engagement_list_command, _args(lifecycle="bogus"), capsys)
    assert code == 1
    assert payload["ok"] is False


def test_list_org_filter(capsys):
    _seed("e-acme-ticket", "Acme ticket", domain="support", org="acme")
    code, lst = _run(handle_engagement_list_command, _args(org="acme"), capsys)
    assert code == 0
    assert any(e["engagement_id"] == "e-acme-ticket" for e in lst["engagements"])


# ── show / walk ──────────────────────────────────────────────────────────────


def test_show_returns_engagement(capsys):
    _seed("e-show", "Show me", domain="support")
    code, payload = _run(handle_engagement_show_command, _args(engagement_id="e-show"), capsys)
    assert code == 0
    assert payload["engagement"]["engagement_id"] == "e-show"


def test_show_missing_errors(capsys):
    code, payload = _run(handle_engagement_show_command, _args(engagement_id="nope"), capsys)
    assert code == 1
    assert payload["ok"] is False


def test_walk_returns_nodes(capsys):
    _seed("e-walk", "Walk", domain="support", org="acme")
    code, payload = _run(handle_engagement_walk_command, _args(engagement_id="e-walk", depth=2), capsys)
    assert code == 0
    assert any(n["entity_id"] == "e-walk" for n in payload["nodes"])
    # The ticket_of edge to the org is recorded even though the org entity
    # itself isn't registered in this test (the seed only writes the membership
    # edge; the org is minted separately via entity-create).
    assert any(edge.get("group_id") == "acme" and edge.get("role") == "ticket_of" for edge in payload["edges"])
