"""_resolve_project_and_setup in post-compact.py — n-gated filesystem fallback + graceful exit.

Regression guard for the ecodex instance-isolation design-intent fix
(prop_upsyzrpgg). A fresh practitioner (empty instance_projects cache) must
resolve the practice from the filesystem when the harness declares CWD is the
verified practice (``n=true``), and must exit 0 — not 1 — when nothing resolves:
a session-boundary hook with no practice is a no-op, not a failure. The ``n``
gate keeps CWD/git fallback OFF in a multiplexer where CWD is the empirica launch
dir rather than the user's project (KNOWN_ISSUES 11.10 cross-project bleed).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HOOK_DIR = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
# post-compact.py inserts its sibling lib on sys.path at import; do the same so
# `from project_resolver import ...` resolves during exec_module.
sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
_spec = importlib.util.spec_from_file_location("post_compact_hook", _HOOK_DIR / "post-compact.py")
assert _spec is not None and _spec.loader is not None
post_compact = importlib.util.module_from_spec(_spec)
sys.modules["post_compact_hook"] = post_compact
_spec.loader.exec_module(post_compact)

_resolve = post_compact._resolve_project_and_setup


def test_cwd_fallback_enabled_when_n_true(monkeypatch, tmp_path):
    """n=true → the harness vouches for CWD → filesystem fallback is enabled."""
    monkeypatch.setenv("EMPIRICA_CWD_RELIABLE", "true")
    with (
        patch.object(post_compact, "find_project_root", return_value=tmp_path) as fpr,
        patch.object(post_compact.os, "chdir"),
        patch.object(post_compact, "get_instance_id", return_value="i"),
    ):
        _resolve("cc-1")
    kwargs = fpr.call_args.kwargs
    assert kwargs["allow_cwd_fallback"] is True
    assert kwargs["allow_git_root"] is True


def test_cwd_fallback_off_when_n_unset(monkeypatch, tmp_path):
    """No n → CWD is untrusted (could be the launch dir) → fallback stays off."""
    monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
    with (
        patch.object(post_compact, "find_project_root", return_value=tmp_path) as fpr,
        patch.object(post_compact.os, "chdir"),
        patch.object(post_compact, "get_instance_id", return_value="i"),
    ):
        _resolve("cc-1")
    kwargs = fpr.call_args.kwargs
    assert kwargs["allow_cwd_fallback"] is False
    assert kwargs["allow_git_root"] is False


def test_unresolved_exits_zero_not_one(monkeypatch):
    """Nothing resolves → graceful no-op (exit 0), not a 'hook failed' (exit 1)."""
    monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
    with (
        patch.object(post_compact, "find_project_root", return_value=None),
        pytest.raises(SystemExit) as exc,
    ):
        _resolve("cc-fresh")
    assert exc.value.code == 0
