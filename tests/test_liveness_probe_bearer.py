"""The liveness probe resolves its bearer for every probe, and retries a 401 once with a fresh one.

NLE (prop_36a6f6wn7rh3papbu3bzcix6fi), measured against cortex: `_run()` loaded the OAuth bearer once at thread start and
`_do_probe` reused that string for the process lifetime. The access token lives 24 hours, so every listener on a box (they share
credentials.yaml) got a 401 from the roster endpoint at the same expiry, counted four misses (240 s), `os._exit(2)`-ed and was
restarted by its supervisor together with the others, which aligned their clocks so they cycled together every day. Catch-up in
the same process re-resolved per call and got a 200 in the same second.

Now: a bearer per probe, and on a 401 one re-resolve and retry before the miss is counted. The exit-on-stale contract is unchanged.
"""

from __future__ import annotations

import io
import threading
import urllib.error

from empirica.core.loop_scheduler.liveness_probe import LivenessProbe

URL = "https://cortex.test"


class _Tokens:
    """A loader whose bearer changes over time, like a refreshed OAuth access token."""

    def __init__(self, *tokens):
        self.tokens = list(tokens)
        self.calls = 0

    def __call__(self):
        token = self.tokens[min(self.calls, len(self.tokens) - 1)]
        self.calls += 1
        return None if token is None else {"url": URL, "api_key": token}


class _Server:
    """A roster endpoint that accepts only some tokens and records what it was shown."""

    def __init__(self, accepts):
        self.accepts = set(accepts)
        self.seen: list[str] = []

    def __call__(self, url, api_key):
        self.seen.append(api_key)
        if api_key not in self.accepts:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b""))
        return 200


def _probe(loader, server, **kw):
    exits = []
    probe = LivenessProbe(
        "empirica",
        _cortex_loader=loader,
        _probe_fn=server,
        _exit_fn=exits.append,
        _err_stream=io.StringIO(),
        _now=kw.pop("_now", None),
        **kw,
    )
    return probe, exits


def test_each_probe_resolves_the_bearer_afresh():
    loader, server = _Tokens("t1", "t2", "t3"), _Server({"t1", "t2", "t3"})
    probe, _ = _probe(loader, server)

    probe._probe_once()
    probe._probe_once()
    probe._probe_once()

    assert server.seen == ["t1", "t2", "t3"]


def test_a_401_on_a_stale_token_re_resolves_once_and_does_not_count_a_miss():
    """The bug: the captured token expired, every probe 401-ed, four misses, exit. Now the retry carries the refreshed token."""
    loader, server = _Tokens("stale", "fresh"), _Server({"fresh"})
    probe, _ = _probe(loader, server)

    probe._probe_once()

    assert server.seen == ["stale", "fresh"]
    assert probe._consecutive_failures == 0 and probe._last_ok_at is not None


def test_a_401_that_a_re_resolve_cannot_fix_counts_exactly_one_miss_and_does_not_loop():
    """A revoked token stays revoked: the loader keeps returning it, so retrying cannot help and must not spin."""
    loader, server = _Tokens("revoked"), _Server(set())
    probe, _ = _probe(loader, server)

    probe._probe_once()

    assert server.seen == ["revoked"] and probe._consecutive_failures == 1


def test_a_retry_that_also_fails_counts_one_miss_not_two():
    loader, server = _Tokens("a", "b"), _Server(set())
    probe, _ = _probe(loader, server)

    probe._probe_once()

    assert server.seen == ["a", "b"] and probe._consecutive_failures == 1


def test_credentials_vanishing_mid_run_is_a_miss_not_a_crash():
    loader, server = _Tokens("t1", None), _Server({"t1"})
    probe, _ = _probe(loader, server)

    probe._probe_once()
    probe._probe_once()

    assert probe._consecutive_failures == 1 and server.seen == ["t1"]


def test_a_non_401_http_error_is_not_retried():
    """Only an auth failure is worth a fresh bearer; a 503 is the server's, and a retry would double the load."""

    def server(url, api_key):
        seen.append(api_key)
        raise urllib.error.HTTPError(url, 503, "Unavailable", {}, io.BytesIO(b""))

    seen: list[str] = []
    probe, _ = _probe(_Tokens("t1", "t2"), server)

    probe._probe_once()

    assert seen == ["t1"] and probe._consecutive_failures == 1


def test_the_running_loop_follows_a_token_that_changes_and_never_exits():
    """End to end through `_run`: three passes, the accepted token rotates after the first, nothing goes stale."""
    loader = _Tokens("t1", "t1", "t2", "t3", "t3")  # first call is the arming check
    server = _Server({"t1", "t2", "t3"})
    stop = threading.Event()
    passes = {"n": 0}

    def sleep(_s):
        passes["n"] += 1
        if passes["n"] >= 3:
            stop.set()

    probe, exits = _probe(loader, server, _sleep=sleep)
    probe._stop_evt = stop

    probe._run()

    assert len(set(server.seen)) >= 2, server.seen
    assert probe._consecutive_failures == 0 and exits == []


def test_the_exit_on_stale_contract_still_fires_when_every_probe_really_fails():
    """Control for the refactor: a seat whose credentials are genuinely dead still exits for the supervisor."""
    clock = {"t": 1000.0}
    loader, server = _Tokens("dead"), _Server(set())
    probe, exits = _probe(loader, server, fail_threshold_sec=240.0, _now=lambda: clock["t"])
    probe._last_ok_at = clock["t"]

    for _ in range(6):
        probe._probe_once()
        clock["t"] += 60
        probe._check_staleness()
        if exits:
            break  # the real os._exit never returns; the stand-in does

    assert exits == [2]
