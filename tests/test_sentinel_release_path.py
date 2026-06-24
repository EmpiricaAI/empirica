"""Release-path invariant: the Sentinel's universal recovery/measurement pre-gate.

A gate must never block the action that clears it. _is_recovery_or_measurement_action
is main()'s first check — recovery + measurement actions are always-open before any
gate. These tests pin both halves of the contract:
- the trapped shapes are now exempt (the cd-newline-heredoc check-submit that the
  rush-guard caught), across single-line / && / newline / pipe forms;
- the exemption does NOT open a chained-praxic hole (`empirica check-submit && rm`
  or a pipe to a destructive command is NOT exempt).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_hook():
    hook_path = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"
    if not hook_path.exists():
        pytest.skip("sentinel-gate.py not found")
    spec = importlib.util.spec_from_file_location("sentinel_gate_release_path", hook_path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        pytest.skip(f"sentinel-gate.py not importable here: {e}")
    return module


@pytest.fixture(scope="module")
def sg():
    return _load_hook()


def _bash(sg, command):
    return sg._is_recovery_or_measurement_action("Bash", {"command": command})


# ---- exempt: the trapped recovery shapes --------------------------------


def test_exempt_single_line_check_submit(sg):
    assert _bash(sg, "empirica check-submit --session x") is True


def test_exempt_cd_newline_heredoc_check_submit(sg):
    # THE bug: cd<newline>empirica <verb> - <<EOF — was mis-parsed as praxic.
    cmd = "cd /home/u/proj\nempirica check-submit - <<'EOF'\n{\"vectors\": {}}\nEOF"
    assert _bash(sg, cmd) is True


def test_exempt_cd_amp_heredoc_postflight(sg):
    cmd = "cd /home/u/proj && empirica postflight-submit - <<'EOF'\n{}\nEOF"
    assert _bash(sg, cmd) is True


def test_exempt_preflight_heredoc(sg):
    cmd = "cd /x\nempirica preflight-submit - <<'EOF'\n{}\nEOF"
    assert _bash(sg, cmd) is True


def test_exempt_finding_log_piped_to_tail(sg):
    # The common form: a benign read-only pipe filter is allowed.
    assert _bash(sg, "cd /x\nempirica finding-log --finding y | tail -1") is True


def test_exempt_note_and_logs(sg):
    assert _bash(sg, "empirica note 'check later'") is True
    assert _bash(sg, "empirica decision-log --choice a --rationale b") is True
    assert _bash(sg, "empirica mistake-log --mistake a --why-wrong b") is True


def test_exempt_self_heal_and_control(sg):
    assert _bash(sg, "empirica doctor") is True
    assert _bash(sg, "empirica setup-claude-code --force") is True
    assert _bash(sg, "empirica listener off") is True


def test_exempt_empirica_mcp_tool(sg):
    assert sg._is_recovery_or_measurement_action("mcp__empirica__noetic_batch", {}) is True


# ---- NOT exempt: the security boundary ----------------------------------


def test_not_exempt_chained_praxic(sg):
    # A recovery verb chained with a praxic command must NOT be exempted.
    assert _bash(sg, "empirica check-submit x && rm -rf /tmp/foo") is False


def test_not_exempt_pipe_to_destructive(sg):
    assert _bash(sg, "empirica finding-log --finding y | rm -rf /tmp") is False


def test_not_exempt_non_recovery_empirica(sg):
    # goals-create is workflow but not a gate-release/recovery verb — it goes
    # through the normal path, not the universal exemption.
    assert _bash(sg, "empirica goals-create --objective x") is False


def test_not_exempt_plain_praxic_bash(sg):
    assert _bash(sg, "rm -rf /tmp/foo") is False
    assert _bash(sg, "git commit -m x") is False


def test_not_exempt_non_bash_praxic_tool(sg):
    assert (
        sg._is_recovery_or_measurement_action("Edit", {"file_path": "/x", "old_string": "a", "new_string": "b"})
        is False
    )
    assert sg._is_recovery_or_measurement_action("Write", {"file_path": "/x", "content": "y"}) is False
