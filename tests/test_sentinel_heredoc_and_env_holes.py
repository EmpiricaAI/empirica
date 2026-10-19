"""Heredocs and execution-changing variables must not hide a command from the gate.

Found by an independent bypass review of the platform-read change and reproduced on plain ``cat`` and ``git status``, so these
holes predate it and every safe command inherits them (goal 59854adc):

1. A heredoc operator cut the segment, so arguments after it were never classified:
   ``echo a | cat <<E <(touch /tmp/x)`` classified safe.
2. A command on a line AFTER a heredoc's terminator was never classified:
   ``cat <<E\\nx\\nE\\ntouch /tmp/x`` classified safe.
3. ``env`` and bare assignments accepted variables that change WHAT RUNS: ``env PATH=/tmp/evil ls`` ran /tmp/evil/ls,
   ``env LD_PRELOAD=/tmp/x.so ls`` loaded it, ``PATH=/tmp/evil; ls`` did the same across a chain.

The fix only tightens: a command is safe when the old classifier says so AND the same command with its heredoc bodies and
operators removed is safe. Every legitimate heredoc use below must keep flowing, and a heredoc that FEEDS an executor (bash,
python3, sqlite3, ssh, ``| sh``) must stay gated: those were gated before and the body is the program.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(scope="module")
def gate_module():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("sentinel_gate_heredoc_env", _HOOK_DIR / "sentinel-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[None, "code", "infra"])
def safe(gate_module, monkeypatch, request):
    monkeypatch.setattr(gate_module, "_current_work_type", request.param)
    return lambda cmd: gate_module.is_safe_bash_command({"command": cmd}) is True


HEREDOC_HOLES = [
    # 1. arguments after the operator
    "echo a | cat <<E <(touch /tmp/pwn)\nx\nE",
    "echo a | cat <<E <(touch /tmp/pwn)",
    "cat <<E $(touch /tmp/pwn)\nx\nE",
    "cat <<'E' > /tmp/out\nx\nE",
    "cat <<E >> /tmp/out\nx\nE",
    "echo a | grep -c a <<E > /tmp/out\nx\nE",
    "git status <<E -- ; touch /tmp/pwn\nx\nE",
    # 2. a command after the terminator
    "cat <<E\nx\nE\ntouch /tmp/p",
    "cat <<E\nx\nE\nrm -rf /tmp/p",
    "git status <<E\nx\nE\nrm -rf /tmp/p",
    "cat <<'E'\nx\nE\ntouch /tmp/p",
    "cat <<-E\n\tx\n\tE\ntouch /tmp/p",
    "cat <<E\nx\nE\n\ntouch /tmp/p",
    "cat <<A\nx\nA\ncat <<B\ny\nB\ntouch /tmp/p",
    "cat <<E; touch /tmp/p\nx\nE",
    "cat <<E && touch /tmp/p\nx\nE",
    # a second heredoc operator on one line: both bodies are consumed, then the next line runs
    "cat <<A <<B\na\nA\nb\nB\ntouch /tmp/p",
    # unterminated: the body runs to the end of the text, nothing after is a command
    "cat <<E\nx\ntouch /tmp/p",
]

# A `<<` that is NOT a heredoc still triggered the old blind cut, which hid everything after it, later lines included.
NOT_A_HEREDOC_HOLES = [
    "ls # <<E\ntouch /tmp/p\nE",
    "echo a # <<'E'\nrm -rf /tmp/p\nE",
    "ls ; # <<E\ntouch /tmp/p",
    "echo ${x//<</y}\ntouch /tmp/p",
    "echo $((1 << 3))\ntouch /tmp/p",
    "echo hi <<< 'a here-string'\ntouch /tmp/p",
    "cat <<< foo\ntouch /tmp/p",
    "cat <<< $(touch /tmp/p)",
    'echo "a<<b"\ntouch /tmp/p',
    "echo 'a<<b'; touch /tmp/p",
    'grep "a<<b" f; touch /tmp/p',
    "echo \\<<E\ntouch /tmp/p",
]

EXECUTOR_HEREDOCS = [
    "bash <<E\nrm -rf x\nE",
    "sh <<E\ntouch /tmp/p\nE",
    "python3 <<E\nimport os\nos.remove('x')\nE",
    "sqlite3 /tmp/x.db <<E\nDROP TABLE t;\nE",
    "ssh host <<E\nrm -rf x\nE",
    "cat <<E | sh\ntouch /tmp/p\nE",
    "cat <<E | bash\ntouch /tmp/p\nE",
    "empirica x <<EOF\nbody with ; rm -rf x and $(touch y)\nEOF",
    "cat <<EOF\n$(touch /tmp/p)\nEOF",
    "cat <<EOF\n`touch /tmp/p`\nEOF",
]

# Controls are things the gate allows TODAY. Two plausible ones are already over-gated before this change (a quoted heredoc whose body
# mentions $(...), and a planning verb followed by a heredoc verb on the next line), so they are not regression guards here.
LEGIT_HEREDOCS = [
    "cat <<E\nhello\nE",
    "cat <<-E\n\thello\n\tE",
    "empirica finding-log --finding x <<'EOF'\n{\"a\": 1}\nEOF",
    "empirica preflight-submit - <<'EOF'\n{\"vectors\": {}}\nEOF",
    "empirica preflight-submit - << 'EOF' 2>&1 | tail -2\n{}\nEOF",
    "cd /tmp && empirica check-submit - <<'EOF'\n{}\nEOF",
    "cat <<'JSON' | empirica log-artifacts -\n{\"nodes\": []}\nJSON",
    "grep -c foo <<E\nfoo\nE",
    'jq . <<E\n{"a": 1}\nE',
    "wc -l <<'E'\na\nb\nE",
    "git status <<E\nx\nE",
    "echo $((1 << 3))",
    "echo hi <<< 'a here-string'",
    "echo 'a<<b'",
    'grep "a<<b" /dev/null',
    "ls # a comment mentioning <<E",
    "echo a#b",
]

DANGEROUS_ENV = [
    "env PATH=/tmp/evil ls",
    "env LD_PRELOAD=/tmp/x.so ls",
    "env LD_LIBRARY_PATH=/tmp/x ls",
    "env DYLD_INSERT_LIBRARIES=/tmp/x.dylib ls",
    "env BASH_ENV=/tmp/x ls",
    "env ENV=/tmp/x ls",
    "env IFS=x ls",
    "env PYTHONPATH=/tmp/x git status",
    "env PYTHONSTARTUP=/tmp/x git status",
    "env NODE_OPTIONS=--require=/tmp/x git status",
    "env GIT_SSH_COMMAND='touch /tmp/p' git status",
    "env GIT_EXTERNAL_DIFF=/tmp/x git diff",
    "env GIT_PAGER='touch /tmp/p' git log",
    "env PAGER=/tmp/x git log",
    "env EDITOR=/tmp/x git status",
    "env PS4='$(touch /tmp/p)' ls",
    "env PROMPT_COMMAND='touch /tmp/p' ls",
    "env FOO=1 PATH=/tmp/evil ls",
    "env -i PATH=/tmp/evil ls",
    # the same variables as bare assignments that take effect on a LATER command in the chain
    "PATH=/tmp/evil; ls",
    "PATH=/tmp/evil && ls",
    "LD_PRELOAD=/tmp/x.so; ls",
    "IFS=x; ls",
    "BASH_ENV=/tmp/x; ls",
    "export PATH=/tmp/evil; ls",
    "export LD_PRELOAD=/tmp/x.so && git status",
]

BENIGN_ENV = [
    "env FOO=1 ls",
    "env EMPIRICA_INSTANCE_ID=x empirica mailbox poll",
    "env LC_ALL=C sort /dev/null",
    "env",
    "FOO=bar; ls",
    "MSG='hello world'; echo $MSG",
    "N=3; ls",
]


@pytest.mark.parametrize("command", HEREDOC_HOLES)
def test_a_heredoc_does_not_hide_a_command_from_the_gate(safe, command):
    assert not safe(command), command


@pytest.mark.parametrize("command", NOT_A_HEREDOC_HOLES)
def test_a_double_angle_that_is_not_a_heredoc_does_not_hide_later_text(safe, command):
    assert not safe(command), command


@pytest.mark.parametrize("command", EXECUTOR_HEREDOCS)
def test_a_heredoc_that_feeds_an_executor_stays_gated(safe, command):
    assert not safe(command), command


@pytest.mark.parametrize("command", LEGIT_HEREDOCS)
def test_legitimate_heredoc_uses_keep_flowing(safe, command):
    assert safe(command), command


@pytest.mark.parametrize("command", DANGEROUS_ENV)
def test_a_variable_that_changes_what_runs_is_not_inert(safe, command):
    assert not safe(command), command


@pytest.mark.parametrize("command", BENIGN_ENV)
def test_ordinary_environment_use_keeps_flowing(safe, command):
    assert safe(command), command


def test_the_harness_is_live(safe):
    assert safe("ls -la")
    assert not safe("rm -rf /tmp/x")
