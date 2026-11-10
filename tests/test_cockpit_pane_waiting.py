"""A cockpit pane waiting on its person: marked on a permission dialog or a question, shown on the bar, cleared by what follows (goal dfa8b484).

Claude Code has hooks for a dialog being shown (PermissionRequest; AskUserQuestion's PreToolUse, where ruling-shape runs) and none for it being
answered, so the hooks that run on the pane's next tool, prompt or turn end clear the mark. Those run on nearly every call, so a pane that is not
waiting must cost one stat and never start tmux. The desktop notification goes only to a pane that is marked, so a session outside a cockpit is
never notified.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks"
CLEARING_HOOKS = (
    "entity-extractor",
    "truncation-legibility",
    "tool-failure",
    "context-shift-tracker",
    "transaction-enforcer",
)


def _load(name: str, as_name: str):
    spec = importlib.util.spec_from_file_location(as_name, HOOKS / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pw(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    module = _load("pane_waiting.py", "pane_waiting_under_test")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(module, "_tmux", lambda *a: calls.append(a) or True)
    setattr(module, "calls", calls)  # noqa: B010 - the fake's record, read by the tests
    return module


# ── the mark ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool, label",
    [
        ("AskUserQuestion", "question"),
        ("Bash", "approve Bash"),
        ("mcp__cortex__cortex_propose", "approve mcp__cortex__cortex_propose"),
        ("Bash#{pane_id}", "approve Bashpane_id"),  # a tool name cannot carry a tmux format into the bar
        ("", "approve"),
    ],
)
def test_the_label_names_what_the_pane_waits_for(pw, tool, label):
    assert pw.label_for(tool) == label


def test_marking_sets_the_pane_option_and_the_flag(pw):
    assert pw.set_waiting("%7", "approve Bash") is True
    assert pw.calls == [("set-option", "-p", "-t", "%7", "@empirica_waiting", "approve Bash")]
    assert pw.waiting_label("%7") == "approve Bash"


def test_clearing_removes_both(pw):
    pw.set_waiting("%7", "question")
    pw.calls.clear()

    assert pw.clear_if_waiting("%7") is True
    assert pw.calls == [("set-option", "-p", "-u", "-t", "%7", "@empirica_waiting")]
    assert pw.waiting_label("%7") is None


def test_a_pane_that_is_not_waiting_never_starts_tmux(pw):
    assert pw.clear_if_waiting("%7") is False
    assert pw.calls == []


@pytest.mark.parametrize("pane", ["7", "%7; rm -rf x", "%../../etc", "", None, "%"])
def test_a_malformed_pane_id_is_ignored(pw, pane):
    assert pw.set_waiting(pane, "question") is False
    assert pw.clear_if_waiting(pane) is False
    assert pw.calls == []


# ── the hook ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def hook():
    return _load("pane-waiting.py", "pane_waiting_hook_under_test")


def test_a_permission_dialog_marks_the_pane(hook, pw, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%3")

    assert hook.handle({"hook_event_name": "PermissionRequest", "tool_name": "Bash"}, pw) == "marked"
    assert pw.waiting_label("%3") == "approve Bash"


def test_an_unanswered_dialog_notifies_a_marked_pane(hook, pw, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%3")
    sent = []
    monkeypatch.setattr(pw, "notify", lambda title, body: sent.append((title, body)) or True)
    pw.set_waiting("%3", "approve Bash")

    out = hook.handle(
        {
            "hook_event_name": "Notification",
            "notification_type": "permission_prompt",
            "message": "needs permission",
            "cwd": "/w/outreach",
        },
        pw,
    )

    assert out == "notified" and sent == [("outreach: approve Bash", "needs permission")]


@pytest.mark.parametrize(
    "why, env, payload, marked",
    [
        (
            "an idle reminder is not a dialog",
            "%3",
            {"hook_event_name": "Notification", "notification_type": "idle_prompt"},
            True,
        ),
        (
            "a pane that is not marked",
            "%3",
            {"hook_event_name": "Notification", "notification_type": "permission_prompt"},
            False,
        ),
        ("a session outside tmux", None, {"hook_event_name": "PermissionRequest", "tool_name": "Bash"}, False),
        ("another event", "%3", {"hook_event_name": "PreToolUse", "tool_name": "Bash"}, False),
    ],
)
def test_it_stays_quiet_where_nobody_is_waiting(hook, pw, monkeypatch, why, env, payload, marked):
    if env:
        monkeypatch.setenv("TMUX_PANE", env)
    else:
        monkeypatch.delenv("TMUX_PANE", raising=False)
    sent = []
    monkeypatch.setattr(pw, "notify", lambda *a: sent.append(a) or True)
    if marked:
        pw.set_waiting("%3", "approve Bash")
    pw.calls.clear()

    assert hook.handle(payload, pw) == "", why
    assert sent == [] and pw.calls == [], why


def test_the_hook_process_prints_nothing_and_exits_0(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "TMUX_PANE": "%999999", "PATH": "/nonexistent"}
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "pane-waiting.py")],
        input=json.dumps({"hook_event_name": "PermissionRequest", "tool_name": "Bash"}),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0 and proc.stdout == ""


# ── clearing, from the hooks that run next ───────────────────────────────────────────────────────────────────────────────


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(path.read_text(), fn) or ""


TOOL_HOOKS = ("entity-extractor", "truncation-legibility", "tool-failure")


def test_every_clearing_hook_carries_the_same_function_and_calls_it_before_its_work():
    """A prompt or the turn ending clears first, with no payload; a tool hook clears right after reading its payload and
    passes it, so only the dialog's own call clears the mark."""
    sources = {name: _function_source(HOOKS / f"{name}.py", "_clear_pane_waiting") for name in CLEARING_HOOKS}
    assert len(set(sources.values())) == 1, "the copies differ"
    for name in CLEARING_HOOKS:
        tree = ast.parse((HOOKS / f"{name}.py").read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        if name in TOOL_HOOKS:
            assert ast.unparse(main.body[1]).startswith("_clear_pane_waiting("), name
            assert ast.unparse(main.body[1]) != "_clear_pane_waiting()", name
        else:
            assert ast.unparse(main.body[0]) == "_clear_pane_waiting()", name


def test_a_real_hook_process_clears_a_mark(tmp_path):
    """truncation-legibility, run as Claude Code runs it: the flag goes even when tmux has no such pane."""
    flags = tmp_path / ".empirica" / "pane_waiting"
    flags.mkdir(parents=True)
    (flags / "4242").write_text('{"label": "question"}')
    env = {**os.environ, "HOME": str(tmp_path), "TMUX_PANE": "%4242"}
    subprocess.run(
        [sys.executable, str(HOOKS / "truncation-legibility.py")],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {"stdout": ""}}),
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert not (flags / "4242").exists()


