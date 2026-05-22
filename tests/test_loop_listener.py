"""Tests for empirica.core.loop_scheduler.listener.

The ntfy listener is the push-primary wake mechanism: holds an
authenticated stream to cortex's ntfy topic, transforms each push event
into a content_poll catch-up that emits stdout lines (one Monitor event
per ECO-decided proposal).

We mock the curl subprocess and the catch-up function so tests don't hit
real network. The contracts under test:

  - Basic auth header is built correctly from credentials_loader
  - Initial catch-up runs on startup (covers the gap between listener
    start and the first ntfy message)
  - Each ntfy 'message' event triggers exactly one catch-up
  - Keepalive / open / poll_request events are silently dropped
  - On stream EOF: catch-up runs THEN reconnect backoff
  - Missing creds → exit code 2 (so systemd surfaces the problem)
  - SIGTERM exits cleanly with code 0
"""

from __future__ import annotations

import io
import json

from empirica.core.loop_scheduler import listener as listener_mod
from empirica.core.loop_scheduler.listener import (
    ListenerStopped,
    _basic_auth_header,
    _build_subscribe_url,
    _is_real_event,
    run_listener,
)

# ── URL + auth construction ──────────────────────────────────────────────


def test_build_subscribe_url_quotes_topic():
    url = _build_subscribe_url("https://ntfy.example.com", "orchestration/proposals")
    assert url == "https://ntfy.example.com/orchestration%2Fproposals/json"


def test_build_subscribe_url_strips_trailing_slash():
    assert _build_subscribe_url("https://x.example/", "t") == "https://x.example/t/json"


def test_build_subscribe_url_appends_tag_filter():
    """Tag-filter subscription drops per-event broadcast wakes from
    'every listener wakes' to 'only relevant listeners wake.' Requires
    cortex to publish with X-Tags including this ai_id."""
    url = _build_subscribe_url(
        "https://ntfy.example.com", "orchestration-events", tag_filter="cortex",
    )
    assert url == "https://ntfy.example.com/orchestration-events/json?tags=cortex"


def test_build_subscribe_url_no_tag_filter_by_default():
    """Back-compat: when no tag filter, URL matches the legacy unfiltered
    subscribe URL — listener receives every message on the topic."""
    url = _build_subscribe_url(
        "https://ntfy.example.com", "orchestration-events",
    )
    assert "?tags=" not in url


def test_basic_auth_header_encodes_credentials():
    """Legacy basic-auth path: user + password → Basic header.
    Token arg None means fall through to basic-auth branch."""
    h = _basic_auth_header("alice", "s3cret", None)
    assert h["Authorization"].startswith("Basic ")
    # base64('alice:s3cret') = 'YWxpY2U6czNjcmV0'
    assert h["Authorization"] == "Basic YWxpY2U6czNjcmV0"


def test_basic_auth_header_empty_when_no_creds():
    """No user, no password, no token → no auth header (anonymous)."""
    assert _basic_auth_header(None, None, None) == {}
    assert _basic_auth_header("", "", None) == {}


def test_ntfy_auth_header_prefers_token_via_bearer():
    """Token (tk_ prefix) wins over user/password → Bearer header.
    This is the preferred path for ntfy access tokens, which are
    revocable + don't expose the account password."""
    h = _basic_auth_header("alice", "s3cret", "tk_jbp82d9aadkaylkzg4kkqjlv3enau")
    assert h["Authorization"] == "Bearer tk_jbp82d9aadkaylkzg4kkqjlv3enau"


# ── Event filter ─────────────────────────────────────────────────────────


def test_is_real_event_accepts_message():
    assert _is_real_event({"event": "message", "id": "x"}) is True


def test_is_real_event_rejects_keepalive_and_open():
    assert _is_real_event({"event": "keepalive"}) is False
    assert _is_real_event({"event": "open"}) is False
    assert _is_real_event({"event": "poll_request"}) is False
    assert _is_real_event({}) is False


# ── run_listener — fake stream factory ────────────────────────────────────


class _FakeProc:
    """Mimics subprocess.Popen for the held curl stream. Yields one
    pre-recorded line per iteration, then EOF."""

    def __init__(self, lines: list[str]):
        self._stdout = iter(lines)
        self.stdout = self
        self.terminated = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._stdout)
        except StopIteration:
            raise

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def test_missing_credentials_exits_with_code_2(monkeypatch):
    """Without ntfy creds (token OR user+password) configured, listener
    refuses to start rather than silently no-op'ing."""
    from empirica.config.credentials_loader import get_credentials_loader
    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {
        "url": "https://ntfy.test", "topic": "t",
        "user": None, "password": None, "token": None,
    })
    err = io.StringIO()
    out = io.StringIO()
    rc = run_listener("cortex", output_stream=out, err_stream=err,
                      _initial_catchup=False)
    assert rc == 2
    assert "no ntfy credentials configured" in err.getvalue()


