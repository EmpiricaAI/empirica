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

import pytest

from empirica.core.loop_scheduler.practitioner_heartbeat import (
    PractitionerHeartbeatEmitter,
    _practitioner_body,
    _reset_canonical_cache,
    emit_practitioner_heartbeat,
    resolve_canonical_ai_id,
)

CREDS = lambda: ("https://cortex.test/", "key-abc")  # noqa: E731
NO_CREDS = lambda: (None, None)  # noqa: E731

# A fake projects-endpoint GET resolving the test practice → canonical 3-form.
PROJECTS_BODY = {"projects": [{"slug": "empirica", "ai_id_canonical": "empirica.david.empirica"}]}
GET_PROJECTS = lambda url, key, timeout: PROJECTS_BODY  # noqa: E731
GET_EMPTY = lambda url, key, timeout: {"projects": []}  # noqa: E731


@pytest.fixture(autouse=True)
def _clear_canonical_cache():
    """The slug→canonical map is a module-level cache; reset it around each test."""
    _reset_canonical_cache()
    yield
    _reset_canonical_cache()


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


def test_body_omits_ai_id_when_unresolved():
    # No canonical passed → ai_id omitted (cortex leaves practice_id NULL).
    body = _practitioner_body(_record(), machine="host-1")
    assert "ai_id" not in body


def test_body_includes_canonical_ai_id():
    body = _practitioner_body(_record(), machine="host-1", canonical_ai_id="empirica.david.empirica")
    assert body["ai_id"] == "empirica.david.empirica"  # the practice anchor


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
    code = emit_practitioner_heartbeat(
        _record(claude_session_id=""), post_fn=rec, resolve_creds_fn=CREDS, get_fn=GET_EMPTY
    )
    assert code == 0
    assert rec.calls == []


def test_emit_posts_with_canonical_ai_id():
    rec = _Recorder(code=200)
    code = emit_practitioner_heartbeat(
        _record(), machine="host-1", post_fn=rec, resolve_creds_fn=CREDS, get_fn=GET_PROJECTS
    )
    assert code == 200
    call = rec.calls[0]
    assert call["url"] == "https://cortex.test/v1/practitioners/heartbeat"  # rstrip('/') applied
    assert call["headers"]["Authorization"] == "Bearer key-abc"
    assert call["body"]["session_id"] == "cc-1"
    assert call["body"]["ai_id"] == "empirica.david.empirica"  # resolved practice anchor


def test_emit_omits_ai_id_when_canonical_unresolved():
    rec = _Recorder(code=200)
    emit_practitioner_heartbeat(_record(), machine="host-1", post_fn=rec, resolve_creds_fn=CREDS, get_fn=GET_EMPTY)
    assert "ai_id" not in rec.calls[0]["body"]  # graceful — back-compat NULL practice_id


def test_emit_propagates_error_code():
    rec = _Recorder(code=403)
    code = emit_practitioner_heartbeat(_record(), post_fn=rec, resolve_creds_fn=CREDS, get_fn=GET_PROJECTS)
    assert code == 403


# ---- PractitionerHeartbeatEmitter ---------------------------------------


def test_emit_once_emits_each_local_practitioner():
    rec = _Recorder(code=200)
    records = [_record(claude_session_id="cc-a"), _record(claude_session_id="cc-b")]
    emitter = PractitionerHeartbeatEmitter(
        machine="host-1",
        _post_fn=rec,
        _resolve_creds_fn=CREDS,
        _get_fn=GET_PROJECTS,
        _list_fn=lambda: records,
        _refresh_fn=lambda: {},  # hermetic — don't touch the real ~/.empirica
    )
    results = emitter.emit_once()
    assert results == {"cc-a": 200, "cc-b": 200}
    assert len(rec.calls) == 2
    assert {c["body"]["session_id"] for c in rec.calls} == {"cc-a", "cc-b"}
    # canonical ai_id resolved + attached on each
    assert all(c["body"]["ai_id"] == "empirica.david.empirica" for c in rec.calls)


def test_emit_once_survives_list_failure():
    def _boom():
        raise RuntimeError("disk gone")

    emitter = PractitionerHeartbeatEmitter(
        _post_fn=_Recorder(), _resolve_creds_fn=CREDS, _list_fn=_boom, _refresh_fn=lambda: {}
    )
    assert emitter.emit_once() == {}  # never raises — returns empty


