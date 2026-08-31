"""A loud error that cannot be read is diagnosed as a silent one.

A seat reported that `lesson-create --name X --lesson "<body>" --visibility shared`
**silently no-oped** — echoed the text back and exited looking like success. It does
not. It exits 2, on stderr, and creates nothing.

But argparse renders `unrecognized arguments: <every offending token, verbatim>`.
With a real lesson body that is a two-line prefix followed by several thousand
characters of the caller's own text, and the TAIL — where the eye lands, and where a
truncated tool result cuts — is pure lesson text. The report was right about the
experience and wrong about the mechanism, and the diagnosis went to the wrong layer
because the error was unreadable rather than absent.

The failure scales with argument length, so it bites hardest on authoring verbs,
which are exactly the ones whose arguments are long.
"""

from __future__ import annotations

import pytest

from empirica.cli.cli_core import _MAX_ECHOED_VALUE_CHARS, _unrecognized_message, create_argument_parser

LONG = "When a producer and a consumer disagree about shape, say which side expected what. " * 12


def test_a_long_value_is_elided_and_its_flag_survives():
    """THE fix. The flag is the actionable part; the value is the caller's own text
    and they already have it."""
    msg = _unrecognized_message(["--lesson", LONG, "--visibility", "shared"])

    assert "--lesson" in msg, "the rejected flag must still be named"
    assert LONG not in msg, "the body must not be echoed back"
    assert f"{len(LONG)} chars" in msg, "say how much was elided, so it is not mistaken for empty"


def test_the_whole_message_stays_readable_at_a_glance():
    """The property, not the mechanism: whatever the elision rule is, the message has
    to fit somewhere a human or a truncated tool result will actually see it."""
    msg = _unrecognized_message(["--lesson", LONG, "--visibility", "shared", "--domain", "cross_org"])
    assert len(msg) < 200, f"still unreadable at {len(msg)} chars"


def test_short_values_are_still_named_in_full():
    """NEGATIVE CONTROL. Eliding everything would pass the test above perfectly while
    making every ordinary error worse — `--limit abc` must still say `abc`."""
    msg = _unrecognized_message(["--limit", "abc"])
    assert "abc" in msg
    assert "elided" not in msg


@pytest.mark.parametrize("n", [_MAX_ECHOED_VALUE_CHARS - 1, _MAX_ECHOED_VALUE_CHARS])
def test_the_boundary_does_not_elide(n):
    """Values AT the threshold print in full; only past it are they elided."""
    assert "elided" not in _unrecognized_message(["--x", "v" * n])


def test_a_long_flag_name_is_never_elided():
    """The rule keys on being flag-shaped, not on length — a long `--flag` is still
    the one thing the caller needs told."""
    flag = "--" + "a" * (_MAX_ECHOED_VALUE_CHARS + 50)
    assert flag in _unrecognized_message([flag])


def test_the_parser_still_exits_2_on_stderr(capsys):
    """The elision must not soften the failure. It was ALREADY loud; the defect was
    legibility. A fix that made it quieter would be the bug the report described."""
    parser = create_argument_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["lesson-create", "--name", "X", "--lesson", LONG])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err
    assert LONG not in err


def test_a_valid_command_is_unaffected():
    """POSITIVE CONTROL on the parser itself. parse_args is parse_known_args plus an
    error, so overriding it should change nothing when there are no extras — and an
    assertion that bad input fails proves nothing if good input fails too."""
    parsed = create_argument_parser().parse_args(["lesson-list", "--limit", "5"])
    assert parsed.command == "lesson-list"
    assert parsed.limit == 5
