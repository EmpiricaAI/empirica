"""session-init's context block: read where bootstrap puts it, and say what it found.

ecodex (prop_jqrwilgtgjewlpgmeaqqx3bqbm), from David: every session start printed `(No context loaded)`. `_run_bootstrap`
read goals/findings/unknowns from the TOP level of `project-bootstrap --output json`; they live under `breadcrumbs`,
so format_context always found three empty lists. Claude Code was affected too. Also: "project loaded, nothing
retrieved" and "no context at all" printed the same, and the manual-setup block showed a 4-vector PREFLIGHT that
agents copied, leaving nine vectors unassessed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"

THIRTEEN = [
    "engagement",
    "know",
    "do",
    "context",
    "clarity",
    "coherence",
    "signal",
    "density",
    "state",
    "change",
    "completion",
    "impact",
    "uncertainty",
]


@pytest.fixture
def init():
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("si_bootstrap_context", HOOKS / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _bootstrap_returns(monkeypatch, init, payload: dict):
    monkeypatch.setattr(
        init.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )
    return init._run_bootstrap("sid", {})


REAL_SHAPE = {
    "ok": True,
    "project_id": "p",
    "project_name": "practice",
    "breadcrumbs": {
        "goals": [{"objective": f"goal {i}"} for i in range(5)],
        "findings": [{"finding": f"finding {i}"} for i in range(8)],
        "unknowns": [{"unknown": f"unknown {i}"} for i in range(8)],
    },
}


def test_the_context_is_read_from_breadcrumbs_where_bootstrap_puts_it(init, monkeypatch):
    _, ctx = _bootstrap_returns(monkeypatch, init, REAL_SHAPE)

    assert [g["objective"] for g in ctx["goals"]] == ["goal 0", "goal 1", "goal 2"]
    assert len(ctx["findings"]) == 5 and len(ctx["unknowns"]) == 5


def test_the_older_top_level_shape_still_reads(init, monkeypatch):
    """Control: a payload that really has them at the top level keeps working."""
    _, ctx = _bootstrap_returns(monkeypatch, init, {"goals": [{"objective": "g"}], "findings": [], "unknowns": []})

    assert [g["objective"] for g in ctx["goals"]] == ["g"]


def test_a_real_bootstrap_no_longer_renders_no_context_loaded(init, monkeypatch):
    _, ctx = _bootstrap_returns(monkeypatch, init, REAL_SHAPE)

    text = init.format_context(ctx)

    assert "No context loaded" not in text and "goal 0" in text and "finding 0" in text


def test_a_loaded_project_with_nothing_to_retrieve_says_so_and_differs_from_no_context(init, monkeypatch):
    _, ctx = _bootstrap_returns(
        monkeypatch, init, {"ok": True, "project_id": "p", "breadcrumbs": {"goals": [], "findings": [], "unknowns": []}}
    )

    loaded_empty = init.format_context(ctx)
    none_at_all = init.format_context(None)

    assert "nothing retrieved" in loaded_empty.lower()
    assert loaded_empty != none_at_all and "No context loaded" not in loaded_empty


def test_a_failed_bootstrap_is_still_reported_as_no_context(init, monkeypatch):
    monkeypatch.setattr(
        init.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
    )

    bootstrap, ctx = init._run_bootstrap("sid", {})

    assert bootstrap is None and ctx is None and "No context" in init.format_context(ctx)


def test_the_budget_init_now_sees_the_bootstrap_items(init, monkeypatch):
    """Downstream consumer of the same dict: it registered no goal or finding items while the read was empty."""
    _, ctx = _bootstrap_returns(monkeypatch, init, REAL_SHAPE)
    seen = {}
    from empirica.core import context_budget as cb

    real = cb.ContextBudgetManager.register_item

    def spy(self, item):
        seen[item.id] = item
        return real(self, item)

    monkeypatch.setattr(cb.ContextBudgetManager, "register_item", spy)
    monkeypatch.setattr(cb.ContextBudgetManager, "persist_state", lambda self, db_path=None: True)

    init._init_context_budget("sid", ctx)

    assert any(k.startswith("boot_goal_") for k in seen) and any(k.startswith("boot_finding_") for k in seen)


def test_the_manual_setup_example_shows_all_thirteen_vectors(init, capsys):
    with pytest.raises(SystemExit):
        init._emit_session_error("boom", "ai")

    text = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]

    for name in THIRTEEN:
        assert f'"{name}"' in text, f"the example PREFLIGHT must show {name}"
    assert "all 13" in text.lower()
