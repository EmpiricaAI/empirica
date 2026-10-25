"""The heartbeat emitters outlast a cortex restart instead of reading it as an outage (cortex prop_jge3ux4dmjdavapdybrrnl5lnq, 2026-10-23).

Cortex now holds its listening socket under systemd, so a restart or deploy QUEUES connections for the ~5 s the process takes to start
where it used to refuse them. A client whose timeout is 5.0 s therefore times out exactly when the queue is longest. Cortex asked for
a timeout of 10-15 s and for retry with backoff before anything is marked down. The liveness probe (8 s HTTP timeout, hard exit only after
240 s without a success) and the catch-up fetch (10 s) already comply; the two heartbeat emitters sat at 5.0 s.

Raising the timeout has a cost the practitioner emitter would otherwise pay N times: it posts one record per live session, so a real
outage would hold the cycle for N x timeout. It stops the cycle at the first network failure and retries on the next interval, which is
also the backoff cortex asked for. A beat that misses is harmless: cortex's TTL is about two minutes against a 45 s interval.
"""

from __future__ import annotations

import pytest

from empirica.core.loop_scheduler import heartbeat as hb
from empirica.core.loop_scheduler import practitioner_heartbeat as ph

RESTART_QUEUE_S = 5.0  # cortex's measured restart queue


def test_the_listener_heartbeat_timeout_is_longer_than_a_restart_queue_and_shorter_than_its_interval():
    assert hb._DEFAULT_TIMEOUT_SEC > RESTART_QUEUE_S
    assert hb._DEFAULT_TIMEOUT_SEC >= 10.0
    assert hb._DEFAULT_TIMEOUT_SEC < hb._DEFAULT_INTERVAL_SEC, "a hung post must not run into the next beat"


def test_the_practitioner_heartbeat_timeout_is_longer_than_a_restart_queue_and_shorter_than_its_interval():
    assert ph._DEFAULT_TIMEOUT_SEC > RESTART_QUEUE_S
    assert ph._DEFAULT_TIMEOUT_SEC >= 10.0
    assert ph._DEFAULT_TIMEOUT_SEC < ph._DEFAULT_INTERVAL_SEC


def test_the_listener_emitter_really_passes_that_timeout_to_the_post():
    seen = []

    def post(url, body, headers, timeout):
        seen.append(timeout)
        return 200

    hb.HeartbeatEmitter("p", _post_fn=post, _resolve_creds_fn=lambda: ("https://c.example", "k")).emit_once()
    assert seen == [hb._DEFAULT_TIMEOUT_SEC]


def _records(n):
    return [{"claude_session_id": f"s{i}", "ai_id": "p", "status": "alive"} for i in range(n)]


def _emitter(post, n):
    return ph.PractitionerHeartbeatEmitter(
        ai_id="p",
        machine="m",
        _post_fn=post,
        _resolve_creds_fn=lambda: ("https://c.example", "k"),
        _get_fn=lambda url, key, timeout: {},
        _list_fn=lambda: _records(n),
        _refresh_fn=lambda: {},
    )


def test_the_practitioner_emitter_passes_that_timeout_to_every_post():
    seen = []

    def post(url, body, headers, timeout):
        seen.append(timeout)
        return 200

    _emitter(post, 2).emit_once()
    assert seen and set(seen) == {ph._DEFAULT_TIMEOUT_SEC}


def test_one_network_failure_stops_the_cycle_instead_of_timing_out_once_per_session():
    posts = []

    def down(url, body, headers, timeout):
        posts.append(url)
        return -1  # the post helpers return -1 for a timeout or a refused connection

    results = _emitter(down, 5).emit_once()
    assert len(posts) == 1, "five sessions must not mean five 12-second timeouts against a cortex that is down"
    assert list(results.values()) == [-1]


def test_a_failure_after_successes_keeps_what_was_sent_and_stops():
    state = {"n": 0}

    def flaky(url, body, headers, timeout):
        state["n"] += 1
        return 200 if state["n"] < 3 else -1

    results = _emitter(flaky, 6).emit_once()
    assert state["n"] == 3 and list(results.values()) == [200, 200, -1]


@pytest.mark.parametrize("status", [200, 202, 401, 429, 500])
def test_an_http_status_is_not_a_network_failure_and_does_not_stop_the_cycle(status):
    """Only -1 (no answer at all) stops the cycle: a 4xx/5xx is an answer, and the next session may still succeed."""
    posts = []

    def answers(url, body, headers, timeout):
        posts.append(url)
        return status

    _emitter(answers, 3).emit_once()
    assert len(posts) == 3


def test_a_record_that_raises_does_not_silence_the_sessions_after_it():
    """Review finding (mailbox reviewer, traced): -1 is recorded both for 'no answer' and for an exception inside one record's emit. Only
    the first means cortex is down; an exception is one bad record and the next session should still be sent, every interval."""
    state = {"n": 0}

    def first_raises(url, body, headers, timeout):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("one bad record")
        return 200

    results = _emitter(first_raises, 3).emit_once()
    assert state["n"] == 3, "the two sessions after the bad record must still be posted"
    assert list(results.values()) == [-1, 200, 200]
