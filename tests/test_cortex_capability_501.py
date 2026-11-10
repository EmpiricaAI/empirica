"""A server without a capability answers 501 {"capability": name} (contract, empirica-local): the client says so in one
form, "not available on this server: <name>", wherever it hits one (goal 92fe2c88, task 845fe844)."""

from __future__ import annotations

import io
import json
import urllib.error

from empirica.core.cortex_capability import annotate_unavailable, unavailable_from_error, unavailable_message

MSG = "not available on this server: sources.shared"


def _err(code, body):
    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    return urllib.error.HTTPError("http://x/v1/sources/register", code, "x", {}, io.BytesIO(raw))  # type: ignore[arg-type]


def test_a_501_with_a_capability_becomes_the_one_message():
    assert unavailable_message(501, {"capability": "sources.shared"}) == MSG
    assert unavailable_message(501, json.dumps({"capability": "sources.shared"})) == MSG


def test_anything_else_is_not_this_case():
    assert unavailable_message(500, {"capability": "sources.shared"}) is None
    assert unavailable_message(501, {"error": "nope"}) is None
    assert unavailable_message(501, "not json") is None
    assert unavailable_message(501, None) is None


def test_from_an_http_error_reads_the_body_once_and_keeps_it():
    e = _err(501, {"capability": "sources.shared"})
    assert unavailable_from_error(e) == MSG
    assert unavailable_from_error(e) == MSG  # second call: the body is cached on the error, not re-read empty
    assert unavailable_from_error(_err(502, b"bad gateway")) is None


def test_annotate_sets_the_error_on_a_parsed_body():
    assert annotate_unavailable(501, {"capability": "sources.shared"})["error"] == MSG
    body = {"error": "real"}
    assert annotate_unavailable(400, body) is body and body["error"] == "real"
    assert annotate_unavailable(501, None) is None


def _raise_501(capability):
    def fake(req, timeout=None):
        raise _err(501, {"capability": capability})

    return fake


def test_a_reply_to_another_tenant_on_a_local_server_says_the_capability(monkeypatch):
    from empirica.cli.command_handlers import mailbox_commands

    monkeypatch.setattr(mailbox_commands.urllib.request, "urlopen", _raise_501("orchestration.cross_tenant"))
    code, body = mailbox_commands._default_http_post("http://x/v1/orchestration/propose", {}, "k")
    assert code == 501 and body["error"] == "not available on this server: orchestration.cross_tenant"


def test_a_source_body_push_on_a_local_server_says_the_capability(monkeypatch):
    from empirica.cli.command_handlers import sources_reconcile_commands as src

    monkeypatch.setattr(src.urllib.request, "urlopen", _raise_501("sources.shared"))
    out = src._push_source_body_to_cortex("http://x", "k", "u1", b"body", "text/plain")
    assert out == {"pushed": False, "status": 501, "error": MSG}
