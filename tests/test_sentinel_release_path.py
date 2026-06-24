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


def test_exempt_goals_tracking(sg):
    # goals-* is MEASUREMENT (recording work + state), same class as *-log —
    # so a practitioner can defer-as-goal even while gated (autonomy-ratified).
    assert _bash(sg, "empirica goals-create --objective x") is True
    assert _bash(sg, "cd /x\nempirica goals-complete-task --task-id y --evidence z") is True


def test_loop_narrowed_to_control(sg):
    # read/control/heartbeat subverbs are exempt …
    assert _bash(sg, "empirica loop status") is True
    assert _bash(sg, "empirica loop heartbeat cortex-mailbox-poll --status ok") is True
    # … but register/install (infrastructure setup) is NOT — normal path.
    assert _bash(sg, "empirica loop register --name x --kind interval") is False


# ---- NOT exempt: the security boundary ----------------------------------


def test_not_exempt_chained_praxic(sg):
    # A recovery verb chained with a praxic command must NOT be exempted.
    assert _bash(sg, "empirica check-submit x && rm -rf /tmp/foo") is False


def test_not_exempt_pipe_to_destructive(sg):
    assert _bash(sg, "empirica finding-log --finding y | rm -rf /tmp") is False


def test_not_exempt_non_recovery_empirica(sg):
    # release is workflow but NOT a recovery/measurement verb (it deploys) — it
    # goes through the normal path, never the universal exemption.
    assert _bash(sg, "empirica release") is False
    assert _bash(sg, "empirica session-create --ai-id x") is False


def test_not_exempt_plain_praxic_bash(sg):
    assert _bash(sg, "rm -rf /tmp/foo") is False
    assert _bash(sg, "git commit -m x") is False


def test_not_exempt_non_bash_praxic_tool(sg):
    assert (
        sg._is_recovery_or_measurement_action("Edit", {"file_path": "/x", "old_string": "a", "new_string": "b"})
        is False
    )
    assert sg._is_recovery_or_measurement_action("Write", {"file_path": "/x", "content": "y"}) is False


# ---- governance on the set itself (autonomy's 2 invariants) --------------

# Floor actions — the recovery whitelist must NEVER intersect these, even though
# the pre-gate runs first (a future edit can't slip a floor-ish verb in).
_FLOOR_TOKENS = ("publish", "trust-escalation", "trust_escalation", "release", "irreversible")
# World-mutation / firewall-bypass tokens the whitelist must never contain.
_WORLD_MUTATION_TOKENS = ("release", "deploy", "git ", "rm ", "docker", "ssh ")


def test_invariant_disjoint_from_floor(sg):
    """DISJOINT-FROM-FLOOR: recovery-whitelist ∩ floor-actions = ∅."""
    for prefix in sg._RECOVERY_MEASUREMENT_PREFIXES:
        verb = prefix.removeprefix("empirica ")
        for floor in _FLOOR_TOKENS:
            assert floor not in verb, f"floor token {floor!r} leaked into recovery whitelist via {prefix!r}"


def test_invariant_no_world_mutation(sg):
    """NO-WORLD-MUTATION: every exempt prefix is an `empirica <recovery/
    measurement/control>` verb — never Edit/Write/git/bash/deploy. Adding a verb
    must pass 'can this mutate the world / bypass the firewall for real work?'"""
    for prefix in sg._RECOVERY_MEASUREMENT_PREFIXES:
        assert prefix.startswith("empirica "), f"{prefix!r} is not an empirica verb"
        for tok in _WORLD_MUTATION_TOKENS:
            assert tok not in prefix, f"world-mutation token {tok!r} in {prefix!r}"
    # The predicate itself never exempts a world action.
    for cmd in ("git push", "rm -rf /tmp", "docker run x", "empirica release", "scripts/release.py"):
        assert _bash(sg, cmd) is False, f"{cmd!r} must never be release-exempted"
