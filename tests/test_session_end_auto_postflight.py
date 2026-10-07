"""The SessionEnd auto-POSTFLIGHT payload is accepted by postflight-submit, and a failure to submit is said out loud.

Phase-1 sweep finding H2 (hooks, high): auto_postflight() sent {session_id, vectors, learnings, delta_summary}. The POSTFLIGHT parser rejects
unknown top-level keys (GH #409) and exits 1 before recording anything, so the auto-POSTFLIGHT could not succeed, and main() then
cleaned up and printed to stderr only on success: the failure was invisible. The test drives the REAL CLI with the hook's real payload
under an isolated HOME and asserts on the parser's own error, so it moves when either side changes.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/session-end-postflight.py"
VECTORS = {"know": 0.8, "uncertainty": 0.2, "engagement": 0.9, "completion": 0.7}
RUN_CLI = (
    "import sys; from empirica.cli.cli_core import main; sys.argv = ['empirica', 'postflight-submit', '-']; main()"
)


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("session_end_postflight_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"session-end-postflight.py not importable here: {exc}")
    return module


def _capture_payload(hook, monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["payload"] = json.loads(kwargs["input"])
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    # hook.subprocess IS the global subprocess module: the patch must end before the test runs the real CLI itself.
    with monkeypatch.context() as scoped:
        scoped.setattr(hook.subprocess, "run", fake_run)
        hook.auto_postflight("sess-1", dict(VECTORS))
    return seen["payload"]


def test_the_payload_carries_no_key_the_parser_rejects(hook, monkeypatch, tmp_path):
    payload = _capture_payload(hook, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "EMPIRICA_INSTANCE_ID": "t"}
    out = subprocess.run(
        [sys.executable, "-c", RUN_CLI],
        input=json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    combined = out.stdout + out.stderr
    assert "Unknown POSTFLIGHT key" not in combined, combined[-600:]


def test_the_old_payload_is_rejected_by_the_same_parser(tmp_path):
    """The control: the keys the hook used to send ARE refused, so the test above can fail."""
    old = {"session_id": "sess-1", "vectors": dict(VECTORS), "learnings": ["x"], "delta_summary": "y"}
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "EMPIRICA_INSTANCE_ID": "t"}
    out = subprocess.run(
        [sys.executable, "-c", RUN_CLI],
        input=json.dumps(old),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert "Unknown POSTFLIGHT key(s): delta_summary, learnings" in out.stdout + out.stderr


def test_the_payload_keeps_the_session_and_the_vectors_and_gives_a_reason(hook, monkeypatch):
    payload = _capture_payload(hook, monkeypatch)
    assert payload["session_id"] == "sess-1" and payload["vectors"] == VECTORS
    assert "auto-captured" in payload["reasoning"].lower()


def test_a_failed_submit_is_reported_on_stderr_not_only_in_unread_json(hook, monkeypatch, capsys):
    monkeypatch.setattr(
        hook, "get_session_state", lambda sid: {"needs_postflight": True, "last_vectors": dict(VECTORS)}
    )
    monkeypatch.setattr(hook, "_resolve_session_and_project", lambda cid: "sess-1")
    monkeypatch.setattr(
        hook, "auto_postflight", lambda sid, vectors: {"ok": False, "error": "Unknown POSTFLIGHT key(s): x"}
    )
    monkeypatch.setattr(hook, "_cleanup_session_files", lambda cid: None)
    monkeypatch.setattr(hook, "_run_postflight_cortex_sync", lambda v: None)
    monkeypatch.setattr(hook, "_auto_embed_project", lambda sid: None)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps({"session_id": "claude-1"})))
    with pytest.raises(SystemExit) as exit_info:
        hook.main()
    captured = capsys.readouterr()
    assert exit_info.value.code == 0  # session end is never blocked
    assert "auto-POSTFLIGHT FAILED" in captured.err and "Unknown POSTFLIGHT key" in captured.err
    assert json.loads(captured.out)["ok"] is False


# ── the sibling: text that TEACHES a payload ────────────────────────────────────────────────────────────────────────────────

ENFORCER = HOOK.parent / "transaction-enforcer.py"


def test_the_payload_the_transaction_enforcer_teaches_uses_only_accepted_keys():
    """The enforcer blocks stopping until POSTFLIGHT and showed an example with a `task_outcome` key the parser refuses, so a model
    that followed the instruction exactly got 'Unknown POSTFLIGHT key(s)' on the POSTFLIGHT it was being forced to make."""
    import re

    from empirica.cli.command_handlers._workflow_postflight import POSTFLIGHT_TOP_LEVEL_KEYS

    source = ENFORCER.read_text()
    examples = re.findall(r"postflight-submit - << 'EOF'\\n(.*?)EOF", source, flags=re.S)
    assert len(examples) == 2  # the soft reminder and the hard block: the enumerator found both
    vector_names = {
        "know",
        "uncertainty",
        "context",
        "completion",
        "impact",
        "do",
        "change",
        "clarity",
        "coherence",
        "signal",
        "density",
        "state",
        "engagement",
    }
    for example in examples:
        keys = set(re.findall(r'"([a-z_]+)":', example)) - vector_names  # what is left are the payload's top-level keys
        assert {"session_id", "vectors", "reasoning"} <= keys  # the extraction really saw the payload
        assert keys <= POSTFLIGHT_TOP_LEVEL_KEYS, sorted(keys - POSTFLIGHT_TOP_LEVEL_KEYS)


def test_the_accepted_key_set_is_the_one_the_parser_enforces():
    from empirica.cli.command_handlers import _workflow_postflight as wp

    assert {"session_id", "vectors", "reasoning", "claims", "falsifiers"} <= wp.POSTFLIGHT_TOP_LEVEL_KEYS
    assert "task_outcome" not in wp.POSTFLIGHT_TOP_LEVEL_KEYS and "learnings" not in wp.POSTFLIGHT_TOP_LEVEL_KEYS
