"""Regression: the Sentinel pacing-guard must never deadlock remote-ops Bash.

A remote-ops AI doing SSH field-install over `timeout`-wrapped ssh was
repeatedly blocked by the pacing guard. Three compounding defects in
sentinel-gate.py, fixed together:

Fix 1 — wrapper-aware ssh/scp/rsync detection. The remote-ops passthrough was
        start-anchored (startswith "ssh "), so `timeout 160 ssh host '...'`
        (the standard hang-bounded recon form) slipped past it and fell through
        to the rush-guard. Now benign wrappers (timeout/env/nice/...) are peeled
        before the leading-token check.

Fix 2 — the rush-guard counted findings/unknowns in the FROZEN window
        (preflight_ts, check_ts). Once a rushed CHECK was recorded, that window
        was closed in the past, so a finding logged AFTER it could never count —
        the "Investigate and log learnings first" message was unsatisfiable and
        the deny unrecoverable (the constant-Ns deadlock). Now it counts up to
        NOW, making the guard recoverable exactly as its message promises.

Fix 3 — work_type=remote-ops is exempt from the rush deny entirely (it's
        ungrounded_remote_ops by design; "log locally first" is a category
        error). The work_type-level safety net: even if a future command shape
        slips the classifier, a remote-ops session can never deadlock here.

The anti-rubber-stamp value is preserved: a genuine zero-artifact rushed CHECK
under a normal work_type still denies.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import time
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"


def _load_hook():
    if not _HOOK.exists():
        pytest.skip("sentinel-gate.py not found")
    spec = importlib.util.spec_from_file_location("sentinel_gate_remote_ops_pacing", _HOOK)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"sentinel-gate.py not importable here: {e}")
    return mod


sg = _load_hook()


# ── Fix 1: wrapper-aware ssh/scp/rsync classification ───────────────────────


def _bash_safe(monkeypatch, command, work_type=None):
    monkeypatch.setattr(sg, "_current_work_type", work_type)
    return sg.is_safe_bash_command({"command": command})


def test_unwrap_sees_through_wrappers():
    assert sg._unwrap_command("timeout 160 ssh -o X=1 host 'ls'") == "ssh -o X=1 host 'ls'"
    assert sg._unwrap_command("env LC_ALL=C ssh host 'ls'") == "ssh host 'ls'"
    assert sg._unwrap_command("nice -n 10 ssh host 'ls'") == "ssh host 'ls'"
    # no wrapper → unchanged
    assert sg._unwrap_command("ssh host 'ls'") == "ssh host 'ls'"
    # wrapper but non-ssh inner → never raises, inner command preserved
    assert "rm" in sg._unwrap_command("timeout 5 rm -rf /x")


def test_remote_prefix_recognizes_wrapped_ssh():
    assert sg._remote_prefix("timeout 160 ssh host 'ls'") == "ssh host 'ls'"
    assert sg._remote_prefix("ssh host 'ls'") == "ssh host 'ls'"
    # non-ssh inner → None (not routed to the remote classifier)
    assert sg._remote_prefix("timeout 5 rm -rf /x") is None
    assert sg._remote_prefix("ls -la") is None


@pytest.mark.parametrize(
    "command",
    [
        "ssh host 'ls'",  # plain ssh read (unchanged behavior)
        "timeout 160 ssh -o ConnectTimeout=12 host 'ls'",  # the exact wrapped-ssh recon case
        "env LC_ALL=C ssh host 'docker ps'",
        "nice -n 10 ssh host 'ls -la'",
        "nohup ssh host 'cat /etc/os-release'",
    ],
)
def test_wrapped_ssh_read_is_noetic(monkeypatch, command):
    # General path (no work_type declared): is_safe_remote_command classifies
    # the inner read as noetic → safe/True. Pre-fix, the wrapped forms returned
    # False (unrecognized) and fell through to the rush-guard.
    assert _bash_safe(monkeypatch, command) is True


def test_wrapped_ssh_write_still_praxic(monkeypatch):
    # A wrapped ssh that WRITES remotely is still praxic on the general path —
    # wrapper-awareness must not blanket-allow writes.
    assert _bash_safe(monkeypatch, "timeout 160 ssh host 'rm -rf /data'") is False


def test_wrapped_non_ssh_destructive_not_treated_as_remote(monkeypatch):
    # timeout wrapping a LOCAL destructive command must NOT get the ssh pass,
    # even under remote-ops. A wrapper can't smuggle a local write through.
    assert _bash_safe(monkeypatch, "timeout 5 rm -rf /important", work_type="remote-ops") is False


def test_wrapped_ssh_wholesale_under_remote_ops(monkeypatch):
    # Under remote-ops, wrapped ssh passes wholesale (the PREFLIGHT declaration
    # is the gate; local sensors can't observe the remote box) — incl. remote
    # writes, matching the existing unwrapped-ssh remote-ops behavior.
    assert _bash_safe(monkeypatch, "timeout 160 ssh host 'systemctl restart x'", work_type="remote-ops") is True


# ── Fix 2 + Fix 3: the rush-guard itself ────────────────────────────────────

# A praxic, non-safe command that reaches the rush-guard (not in any safe set,
# so it does not short-circuit via the recovery escape-hatch).
_PRAXIC = "mv /tmp/aaa /tmp/bbb"


def _rush_cursor(check_offset_s, n_findings=0, n_unknowns=0):
    """In-memory cursor with a CHECK reflex row at preflight_ts+check_offset_s,
    and findings/unknowns logged AFTER check_ts — i.e. the frozen-window
    deadlock case (artifacts the pre-fix guard would never count).
    Returns (cursor, preflight_ts)."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE reflexes (session_id TEXT, phase TEXT, transaction_id TEXT, "
        "know REAL, uncertainty REAL, reflex_data TEXT, timestamp REAL)"
    )
    cur.execute("CREATE TABLE project_findings (session_id TEXT, created_timestamp REAL)")
    cur.execute("CREATE TABLE project_unknowns (session_id TEXT, created_timestamp REAL)")
    pf = time.time() - 100.0
    check_ts = pf + check_offset_s
    cur.execute(
        "INSERT INTO reflexes VALUES (?,?,?,?,?,?,?)",
        ("sess", "CHECK", "tx1", 0.7, 0.2, '{"decision": "proceed"}', check_ts),
    )
    for _ in range(n_findings):
        cur.execute("INSERT INTO project_findings VALUES (?,?)", ("sess", check_ts + 1.0))
    for _ in range(n_unknowns):
        cur.execute("INSERT INTO project_unknowns VALUES (?,?)", ("sess", check_ts + 1.0))
    return cur, pf


