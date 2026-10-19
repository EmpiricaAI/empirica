"""The listener must never subscribe with the bare basename, and one failed lookup must not stick.

Reported by empirica.philipp.empirica-mesh-support (prop_bxjntibm, defect 1), first diagnosed by empirica-workspace: after a
network drop the ntfy stream goes stale, the process restarts, canonical ai_id resolution fails while DNS is down, and the
listener subscribed with ``tags=<basename>``. Publishers tag with the canonical 3-form, so the stream stayed connected and
received nothing while the health file read ok: nine fleet-wide outages since 2026-10-06.

Two causes, both read before the fix:

1. ``content_poll._resolve_canonical_ai_id`` stored the basename FALLBACK in a process-wide cache, so one failed lookup made
   every later lookup in that process return the basename.
2. ``listener.resolve_tag_filter`` returned the basename on no-credential, on exception and on that cached fallback.

No test here touches the network, the clock or the box: urlopen, sleep and the health writer are all replaced.
"""

from __future__ import annotations

import io
import json
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from empirica.core.loop_scheduler import content_poll, listener

CANONICAL = "empirica.david.empirica-cortex"
ROSTER = {
    "self": {"tenant_slug": "david"},
    "org": {
        "tenants": [{"tenant_slug": "david", "projects": [{"ai_id_short": "empirica-cortex", "ai_id_mesh": CANONICAL}]}]
    },
}


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _clean_cache():
    content_poll.reset_canonical_cache()
    yield
    content_poll.reset_canonical_cache()


# --- 1. one failed lookup must not stick -------------------------------------------------------------------------------


def test_a_failed_lookup_is_not_remembered_for_the_life_of_the_process(monkeypatch):
    outcomes = iter([OSError("dns down"), _Resp(ROSTER)])
    now = [1000.0]
    monkeypatch.setattr(content_poll.time, "monotonic", lambda: now[0])

    def fake_urlopen(*_a, **_k):
        o = next(outcomes)
        if isinstance(o, Exception):
            raise o
        return o

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert content_poll._resolve_canonical_ai_id("http://c", "k", "empirica-cortex") == "empirica-cortex"
    now[0] += content_poll._CANONICAL_FAILURE_TTL_S + 1
    assert content_poll._resolve_canonical_ai_id("http://c", "k", "empirica-cortex") == CANONICAL


def test_a_failure_is_not_retried_on_every_call_inside_the_short_window(monkeypatch):
    calls = {"n": 0}
    now = [1000.0]
    monkeypatch.setattr(content_poll.time, "monotonic", lambda: now[0])

    def fake_urlopen(*_a, **_k):
        calls["n"] += 1
        raise OSError("dns down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    for _ in range(5):
        content_poll._resolve_canonical_ai_id("http://c", "k", "empirica-cortex")
    assert calls["n"] == 1, "the poll paths must not hammer the roster while it is unreachable"


def test_a_success_is_still_cached(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(*_a, **_k):
        calls["n"] += 1
        return _Resp(ROSTER)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    for _ in range(3):
        assert content_poll._resolve_canonical_ai_id("http://c", "k", "empirica-cortex") == CANONICAL
    assert calls["n"] == 1


# --- 2. the listener never returns the basename ------------------------------------------------------------------------


def _tag(resolver_results, *, cred=None, attempts=4):
    """Call the real resolve_tag_filter; `resolver_results` is what the roster lookup returns on each attempt."""
    cred = cred if cred is not None else {"url": "https://cortex.example", "bearer": "t", "source": "oauth"}
    err, sleeps = io.StringIO(), []
    results = iter(resolver_results)

    def resolver(*_a, **_k):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r

    with (
        patch("empirica.core.auth.cortex_oauth.cortex_bearer", return_value=cred),
        patch("empirica.core.loop_scheduler.content_poll._resolve_canonical_ai_id", side_effect=resolver),
        patch("empirica.config.credentials_loader.get_credentials_loader", return_value=MagicMock()),
    ):
        tag = listener.resolve_tag_filter("empirica-cortex", err, sleep=sleeps.append, attempts=attempts)
    return tag, err.getvalue(), sleeps


def test_the_canonical_tag_is_returned_when_it_resolves_first_time():
    tag, err, sleeps = _tag([CANONICAL])
    assert tag == CANONICAL and err == "" and sleeps == []


def test_a_transient_failure_is_retried_until_the_canonical_resolves():
    # the resolver hands back the bare basename on its own failures; that is NOT a resolution
    tag, err, sleeps = _tag(["empirica-cortex", "empirica-cortex", CANONICAL])
    assert tag == CANONICAL
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps), "it backs off between attempts"
    assert "retry" in err.lower()


def test_an_exception_during_resolution_is_retried_too():
    tag, _err, sleeps = _tag([OSError("dns down"), CANONICAL])
    assert tag == CANONICAL and len(sleeps) == 1


def test_a_persistent_failure_refuses_instead_of_returning_the_basename():
    tag, err, sleeps = _tag(["empirica-cortex"] * 4, attempts=4)
    assert tag is None, "returning the basename is the outage this fixes"
    assert len(sleeps) == 3, "it waited between the four attempts, not after the last"
    assert "refus" in err.lower() and "empirica-cortex" in err


def test_no_cortex_credential_refuses_instead_of_returning_the_basename():
    tag, err, sleeps = _tag([], cred={"url": None, "bearer": None, "source": "none", "reason": "no cortex credential"})
    assert tag is None and sleeps == [], "a missing credential is configuration, not a transient failure: no retry"
    assert "no cortex credential" in err and "refus" in err.lower()


def test_backoff_is_bounded():
    _tag_unused, _err, sleeps = _tag(["empirica-cortex"] * 12, attempts=12)
    assert max(sleeps) <= listener._RECONNECT_MAX_SEC


# --- 3. run_listener exits loudly instead of subscribing ---------------------------------------------------------------


def test_run_listener_exits_2_and_never_opens_a_stream_when_the_tag_cannot_be_resolved(monkeypatch):
    """Everything that could reach the box or the network is replaced, and the stream factory FAILS the test if it is
    called: against unfixed code this must go red quickly, not run the real listener loop."""
    degraded = []
    monkeypatch.setattr(listener, "resolve_tag_filter", lambda *a, **k: None)
    monkeypatch.setattr(listener, "_emit_fail_heartbeat", lambda *a, **k: degraded.append(k.get("reason")))
    monkeypatch.delenv("EMPIRICA_NTFY_TAG_FILTER", raising=False)
    for module, name in (
        ("empirica.core.loop_scheduler.liveness_probe", "LivenessProbe"),
        ("empirica.core.loop_scheduler.heartbeat", "HeartbeatEmitter"),
        ("empirica.core.loop_scheduler.practitioner_heartbeat", "PractitionerHeartbeatEmitter"),
    ):
        monkeypatch.setattr(f"{module}.{name}", MagicMock())

    def never_open(*_a, **_k):
        pytest.fail("a stream was opened without the canonical tag")

    loader = MagicMock()
    loader.get_ntfy_config.return_value = {"url": "https://ntfy.example", "topic": "t", "token": "tk_x"}
    err = io.StringIO()
    with patch("empirica.config.credentials_loader.get_credentials_loader", return_value=loader):
        rc = listener.run_listener(
            "empirica-cortex",
            err_stream=err,
            _stream_factory=never_open,
            _sleep=lambda _s: None,
            _initial_catchup=False,
        )
    assert rc == 2
    assert degraded == ["canonical_tag_unresolved"], "the health file must say why, not read ok"
