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


def test_defaults_are_applied_when_no_status_given():
    """Validation must not disturb the defaults — but the outbox default CHANGED.

    This test used to assert ("completed", "changed", "declined"), which encoded
    the bug: it pinned the very filter that hid every collab. A test asserting
    current behaviour is not automatically asserting correct behaviour.
    """
    from empirica.cli.command_handlers.mailbox_commands import _default_poll_statuses

    _, inbox = _statuses_for(_args())
    _, outbox = _statuses_for(_args(outbox=True))

    assert inbox == _default_poll_statuses(outbox=False)
    assert outbox == _default_poll_statuses(outbox=True)
    assert "accepted" in outbox


def test_help_text_and_validator_read_the_same_definition():
    """They were two sources of truth, and only the help text knew the answer.

    The help string listed the valid statuses while the code accepted anything.
    Building the help FROM the constant makes drift between them impossible.
    """
    import inspect

    from empirica.cli.parsers import mailbox_parsers

    src = inspect.getsource(mailbox_parsers.add_mailbox_parsers)
    assert "VALID_POLL_STATUSES" in src, "help text must be built from the constant, not hand-listed"


# --- the defaults are a REPORT, not a wake filter -----------------------------


def test_outbox_default_includes_accepted():
    """`accepted` is a collab's TERMINAL state, not a transient one.

    The old default ("completed", "changed", "declined") came from "status
    changes on your emissions" — right for a wake filter, wrong for a report.
    Measured by cortex against real rows: 182 emissions, 21 visible. The
    "newest visible" timestamp matched a reported cutoff to the second, because
    it was not a date bound at all — it was the last non-collab emission.
    """
    from empirica.cli.command_handlers.mailbox_commands import _default_poll_statuses

    assert "accepted" in _default_poll_statuses(outbox=True), "every collab was invisible without this"
    assert "accepted_pending_dispatch" in _default_poll_statuses(outbox=True)
    assert "completed" in _default_poll_statuses(outbox=True), "acks must still show"


def test_inbox_default_is_unchanged():
    from empirica.cli.command_handlers.mailbox_commands import _default_poll_statuses

    assert _default_poll_statuses(outbox=False) == ("accepted", "changed")


def test_reporting_and_waking_stay_separate():
    """The CLI report shows plain `accepted`; the WAKE filter deliberately does not.

    Waking on every outbox accept is noise and content_poll documents that.
    Collapsing the two would trade a reporting bug for a notification-storm bug.
    """
    from empirica.cli.command_handlers.mailbox_commands import _default_poll_statuses
    from empirica.core.loop_scheduler.content_poll import EMISSION_STATUSES_OUTBOX

    assert "accepted" in _default_poll_statuses(outbox=True)
    assert "accepted" not in EMISSION_STATUSES_OUTBOX


def test_apd_is_wakeable_because_it_is_actionable():
    """The load-bearing half: without this a dropped doorbell is unrecoverable.

    `platform_dispatch_ready` in the relay allowlist fixes LIVE delivery only.
    Recovery needs the status in the catch-up filter — the bug closed both
    paths by two independent mechanisms.
    """
    from empirica.core.loop_scheduler.content_poll import EMISSION_STATUSES_OUTBOX

    assert "accepted_pending_dispatch" in EMISSION_STATUSES_OUTBOX


def test_apd_is_an_accepted_status_value():
    """I hardcoded VALID_POLL_STATUSES from the help text and missed this one.

    `--status accepted_pending_dispatch` was rejected as unknown — my fix for a
    silent-empty had become a false-reject on a real status.
    """
    assert "accepted_pending_dispatch" in VALID_POLL_STATUSES
