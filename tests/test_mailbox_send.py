"""`empirica mailbox send`: start a conversation from the CLI (1.15.1, goal b4cdb5db).

Core could only reply: `mailbox reply` needs a parent, and the MCP `cortex_collab` tool talks to Cortex, not to the local
server. On a machine with no Cortex that meant a practice could answer a message and never begin one.
"""

from __future__ import annotations

import json
import types

from empirica.cli.command_handlers.mailbox_commands import handle_mailbox_send_command


def _args(**kw):
    base = {"to": ["local.dana.dana-research"], "title": "q", "summary": "does X hold?", "type": "collab_brief",
            "payload": None, "source_claude": None, "output": "json"}  # fmt: skip
    base.update(kw)
    return types.SimpleNamespace(**base)


def _post_recorder(answers=None):
    calls, answers = [], list(answers or [])

    def post(url, body, key, timeout=10.0):
        calls.append({"url": url, "body": body})
        if answers:
            return answers.pop(0)
        return 200, {"ok": True, "proposal_id": "prop_sent", "status": "accepted"}

    return calls, post


def _run(args, post, source="local.dana.dana-coordinator"):
    return handle_mailbox_send_command(
        args,
        _resolve_cortex_creds=lambda: ("http://127.0.0.1:8000", "local"),
        _resolve_ai_id=lambda: source.rsplit(".", 1)[-1],
        _http_post=post,
        _http_get=lambda url, key, timeout=10.0: (404, {}),
        _canonical=lambda url, key, local, get: source,
    )


def test_send_proposes_to_the_targets_from_the_canonical_source(capsys):
    calls, post = _post_recorder()
    assert _run(_args(), post) == 0
    (call,) = calls
    body = call["body"]
    assert call["url"].endswith("/v1/orchestration/propose")
    assert body["source_claude"] == "local.dana.dana-coordinator" and body["target_claudes"] == [
        "local.dana.dana-research"
    ]
    assert body["type"] == "collab_brief" and body.get("parent_id") is None
    assert body["payload"]["idempotency_key"]
    assert json.loads(capsys.readouterr().out)["proposal_id"] == "prop_sent"


def test_the_same_message_twice_carries_the_same_key_and_a_different_one_does_not():
    calls, post = _post_recorder()
    _run(_args(), post)
    _run(_args(), post)
    _run(_args(summary="something else"), post)
    keys = [c["body"]["payload"]["idempotency_key"] for c in calls]
    assert keys[0] == keys[1] != keys[2]


def test_targets_must_be_canonical_addresses(capsys):
    calls, post = _post_recorder()
    assert _run(_args(to=["dana-research"]), post) == 1
    assert calls == [] and "org.tenant.practice" in capsys.readouterr().err


def test_a_refusal_is_reported_with_its_reason(capsys):
    _, post = _post_recorder(
        [
            (
                501,
                {
                    "capability": "orchestration.cross_tenant",
                    "error": "not available on this server: orchestration.cross_tenant",
                },
            )
        ]
    )
    assert _run(_args(to=["other.org.x"]), post) == 1
    assert "not available on this server: orchestration.cross_tenant" in capsys.readouterr().err


def test_the_parser_has_send():
    from empirica.cli.cli_core import create_argument_parser

    args = create_argument_parser().parse_args(
        ["mailbox", "send", "--to", "local.dana.a", "--to", "local.dana.b", "--title", "t", "--summary", "s"]
    )
    assert args.mailbox_action == "send" and args.to == ["local.dana.a", "local.dana.b"]
