"""Tests for `empirica practice-context` — Ambassador addressbook (Lane 2)."""

from __future__ import annotations

import json
import types
from unittest.mock import patch

from empirica.cli.command_handlers.practice_context_commands import (
    _project_roster_to_addressbook,
    handle_practice_context_command,
)


def _make_args(**overrides):
    defaults = {
        'cortex_url': 'https://cortex.test',
        'api_key': 'test-key',
        'ai_id': None,
        'timeout': 5.0,
        'output': 'json',
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


_SAMPLE_ROSTER = {
    "self": {
        "user_id": "u-david",
        "tenant_slug": "david",
        "ai_ids": ["empirica", "empirica-cortex"],
    },
    "org": {
        "id": "org-empirica",
        "slug": "empirica",
        "name": "Empirica",
        "tenants": [
            {
                "tenant_slug": "david",
                "user_name": "David",
                "is_admin": True,
                "governance_mode": "tenant_priority",
                "projects": [
                    {
                        "id": "p-david-empirica",
                        "slug": "empirica",
                        "display_name": "empirica",
                        "ai_id_short": "empirica",
                        "ai_id_tenant": "david.empirica",
                        "ai_id_mesh": "empirica.david.empirica",
                        "substrate": "cortex",
                    },
                    {
                        "id": "p-david-cortex",
                        "slug": "empirica-cortex",
                        "display_name": "Empirica Cortex",
                        "ai_id_short": "empirica-cortex",
                        "ai_id_tenant": "david.empirica-cortex",
                        "ai_id_mesh": "empirica.david.empirica-cortex",
                        "substrate": "cortex",
                    },
                ],
            },
            {
                "tenant_slug": "philipp",
                "user_name": "Philipp",
                "is_admin": False,
                "governance_mode": "tenant_priority",
                "projects": [
                    {
                        "id": "p-philipp-mesh-support",
                        "slug": "empirica-mesh-support",
                        "display_name": "Philipp Mesh Support",
                        "ai_id_short": "empirica-mesh-support",
                        "ai_id_tenant": "philipp.empirica-mesh-support",
                        "ai_id_mesh": "empirica.philipp.empirica-mesh-support",
                        "substrate": "git",
                    },
                ],
            },
        ],
    },
    "version": 42,
    "etag": "abc123",
}


# ─── _project_roster_to_addressbook ────────────────────────────────────


def test_projection_flattens_all_tenants():
    rows = _project_roster_to_addressbook(_SAMPLE_ROSTER, self_ai_id="empirica")
    assert len(rows) == 3
    assert {r["ai_id"] for r in rows} == {
        "empirica", "empirica-cortex", "empirica-mesh-support",
    }


def test_projection_marks_self_when_match_in_own_tenant():
    rows = _project_roster_to_addressbook(_SAMPLE_ROSTER, self_ai_id="empirica")
    self_rows = [r for r in rows if r["role"] == "self"]
    assert len(self_rows) == 1
    assert self_rows[0]["ai_id"] == "empirica"
    assert self_rows[0]["tenant"] == "david"


def test_projection_does_not_mark_self_in_peer_tenant():
    """An ai_id matching `self_ai_id` but in a peer's tenant is still a peer."""
    rows = _project_roster_to_addressbook(_SAMPLE_ROSTER, self_ai_id="empirica-mesh-support")
    # philipp also has empirica-mesh-support — must NOT be marked self
    philipp_row = next(r for r in rows if r["tenant"] == "philipp")
    assert philipp_row["role"] == "peer"


def test_projection_substrate_passthrough():
    rows = _project_roster_to_addressbook(_SAMPLE_ROSTER, self_ai_id="empirica")
    substrates = {(r["ai_id"], r["tenant"]): r["substrate"] for r in rows}
    assert substrates[("empirica", "david")] == "cortex"
    assert substrates[("empirica-mesh-support", "philipp")] == "git"


def test_projection_substrate_defaults_to_cortex_when_missing():
    roster = {
        "self": {"tenant_slug": "david"},
        "org": {
            "slug": "empirica",
            "tenants": [{
                "tenant_slug": "david",
                "projects": [{
                    "slug": "empirica",
                    "ai_id_short": "empirica",
                    # no substrate field
                }],
            }],
        },
    }
    rows = _project_roster_to_addressbook(roster, self_ai_id="empirica")
    assert rows[0]["substrate"] == "cortex"


def test_projection_handles_empty_roster():
    rows = _project_roster_to_addressbook({}, self_ai_id="empirica")
    assert rows == []


# ─── handle_practice_context_command ───────────────────────────────────


def test_command_renders_json_when_requested(capsys):
    args = _make_args(output='json')
    with patch(
        'empirica.cli.command_handlers.practice_context_commands._fetch_roster',
        return_value=_SAMPLE_ROSTER,
    ), patch(
        'empirica.cli.command_handlers.practice_context_commands._resolve_self_ai_id',
        return_value='empirica',
    ):
        rc = handle_practice_context_command(args)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 3
    assert any(r["role"] == "self" for r in out["practices"])


def test_command_filters_by_ai_id(capsys):
    args = _make_args(output='json', ai_id='empirica-mesh-support')
    with patch(
        'empirica.cli.command_handlers.practice_context_commands._fetch_roster',
        return_value=_SAMPLE_ROSTER,
    ), patch(
        'empirica.cli.command_handlers.practice_context_commands._resolve_self_ai_id',
        return_value='empirica',
    ):
        handle_practice_context_command(args)
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    assert out["practices"][0]["ai_id"] == "empirica-mesh-support"


def test_command_human_output_renders_table(capsys):
    args = _make_args(output='human')
    with patch(
        'empirica.cli.command_handlers.practice_context_commands._fetch_roster',
        return_value=_SAMPLE_ROSTER,
    ), patch(
        'empirica.cli.command_handlers.practice_context_commands._resolve_self_ai_id',
        return_value='empirica',
    ):
        handle_practice_context_command(args)
    out = capsys.readouterr().out
    assert "ai_id" in out  # header
    assert "empirica" in out
    assert "philipp" in out  # peer tenant
    assert "3 practitioner(s)" in out


def test_command_errors_when_no_cortex_config(capsys):
    args = _make_args(cortex_url=None, api_key=None)
    with patch(
        'empirica.cli.command_handlers.practice_context_commands._resolve_cortex_config',
        return_value=(None, None),
    ):
        rc = handle_practice_context_command(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "cortex config missing" in err.lower()


def test_command_handles_cortex_unreachable_gracefully(capsys):
    import urllib.error
    args = _make_args()
    with patch(
        'empirica.cli.command_handlers.practice_context_commands._fetch_roster',
        side_effect=urllib.error.URLError("connection refused"),
    ):
        rc = handle_practice_context_command(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "cortex unreachable" in err.lower()


def test_command_handles_http_error(capsys):
    import urllib.error
    args = _make_args()
    err = urllib.error.HTTPError(
        url="https://cortex.test/v1/users/me/roster",
        code=401, msg="Unauthorized", hdrs=None, fp=None,
    )
    with patch(
        'empirica.cli.command_handlers.practice_context_commands._fetch_roster',
        side_effect=err,
    ):
        rc = handle_practice_context_command(args)
    assert rc == 1
    err_out = capsys.readouterr().err
    assert "401" in err_out
