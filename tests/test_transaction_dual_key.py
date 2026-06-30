"""B3: transaction-file dual-key resolution (instance suffix → durable claude_session_id).

The sentinel firewall resolves the active transaction from
``active_transaction{suffix}.json`` to decide whether a praxic action is gated.
The suffix (tmux_N / pts-N) is EPHEMERAL — it rotates across compaction and
isn't always inherited. The empirica ``session_id`` fallback ALSO rotates per
compact window. ``claude_session_id`` is the durable practitioner key.

These tests assert the dual-key cutover: writers store ``claude_session_id``,
and ``_find_transaction_file`` resolves by it (preferred), so a transaction
stays resolvable across suffix/session rotation — and the exact-suffix primary
path is unchanged, so the firewall never regresses.

The firewall hook (sentinel-gate.py) carries its own standalone copy of
``_find_transaction_file``; ``test_hook_resolver_mirrors_package`` asserts that
copy has identical dual-key behaviour.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from empirica.utils import session_resolver as sr
from empirica.utils.session_resolver import _find_transaction_file, write_active_transaction


def _write_tx(empirica_dir: Path, suffix: str, **fields):
    """Write a transaction file directly (bypassing the writer) for resolver tests."""
    empirica_dir.mkdir(parents=True, exist_ok=True)
    path = empirica_dir / f"active_transaction{suffix}.json"
    path.write_text(json.dumps(fields))
    return path


# ---- write_active_transaction: stores + preserves the durable key -------


def test_write_stores_claude_session_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_get_instance_suffix", lambda: "_tmux_1")
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    write_active_transaction("tx-1", session_id="es-1", project_path=str(proj), claude_session_id="cc-1")

    data = json.loads((proj / ".empirica" / "active_transaction_tmux_1.json").read_text())
    assert data["claude_session_id"] == "cc-1"
    assert data["transaction_id"] == "tx-1"


def test_write_preserves_cc_when_none(tmp_path, monkeypatch):
    """A status-update write (e.g. POSTFLIGHT) with no claude_session_id must
    not drop the durable key established at PREFLIGHT."""
    monkeypatch.setattr(sr, "_get_instance_suffix", lambda: "_tmux_1")
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    write_active_transaction("tx-1", session_id="es-1", project_path=str(proj), claude_session_id="cc-1")
    # Re-write with status update, no claude_session_id supplied
    write_active_transaction("tx-1", session_id="es-1", status="closed", project_path=str(proj))

    data = json.loads((proj / ".empirica" / "active_transaction_tmux_1.json").read_text())
    assert data["claude_session_id"] == "cc-1"  # preserved
    assert data["status"] == "closed"


# ---- _find_transaction_file: dual-key resolution ------------------------


def test_find_exact_suffix_unchanged(tmp_path):
    """Primary path: exact suffix match still wins (firewall must not regress)."""
    ed = tmp_path / ".empirica"
    p = _write_tx(ed, "_tmux_1", transaction_id="tx-1", claude_session_id="cc-1")
    assert _find_transaction_file(ed, "_tmux_1", None, "cc-1") == p


def test_find_by_claude_session_after_suffix_rotation(tmp_path):
    """The core B3 win: the suffix rotated (tmux_1 → tmux_9) but the durable
    claude_session_id still resolves the transaction."""
    ed = tmp_path / ".empirica"
    p = _write_tx(ed, "_tmux_1", transaction_id="tx-1", session_id="es-1", claude_session_id="cc-1")
    # Current instance is now tmux_9 (rotated); resolve by the durable key.
    assert _find_transaction_file(ed, "_tmux_9", None, "cc-1") == p


def test_find_prefers_cc_when_empirica_session_rotated(tmp_path):
    """The empirica session_id rotated across compaction; the cc field still
    resolves it where a pure session_id scan would miss."""
    ed = tmp_path / ".empirica"
    p = _write_tx(ed, "_tmux_1", transaction_id="tx-1", session_id="es-OLD", claude_session_id="cc-1")
    # session_id we know now is es-NEW (rotated) — only cc-1 matches.
    assert _find_transaction_file(ed, "_tmux_9", "es-NEW", "cc-1") == p


def test_find_backward_compat_session_id(tmp_path):
    """A pre-B3 transaction file (no claude_session_id field) still resolves by
    the empirica session_id fallback — no regression for in-flight transactions."""
    ed = tmp_path / ".empirica"
    p = _write_tx(ed, "_tmux_1", transaction_id="tx-1", session_id="es-1")  # no cc field
    assert _find_transaction_file(ed, "_tmux_9", "es-1", None) == p


def test_find_no_keys_returns_none(tmp_path):
    ed = tmp_path / ".empirica"
    _write_tx(ed, "_tmux_1", transaction_id="tx-1", claude_session_id="cc-1")
    # Suffix mismatch + no keys to scope the scan → no match (no cross-talk).
    assert _find_transaction_file(ed, "_tmux_9", None, None) is None


def test_find_no_crosstalk_between_practitioners(tmp_path):
    """The scan is scoped by the key — another practitioner's file is not returned."""
    ed = tmp_path / ".empirica"
    _write_tx(ed, "_tmux_1", transaction_id="tx-other", claude_session_id="cc-OTHER")
    assert _find_transaction_file(ed, "_tmux_9", None, "cc-MINE") is None


# ---- prefer OPEN over stale CLOSED (the B3-slice-1 regression) -----------


