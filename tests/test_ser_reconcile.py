"""Catch-up SER-escalation reconcile (part B of the two-path ser_escalation fix).

autonomy prop_tr4dbwcf / prop_4wo5huw5: a DROPPED ser_escalation doorbell is
unrecoverable if catch-up stays proposal-only. reconcile_ser_escalations re-pulls
the durable SER projection (ONE GET /v1/sers) and emits a catch-up escalation for
any active SER this instance is required-tier on that transitioned since its last
ack. Covers autonomy's 4 live-use gotchas: mixed ts types, exact-canonical match,
(ser_id, last_transition_at) de-dup cursor, via='catchup_reconcile' marker.
"""

from __future__ import annotations

import json

from empirica.core.loop_scheduler.content_poll import (
    EscalationEvent,
    _normalize_ts,
    reconcile_ser_escalations,
)

_C = "empirica.david.empirica"


def _reconcile(sers, cursor=None):
    return reconcile_ser_escalations(
        "empirica", "cortex-mailbox-poll", "http://x", "k", _C, cursor or {}, fetch_fn=lambda *a, **k: sers
    )


def _ser(ser_id, state="in_progress", transition="2026-07-05T21:00:00+00:00", role="required", ack=None):
    return {
        "ser_id": ser_id,
        "coordination_state": state,
        "last_transition_at": transition,
        "participants": [{"practice_id": _C, "role": role, "last_ack_at": ack}],
    }


# ── the predicate ────────────────────────────────────────────────────────


def test_emits_for_active_required_transition_after_ack():
    events, cursor = _reconcile([_ser("ser_1")])
    assert [e.ser_id for e in events] == ["ser_1"]
    assert events[0].via == "catchup_reconcile"
    assert "ser_1" in cursor


def test_skips_closed_ser():
    events, _ = _reconcile([_ser("s", state="closed")])
    assert events == []


def test_skips_non_required_tier():
    events, _ = _reconcile([_ser("s", role="participating")])
    assert events == []


def test_skips_when_acked_past_transition():
    # my last_ack_at is AFTER the transition → already current, no escalation.
    events, _ = _reconcile([_ser("s", transition=1_751_000_000, ack=1_751_000_001)])
    assert events == []


def test_exact_canonical_match_no_basename_fallback():
    # participant row keyed on the bare basename must NOT match (gotcha 2).
    ser = {
        "ser_id": "s",
        "coordination_state": "open",
        "last_transition_at": 1,
        "participants": [{"practice_id": "empirica", "role": "required", "last_ack_at": None}],
    }
    events, _ = _reconcile([ser])
    assert events == []


# ── de-dup cursor (gotcha 3) ─────────────────────────────────────────────


def test_cursor_dedups_same_transition():
    events, cursor = _reconcile([_ser("ser_1")])
    assert len(events) == 1
    again, _ = _reconcile([_ser("ser_1")], cursor=cursor)
    assert again == []  # same (ser_id, last_transition_at) → suppressed


def test_new_transition_refires_after_cursor():
    _events, cursor = _reconcile([_ser("ser_1", transition="2026-07-05T21:00:00+00:00")])
    # a genuinely newer transition must re-fire despite the cursor.
    again, _ = _reconcile([_ser("ser_1", transition="2026-07-05T22:00:00+00:00")], cursor=cursor)
    assert [e.ser_id for e in again] == ["ser_1"]


# ── unreachable / empty (fail-soft) ──────────────────────────────────────


def test_fetch_none_is_noop_and_preserves_cursor():
    seed = {"ser_9": 123.0}
    events, cursor = reconcile_ser_escalations(
        "empirica", "l", "http://x", "k", _C, seed, fetch_fn=lambda *a, **k: None
    )
    assert events == []
    assert cursor == seed  # unreachable never wipes the cursor


def test_empty_canonical_skips():
    events, _ = reconcile_ser_escalations(
        "empirica", "l", "http://x", "k", "", {}, fetch_fn=lambda *a, **k: [_ser("s")]
    )
    assert events == []


# ── timestamp normalization (gotcha 1) ───────────────────────────────────


def test_normalize_ts_handles_epoch_iso_null():
    assert _normalize_ts(1_751_000_000) == 1_751_000_000.0
    assert _normalize_ts("2026-07-05T21:00:00Z") is not None
    assert _normalize_ts(None) is None
    assert _normalize_ts("") is None
    assert _normalize_ts("not-a-timestamp") is None


def test_mixed_epoch_transition_beats_null_ack():
    # transition as epoch NUMBER, ack null → must still emit (mixed types).
    events, _ = _reconcile([_ser("s", transition=1_751_000_000, ack=None)])
    assert [e.ser_id for e in events] == ["s"]


# ── event shape ──────────────────────────────────────────────────────────


def test_escalation_event_log_line_shape():
    line = json.loads(EscalationEvent("empirica", "l", "ser_x", "in_progress", "2026-07-05T21:00:00Z").to_log_line())
    assert line["event_type"] == "ser_escalation"
    assert line["escalation"] is True
    assert line["source_claude"] == "system:ser-escalation"
    assert line["ser_id"] == "ser_x"
    assert line["via"] == "catchup_reconcile"
