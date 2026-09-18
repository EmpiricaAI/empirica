"""Truncation is flagged where the model reads, and says what it cannot see.

prop_mysjzbnpvjezfb2s6eokgnnn6q (empirica-outreach): a view truncated by the
reader's own `| head -40` reads as the whole output, and a paged response read
through `| jq` loses the fields that said it was a page. Both are detectable;
filtered and wrong-population views are not, and the notice must say so.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "truncation-legibility.py"


@pytest.fixture
def hook():
    spec = importlib.util.spec_from_file_location("truncation_legibility", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TMUX_PANE", "%91")
    (tmp_path / ".empirica").mkdir()
    return tmp_path


def _lines(n: int) -> str:
    return "".join(f"line {i}\n" for i in range(n))


def test_asked_forty_got_forty_is_flagged(hook, home):
    out = hook.notices("empirica setup-claude-code --help | head -40", _lines(40))
    assert len(out) == 1 and "exactly 40 lines" in out[0]


def test_asked_forty_got_twelve_is_quiet(hook, home):
    assert hook.notices("rg -n foo src | head -40", _lines(12)) == []


def test_compound_lines_are_not_judged(hook, home):
    """`a; b | head -5` mixes a's output in; a line count proves nothing."""
    assert hook.self_limit("ruff check x && pytest -q | head -5") is None
    assert hook.self_limit("echo a\nls | head -5") is None


def test_tail_and_unpiped_head_are_not_judged(hook):
    assert hook.self_limit("pytest -q 2>&1 | tail -5") is None
    assert hook.self_limit("head -5 README.md") is None


def test_sed_ranges_and_bare_head(hook):
    assert hook.self_limit("git log | sed -n '1,20p'") == ("sed -n '1,20p'", 20)
    assert hook.self_limit("ls | head") == ("head", 10)
    assert hook.self_limit("ls | head -n 7") == ("head -n 7", 7)


def test_declared_page_in_stdout_is_flagged(hook, home):
    out = hook.notices("gh api x", json.dumps({"count": 20, "matched": 118, "has_more": True}))
    assert len(out) == 1 and "has_more" in out[0] and "matched 118 > returned 20" in out[0]


def test_a_page_read_through_jq_is_still_flagged(hook, home):
    """The CLI's record survives the jq filter that dropped the fields."""
    from empirica.utils import partial_view

    assert partial_view.record("mailbox poll", {"count": 20, "matched": 118, "has_more": True})
    out = hook.notices("empirica mailbox poll --output json | jq '.count'", "20\n")
    assert len(out) == 1 and "20 of 118" in out[0]
    # consumed: reported once
    assert hook.notices("empirica mailbox poll --output json | jq '.count'", "20\n") == []


def test_a_complete_response_writes_no_record(home):
    from empirica.utils import partial_view

    assert not partial_view.record("goals-list", {"goals_count": 3, "total_matching": 3, "truncated": False})
    assert not list((home / ".empirica").glob("partial_view*"))


def test_goals_list_truncation_is_recognised():
    from empirica.utils import partial_view

    gap = partial_view.completeness_gap({"goals_count": 20, "total_matching": 57, "truncated": True, "limit": 20})
    assert gap == {"returned": 20, "matched": 57, "truncated": True, "limit": 20}


def test_a_stale_record_is_ignored(hook, home):
    from empirica.utils.session_resolver import _get_instance_suffix

    path = home / ".empirica" / f"partial_view{_get_instance_suffix()}.json"
    path.write_text(json.dumps({"verb": "x", "ts": 1.0, "returned": 1, "matched": 9}))
    assert hook.notices("empirica x | jq .", "1\n") == []


def test_every_notice_states_its_reach(home):
    """Silence must not read as completeness, so the notice names what it cannot see."""
    env = {**os.environ, "HOME": str(home), "TMUX_PANE": "%91"}
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "rg -n x src | head -3"},
        "tool_response": {"stdout": _lines(3), "stderr": ""},
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUse"
    assert "exactly 3 lines" in ctx["additionalContext"]
    assert "Not checkable here" in ctx["additionalContext"]
    assert "permissionDecision" not in ctx


def test_quiet_calls_print_nothing(home):
    env = {**os.environ, "HOME": str(home), "TMUX_PANE": "%91"}
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {"stdout": "a\nb\n"}}
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30
    )
    assert proc.returncode == 0 and proc.stdout == ""


def test_the_cli_core_records_returned_results():
    import inspect

    from empirica.cli import cli_core

    assert "partial_view.record(" in inspect.getsource(cli_core._handle_command_result)
