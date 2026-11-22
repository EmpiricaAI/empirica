"""Discipline mode: read-only probes are not changes, and the workflow verbs flow on any line (goal 329c014e).

2026-11-20, diagnosing a slow machine outside a transaction: `uptime`, `nproc`, `top -b`, `pstree`, `pgrep`, `journalctl`,
`tmux list-panes` and an arithmetic `$(( ))` were each classified unknown and counted as changes, so three probes spent the
per-prompt budget and the fourth read was refused. And `empirica note` beside any other statement was refused outright: the
hook lets a bare workflow verb through, but a compound line goes to the discipline judge, which read `empirica note` as a
change it cannot read, or refused the line for a `{ok}` in a jq filter. David: "its also gating empirica commands like notes
which it should not".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_CORE = _ROOT / "empirica" / "core"
_HOOK = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "sentinel-gate.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dr = _load("_t329_decision_record", _CORE / "decision_record.py")
sd = _load("_t329_sentinel_discipline", _CORE / "sentinel_discipline.py")
gate = _load("_t329_sentinel_gate", _HOOK)
P = "/p"


def _classes(command):
    return dr.infer_shell_effects(command, cwd=P, project_path=P, empirica_is_read=lambda w: False)["classes"]


@pytest.mark.parametrize(
    "command",
    [
        "uptime",
        "nproc",
        "top -b -n 2 -d 3 -o %CPU",
        "pstree -p 1890209",
        "pgrep -af term-browser",
        "journalctl -u cloudflared --since '10 min ago' --no-pager",
        "tmux -S /tmp/tmux-1001/default list-panes -a -F '#{pane_id} #{session_name}'",
        "tmux -L cockpit show-options -g",
        "tmux display-message -p '#{session_name}'",
        'echo "grows $(( (b-a)/5/1024 )) KB/s"',
        "a=$(stat -c %s f); sleep 5; b=$(stat -c %s f); echo $(( b - a ))",
    ],
)
def test_read_only_probes_are_reads(command):
    assert _classes(command) == []


@pytest.mark.parametrize(
    "command",
    [
        "journalctl --vacuum-time=1d",
        "journalctl --rotate",
        "tmux kill-server",
        "tmux -S /tmp/s send-keys -t x 'rm -rf a' Enter",
        "echo $(( $(rm -rf a) + 1 ))",
        "tmux -c 'rm -rf a' list-panes",
        "tmux display-message -p '#(rm -rf a)'",
        "tmux list-panes -F '#(touch x)'",
        # Review of goal 329c014e (all probed):
        "echo '$((' ; rm -rf ~ ; echo '))'",
        'echo "$((" ; rm -rf ~ ; echo "))"',
        "echo \\$(( ; rm -rf ~ ; echo ))",
        "echo $((true) && rm -rf ~ && (true))",
        "tmux list-sessions -F 'x;' kill-server",
        "tmux -v ls",
        "journalctl --rot",
        "journalctl --vac=1d",
        "journalctl --cursor-file=/home/u/.bashrc",
    ],
)
def test_the_changing_forms_are_not_reads(command):
    assert _classes(command) != []


def _judge(command):
    return sd._judge_shell(command, P, P, gate.empirica_command_free_in_discipline)


@pytest.mark.parametrize(
    "command",
    [
        "empirica note \"over-gated reads\" --tag followup >/dev/null 2>&1; empirica sessions-list | jq -c '{ok}'",
        "empirica finding-log --finding \"x\" --impact 0.5 --output json 2>/dev/null | jq -c '{ok}'",
        "cd /p && empirica note \"x\"; tmux -S /tmp/s list-panes -a -F '#{pane_id}' | sort -k4 -n | tail -6; date +%s",
        'empirica mailbox reply --parent-id prop_x --result shipped --summary "$(cat /tmp/r.txt)"',
    ],
)
def test_workflow_verbs_on_a_compound_line_flow_uncounted(command):
    answer = _judge(command)
    assert answer["trivial"] and answer.get("read"), answer


@pytest.mark.parametrize(
    "command",
    [
        "empirica sync-push",
        "empirica rebuild --qdrant",
        "empirica setup-claude-code --force",
        "empirica release --publish",
        "empirica project-init",
        "empirica --config note update-artifacts -",
        "true; empirica -v --config note goals-claim",
        "empirica --verbose --config note project-init",
        "empirica delete-artifacts --apply; date",
        "empirica notes-purge",
        'empirica note "x"; rm -rf /etc/x',
    ],
)
def test_other_empirica_verbs_and_neighbours_are_still_judged(command):
    answer = _judge(command)
    assert not (answer["trivial"] and answer.get("read")), answer


def test_the_predicate_matches_whole_verb_tokens():
    free = gate.empirica_command_free_in_discipline
    assert free("empirica note x") and free("empirica mailbox reply x") and free("empirica preflight-submit -")
    assert not free("empirica notebook x") and not free("empirica mailbox delete x")


def test_the_arithmetic_mask_is_linear_on_hostile_input():
    import time

    start = time.monotonic()
    _classes("echo " + "$((" * 8000)
    assert time.monotonic() - start < 2


# ── 1.17.16 pre-publish sweep ───────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "ssh host empirica note x",
        "ssh host 'empirica mailbox reply --parent-id p --result shipped'",
        "ssh host empirica goals-complete --goal-id g",
    ],
)
def test_a_workflow_verb_on_a_remote_host_is_a_remote_change(command):
    """The free path is for this practice's own record; on another machine it is a remote change (SENTINEL_GATE_REFERENCE)."""
    answer = _judge(command)
    assert not (answer["trivial"] and answer.get("read")), answer


def test_a_remote_empirica_read_is_still_a_remote_read():
    assert _judge("ssh host empirica goals-list")["read"]


@pytest.mark.parametrize(
    "command",
    [
        "date; empirica goals-complete --goal-id g --merge-branch",
        "date; empirica goals-complete --goal-id g --delete-branch",
        "date; empirica source-add --title t --media ./clip.mp4 --visibility shared",
        "empirica goals-complete --goal-id g --m --del; true",
        "date; empirica source-add --title t --med ./clip.mp4 --visibility shared",
        "date; empirica goals-complete --goal-id g --merge=yes",
    ],
)
def test_workflow_verbs_with_git_or_upload_options_are_judged(command):
    answer = _judge(command)
    assert not (answer["trivial"] and answer.get("read")), answer


def test_the_same_verbs_without_those_options_stay_free():
    for command in (
        "date; empirica goals-complete --goal-id g --reason done",
        "date; empirica source-add --title t --url u",
    ):
        answer = _judge(command)
        assert answer["trivial"] and answer.get("read"), (command, answer)