def test_emit_once_empty_when_no_practitioners():
    rec = _Recorder()
    emitter = PractitionerHeartbeatEmitter(
        _post_fn=rec, _resolve_creds_fn=CREDS, _list_fn=lambda: [], _refresh_fn=lambda: {}
    )
    assert emitter.emit_once() == {}
    assert rec.calls == []


def test_emit_once_refreshes_liveness_before_listing():
    """The daemon re-stamps alive-PID presence BEFORE listing — so an alive but
    quiet (blocked) session is fresh when forwarded, not dropped as stale."""
    order = []
    rec = _Recorder(code=200)

    def _refresh():
        order.append("refresh")
        return {"refreshed": 1}

    def _list():
        order.append("list")
        return [_record(claude_session_id="cc-a")]

    emitter = PractitionerHeartbeatEmitter(
        machine="host-1",
        _post_fn=rec,
        _resolve_creds_fn=CREDS,
        _get_fn=GET_PROJECTS,
        _list_fn=_list,
        _refresh_fn=_refresh,
    )
    emitter.emit_once()
    assert order == ["refresh", "list"]  # liveness re-stamp precedes the read


def test_emit_once_survives_refresh_failure():
    """A refresh blowup never breaks emission — list/forward still proceed."""

    def _boom_refresh():
        raise RuntimeError("fs gone")

    rec = _Recorder(code=200)
    emitter = PractitionerHeartbeatEmitter(
        machine="host-1",
        _post_fn=rec,
        _resolve_creds_fn=CREDS,
        _get_fn=GET_PROJECTS,
        _list_fn=lambda: [_record(claude_session_id="cc-a")],
        _refresh_fn=_boom_refresh,
    )
    assert emitter.emit_once() == {"cc-a": 200}  # emission unaffected by refresh failure


def test_start_stop_idempotent():
    # Use a long interval so the loop blocks on the stop event after one tick.
    emitter = PractitionerHeartbeatEmitter(
        interval_sec=3600,
        _post_fn=_Recorder(),
        _resolve_creds_fn=NO_CREDS,
        _list_fn=lambda: [],
        _refresh_fn=lambda: {},
    )
    emitter.start()
    emitter.start()  # idempotent — no second thread
    assert emitter._thread is not None and emitter._thread.is_alive()
    emitter.stop(timeout=2.0)
    emitter.stop(timeout=2.0)  # idempotent
    assert emitter._thread is None


# ---- resolve_canonical_ai_id (slug → canonical 3-form, cached) ---------------


def test_resolve_canonical_hit():
    canon = resolve_canonical_ai_id("empirica", resolve_creds_fn=CREDS, get_fn=GET_PROJECTS)
    assert canon == "empirica.david.empirica"


def test_resolve_canonical_unknown_basename():
    assert resolve_canonical_ai_id("not-a-practice", resolve_creds_fn=CREDS, get_fn=GET_PROJECTS) is None


def test_resolve_canonical_none_inputs():
    assert resolve_canonical_ai_id(None, resolve_creds_fn=CREDS, get_fn=GET_PROJECTS) is None
    assert resolve_canonical_ai_id("empirica", resolve_creds_fn=NO_CREDS, get_fn=GET_PROJECTS) is None


def test_resolve_canonical_caches_successful_fetch():
    calls = {"n": 0}

    def _counting_get(url, key, timeout):
        calls["n"] += 1
        return PROJECTS_BODY

    resolve_canonical_ai_id("empirica", resolve_creds_fn=CREDS, get_fn=_counting_get)
    resolve_canonical_ai_id("empirica", resolve_creds_fn=CREDS, get_fn=_counting_get)
    assert calls["n"] == 1  # second lookup hits the cache, no re-fetch


def test_resolve_canonical_retries_after_empty_fetch():
    # An empty (failed) fetch must NOT be cached — the next call retries.
    calls = {"n": 0}

    def _flaky_get(url, key, timeout):
        calls["n"] += 1
        return {"projects": []} if calls["n"] == 1 else PROJECTS_BODY

    assert resolve_canonical_ai_id("empirica", resolve_creds_fn=CREDS, get_fn=_flaky_get) is None
    assert resolve_canonical_ai_id("empirica", resolve_creds_fn=CREDS, get_fn=_flaky_get) == "empirica.david.empirica"
    assert calls["n"] == 2
