"""The MCP wrapper must not invent a success projection it did not receive.

`_call_tool_impl` shells out to the empirica CLI and hands the caller whatever the
CLI printed. When the CLI exited 0 but printed nothing, the wrapper used to
substitute a literal `{"ok": true}` — a body indistinguishable from a real
response. A caller could not tell "the verb reported success with no detail" from
"the verb reported nothing at all", which is the unfalsifiable-success shape: if it
had failed to produce its projection, the caller would see the same bytes.

Cortex hit that shape on the mesh ack path (prop_t5tl6noq) and had to run a second
outbox poll to work out what it had emitted.
"""

from __future__ import annotations

import asyncio
import json
import subprocess

import empirica_mcp.server as server
import pytest


def _run_result(*, stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=["empirica"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def fake_cli(monkeypatch):
    """Pin the CLI path and let each test dictate what the subprocess returns."""
    monkeypatch.setattr(server, "EMPIRICA_CLI", "/usr/bin/true")

    def _install(result):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: result)

    return _install


def _call(tool: str, arguments: dict | None = None) -> dict:
    out = asyncio.run(server._call_tool_impl(tool, arguments or {}))
    return json.loads(out[0].text)


def test_zero_output_is_reported_as_zero_output(fake_cli):
    """POSITIVE CONTROL — silence must be legible as silence."""
    fake_cli(_run_result(stdout="", stderr=""))

    payload = _call("goals_list")

    assert payload["ok"] is True, "exit 0 did succeed — the fix is about content, not the verdict"
    assert payload["output"] is None
    assert "no output" in payload["note"]
    # The old fallback was a bare two-key body. A caller must be able to tell this
    # apart from a real projection, so it has to carry more than ok.
    assert set(payload) > {"ok"}


def test_real_output_is_passed_through_untouched(fake_cli):
    """NEGATIVE CONTROL — the wrapper must not decorate a genuine projection."""
    real = json.dumps({"ok": True, "goals": [{"goal_id": "g1"}]})
    fake_cli(_run_result(stdout=real))

    assert _call("goals_list") == json.loads(real)


def test_stderr_only_output_still_wins_over_the_placeholder(fake_cli):
    """A verb that writes its result to stderr has said something — relay it."""
    fake_cli(_run_result(stdout="", stderr="warning: nothing to do"))

    out = asyncio.run(server._call_tool_impl("goals_list", {}))

    assert out[0].text == "warning: nothing to do"


def test_a_failing_command_is_not_dressed_up_as_success(fake_cli):
    """Guards the branch next door: nonzero exit must never reach the ok:True path."""
    fake_cli(_run_result(stdout="", stderr="boom", returncode=1))

    payload = _call("goals_list")

    assert payload["ok"] is False
    assert payload["error"] == "boom"