def test_find_prefers_open_over_closed_same_cc(tmp_path):
    """The bug: a CC session accumulates one tx file per past (closed) transaction
    plus the current open one, all sharing claude_session_id. The resolver must
    NOT return the stale closed file just because it sorts first."""
    ed = tmp_path / ".empirica"
    # 'aaa' sorts before 'zzz' — the closed file is encountered first.
    _write_tx(ed, "_aaa", transaction_id="T-CLOSED", claude_session_id="cc-1", status="closed")
    open_p = _write_tx(ed, "_zzz", transaction_id="T-OPEN", claude_session_id="cc-1", status="open")
    assert _find_transaction_file(ed, "_tmux_9", None, "cc-1") == open_p


def test_find_returns_closed_when_only_closed(tmp_path):
    """Read-of-closed is preserved: if the only cc-match is closed (no open
    transaction), it's still resolvable (read_active_transaction_full needs it)."""
    ed = tmp_path / ".empirica"
    closed_p = _write_tx(ed, "_aaa", transaction_id="T-CLOSED", claude_session_id="cc-1", status="closed")
    assert _find_transaction_file(ed, "_tmux_9", None, "cc-1") == closed_p


def test_find_prefers_most_recent_open(tmp_path):
    """Among open cc-matches, the most recently updated wins."""
    ed = tmp_path / ".empirica"
    _write_tx(ed, "_aaa", transaction_id="T-OLD", claude_session_id="cc-1", status="open", updated_at=100.0)
    new_p = _write_tx(ed, "_bbb", transaction_id="T-NEW", claude_session_id="cc-1", status="open", updated_at=200.0)
    assert _find_transaction_file(ed, "_tmux_9", None, "cc-1") == new_p


# ---- firewall hook copy mirrors the package -----------------------------


def _load_hook_module():
    """Import sentinel-gate.py (hyphenated → importlib) for the firewall-path test.

    main() is guarded behind ``if __name__ == '__main__'`` so import is
    side-effect-free. If the hook's plugin-lib imports can't resolve in the test
    env, skip — the package copy above is the authoritative logic and the hook
    copy is a literal mirror.
    """
    hook_path = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"
    if not hook_path.exists():
        pytest.skip("sentinel-gate.py not found")
    spec = importlib.util.spec_from_file_location("sentinel_gate_under_test", hook_path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # plugin-lib import path not available in this env
        pytest.skip(f"sentinel-gate.py not importable here: {e}")
    return module


def test_hook_resolver_mirrors_package(tmp_path):
    """The firewall's own _find_transaction_file resolves by the durable
    claude_session_id after suffix rotation, identically to the package."""
    hook = _load_hook_module()
    ed = tmp_path / ".empirica"
    p = _write_tx(ed, "_tmux_1", transaction_id="tx-1", session_id="es-OLD", claude_session_id="cc-1")
    # Rotated suffix + rotated empirica session — only the durable cc matches.
    assert hook._find_transaction_file(ed, "_tmux_9", "es-NEW", "cc-1") == p
    # Exact-suffix primary path still wins.
    assert hook._find_transaction_file(ed, "_tmux_1", None, "cc-1") == p
    # No keys → no cross-talk.
    assert hook._find_transaction_file(ed, "_tmux_9", None, None) is None


# ---- read_active_transaction_full: self-sources cc when caller passes none ----
# 1.12.10: CLI verbs (check-submit, postflight) call R.transaction_id() /
# transaction_read() with no cc. The resolver must self-source the durable key
# via get_claude_session_id() so the active transaction survives a compaction
# that rotated the instance suffix — else CHECK stores UNBOUND and the firewall
# blocks praxic despite an OPEN transaction.


def test_read_full_self_sources_cc_after_suffix_rotation(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    ed = proj / ".empirica"
    _write_tx(ed, "_tmux_1", transaction_id="tx-1", session_id="es-1", claude_session_id="cc-1", status="open")
    # Compaction rotated the suffix; the tty-anchored cc is still recoverable.
    monkeypatch.setattr(sr, "_get_instance_suffix", lambda: "_tmux_9")
    monkeypatch.setattr(sr, "get_claude_session_id", lambda: "cc-1")
    monkeypatch.setattr(sr, "get_active_project_path", lambda cc=None: str(proj))

    data = sr.read_active_transaction_full()  # no cc passed — the regression path
    assert data is not None and data["transaction_id"] == "tx-1"


def test_read_full_no_cc_recoverable_returns_none(tmp_path, monkeypatch):
    """No passed cc AND none recoverable + suffix rotated → None, gracefully."""
    proj = tmp_path / "proj"
    ed = proj / ".empirica"
    _write_tx(ed, "_tmux_1", transaction_id="tx-1", claude_session_id="cc-1", status="open")
    monkeypatch.setattr(sr, "_get_instance_suffix", lambda: "_tmux_9")
    monkeypatch.setattr(sr, "get_claude_session_id", lambda: None)
    monkeypatch.setattr(sr, "get_active_project_path", lambda cc=None: str(proj))
    assert sr.read_active_transaction_full() is None


def test_read_full_explicit_cc_not_overridden_by_self_source(tmp_path, monkeypatch):
    """An explicitly-passed cc is used as-is; self-source only fills None."""
    proj = tmp_path / "proj"
    ed = proj / ".empirica"
    _write_tx(ed, "_tmux_1", transaction_id="tx-mine", claude_session_id="cc-mine", status="open")
    monkeypatch.setattr(sr, "_get_instance_suffix", lambda: "_tmux_9")
    monkeypatch.setattr(sr, "get_claude_session_id", lambda: "cc-other")  # must NOT be used
    monkeypatch.setattr(sr, "get_active_project_path", lambda cc=None: str(proj))
    data = sr.read_active_transaction_full(claude_session_id="cc-mine")
    assert data is not None and data["transaction_id"] == "tx-mine"
