"""An unrecognised --status must be an error, never an empty mailbox.

`--status` was free text: split on commas and passed straight through. A value
cortex does not store matched nothing, so the poll returned zero proposals with
`matched: 0` — **indistinguishable from having no mail**.

`--status all` was the case that bit. It is a reasonable thing to type; I typed
it, got an empty result, and reported the outbox as broken. The outbox was fine
— 80 proposals sat behind a filter that silently selected nothing.

A filter selecting nothing because the FILTER is wrong must not look like a
filter selecting nothing because there IS nothing. The help text already listed
the valid set, so the knowledge existed and nothing enforced it.
"""

from __future__ import annotations

import argparse

import pytest

from empirica.cli.parsers.mailbox_parsers import POLL_STATUS_ALL, VALID_POLL_STATUSES


def _args(status=None, outbox=False):
    return argparse.Namespace(
        status=status, outbox=outbox, ai_id="empirica", since=None, limit=5, related=False, output="json"
    )


def _statuses_for(args):
    """Run the handler far enough to capture the status tuple it resolves.

    Uses the handler's own dependency-injection seams rather than monkeypatching
    the module — they exist precisely so a caller can substitute the network.
    """
    from empirica.cli.command_handlers import mailbox_commands as mc

    captured = {}

    def _fake_fetch(*_a, **kw):
        captured["statuses"] = kw.get("statuses")
        return []

    rc = mc.handle_mailbox_poll_command(
        args,
        _resolve_cortex_creds=lambda: ("http://cortex.test", "key"),
        _resolve_ai_id=lambda: "empirica",
        _fetch_mailbox=_fake_fetch,
    )
    return rc, captured.get("statuses")


def test_all_is_expanded_to_every_status():
    """'all' is not a stored status — it has to be translated, not forwarded."""
    assert POLL_STATUS_ALL not in VALID_POLL_STATUSES


@pytest.mark.parametrize("bad", ["acceptd", "all,bogus", "ACCEPTED", "done", ""])
def test_unknown_status_is_rejected_not_silently_empty(capsys, bad):
    from empirica.cli.command_handlers import mailbox_commands as mc

    if not bad:
        pytest.skip("empty string falls through to the default filter, which is correct")

    rc = mc.handle_mailbox_poll_command(_args(status=bad))
    err = capsys.readouterr().err

    assert rc == 1, "an unusable filter must not exit 0"
    assert "unknown --status" in err
    assert "valid:" in err, "name the alternatives — a rejection with no menu is a dead end"


def test_the_error_names_the_offending_value(capsys):
    from empirica.cli.command_handlers import mailbox_commands as mc

    mc.handle_mailbox_poll_command(_args(status="accepted,acceptd,changed"))
    err = capsys.readouterr().err

    assert "acceptd" in err
    assert "accepted," not in err.split("valid:")[0].replace("acceptd", ""), "only the BAD value is blamed"


def test_every_valid_status_is_accepted():
    for status in VALID_POLL_STATUSES:
        rc, statuses = _statuses_for(_args(status=status))
        assert rc != 1, f"{status} must be accepted"
        assert statuses == (status,)


def test_all_expands_to_the_full_set():
    rc, statuses = _statuses_for(_args(status=POLL_STATUS_ALL))

    assert rc != 1
    assert set(statuses) == set(VALID_POLL_STATUSES), "'all' must mean every status, not the literal string"


def test_defaults_are_unchanged_when_no_status_given():
    """The wake-react defaults are deliberate; validation must not disturb them."""
    _, inbox = _statuses_for(_args())
    _, outbox = _statuses_for(_args(outbox=True))

    assert inbox == ("accepted", "changed")
    assert outbox == ("completed", "changed", "declined")


def test_help_text_and_validator_read_the_same_definition():
    """They were two sources of truth, and only the help text knew the answer.

    The help string listed the valid statuses while the code accepted anything.
    Building the help FROM the constant makes drift between them impossible.
    """
    import inspect

    from empirica.cli.parsers import mailbox_parsers

    src = inspect.getsource(mailbox_parsers.add_mailbox_parsers)
    assert "VALID_POLL_STATUSES" in src, "help text must be built from the constant, not hand-listed"
