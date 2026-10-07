"""session-init decides resume vs startup from the field Claude Code actually sends (`source`), and main() reaches the resume branch.

Phase-1 sweep finding H1 (hooks, high): main() read hook_input['type'] with a default of 'startup'. Claude Code's SessionStart payload carries
`source` (startup | resume | clear | compact); _unpersisted_reason in the same file, the post-compact hook and their tests all read `source`.
With no `type` in the payload every start looked like a startup, so `claude --resume` ran startup adoption and created a NEW Empirica
session, and _handle_resume_path (tested only by direct calls) could not be reached. The change is deliberately narrow: only an exact
'resume' changes behaviour; every other value (clear, compact, unknown, missing) keeps today's startup handling.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks"


class _Reached(Exception):
    def __init__(self, name):
        self.name = name


@pytest.fixture
def si(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("session_init_start_event", HOOKS / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"session-init.py not importable here: {exc}")
    monkeypatch.setattr(mod, "_auto_sync_plugin", lambda: None)
    monkeypatch.setattr(mod, "find_project_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(mod, "_resolve_ai_id_for_session", lambda root: "ai")
    monkeypatch.setattr(mod, "_run_stale_cleanup", lambda sid: None)
    monkeypatch.setattr(mod, "archive_stale_plans", lambda: [])
    monkeypatch.setattr(mod, "_try_cwd_adoption", lambda: (_ for _ in ()).throw(_Reached("startup_adoption")))
    monkeypatch.setattr(mod, "_handle_resume_path", lambda *a, **k: (_ for _ in ()).throw(_Reached("resume_path")))
    monkeypatch.setattr(
        mod, "_handle_orphan_adoption", lambda *a, **k: (_ for _ in ()).throw(_Reached("orphan_adoption"))
    )
    monkeypatch.chdir(tmp_path)
    return mod


def _run(si, monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(_Reached) as reached:
        si.main()
    return reached.value.name


@pytest.mark.parametrize(
    ("payload", "event"),
    [
        ({"source": "resume"}, "resume"),
        ({"type": "resume"}, "resume"),  # the legacy field still works
        ({"source": "resume", "type": "startup"}, "resume"),  # source wins, as in _unpersisted_reason
        ({"source": " Resume "}, "resume"),
        ({"source": "startup"}, "startup"),
        ({"source": "clear"}, "startup"),  # unchanged: only 'resume' changes behaviour
        ({"source": "compact"}, "startup"),
        ({"source": "something-new"}, "startup"),
        ({"source": None, "type": None}, "startup"),
        ({}, "startup"),
    ],
)
def test_the_start_event_is_read_from_source_then_type(si, payload, event):
    assert si._session_start_event(payload) == event


def test_a_resume_payload_reaches_the_resume_path_from_main(si, monkeypatch):
    assert _run(si, monkeypatch, {"session_id": "c1", "source": "resume"}) == "resume_path"


def test_a_resume_payload_does_not_run_startup_adoption_first(si, monkeypatch):
    """The reached branch is the resume one: _try_cwd_adoption (startup only) would have raised its own marker."""
    assert _run(si, monkeypatch, {"session_id": "c1", "source": "resume"}) != "startup_adoption"


@pytest.mark.parametrize(
    "payload",
    [{"session_id": "c1", "source": "startup"}, {"session_id": "c1"}, {"session_id": "c1", "source": "clear"}],
)
def test_a_startup_payload_still_takes_the_startup_path(si, monkeypatch, payload):
    assert _run(si, monkeypatch, payload) == "startup_adoption"