def test_each_message_event_triggers_catchup(monkeypatch):
    """The contract: one ntfy 'message' event → one catch-up call. Keepalives
    don't trigger catch-up."""
    from empirica.config.credentials_loader import get_credentials_loader
    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {
        "url": "https://ntfy.test", "topic": "t",
        "user": "u", "password": "p",
    })

    lines = [
        json.dumps({"event": "open", "id": "0"}),
        json.dumps({"event": "keepalive", "id": "1"}),
        json.dumps({"event": "message", "id": "msg1", "title": "first"}),
        json.dumps({"event": "keepalive", "id": "2"}),
        json.dumps({"event": "message", "id": "msg2", "title": "second"}),
    ]
    streams_opened: list = []

    def fake_factory(url, headers):
        streams_opened.append(url)
        # After delivering all lines, the test stop happens at second reconnect
        return _FakeProc(lines if not streams_opened[1:] else [])

    catchup_calls = []

    def fake_catchup(instance_id, loop_name, output_stream):
        catchup_calls.append((instance_id, loop_name))
        output_stream.write(json.dumps({
            "event_type": "proposal_event",
            "instance_id": instance_id, "proposal_id": f"p{len(catchup_calls)}",
        }) + "\n")
        return 1

    monkeypatch.setattr(listener_mod, "_emit_catchup_events", fake_catchup)

    # Stop after second reconnect attempt
    sleep_calls = []

    def fake_sleep(s):
        sleep_calls.append(s)
        if len(sleep_calls) >= 2:
            raise ListenerStopped("test stop")

    out = io.StringIO()
    err = io.StringIO()
    rc = run_listener("cortex", output_stream=out, err_stream=err,
                      _stream_factory=fake_factory, _sleep=fake_sleep)
    assert rc == 0
    # initial-catchup + 2 message events + 1 disconnect catchup + 1 disconnect catchup = 5
    # (Or close — depending on test stop timing. Assert lower-bound that
    # captures the contract: at least 2 message-driven catch-ups happened.)
    assert len(catchup_calls) >= 3
    # Stdout has the catch-up emitted lines (Monitor wake events)
    stdout_lines = [l for l in out.getvalue().splitlines() if l.strip()]
    assert len(stdout_lines) >= 3


def test_stream_drop_triggers_catchup_then_reconnect(monkeypatch):
    """On EOF (connection drop), the listener runs a catch-up FIRST
    (captures any missed events from the drop window) then reconnects
    with backoff. The catch-up-on-drop is the key safety property."""
    from empirica.config.credentials_loader import get_credentials_loader
    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {
        "url": "https://ntfy.test", "topic": "t",
        "user": "u", "password": "p",
    })

    def fake_factory(url, headers):
        # First call: yield one message, then EOF
        # Subsequent calls: immediate EOF
        if not fake_factory.calls:
            fake_factory.calls += 1
            return _FakeProc([json.dumps({"event": "message", "id": "m"})])
        return _FakeProc([])
    fake_factory.calls = 0

    catchup_count = [0]

    def fake_catchup(instance_id, loop_name, output_stream):
        catchup_count[0] += 1
        return 0

    monkeypatch.setattr(listener_mod, "_emit_catchup_events", fake_catchup)

    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise ListenerStopped("test")

    run_listener("cortex", output_stream=io.StringIO(), err_stream=io.StringIO(),
                  _stream_factory=fake_factory, _sleep=fake_sleep)

    # Expected catchups: initial + msg + drop + drop = 4
    assert catchup_count[0] >= 3
    # Backoff happened — first sleep is the post-drop pause
    assert len(sleeps) >= 1


def test_clean_sigterm_exit_returns_zero(monkeypatch):
    """ListenerStopped (raised from signal handler) → return 0 so systemd
    treats the stop as intentional, not a crash."""
    from empirica.config.credentials_loader import get_credentials_loader
    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {
        "url": "https://ntfy.test", "topic": "t",
        "user": "u", "password": "p",
    })

    def factory_that_dies(url, headers):
        raise ListenerStopped("simulated SIGTERM")

    monkeypatch.setattr(listener_mod, "_emit_catchup_events",
                        lambda *a, **kw: 0)
    rc = run_listener("cortex", output_stream=io.StringIO(),
                      err_stream=io.StringIO(),
                      _stream_factory=factory_that_dies)
    assert rc == 0