def _validate(monkeypatch, cur, pf, command, work_type):
    monkeypatch.setattr(sg, "_current_work_type", work_type)
    return sg._validate_check_record(cur, "sess", "tx1", pf, tool_input={"command": command}, tool_name="Bash")


def _is_deny(result):
    return isinstance(result, tuple) and len(result) == 2 and result[0] == "deny"


def test_rush_guard_denies_genuine_rubber_stamp(monkeypatch):
    # Anti-rubber-stamp preserved: normal work_type, rushed (<30s), zero
    # artifacts → deny. This is the value the guard exists for.
    cur, pf = _rush_cursor(check_offset_s=11)
    result = _validate(monkeypatch, cur, pf, _PRAXIC, "code")
    assert _is_deny(result) and "Rushed assessment" in result[1]


def test_rush_guard_recoverable_by_post_check_finding(monkeypatch):
    # Fix 2: a finding logged AFTER a rushed CHECK now clears the guard. On the
    # pre-fix frozen window this finding (created_timestamp > check_ts) would not
    # count and the command would still deny — the deadlock.
    cur, pf = _rush_cursor(check_offset_s=11, n_findings=1)
    result = _validate(monkeypatch, cur, pf, _PRAXIC, "code")
    assert not _is_deny(result), result


def test_rush_guard_recoverable_by_post_check_unknown(monkeypatch):
    cur, pf = _rush_cursor(check_offset_s=11, n_unknowns=1)
    result = _validate(monkeypatch, cur, pf, _PRAXIC, "code")
    assert not _is_deny(result), result


def test_rush_guard_exempts_remote_ops_even_with_zero_artifacts(monkeypatch):
    # Fix 3: remote-ops, rushed, zero local artifacts → never denied. The
    # category-error case (nothing local to investigate).
    cur, pf = _rush_cursor(check_offset_s=11)
    result = _validate(monkeypatch, cur, pf, _PRAXIC, "remote-ops")
    assert not _is_deny(result), result


def test_rush_guard_still_passes_unrushed_check(monkeypatch):
    # Long-enough noetic phase (>30s) → guard never engages, regardless of
    # artifacts or work_type.
    cur, pf = _rush_cursor(check_offset_s=45)
    result = _validate(monkeypatch, cur, pf, _PRAXIC, "code")
    assert not _is_deny(result), result
