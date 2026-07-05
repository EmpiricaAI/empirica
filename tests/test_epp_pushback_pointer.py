"""EPP semantic-pushback injection is a terse pointer, not the full block.

ecodex prop_v4tqe4qe (David-directed): the full ~21-line block was injected on
EVERY substantive UserPromptSubmit — per-prompt token cost fleet-wide + a visible
user-role hook-prompt on harnesses that render additionalContext. It's now a
compact one-line pointer. Kept SEMANTIC (fires every substantive prompt, no
keyword gate) so paraphrase / implicit pushback isn't missed — the case EPP
exists to catch.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path


def _load_hook():
    hook_path = Path(__file__).resolve().parents[1] / ("empirica/plugins/claude-code-integration/hooks/tool-router.py")
    spec = _ilu.spec_from_file_location("tool_router_hook", hook_path)
    assert spec and spec.loader, "could not load hook spec"
    mod = _ilu.module_from_spec(spec)
    sys.modules["tool_router_hook"] = mod
    spec.loader.exec_module(mod)
    return mod


_LONG = "This is a substantive user message that is comfortably over twenty characters."


def test_pointer_returned_for_substantive_prompt():
    mod = _load_hook()
    out = mod.build_semantic_pushback_check(_LONG)
    assert out is not None
    assert "epp-check" in out
    assert "/epistemic-persistence-protocol" in out  # links the full protocol
    # carries the EPP core so the nudge is self-contained
    assert "HOLD" in out and "REFRAME" in out
    assert "cave" in out.lower()


def test_pointer_is_terse_not_the_full_block():
    # Regression guard: the whole point is it stays compact. The old block was
    # ~945 chars / 21 lines; keep the pointer well under that.
    mod = _load_hook()
    out = mod.build_semantic_pushback_check(_LONG)
    assert len(out) < 500
    assert out.count("\n") <= 1  # one-liner, not a multi-line dump


def test_none_below_min_length():
    mod = _load_hook()
    assert mod.build_semantic_pushback_check("ok") is None
    assert mod.build_semantic_pushback_check("too short") is None


def test_none_for_slash_command():
    mod = _load_hook()
    assert mod.build_semantic_pushback_check("/some-skill with a long enough argument tail here") is None


def test_min_length_boundary():
    mod = _load_hook()
    n = mod.SEMANTIC_CHECK_MIN_LENGTH
    assert mod.build_semantic_pushback_check("x" * (n - 1)) is None
    assert mod.build_semantic_pushback_check("x" * n) is not None
