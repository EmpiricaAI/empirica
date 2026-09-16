"""An unrecognised --status must never produce a silent empty mailbox.

That requirement has not changed. How it is met has, twice, and the second change
is the interesting one.

**Round one.** `--status` was free text passed straight through. A value cortex
does not store matched nothing, so the poll returned zero proposals with
`matched: 0` — indistinguishable from having no mail. `--status all` was the case
that bit: a reasonable thing to type, answering "you have nothing" while 80
proposals sat behind it. The fix was a client-side allowlist that REJECTED
anything outside it.

**Round two.** The allowlist was hand-maintained over cortex's vocabulary, and its
own comment predicted what happened next — it went stale and the rejection began
blocking real work. `targets_pending` could not be enumerated. `failed` and
`wont_fix` were unreachable by any means, while the mailbox protocol instructs
practitioners to act on exactly those states, so no emitter could count their own
undelivered sends. And `--status all` expanded to the known list while its own
help promised "every status" — a caller asking for everything got a silent subset,
which is round one's defect wearing round one's fix.

So the gate is gone and the requirement is met differently: cortex validates its
own vocabulary and answers an unknown value with a 400 naming the valid set, and
this CLI emits a note for anything it does not recognise so a typo is loud even
against an older cortex that does not validate. `all` now sends NO filter.

The tests below were rewritten, not deleted. Each one still asserts the original
requirement — *a wrong filter must not look like an empty mailbox* — against the
new mechanism.
"""

from __future__ import annotations

import argparse

import pytest

from empirica.cli.parsers.mailbox_parsers import POLL_NO_FILTER, POLL_STATUS_ALL, VALID_POLL_STATUSES


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


def test_all_is_not_itself_a_status():
    """'all' is not stored — it has to be translated, never forwarded as a literal."""
    assert POLL_STATUS_ALL not in VALID_POLL_STATUSES


@pytest.mark.parametrize("unrecognised", ["acceptd", "ACCEPTED", "done"])
def test_an_unrecognised_status_is_noted_loudly_and_passed_through(unrecognised):
    """The original requirement, against the new mechanism.

    Passed through rather than rejected — the list this CLI checks against has
    gone stale twice, and a false reject blocks work outright where a passed-through
    typo costs one round-trip and a clear 400. The NOTE is what keeps it from being
    silent on a cortex too old to validate.
    """
    rc, statuses = _statuses_for(_args(status=unrecognised))

    assert rc != 1, "an unrecognised value is cortex's to judge, not ours"
    assert statuses == (unrecognised,), "the value must reach cortex to be validated there"


@pytest.mark.parametrize("unrecognised", ["acceptd", "ACCEPTED", "done"])
def test_the_note_warns_that_an_empty_result_may_mean_a_wrong_value(capsys, unrecognised):
    """A note that does not say WHY it matters is decoration.

    The whole point is the reader knowing that zero rows might mean "wrong filter"
    rather than "no mail" — the confusion that started this file.
    """
    _statuses_for(_args(status=unrecognised))
    err = capsys.readouterr().err

    assert unrecognised in err, "the note must name the offending value"
    assert "empty result" in err, "the note must connect itself to the failure mode it prevents"


def test_the_note_blames_only_the_unrecognised_value(capsys):
    _statuses_for(_args(status="accepted,acceptd,changed"))
    err = capsys.readouterr().err

    assert "acceptd" in err
    assert "changed" not in err, "a known value must not be swept into the note"


def test_a_recognised_status_produces_no_note(capsys):
    """The positive control.

    Without it, a note attached to every poll would satisfy every assertion above
    while training readers to ignore the one that matters.
    """
    _statuses_for(_args(status="accepted"))

    assert capsys.readouterr().err == ""


def test_every_known_status_is_passed_through_unchanged():
    for status in VALID_POLL_STATUSES:
        rc, statuses = _statuses_for(_args(status=status))
        assert rc != 1, f"{status} must be accepted"
        assert statuses == (status,)


def test_the_statuses_the_protocol_acts_on_are_reachable():
    """`failed` and `wont_fix` are what the mailbox protocol tells you to act on.

    The poll skill's reaction table says an outbox `failed`/`wont_fix` means the
    leg is dead and must not be chained as if it shipped. While the allowlist
    omitted them they could not be queried by default, by name, or via `all` — so
    a practitioner saw silence, and silence reads as awaiting-reply. The one signal
    that says stop waiting was the one the tool filtered out.
    """
    for status in ("failed", "wont_fix", "targets_pending"):
        rc, statuses = _statuses_for(_args(status=status, outbox=True))
        assert rc != 1
        assert statuses == (status,)


def test_all_sends_no_filter_rather_than_an_enumeration():
    """`all` expanded to the known list, while the help promised "every status".

    Measured: 7 of 13. A caller asking for everything got a silent subset — the
    exact defect this file was opened for, reintroduced by its own first fix.
    Sending no filter is the only form that cannot go stale.
    """
    rc, statuses = _statuses_for(_args(status=POLL_STATUS_ALL))

    assert rc != 1
    assert statuses == POLL_NO_FILTER, "'all' must send NO filter, not the statuses we happen to know"


