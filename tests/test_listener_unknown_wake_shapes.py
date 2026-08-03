"""An unrecognised wake shape must be audible, not silently dropped.

`_NON_PROPOSAL_WAKE_SHAPES` is an allowlist over ANOTHER system's vocabulary.
Cortex can add a wake shape whenever it likes, and this listener dropped
anything it did not recognise with no log, no counter, nothing — so a new shape
would simply never arrive, and the only symptom is an AI that never reacts to
something nobody can see was sent.

Reported by empirica-cortex as "`platform_dispatch_ready` is excluded, so every
publish Accept is undeliverable by construction". Whether THAT shape is actually
lost depends on whether it has a proposal-store row — if it does, the catch-up
immediately after the relay reconstructs it and nothing is lost. That is cortex's
semantics to answer.

What is a defect on this side regardless: the drop was silent, so neither of us
could tell which case we were in from the receiving end.

Deliberately NOT widened to relay unknown shapes — relaying a body whose
reaction protocol we do not know invents behaviour. Making the drop audible is
the correct half.
"""

from __future__ import annotations

import io
import json

import pytest

from empirica.core.loop_scheduler.listener import (
    _NON_PROPOSAL_WAKE_SHAPES,
    _relay_non_proposal_wake,
)


def _relay(body, canonical=None):
    out, err = io.StringIO(), io.StringIO()
    msg = {"message": json.dumps(body)} if isinstance(body, dict) else {"message": body}
    relayed = _relay_non_proposal_wake(msg, "empirica", "cortex-mailbox-poll", canonical, out, err)
    return relayed, out.getvalue(), err.getvalue()


def test_a_known_shape_is_still_relayed():
    relayed, out, _err = _relay({"event": "ser_escalation", "ser_id": "ser_1"})

    assert relayed is True
    emitted = json.loads(out.strip())
    assert emitted["event_type"] == "ser_escalation"
    assert emitted["ser_id"] == "ser_1"
    assert emitted["via"] == "push_relay"


def test_an_unknown_shape_is_reported_on_stderr():
    """THE FIX: previously this returned False and wrote nothing anywhere."""
    relayed, out, err = _relay({"event": "platform_dispatch_ready", "dispatch_id": "d1"})

    assert relayed is False, "still not relayed — we do not know its reaction protocol"
    assert out == "", "and nothing is emitted into the session"
    assert "UNHANDLED" in err
    assert "platform_dispatch_ready" in err, "name the shape, or the log cannot be acted on"
    assert "ser_escalation" in err, "list what IS known, so the gap is legible"


def test_proposal_events_do_not_warn():
    """They are reconstructed by the catch-up, so they are not dropped at all.

    Warning about them would make the new log pure noise on the busiest path —
    and a log everyone learns to ignore is worse than no log.
    """
    relayed, _out, err = _relay({"event": "proposal_event", "proposal_id": "prop_1"})

    assert relayed is False
    assert err == ""


@pytest.mark.parametrize("body", ["not json at all", "", "[]", "null"])
def test_non_dict_bodies_are_ignored_quietly(body):
    """ntfy carries plenty of non-JSON. Warning on those would drown the signal."""
    relayed, _out, err = _relay(body)

    assert relayed is False
    assert err == ""


def test_a_body_with_no_event_key_does_not_warn():
    relayed, _out, err = _relay({"proposal_id": "prop_1"})

    assert relayed is False
    assert err == ""


def test_recipient_gate_still_rejects_a_body_that_names_other_targets():
    """Defense-in-depth ahead of the tag filter — must survive the change."""
    relayed, out, _err = _relay(
        {"event": "ser_escalation", "target_claudes": ["empirica.david.empirica-cortex"]},
        canonical="empirica.david.empirica",
    )

    assert relayed is False
    assert out == ""


def test_the_allowlist_is_still_an_allowlist():
    """If this ever becomes 'relay anything', the reaction protocol is undefined."""
    assert "ser_escalation" in _NON_PROPOSAL_WAKE_SHAPES
    assert "platform_dispatch_ready" not in _NON_PROPOSAL_WAKE_SHAPES, (
        "adding a shape here is a deliberate act — it asserts we know how to react to it"
    )
