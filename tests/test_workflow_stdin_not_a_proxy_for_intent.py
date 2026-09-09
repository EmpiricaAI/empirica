"""`not isatty()` is not "the caller is piping me JSON".

GH #414 (kars85). The workflow commands fell back to `sys.stdin.read()` whenever
stdin was not a TTY — which is true on every non-interactive host that hands the
CLI an open pipe it never closes: CI runners, cron, `subprocess.Popen` with
inherited stdin, agent harnesses backgrounding a shell script. The read blocks
until an EOF that never comes, and under a `timeout` wrapper it surfaces as a
silent rc 124 with the transaction skipped rather than as an error.

    (sleep 200) | timeout 75 empirica preflight-submit --session-id X --vectors '{...}'
      -> hangs, killed at 75s
    same command with </dev/null
      -> completes in ~1.7s

One predicate answering two questions: "am I non-interactive" and "is JSON
coming". They agree in a terminal and diverge everywhere automation runs.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from empirica.cli.command_handlers._workflow_shared import _explicit_input_given

VECTORS = '{"know": 0.7, "do": 0.7}'


@pytest.mark.parametrize(
    "flag,value",
    [
        ("vectors", VECTORS),
        ("reasoning", "because"),
        ("task_context", "doing a thing"),
        ("work_type", "code"),
        ("current_phase", "praxic"),
    ],
)
def test_any_payload_flag_means_do_not_wait_on_stdin(flag, value):
    assert _explicit_input_given(Namespace(**{flag: value})) is True


def test_no_payload_flags_means_stdin_is_still_the_input():
    """Positive control, and the behaviour that must NOT regress.

    `empirica preflight-submit - << EOF` is the documented and most-used path.
    A fix that stopped reading stdin whenever it was non-interactive would break
    every heredoc call in every hook and script.
    """
    assert _explicit_input_given(Namespace(session_id="s1", output="json")) is False
    assert _explicit_input_given(Namespace()) is False


def test_an_EMPTY_flag_does_not_count_as_input():
    """`--vectors ''` supplies nothing. Treating a present-but-empty flag as
    input would skip the stdin read and then fail on missing vectors — trading a
    hang for a confusing error."""
    assert _explicit_input_given(Namespace(vectors="")) is False
    assert _explicit_input_given(Namespace(vectors=None)) is False


def test_session_id_alone_does_NOT_suppress_stdin():
    """The narrow case that makes this a payload question rather than a flag count.

    `--session-id` identifies the transaction; it is not the payload. Piping
    vectors on stdin WHILE naming the session on the command line is a normal
    call, and suppressing the read there would break it.
    """
    assert _explicit_input_given(Namespace(session_id="s1")) is False