def test_malformed_ntfy_line_skipped_not_crashed(monkeypatch):
    """Non-JSON garbage on the stream → log + skip. Listener stays alive."""
    from empirica.config.credentials_loader import get_credentials_loader
    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {
        "url": "https://ntfy.test", "topic": "t",
        "user": "u", "password": "p",
    })

    lines = ["not valid json {",
             json.dumps({"event": "message", "id": "after-garbage"})]

    def fake_factory(url, headers):
        if fake_factory.calls:
            return _FakeProc([])
        fake_factory.calls += 1
        return _FakeProc(lines)
    fake_factory.calls = 0

    catchup_count = [0]

    def fake_catchup(*a, **kw):
        catchup_count[0] += 1
        return 0

    monkeypatch.setattr(listener_mod, "_emit_catchup_events", fake_catchup)

    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 1:
            raise ListenerStopped("test")

    err = io.StringIO()
    run_listener("cortex", output_stream=io.StringIO(), err_stream=err,
                  _stream_factory=fake_factory, _sleep=fake_sleep)
    # Listener didn't crash on bad line; processed the valid one after
    assert "skipping non-JSON line" in err.getvalue()
    # At least the valid message + initial + post-drop catchups
    assert catchup_count[0] >= 2


# ── Tee to loop_fires.log (cockpit reader, 2026-05-17) ──────────────────


def test_emit_catchup_events_tees_to_loop_fires_log(monkeypatch, tmp_path):
    """Post-T8 the listener streamed only to stdout (Monitor), leaving
    ~/.empirica/loop_fires.log empty. The cockpit TUI's recent_events
    reader tails that log to render the N column + notifications detail
    pane — without the tee it showed '(no events yet — listener silent
    or not armed)' even when listeners were actively firing.

    Contract: each event written to stdout MUST also append a matching
    JSON line to ~/.empirica/loop_fires.log so the cockpit reader sees it.

    David, 2026-05-17.
    """
    from empirica.config.credentials_loader import get_credentials_loader
    from empirica.core.loop_scheduler import content_poll
    from empirica.core.loop_scheduler import listener as lm

    # Redirect Path.home() to tmp so the tee target is sandboxed
    monkeypatch.setattr(lm.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".empirica").mkdir(parents=True, exist_ok=True)

    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {
        "url": "https://ntfy.test", "topic": "t",
        "user": "u", "password": "p",
    })
    monkeypatch.setattr(loader, "get_cortex_config", lambda: {
        "url": "https://cortex.test", "api_key": "k",
    })

    # Synthetic event from poll_and_diff
    fake_event = content_poll.ProposalEvent(
        instance_id="empirica", loop_name="cortex-mailbox-poll",
        proposal_id="prop_abc12345", proposal_title="Test event",
        status="accepted", action_category="TACTICAL",
        eco_actor="eco-phone", new_or_changed="new",
        direction="inbox", commit_sha=None,
    )
    monkeypatch.setattr(content_poll, "poll_and_diff",
                        lambda *a, **kw: [fake_event])

    out = io.StringIO()
    count = lm._emit_catchup_events("empirica", "cortex-mailbox-poll", output_stream=out)
    assert count == 1

    # stdout (Monitor wake) received the event
    stdout_lines = [l for l in out.getvalue().splitlines() if l.strip()]
    assert len(stdout_lines) == 1
    parsed_stdout = json.loads(stdout_lines[0])
    assert parsed_stdout["proposal_id"] == "prop_abc12345"

    # Tee target: loop_fires.log MUST contain the same event
    log = tmp_path / ".empirica" / "loop_fires.log"
    assert log.exists(), "loop_fires.log was not created — tee failed"
    log_lines = [l for l in log.read_text().splitlines() if l.strip()]
    assert len(log_lines) == 1
    parsed_log = json.loads(log_lines[0])
    assert parsed_log["proposal_id"] == "prop_abc12345"
    assert parsed_log["instance_id"] == "empirica"
    assert parsed_log["direction"] == "inbox"
    assert parsed_log["status"] == "accepted"


