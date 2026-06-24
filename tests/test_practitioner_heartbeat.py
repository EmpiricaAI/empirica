"""Tests for the practitioner-presence heartbeat emitter (B2c).

The emitter forwards the LOCAL per-session presence store to cortex's
POST /v1/practitioners/heartbeat. The load-bearing contract details (encoded as
invariants below):

- ``ai_id`` is OMITTED from the body — cortex resolves it strict-canonically, a
  bare basename 403s, and the field isn't stored anyway (the api_key identifies
  the writer).
- ``machine`` + ``session_id`` are required and non-empty; ``session_id`` is the
  durable ``claude_session_id``.
- a ``blocked`` status surfaces ``pending_question`` as cortex's ``blocked_reason``.

All HTTP / creds / local-read seams are injected, so these tests never touch the
network or disk.
"""

from __future__ import annotations

import json

from empirica.core.loop_scheduler.practitioner_heartbeat import (
    PractitionerHeartbeatEmitter,
    _practitioner_body,
    emit_practitioner_heartbeat,
)

CREDS = lambda: ("https://cortex.test/", "key-abc")  # noqa: E731
NO_CREDS = lambda: (None, None)  # noqa: E731


def _record(**over):
    base = {
        "claude_session_id": "cc-1",
        "practitioner_id": None,
        "practice_ai_id": "empirica",
        "location": "tmux_2",
        "status": "active",
        "pending_question": None,
        "active_transaction_id": "tx-9",
        "empirica_session_id": "es-1",
        "last_heartbeat": 1000.0,
    }
    base.update(over)
    return base


class _Recorder:
    """A post_fn stand-in that records its call args and returns a fixed code."""

    def __init__(self, code=200):
        self.code = code
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": json.loads(body.decode()), "headers": headers, "timeout": timeout})
        return self.code


# ---- _practitioner_body --------------------------------------------------


def test_body_omits_ai_id():
    body = _practitioner_body(_record(), machine="host-1")
    assert "ai_id" not in body  # strict-canonical resolver would 403 a basename


def test_body_maps_required_and_optional_fields():
    body = _practitioner_body(_record(), machine="host-1")
    assert body["machine"] == "host-1"
    assert body["session_id"] == "cc-1"  # the durable claude_session_id
    assert body["status"] == "active"
    assert body["location"] == "tmux_2"
    assert body["active_transaction_id"] == "tx-9"
    assert body["practitioner_id"] is None


def test_body_blocked_sets_blocked_reason():
    body = _practitioner_body(_record(status="blocked", pending_question="need schema review"), machine="host-1")
    assert body["status"] == "blocked"
    assert body["blocked_reason"] == "need schema review"


def test_body_none_without_session_id():
    assert _practitioner_body(_record(claude_session_id=""), machine="host-1") is None
    assert _practitioner_body(_record(claude_session_id=None), machine="host-1") is None


def test_body_none_without_machine():
    assert _practitioner_body(_record(), machine="") is None


# ---- emit_practitioner_heartbeat ----------------------------------------


def test_emit_skips_when_no_creds():
    rec = _Recorder()
    code = emit_practitioner_heartbeat(_record(), post_fn=rec, resolve_creds_fn=NO_CREDS)
    assert code == 0
    assert rec.calls == []  # never posts when cortex isn't configured


def test_emit_skips_unmappable_record():
    rec = _Recorder()
    code = emit_practitioner_heartbeat(_record(claude_session_id=""), post_fn=rec, resolve_creds_fn=CREDS)
    assert code == 0
    assert rec.calls == []


def test_emit_posts_to_endpoint_with_bearer():
    rec = _Recorder(code=200)
    code = emit_practitioner_heartbeat(_record(), machine="host-1", post_fn=rec, resolve_creds_fn=CREDS)
    assert code == 200
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["url"] == "https://cortex.test/v1/practitioners/heartbeat"  # rstrip('/') applied
    assert call["headers"]["Authorization"] == "Bearer key-abc"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["body"]["session_id"] == "cc-1"
    assert "ai_id" not in call["body"]


def test_emit_propagates_error_code():
    rec = _Recorder(code=403)
    code = emit_practitioner_heartbeat(_record(), post_fn=rec, resolve_creds_fn=CREDS)
    assert code == 403


# ---- PractitionerHeartbeatEmitter ---------------------------------------


def test_emit_once_emits_each_local_practitioner():
    rec = _Recorder(code=200)
    records = [_record(claude_session_id="cc-a"), _record(claude_session_id="cc-b")]
    emitter = PractitionerHeartbeatEmitter(
        machine="host-1",
        _post_fn=rec,
        _resolve_creds_fn=CREDS,
        _list_fn=lambda: records,
    )
    results = emitter.emit_once()
    assert results == {"cc-a": 200, "cc-b": 200}
    assert len(rec.calls) == 2
    assert {c["body"]["session_id"] for c in rec.calls} == {"cc-a", "cc-b"}


def test_emit_once_survives_list_failure():
    def _boom():
        raise RuntimeError("disk gone")

    emitter = PractitionerHeartbeatEmitter(_post_fn=_Recorder(), _resolve_creds_fn=CREDS, _list_fn=_boom)
    assert emitter.emit_once() == {}  # never raises — returns empty


def test_emit_once_empty_when_no_practitioners():
    rec = _Recorder()
    emitter = PractitionerHeartbeatEmitter(_post_fn=rec, _resolve_creds_fn=CREDS, _list_fn=lambda: [])
    assert emitter.emit_once() == {}
    assert rec.calls == []


def test_start_stop_idempotent():
    # Use a long interval so the loop blocks on the stop event after one tick.
    emitter = PractitionerHeartbeatEmitter(
        interval_sec=3600,
        _post_fn=_Recorder(),
        _resolve_creds_fn=NO_CREDS,
        _list_fn=lambda: [],
    )
    emitter.start()
    emitter.start()  # idempotent — no second thread
    assert emitter._thread is not None and emitter._thread.is_alive()
    emitter.stop(timeout=2.0)
    emitter.stop(timeout=2.0)  # idempotent
    assert emitter._thread is None