def test_no_filter_omits_the_query_key_entirely():
    """An empty tuple must not become `status=` on the wire.

    `",".join(())` is `""` — a filter matching nothing, not the absence of one.
    "Every status" and "no results" would go out as almost the same request.
    """
    import urllib.parse

    from empirica.core.loop_scheduler import content_poll as cp

    seen = {}

    def _fake_get(req, *_a, **_kw):
        # urlopen is called with a Request, not a URL string — the full URL
        # lives on .full_url. Reading the first positional as a str gives a
        # Request object and an AttributeError three frames down in urllib.
        url = getattr(req, "full_url", req)
        # keep_blank_values=True is LOAD-BEARING. By default parse_qs DROPS a blank
        # value, so `status=` reads as absent and this assertion passes against
        # the very bug it guards. The negative control caught it: reverting the
        # fix left this test green.
        seen["query"] = urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True)

        class _R:
            def read(self):
                return b'{"proposals": []}'

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        return _R()

    import urllib.request

    orig = urllib.request.urlopen
    cp_resolve = cp._resolve_canonical_ai_id
    try:
        urllib.request.urlopen = _fake_get  # type: ignore[assignment]
        cp._resolve_canonical_ai_id = lambda *_a, **_kw: "org.tenant.proj"  # type: ignore[assignment]
        cp.fetch_cortex_outbox("http://c", "k", "empirica", statuses=())
    finally:
        urllib.request.urlopen = orig  # type: ignore[assignment]
        cp._resolve_canonical_ai_id = cp_resolve  # type: ignore[assignment]

    assert "status" not in seen["query"], "an empty filter must be ABSENT, not empty"


def test_a_non_empty_filter_still_sends_the_key():
    """Positive control for the omission above — and a guard for every listener.

    The listener callers always pass a non-empty default, so their query must be
    byte-for-byte what it was. If omission leaked into that path, every wake filter
    would silently widen to everything.
    """
    import urllib.parse
    import urllib.request

    from empirica.core.loop_scheduler import content_poll as cp

    seen = {}

    def _fake_get(req, *_a, **_kw):
        # urlopen is called with a Request, not a URL string — the full URL
        # lives on .full_url. Reading the first positional as a str gives a
        # Request object and an AttributeError three frames down in urllib.
        url = getattr(req, "full_url", req)
        # keep_blank_values=True is LOAD-BEARING. By default parse_qs DROPS a blank
        # value, so `status=` reads as absent and this assertion passes against
        # the very bug it guards. The negative control caught it: reverting the
        # fix left this test green.
        seen["query"] = urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True)

        class _R:
            def read(self):
                return b'{"proposals": []}'

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        return _R()

    orig = urllib.request.urlopen
    cp_resolve = cp._resolve_canonical_ai_id
    try:
        urllib.request.urlopen = _fake_get  # type: ignore[assignment]
        cp._resolve_canonical_ai_id = lambda *_a, **_kw: "org.tenant.proj"  # type: ignore[assignment]
        cp.fetch_cortex_outbox("http://c", "k", "empirica", statuses=("accepted", "changed"))
    finally:
        urllib.request.urlopen = orig  # type: ignore[assignment]
        cp._resolve_canonical_ai_id = cp_resolve  # type: ignore[assignment]

    assert seen["query"]["status"] == ["accepted,changed"]


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


def test_help_text_and_the_known_list_read_the_same_definition():
    """They were two sources of truth, and only the help text knew the answer.

    Still built from the constant. The constant is a hint now rather than a gate,
    but a help text that hand-lists statuses would drift from it just as before.
    """
    import inspect

    from empirica.cli.parsers import mailbox_parsers

    src = inspect.getsource(mailbox_parsers.add_mailbox_parsers)
    assert "VALID_POLL_STATUSES" in src, "help text must be built from the constant, not hand-listed"


def test_the_help_no_longer_claims_an_unrecognised_value_is_an_error():
    """The help promised a rejection this CLI no longer performs.

    A help text describing the previous contract is the two-sources-of-truth
    defect again, one layer out — and this one is read by a human deciding what
    to type.
    """
    import inspect

    from empirica.cli.parsers import mailbox_parsers

    src = inspect.getsource(mailbox_parsers.add_mailbox_parsers)
    assert "an error, not an empty result" not in src


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


def test_apd_is_a_known_status_value():
    """I hardcoded VALID_POLL_STATUSES from the help text and missed this one.

    `--status accepted_pending_dispatch` was rejected as unknown — my fix for a
    silent-empty had become a false-reject on a real status. That is the pattern
    the gate removal above is the answer to.
    """
    assert "accepted_pending_dispatch" in VALID_POLL_STATUSES
