"""Integration tests for the engagement CLI verbs (E2 / A4).

Each handler runs against a throwaway workspace.db (via the
EMPIRICA_WORKSPACE_DB override) so the real workspace is never touched.
engagement-create rides the entities-mint path; the others wrap the
repository methods.
"""

from __future__ import annotations

import json
import types

import pytest

from empirica.cli.command_handlers.engagement_commands import (
    handle_engagement_create_command,
    handle_engagement_list_command,
    handle_engagement_show_command,
    handle_engagement_walk_command,
)


@pytest.fixture(autouse=True)
def ws_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(tmp_path / "workspace.db"))


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


# ── create ───────────────────────────────────────────────────────────────────


def test_create_returns_open_engagement(capsys):
    code, payload = _run(
        handle_engagement_create_command,
        _args(title="Ticket one", id="e-ticket-one", domain="support", stage="support.new"),
        capsys,
    )
    assert code == 0
    assert payload["ok"] is True
    assert payload["engagement"]["lifecycle_state"] == "open"
    assert payload["engagement"]["domain"] == "support"


def test_create_invalid_domain_errors(capsys):
    code, payload = _run(
        handle_engagement_create_command,
        _args(title="x", id="e-x", domain="not_a_domain"),
        capsys,
    )
    assert code == 1
    assert payload["ok"] is False


def test_create_is_idempotent(capsys):
    a = _args(title="Dup", id="e-dup", domain="outreach")
    _run(handle_engagement_create_command, a, capsys)
    code, payload = _run(handle_engagement_create_command, _args(title="Dup", id="e-dup", domain="outreach"), capsys)
    assert code == 0
    assert payload["sidecar_created"] is False  # second run is a no-op create


def test_create_with_org_links_ticket_of(capsys):
    code, payload = _run(
        handle_engagement_create_command,
        _args(title="Acme ticket", id="e-acme-ticket", domain="support", org="acme"),
        capsys,
    )
    assert code == 0
    assert payload["org"] == "acme"
    # the org-scoped list now finds it
    code, lst = _run(handle_engagement_list_command, _args(org="acme"), capsys)
    assert code == 0
    assert any(e["engagement_id"] == "e-acme-ticket" for e in lst["engagements"])


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_filters(capsys):
    _run(handle_engagement_create_command, _args(title="s", id="e-s", domain="support"), capsys)
    _run(handle_engagement_create_command, _args(title="o", id="e-o", domain="outreach"), capsys)
    code, payload = _run(handle_engagement_list_command, _args(domain="support"), capsys)
    assert code == 0
    assert {e["engagement_id"] for e in payload["engagements"]} == {"e-s"}


def test_list_invalid_lifecycle_errors(capsys):
    code, payload = _run(handle_engagement_list_command, _args(lifecycle="bogus"), capsys)
    assert code == 1
    assert payload["ok"] is False


# ── show / walk ──────────────────────────────────────────────────────────────


def test_show_returns_engagement(capsys):
    _run(handle_engagement_create_command, _args(title="Show me", id="e-show", domain="support"), capsys)
    code, payload = _run(handle_engagement_show_command, _args(engagement_id="e-show"), capsys)
    assert code == 0
    assert payload["engagement"]["engagement_id"] == "e-show"


def test_show_missing_errors(capsys):
    code, payload = _run(handle_engagement_show_command, _args(engagement_id="nope"), capsys)
    assert code == 1
    assert payload["ok"] is False


def test_walk_returns_nodes(capsys):
    _run(handle_engagement_create_command, _args(title="Walk", id="e-walk", domain="support", org="acme"), capsys)
    code, payload = _run(handle_engagement_walk_command, _args(engagement_id="e-walk", depth=2), capsys)
    assert code == 0
    assert any(n["entity_id"] == "e-walk" for n in payload["nodes"])
    # The ticket_of edge to the org is recorded even though the org entity
    # itself isn't registered in this test (engagement-create --org only writes
    # the membership edge; the org is minted separately via entity-create).
    assert any(edge.get("group_id") == "acme" and edge.get("role") == "ticket_of" for edge in payload["edges"])
