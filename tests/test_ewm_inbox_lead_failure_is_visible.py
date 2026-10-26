"""A mailbox poll that says "the inbox was NOT checked" must not render as "no mail" at SessionStart (goal 74e7a298, unknown cc13b98d).

`empirica mailbox poll` fails loudly since the unresolved-canonical-id fix (`ok: false`, exit 1, an error that says an empty result would
have meant "unknown", not "no mail"). `_build_pending_inbox_lead` returned "" on ANY non-zero exit, so the practitioner's first screen read
the same whether the inbox was empty or was never read. The rest of the fail-open contract stays: a plain-text failure (no cortex
configured, which is most installs) and a timeout still render nothing, and nothing here can raise.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import subprocess
import sys
from pathlib import Path


def _load_hook():
    hook_path = Path(__file__).resolve().parents[1] / (
        "empirica/plugins/claude-code-integration/hooks/ewm-protocol-loader.py"
    )
    spec = _ilu.spec_from_file_location("ewm_protocol_loader_hook_failure", hook_path)
    assert spec and spec.loader, "could not load hook spec"
    mod = _ilu.module_from_spec(spec)
    sys.modules["ewm_protocol_loader_hook_failure"] = mod
    spec.loader.exec_module(mod)
    return mod


def _poll_returns(monkeypatch, mod, *, stdout: str, returncode: int):
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: "empirica")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=""),
    )
    return mod._build_pending_inbox_lead()


UNRESOLVED = json.dumps(
    {
        "ok": False,
        "error": "canonical id for 'empirica' could not be resolved (cortex's roster was unreachable), so the inbox was NOT checked",
        "ai_id": "empirica",
    }
)


def test_a_poll_that_reports_the_inbox_was_not_checked_is_shown_not_swallowed(monkeypatch):
    out = _poll_returns(monkeypatch, _load_hook(), stdout=UNRESOLVED, returncode=1)
    assert out, "the lead was empty: the practitioner reads this as 'no mail'"
    assert "NOT checked" in out or "not checked" in out.lower(), out
    assert "empirica mailbox poll" in out, "the line must say how to check by hand"


def test_the_failure_line_is_one_short_block_not_the_whole_error(monkeypatch):
    long_error = json.dumps({"ok": False, "error": "x" * 5000})
    out = _poll_returns(monkeypatch, _load_hook(), stdout=long_error, returncode=1)
    assert 0 < len(out) < 800, len(out)


def test_a_plain_text_failure_stays_silent_for_installs_with_no_mesh(monkeypatch):
    """Control: 'Cortex creds missing' is the normal state of a non-mesh install and must not nag on every session start."""
    out = _poll_returns(monkeypatch, _load_hook(), stdout="mailbox poll: Cortex creds missing", returncode=1)
    assert out == ""


def test_a_json_failure_without_an_error_message_stays_silent(monkeypatch):
    out = _poll_returns(monkeypatch, _load_hook(), stdout=json.dumps({"ok": False}), returncode=1)
    assert out == ""


def test_an_empty_inbox_is_still_empty(monkeypatch):
    """Control: success with no proposals renders nothing, so the new line cannot appear for 'no mail'."""
    ok = json.dumps({"ok": True, "ai_id": "empirica", "count": 0, "proposals": []})
    assert _poll_returns(monkeypatch, _load_hook(), stdout=ok, returncode=0) == ""
