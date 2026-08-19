"""Tests for `empirica mailbox poll|show|archive` — the receive-side CLI.

Implements prop_jdldx2pz (ecodex), shape endorsed by cortex prop_bbtqnc. Closes
the send/receive asymmetry: `mailbox reply` sent/acked, these three receive.
All dependency-injected — no network.
"""

from __future__ import annotations

import json
import types

from empirica.cli.command_handlers.mailbox_commands import (
    handle_mailbox_archive_command,
    handle_mailbox_poll_command,
    handle_mailbox_show_command,
)

_CREDS_OK = lambda: ("http://cortex.test", "key-abc")  # noqa: E731
_CREDS_MISSING = lambda: (None, None)  # noqa: E731


def _poll_args(**overrides):
    defaults = {
        "ai_id": "empirica.david.empirica",
        "outbox": False,
        "status": None,
        "since": None,
        "limit": 20,
        "related": False,
        "output": "json",
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def _fake_fetch(proposals, recorder=None):
    # `meta_out` is an optional out-dict the handler now passes so a truncated
    # poll can report `matched`/`has_more`. Accepted and ignored here — these
    # tests predate completeness and assert the envelope's other fields.
    def fn(cortex_url, api_key, ai_id, *, outbox, statuses, since, limit, related, timeout=10.0, meta_out=None):
        if recorder is not None:
            recorder.update(
                {
                    "ai_id": ai_id,
                    "outbox": outbox,
                    "statuses": statuses,
                    "since": since,
                    "limit": limit,
                    "related": related,
                }
            )
        return proposals

    return fn


# ─── poll ───────────────────────────────────────────────────────────────


def test_poll_inbox_default_statuses(capsys):
    rec: dict = {}
    props = [{"id": "prop_a", "status": "accepted", "title": "hi", "source_claude": "x"}]
    rc = handle_mailbox_poll_command(
        _poll_args(),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_mailbox=_fake_fetch(props, rec),
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["direction"] == "inbox"
    assert out["count"] == 1
    assert out["statuses"] == ["accepted", "changed"]  # wake-react default, NOT eco_review
    assert rec["outbox"] is False
    assert rec["statuses"] == ("accepted", "changed")


def test_poll_outbox_flips_direction_and_default_statuses(capsys):
    rec: dict = {}
    rc = handle_mailbox_poll_command(
        _poll_args(outbox=True),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_mailbox=_fake_fetch([], rec),
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["direction"] == "outbox"
    # Was ["completed", "changed", "declined"] — that expectation ENCODED the
    # bug. `accepted` is a collab's terminal state, so excluding it made every
    # collab invisible: 182 emissions, 21 visible when cortex measured it.
    # Asserted against the shared default rather than a literal, so the two
    # cannot drift apart again.
    from empirica.cli.command_handlers.mailbox_commands import _default_poll_statuses

    assert out["statuses"] == list(_default_poll_statuses(outbox=True))
    assert "accepted" in out["statuses"]
    assert rec["outbox"] is True


def test_poll_custom_status_csv_parsed(capsys):
    rec: dict = {}
    handle_mailbox_poll_command(
        _poll_args(status="accepted, declined ,changed"),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_mailbox=_fake_fetch([], rec),
    )
    assert rec["statuses"] == ("accepted", "declined", "changed")


def test_poll_passes_since_limit_related(capsys):
    rec: dict = {}
    handle_mailbox_poll_command(
        _poll_args(since="2026-07-05T00:00:00Z", limit=5, related=True),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_mailbox=_fake_fetch([], rec),
    )
    assert rec["since"] == "2026-07-05T00:00:00Z"
    assert rec["limit"] == 5
    assert rec["related"] is True


def test_poll_creds_missing_returns_1(capsys):
    rc = handle_mailbox_poll_command(_poll_args(), _resolve_cortex_creds=_CREDS_MISSING)
    assert rc == 1
    assert "creds missing" in capsys.readouterr().err


def test_poll_ai_id_unresolved_returns_1(capsys):
    rc = handle_mailbox_poll_command(
        _poll_args(ai_id=None),
        _resolve_cortex_creds=_CREDS_OK,
        _resolve_ai_id=lambda: None,
    )
    assert rc == 1
    assert "ai_id unresolved" in capsys.readouterr().err


def test_poll_fetch_failure_surfaces_not_crashes(capsys):
    def boom(*a, **k):
        raise ConnectionError("cortex down")

    rc = handle_mailbox_poll_command(_poll_args(), _resolve_cortex_creds=_CREDS_OK, _fetch_mailbox=boom)
    assert rc == 1
    assert "fetch failed" in capsys.readouterr().err


def test_poll_human_output(capsys):
    props = [
        {"id": "prop_abc", "status": "accepted", "title": "Do the thing", "source_claude": "empirica.david.ecodex"}
    ]
    rc = handle_mailbox_poll_command(
        _poll_args(output="human"),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_mailbox=_fake_fetch(props),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "inbox: 1 proposal" in out
    assert "prop_abc" in out and "Do the thing" in out


# ─── show ───────────────────────────────────────────────────────────────


def test_show_returns_proposal(capsys):
    prop = {"id": "prop_x", "status": "accepted", "title": "T", "summary": "body", "source_claude": "y"}
    rc = handle_mailbox_show_command(
        types.SimpleNamespace(proposal_id="prop_x", output="json"),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_parent=lambda u, k, pid: prop,
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["proposal"]["id"] == "prop_x"


def test_show_missing_id_returns_1(capsys):
    rc = handle_mailbox_show_command(
        types.SimpleNamespace(proposal_id=None, output="json"),
        _resolve_cortex_creds=_CREDS_OK,
    )
    assert rc == 1


def test_show_not_found_returns_1(capsys):
    rc = handle_mailbox_show_command(
        types.SimpleNamespace(proposal_id="prop_gone", output="json"),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_parent=lambda u, k, pid: None,
    )
    assert rc == 1
    assert "not found" in capsys.readouterr().err


# ─── archive ────────────────────────────────────────────────────────────


def test_archive_success(capsys):
    calls: list = []

    def post(url, body, api_key, timeout):
        calls.append((url, body))
        return 200, {"ok": True, "status": "archived"}

    rc = handle_mailbox_archive_command(
        types.SimpleNamespace(proposal_id="prop_z", reason="cleanup", output="json"),
        _resolve_cortex_creds=_CREDS_OK,
        _http_post=post,
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["archived"] is True
    assert "/v1/orchestration/prop_z/archive" in calls[0][0]
    assert calls[0][1]["reason"] == "cleanup"


def test_archive_failure_returns_1(capsys):
    rc = handle_mailbox_archive_command(
        types.SimpleNamespace(proposal_id="prop_z", reason=None, output="json"),
        _resolve_cortex_creds=_CREDS_OK,
        _http_post=lambda u, b, k, t: (500, {"error": "boom"}),
    )
    assert rc == 1
    assert "failed" in capsys.readouterr().err


# ─── completeness in the HUMAN format (prop_sybqhrku) ────────────────────


def _fetch_with_meta(proposals, matched, has_more):
    def fn(cortex_url, api_key, ai_id, *, outbox, statuses, since, limit, related, timeout=10.0, meta_out=None):
        if meta_out is not None:
            meta_out["matched"] = matched
            meta_out["has_more"] = has_more
        return proposals

    return fn


def test_human_output_says_when_it_is_showing_a_page(capsys):
    """A page of N is indistinguishable from a backlog of N without the count.

    The completeness pair was surfaced in the JSON envelope and nowhere else, so
    `--output human` went on printing only the page size. Measured cost on a
    peer's cleanup sweep: they read `--limit 60 -> 60` as 60 unarchived when the
    real figure was 154, and then ran nine no-op passes because nothing told
    them when they were done. Fixing one output format and leaving the other is
    how an already-fixed defect keeps being encountered.
    """
    props = [{"id": f"prop_{i}", "status": "accepted", "title": "t", "source_claude": "x"} for i in range(3)]
    rc = handle_mailbox_poll_command(
        _poll_args(output="human", limit=3),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_mailbox=_fetch_with_meta(props, matched=154, has_more=True),
    )
    assert rc == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert "154" in header, "the backlog figure must be visible, not only the page size"
    assert "SHOWING A PAGE" in header


def test_human_output_says_when_the_view_is_complete(capsys):
    """'Complete' has to be sayable, or cleanup has no stopping condition."""
    props = [{"id": f"prop_{i}", "status": "accepted", "title": "t", "source_claude": "x"} for i in range(4)]
    rc = handle_mailbox_poll_command(
        _poll_args(output="human", limit=50),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_mailbox=_fetch_with_meta(props, matched=4, has_more=False),
    )
    assert rc == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert "complete" in header.lower()
    assert "SHOWING A PAGE" not in header


def test_human_output_is_unchanged_when_cortex_sends_no_count(capsys):
    """An older cortex must not gain a fabricated completeness claim."""
    props = [{"id": "prop_a", "status": "accepted", "title": "t", "source_claude": "x"}]
    rc = handle_mailbox_poll_command(
        _poll_args(output="human"),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_mailbox=_fake_fetch(props),
    )
    assert rc == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert header == "inbox: 1 proposal(s)"


def test_an_empty_inbox_still_reports_a_nonzero_backlog(capsys):
    """The worst case: an under-read inbox looks exactly like an empty one.

    Nobody reconciles an empty inbox, so this is the reading most likely to be
    trusted and least likely to be checked.
    """
    rc = handle_mailbox_poll_command(
        _poll_args(output="human", status="completed"),
        _resolve_cortex_creds=_CREDS_OK,
        _fetch_mailbox=_fetch_with_meta([], matched=87, has_more=True),
    )
    assert rc == 0
    assert "87" in capsys.readouterr().out
