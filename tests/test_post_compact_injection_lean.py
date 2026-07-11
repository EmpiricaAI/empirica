"""Guards the post-compact injection surface against re-bloat (epistemic hygiene).

The post-compact prompt must inject only DYNAMIC data (vectors, last_task, focus,
memory) + a terse action cue — NOT re-teach discipline or re-inject command
templates that already live permanently in the system prompts + skills (they're
re-loaded every turn, so duplicating them just burns attention budget).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


def _load_hook():
    import sys

    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("post_compact_hook", _HOOK_DIR / "post-compact.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# Markers that MUST NOT appear — they duplicate the system prompt / skills.
_REDUNDANT = (
    "preflight-submit - <<",  # the bash template (CORE COMMANDS has the verb)
    "check-submit - <<",
    "What do you ACTUALLY know",  # the per-vector glossary (13-vectors table)
    "Key principle:",  # discipline re-explanation
    "### Step 1:",  # numbered command walkthroughs
)

_CTX = {
    "recent_findings": [],
    "open_unknowns": [],
    "critical_dead_ends": [],
    "active_goals": [],
    "pending_subtasks": [],
    "last_task": "x",
}


def test_new_session_prompt_is_lean():
    m = _load_hook()
    out = m._generate_new_session_prompt(
        pre_vectors={"know": 0.9, "uncertainty": 0.1},
        dynamic_context=dict(_CTX),
        old_session_id="cc5778ea",
        ai_id="empirica",
        session_bootstrap={"session_id": "new-1", "memory_context": None},
    )
    for marker in _REDUNDANT:
        assert marker not in out, f"post-compact re-injects prompt-layer content: {marker!r}"
    assert "PREFLIGHT" in out and "new-1" in out  # keeps the action cue + dynamic id


def test_check_prompt_is_lean():
    m = _load_hook()
    out = m._generate_check_prompt(
        pre_vectors={"know": 0.6, "uncertainty": 0.4},
        pre_reasoning="r",
        dynamic_context={**_CTX, "session_context": {"session_id": "s-1"}},
    )
    for marker in _REDUNDANT:
        assert marker not in out, f"post-compact CHECK re-injects prompt-layer content: {marker!r}"
    assert "CHECK" in out  # keeps the action cue


def test_new_session_prompt_keeps_dynamic_data():
    m = _load_hook()
    out = m._generate_new_session_prompt(
        pre_vectors={"know": 0.9, "uncertainty": 0.08},
        dynamic_context={**_CTX, "last_task": "audit injection surfaces"},
        old_session_id="cc5778ea",
        ai_id="empirica",
        session_bootstrap={"session_id": "new-1", "memory_context": None},
    )
    # the payload the surface EXISTS to carry — vectors + last_task must survive
    assert "know=0.9" in out and "audit injection surfaces" in out
