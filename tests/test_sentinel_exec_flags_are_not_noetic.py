"""Flags that make a read-only tool RUN a program must gate (goal 24fc4079, sweep finding 6f34ed7c).

Measured 2026-10-26 against the real classifier: `git diff --ext-diff`, `git diff --textconv` and `man -C <file>` all classified as safe
(noetic), although each names a program the tool will run: `--ext-diff` the `diff.external` command, `--textconv` the driver's textconv
command, and a man-db config file (`-C`) can DEFINE the pager/nroff/compressor it executes. The default invocations stay safe; only the
flags that opt in to execution gate. `man -P`, an inline `LESSOPEN=` and an inline `GIT_EXTERNAL_DIFF=` were already caught.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("sentinel_gate_exec_flags", _HOOK_DIR / "sentinel-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "command",
    [
        "git diff --ext-diff HEAD~1",
        "git log -p --ext-diff -1",
        "git show --ext-diff HEAD",
        "git diff --textconv HEAD~1",
        "git show --textconv HEAD:README.md",
        "man -C /tmp/man.conf ls",
        "man --config-file=/tmp/man.conf ls",
        "man --config-file /tmp/man.conf ls",
    ],
)
def test_a_flag_that_names_a_program_to_run_is_not_noetic(gate, command):
    assert gate.is_safe_bash_command({"command": command}) is False, command


@pytest.mark.parametrize(
    "command",
    [
        "git diff HEAD~1",
        "git diff --stat",
        "git log -p -1",
        "git show HEAD:README.md",
        "git diff --no-ext-diff HEAD~1",
        "man ls",
        "man -k sentinel",
        "man 5 passwd",
    ],
)
def test_the_default_invocations_stay_safe(gate, command):
    """Control: the guard is on the opt-in flags, not on the tools, so ordinary reading is not over-gated."""
    assert gate.is_safe_bash_command({"command": command}) is True, command


@pytest.mark.parametrize("command", ["man -P 'sh -c id' ls", "GIT_EXTERNAL_DIFF=/tmp/x git diff"])
def test_the_already_caught_forms_stay_caught(gate, command):
    assert gate.is_safe_bash_command({"command": command}) is False, command


# ── tmux under a self-declared infra work_type (David's ruling 2026-10-26) ──────────────────────────────────────
#
# INFRA_SAFE_PREFIXES carried a blanket "tmux " ("Tmux full access"), so under work_type infra/config/debug/remote-ops `tmux send-keys
# '<cmd>' Enter` ran any command unprompted: work_type is declared by the same AI the gate constrains. The read verbs stay safe in every
# work type; what executes or changes a session gates. (Probed on tmux 3.6: `#(shell)` in display-message -p and list-* -F formats does
# NOT run a job, so format expansion is not a way around this.)

INFRA_TMUX_MUTATING = [
    "tmux send-keys 'rm -rf x' Enter",
    "tmux send-keys -t 0 'id' C-m",
    "tmux run-shell 'id'",
    "tmux pipe-pane -t 0 'cat > /tmp/x'",
    "tmux new-session -d 'id'",
    "tmux new-window 'id'",
    "tmux split-window 'id'",
    "tmux source-file /tmp/x.conf",
    "tmux kill-server",
]
INFRA_TMUX_READS = [
    "tmux list-sessions",
    "tmux list-clients",
    "tmux list-keys",
    "tmux show-environment",
    "tmux show-messages",
    "tmux has-session -t main",
    "tmux capture-pane -p -t 0",
]


@pytest.mark.parametrize("work_type", ["infra", "config", "debug", "remote-ops"])
@pytest.mark.parametrize("command", INFRA_TMUX_MUTATING)
def test_tmux_that_executes_or_changes_a_session_gates_under_every_widened_work_type(
    gate, monkeypatch, work_type, command
):
    monkeypatch.setattr(gate, "_current_work_type", work_type)
    assert gate.is_safe_bash_command({"command": command}) is False, (work_type, command)


@pytest.mark.parametrize("work_type", [None, "infra", "remote-ops"])
@pytest.mark.parametrize("command", INFRA_TMUX_READS)
def test_the_tmux_read_verbs_stay_safe_with_and_without_a_widened_work_type(gate, monkeypatch, work_type, command):
    """Control: narrowing must not over-gate reading a session."""
    monkeypatch.setattr(gate, "_current_work_type", work_type)
    assert gate.is_safe_bash_command({"command": command}) is True, (work_type, command)


@pytest.mark.parametrize("command", INFRA_TMUX_MUTATING)
def test_without_a_widened_work_type_the_same_commands_were_already_gated(gate, monkeypatch, command):
    """Control: the hole was only the work_type widening."""
    monkeypatch.setattr(gate, "_current_work_type", None)
    assert gate.is_safe_bash_command({"command": command}) is False, command


# ── abbreviations and attached short options (pre-publish sweep, C1 and C2) ──────────────────────────────────
# getopt_long parsers accept any unambiguous PREFIX of a long option and accept a short option with its value attached or inside a cluster, so an
# exact-token denylist let `man --conf cfg`, `git cat-file --textc`, `man -C/tmp/m.conf`, `man -aC cfg` and `man -Pcat` through as noetic. The
# reviewer ran the first two and watched the helper run.


@pytest.mark.parametrize(
    "command",
    [
        "man --config cfg -l x.1",
        "man --conf cfg -l x.1",
        "man --config-f cfg ls",
        "man --conf=cfg ls",
        "man --pag cat ls",
        "man --pager=cat ls",
        "man --htm ls",
        "git cat-file --textc HEAD:f",
        "git blame --textco f",
        "git diff --textconv=1 HEAD",
        "git diff --ext-dif HEAD",
        "man -C/tmp/m.conf ls",
        "man -aC /tmp/m.conf ls",
        "man -Pcat ls",
        "man -aPcat ls",
        "man -Hfirefox ls",
    ],
)
def test_an_abbreviated_or_attached_form_of_a_program_naming_flag_is_not_noetic(gate, command):
    assert gate.is_safe_bash_command({"command": command}) is False, command


@pytest.mark.parametrize(
    "command",
    [
        "man ls",
        "man 3 printf",
        "man -k printf",
        "man -l x.1",
        "man -w ls",
        "man -a ls",
        "man --apropos printf",
        "man --where ls",
        "git diff --stat",
        "git diff --stat --numstat HEAD~1",
        "git log --stat -3",
        "git log --text-nothing-else",
        "git blame README.md",
        "git show --name-only HEAD",
    ],
)
def test_ordinary_man_and_git_reads_stay_noetic(gate, command):
    """Controls: the broader match must not turn everyday reads into praxic ones."""
    assert gate.is_safe_bash_command({"command": command}) is True, command