def test_ruling_shape_marks_a_question():
    tree = ast.parse((HOOKS / "ruling-shape.py").read_text())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    assert "_mark_question()" in ast.unparse(main)


# ── setup and the bar ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_setup_registers_the_hook_on_both_events(tmp_path):
    from empirica.cli.command_handlers.setup_claude_code import _register_all_hooks

    settings: dict = {}
    _register_all_hooks(settings, tmp_path / "plugin", "python3", "json")

    for event in ("PermissionRequest", "Notification"):
        commands = [h["command"] for e in settings["hooks"][event] for h in e["hooks"]]
        assert any(c.endswith("hooks/pane-waiting.py") for c in commands), event


def test_the_bar_draws_the_mark_from_the_pane_option():
    from empirica.core.cockpit.bar import commands_format, tabs_format

    assert "@empirica_waiting" in tabs_format()
    assert "waiting: #{@empirica_waiting}" in commands_format()


def test_a_permission_dialog_is_written_to_the_decision_record_in_or_out_of_a_cockpit(hook, pw, tmp_path, monkeypatch):
    """The record's permission_request line is what infer_overrides reads an answer from; it had no caller before."""
    monkeypatch.delenv("TMUX_PANE", raising=False)
    practice = tmp_path / "practice"
    (practice / ".empirica").mkdir(parents=True)
    (practice / ".empirica" / "project.yaml").write_text("name: practice\n")

    hook.handle(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "cwd": str(practice),
            "session_id": "s-1",
        },
        pw,
    )

    lines = [json.loads(x) for x in (practice / ".empirica" / "decisions.jsonl").read_text().splitlines()]
    assert [(r["event"], r["tool"]) for r in lines] == [("permission_request", "Bash")]
    assert "git push" not in json.dumps(lines), "the input is kept only as a hash"


# ── a parallel call does not clear another call's dialog (1.14.23 sweep, item 6) ─────────────────────────────────────────

DIALOG = {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "git push"}}


def _post(tool_name, tool_input, event="PostToolUse", **extra):
    return {"hook_event_name": event, "tool_name": tool_name, "tool_input": tool_input, **extra}


def test_a_parallel_call_finishing_leaves_the_dialog_s_mark(hook, pw, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%3")
    hook.handle(DIALOG, pw)
    assert pw.clear_if_waiting("%3", _post("Read", {"file_path": "/x"})) is False
    assert pw.clear_if_waiting("%3", _post("Bash", {"command": "ls"})) is False
    assert pw.waiting_label("%3") == "approve Bash"


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure"])
def test_the_dialog_s_own_call_finishing_or_failing_clears_it(hook, pw, monkeypatch, event):
    monkeypatch.setenv("TMUX_PANE", "%3")
    hook.handle(DIALOG, pw)
    assert pw.clear_if_waiting("%3", _post("Bash", {"command": "git push"}, event)) is True


def test_a_matching_tool_use_id_clears_and_a_different_one_does_not(hook, pw, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%3")
    hook.handle({**DIALOG, "tool_use_id": "toolu_1"}, pw)
    assert pw.clear_if_waiting("%3", _post("Bash", {"command": "git push"}, tool_use_id="toolu_2")) is False
    assert pw.clear_if_waiting("%3", _post("Bash", {"command": "other"}, tool_use_id="toolu_1")) is True


@pytest.mark.parametrize("payload", [None, {"hook_event_name": "UserPromptSubmit"}, {"hook_event_name": "Stop"}])
def test_a_prompt_or_the_turn_ending_clears_whatever_set_it(hook, pw, monkeypatch, payload):
    monkeypatch.setenv("TMUX_PANE", "%3")
    hook.handle(DIALOG, pw)
    assert pw.clear_if_waiting("%3", payload) is True


def test_a_mark_written_before_owners_were_recorded_still_clears_on_any_call(pw):
    pw.flag_path("%3").parent.mkdir(parents=True, exist_ok=True)
    pw.flag_path("%3").write_text('{"label": "question", "ts": 1}')
    assert pw.clear_if_waiting("%3", _post("Read", {"file_path": "/x"})) is True


def test_a_real_hook_process_for_another_call_leaves_the_mark(tmp_path):
    flags = tmp_path / ".empirica" / "pane_waiting"
    flags.mkdir(parents=True)
    (flags / "4243").write_text(json.dumps({"label": "approve Bash", "owner": {"tool": "Bash", "input": "0" * 16}}))
    env = {**os.environ, "HOME": str(tmp_path), "TMUX_PANE": "%4243"}
    subprocess.run(
        [sys.executable, str(HOOKS / "entity-extractor.py")],
        input=json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "/x"}}),
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert (flags / "4243").exists()
