"""`empirica mailbox sers` — read-only SER participation.

Built because nothing in the CLI reached `/v1/sers`, so a practitioner could not
answer "am I a participant on this SER" without MCP or raw HTTP. Three seats spent
five messages establishing required-tier on two records, and answered at three
different evidence grades because only some could reach the store.

**The test that matters most is the canonical-id one**, because the first live run of
this verb reported "participates in none" while the practice held `role=required` on
five SERs. `.empirica/project.yaml` carries the bare slug; participants are keyed by
canonical 3-form; a bare slug matches no row, so the query SUCCEEDS and returns an
empty list. Not an error — a confident zero, produced inside the tool built to remove
confident zeros.
"""

from __future__ import annotations

import json
import types

import pytest

from empirica.cli.command_handlers.mailbox_commands import (
    _resolve_canonical_ai_id,
    handle_mailbox_sers_command,
)

ROSTER = {
    "self": {"tenant_slug": "david"},
    "org": {
        "tenants": [
            {
                "tenant_slug": "philipp",
                "projects": [
                    {"slug": "empirica", "ai_id_short": "empirica", "ai_id_mesh": "empirica.philipp.empirica"}
                ],
            },
            {
                "tenant_slug": "david",
                "projects": [
                    {"slug": "empirica", "ai_id_short": "empirica", "ai_id_mesh": "empirica.david.empirica"},
                    {
                        "slug": "empirica-cortex",
                        "ai_id_short": "empirica-cortex",
                        "ai_id_mesh": "empirica.david.empirica-cortex",
                    },
                ],
            },
        ]
    },
}

SERS = {
    "sers": [
        {
            "ser_id": "ser_abc",
            "coordination_state": "blocked",
            "title": "Boundary cleanup",
            "participants": [{"practice_id": "empirica.david.empirica", "role": "required", "last_ack_at": None}],
        }
    ]
}


def _args(**over):
    d = {"ser_id": None, "ai_id": None, "output": "json"}
    d.update(over)
    return types.SimpleNamespace(**d)


def _creds():
    return lambda: ("https://cortex.example.com", "ctx_test")


def _getter(routes: dict, calls: list | None = None):
    def fn(url, api_key, timeout=10.0):
        if calls is not None:
            calls.append(url)
        for frag, resp in routes.items():
            if frag in url:
                return resp
        return 404, {"error": "not found"}

    return fn


# ── canonical resolution: the false negative that bit ─────────────────


def test_the_bare_project_slug_is_never_used_to_query():
    """POSITIVE CONTROL. The resolver must turn `empirica` into the canonical form;
    querying with the slug returns an empty list that reads as non-participation."""
    calls: list[str] = []
    rc = handle_mailbox_sers_command(
        _args(),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=lambda: "empirica",
        _http_get=_getter({"/v1/users/me/roster": (200, ROSTER), "/v1/sers": (200, SERS)}, calls),
    )

    assert rc == 0
    ser_call = next(c for c in calls if "/v1/sers" in c)
    assert "ai_id=empirica.david.empirica" in ser_call, f"queried with a non-canonical id: {ser_call}"


def test_resolution_is_scoped_to_the_callers_own_tenant():
    """Two tenants legitimately hold the same practice slug. An unscoped walk would
    return whichever came first — here, Philipp's."""
    assert (
        _resolve_canonical_ai_id("https://c", "k", "empirica", _getter({"/roster": (200, ROSTER)}))
        == "empirica.david.empirica"
    )


def test_a_non_canonical_ai_id_is_refused_not_answered_with_zero(capsys):
    """NEGATIVE CONTROL on the failure mode itself: passing a bare slug must error,
    because the query would otherwise succeed and report zero participation."""
    rc = handle_mailbox_sers_command(
        _args(ai_id="empirica"),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=lambda: None,
        _http_get=_getter({"/v1/sers": (200, {"sers": []})}),
    )

    assert rc == 1
    assert "not a canonical 3-form" in capsys.readouterr().err


def test_an_unresolvable_roster_fails_loudly_rather_than_listing_nothing(capsys):
    rc = handle_mailbox_sers_command(
        _args(),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=lambda: "empirica",
        _http_get=_getter({"/roster": (500, {})}),
    )

    assert rc == 1
    assert "could not resolve a canonical ai_id" in capsys.readouterr().err


# ── the three outcomes stay distinguishable ───────────────────────────


def test_genuine_non_participation_says_so_explicitly(capsys):
    """An empty result from a SUCCESSFUL query is an answer, and must read as one."""
    rc = handle_mailbox_sers_command(
        _args(output="human"),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=lambda: "empirica",
        _http_get=_getter({"/roster": (200, ROSTER), "/v1/sers": (200, {"sers": []})}),
    )

    assert rc == 0
    assert "participates in none" in capsys.readouterr().out


def test_missing_credentials_is_a_distinct_failure(capsys):
    rc = handle_mailbox_sers_command(_args(), _resolve_cortex_creds=lambda: (None, None))

    assert rc == 1
    assert "no cortex credentials" in capsys.readouterr().err


def test_an_unknown_ser_id_is_a_distinct_failure(capsys):
    rc = handle_mailbox_sers_command(
        _args(ser_id="ser_nope"),
        _resolve_cortex_creds=_creds(),
        _http_get=_getter({"/v1/sers/ser_nope": (404, {})}),
    )

    assert rc == 1
    assert "not found" in capsys.readouterr().err


# ── output ────────────────────────────────────────────────────────────


def test_participant_rows_name_required_tier_holders(capsys):
    handle_mailbox_sers_command(
        _args(output="human"),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=lambda: "empirica",
        _http_get=_getter({"/roster": (200, ROSTER), "/v1/sers": (200, SERS)}),
    )

    out = capsys.readouterr().out
    assert "empirica.david.empirica" in out
    assert "role=required" in out


@pytest.mark.parametrize(
    "body",
    [SERS, SERS["sers"], SERS["sers"][0]],
    ids=["wrapped", "bare-list", "single-object"],
)
def test_all_three_response_envelopes_parse(body, capsys):
    """The envelope is specified in the mesh skills rather than observed here, so the
    parser tolerates all three shapes instead of assuming one."""
    handle_mailbox_sers_command(
        _args(ser_id="ser_abc"),
        _resolve_cortex_creds=_creds(),
        _http_get=_getter({"/v1/sers/ser_abc": (200, body)}),
    )

    assert json.loads(capsys.readouterr().out)["count"] == 1