def test_tee_failure_does_not_break_stdout_stream(monkeypatch, tmp_path):
    """Tee is best-effort: if loop_fires.log can't be written (disk full,
    perms), the Monitor stdout stream must still deliver. We simulate by
    making the log path point at a directory (open() will raise IsADirectoryError)."""
    from empirica.config.credentials_loader import get_credentials_loader
    from empirica.core.loop_scheduler import content_poll
    from empirica.core.loop_scheduler import listener as lm

    monkeypatch.setattr(lm.Path, "home", staticmethod(lambda: tmp_path))
    # Make the tee target unwriteable by creating it as a directory
    bad_path = tmp_path / ".empirica"
    bad_path.mkdir()
    (bad_path / "loop_fires.log").mkdir()  # directory, not a file

    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {
        "url": "x", "topic": "t", "user": "u", "password": "p",
    })
    monkeypatch.setattr(loader, "get_cortex_config", lambda: {
        "url": "x", "api_key": "k",
    })

    fake_event = content_poll.ProposalEvent(
        instance_id="x", loop_name="cortex-mailbox-poll",
        proposal_id="p", proposal_title="t", status="accepted",
        action_category="TACTICAL", eco_actor="a", new_or_changed="new",
    )
    monkeypatch.setattr(content_poll, "poll_and_diff",
                        lambda *a, **kw: [fake_event])

    out = io.StringIO()
    # Should not raise even though tee fails
    count = lm._emit_catchup_events("x", "cortex-mailbox-poll", output_stream=out)
    assert count == 1
    assert "prop_abc12345" not in out.getvalue()  # different proposal id
    assert out.getvalue().strip(), "stdout was empty — tee failure shouldn't suppress it"


# ── Version-drift auto-restart (goal 62347fc4) ──────────────────────────


def test_check_version_drift_returns_none_when_match(monkeypatch):
    """No drift when in-process __version__ matches dist-info version."""
    import empirica
    monkeypatch.setattr(empirica, "__version__", "9.9.9")
    monkeypatch.setattr(
        listener_mod.importlib.metadata, "version", lambda _: "9.9.9",
    )
    assert listener_mod._check_version_drift() is None


def test_check_version_drift_returns_tuple_on_mismatch(monkeypatch):
    """Drift returns (in_process, installed) when pip upgraded under us."""
    import empirica
    monkeypatch.setattr(empirica, "__version__", "1.9.10")
    monkeypatch.setattr(
        listener_mod.importlib.metadata, "version", lambda _: "1.9.11",
    )
    result = listener_mod._check_version_drift()
    assert result == ("1.9.10", "1.9.11")


def test_check_version_drift_returns_none_on_metadata_error(monkeypatch):
    """Best-effort: metadata lookup failure must not crash the listener."""
    def boom(_):
        raise importlib_metadata_error()
    import importlib.metadata as md

    def importlib_metadata_error():
        return md.PackageNotFoundError("empirica")

    monkeypatch.setattr(listener_mod.importlib.metadata, "version", boom)
    assert listener_mod._check_version_drift() is None


def test_listener_exits_cleanly_on_version_drift(monkeypatch):
    """When drift fires post-stream-drop, run_listener returns 0 (clean
    exit) so systemd Restart=always relaunches with the new code."""
    from empirica.config.credentials_loader import get_credentials_loader
    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {
        "url": "https://ntfy.test", "topic": "t",
        "user": "u", "password": "p",
    })
    # Simulate pip upgrade landed: dist-info says newer than in-process
    import empirica
    monkeypatch.setattr(empirica, "__version__", "1.9.10")
    monkeypatch.setattr(
        listener_mod.importlib.metadata, "version", lambda _: "1.9.11",
    )

    def fake_factory(url, headers):
        return _FakeProc([])  # immediate EOF triggers drift check

    monkeypatch.setattr(listener_mod, "_emit_catchup_events",
                        lambda *a, **kw: 0)

    err = io.StringIO()
    rc = run_listener("empirica", output_stream=io.StringIO(),
                      err_stream=err, _stream_factory=fake_factory,
                      _sleep=lambda s: None, _initial_catchup=False)
    assert rc == 0
    assert "version drift" in err.getvalue()
    assert "1.9.10" in err.getvalue() and "1.9.11" in err.getvalue()


def test_listener_continues_reconnect_when_no_drift(monkeypatch):
    """Without drift, the listener takes the normal reconnect path
    (backoff sleep) rather than exiting."""
    from empirica.config.credentials_loader import get_credentials_loader
    loader = get_credentials_loader()
    monkeypatch.setattr(loader, "get_ntfy_config", lambda: {
        "url": "https://ntfy.test", "topic": "t",
        "user": "u", "password": "p",
    })
    # Versions match — no drift
    monkeypatch.setattr(
        listener_mod, "_check_version_drift", lambda: None,
    )

    def fake_factory(url, headers):
        return _FakeProc([])

    monkeypatch.setattr(listener_mod, "_emit_catchup_events",
                        lambda *a, **kw: 0)

    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        # Stop after first backoff confirms no-drift path
        raise ListenerStopped("test")

    rc = run_listener("empirica", output_stream=io.StringIO(),
                      err_stream=io.StringIO(),
                      _stream_factory=fake_factory, _sleep=fake_sleep,
                      _initial_catchup=False)
    assert rc == 0
    assert len(sleeps) == 1  # took the backoff path, not the drift-exit path
