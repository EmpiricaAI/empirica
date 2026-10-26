"""A terminal 401 is remembered, so the retry loop stops (goal 6ef9f83f, David's ruling 2026-10-26: keep credential_health.mark and wire it).

`credential_health` is core's side of cortex's 401 contract: a 401 body carries `credential_status` and `retry`, and a terminal status means
retrying can never work. `cortex_bearer` already SKIPS a key recorded as dead, but nothing ever recorded one: `mark` had no caller, so the
guard never fired and the loops kept re-presenting a rejected credential (measured elsewhere: 25 hours, about 10,000 requests). The loops
that run unattended (the two heartbeats, the liveness probe, auto-accept) swallowed the HTTPError together with its body. They now hand
the response and the credential they sent to `note_http_error`, which marks only a 401 carrying a terminal verdict and never raises.
"""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest

from empirica.core.auth import credential_health as ch

SECRET = "tk_secret_value_1234567890"  # noqa: S105 - a fixture value, not a credential


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _http_error(code=401, body=None, headers=None):
    hdrs = Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b"")
    return urllib.error.HTTPError("https://cortex.invalid/v1/x", code, "err", hdrs, io.BytesIO(raw))


TERMINAL_BODY = {"error": "bad key", "credential_status": "invalid_key", "retry": "reauthenticate"}


# ── the helper ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_terminal_401_marks_the_credential_dead():
    assert ch.note_http_error(SECRET, _http_error(401, TERMINAL_BODY)) is True
    assert ch.dead_reason(SECRET) == "invalid_key"


def test_a_bearer_header_value_is_unwrapped_to_the_secret():
    assert ch.note_http_error(f"Bearer {SECRET}", _http_error(401, TERMINAL_BODY)) is True
    assert ch.dead_reason(SECRET) == "invalid_key", "marked under the secret's fingerprint, not under the header text"


def test_an_expired_token_is_refreshable_and_is_not_marked():
    body = {"credential_status": "expired_token", "retry": "refresh"}
    assert ch.note_http_error(SECRET, _http_error(401, body)) is False
    assert ch.dead_reason(SECRET) is None


@pytest.mark.parametrize("body", [None, b"", b"not json", {"error": "no verdict"}, {"credential_status": ""}])
def test_a_401_without_a_verdict_fails_open_and_marks_nothing(body):
    assert ch.note_http_error(SECRET, _http_error(401, body)) is False
    assert ch.dead_reason(SECRET) is None


@pytest.mark.parametrize("code", [400, 403, 404, 429, 500, 503])
def test_only_a_401_is_considered(code):
    assert ch.note_http_error(SECRET, _http_error(code, TERMINAL_BODY)) is False
    assert ch.dead_reason(SECRET) is None


def test_an_empty_challenge_with_invalid_token_in_www_authenticate_is_terminal():
    err = _http_error(401, b"", {"WWW-Authenticate": 'Bearer error="invalid_token"'})
    assert ch.note_http_error(SECRET, err) is True
    assert ch.dead_reason(SECRET) == "invalid_token"


@pytest.mark.parametrize("credential", [None, ""])
def test_no_credential_means_nothing_to_mark(credential):
    assert ch.note_http_error(credential, _http_error(401, TERMINAL_BODY)) is False


def test_it_never_raises_on_something_that_is_not_an_http_error():
    for junk in (None, 7, "401", object(), RuntimeError("x")):
        assert ch.note_http_error(SECRET, junk) is False


def test_it_never_raises_when_the_body_cannot_be_read():
    class Broken:
        code = 401
        headers = None

        def read(self, *_a):
            raise OSError("closed")

    assert ch.note_http_error(SECRET, Broken()) is False


def test_the_other_credential_is_unaffected():
    ch.note_http_error(SECRET, _http_error(401, TERMINAL_BODY))
    assert ch.dead_reason("tk_a_different_key_9999999999") is None


# ── the guard that was already there now has something to read ──────────────────────────────────────────────────


def test_after_a_terminal_401_cortex_bearer_stops_offering_the_key():
    from empirica.core.auth.cortex_oauth import cortex_bearer

    class Loader:
        def get_cortex_config(self):
            return {"url": "https://cortex.invalid", "api_key": SECRET}

        def cortex_access_token(self, refresh=None):
            return None

    assert cortex_bearer(Loader(), http=lambda *a, **k: {})["bearer"] == SECRET, "control: usable before the 401"
    ch.note_http_error(SECRET, _http_error(401, TERMINAL_BODY))
    after = cortex_bearer(Loader(), http=lambda *a, **k: {})
    assert after["bearer"] is None and after["source"] == "none"
    assert "invalid_key" in after["reason"], "suppressed, and says why"


# ── the looping callers ─────────────────────────────────────────────────────────────────────────────────────────


def _raise_401(*_a, **_k):
    raise _http_error(401, TERMINAL_BODY)


@pytest.mark.parametrize(
    "module", ["empirica.core.loop_scheduler.heartbeat", "empirica.core.loop_scheduler.practitioner_heartbeat"]
)
def test_a_heartbeat_post_records_a_terminal_401(monkeypatch, module):
    import importlib

    mod = importlib.import_module(module)
    monkeypatch.setattr(mod.urllib.request, "urlopen", _raise_401)
    status = mod._default_post(
        "https://cortex.invalid/v1/listeners/heartbeat", b"{}", {"Authorization": f"Bearer {SECRET}"}, 1.0
    )
    assert status == 401, "the status still comes back to the caller"
    assert ch.dead_reason(SECRET) == "invalid_key"


def test_a_heartbeat_post_to_a_healthy_server_marks_nothing(monkeypatch):
    from empirica.core.loop_scheduler import heartbeat as mod

    class Ok:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: Ok())
    assert mod._default_post("https://cortex.invalid/x", b"{}", {"Authorization": f"Bearer {SECRET}"}, 1.0) == 200
    assert ch.dead_reason(SECRET) is None


def test_the_liveness_probe_records_a_terminal_401():
    from empirica.core.loop_scheduler.liveness_probe import LivenessProbe

    probe = LivenessProbe.__new__(LivenessProbe)
    probe._probe_fn = _raise_401
    probe._cortex_loader = lambda: None
    probe._consecutive_failures = 0
    probe._log = lambda *_a, **_k: None
    probe._do_probe("https://cortex.invalid", SECRET, retry_on_401=False)
    assert ch.dead_reason(SECRET) == "invalid_key"
    assert probe._consecutive_failures == 1, "it is still counted as a miss"


def test_auto_accept_records_a_terminal_401(monkeypatch):
    from empirica.core.cockpit import auto_accept as mod

    monkeypatch.setattr(mod.urllib.request, "urlopen", _raise_401)
    assert mod._request("GET", "https://cortex.invalid/v1/auto-accept", SECRET) is None
    assert ch.dead_reason(SECRET) == "invalid_key"


# ── doctor says so: a suppressed credential must not look like an absent one ───────────────────────────────────


def test_doctor_reports_no_dead_credentials_when_none_is_marked():
    from empirica.cli.command_handlers import doctor

    check = doctor.check_credential_health()
    assert check.status == doctor.PASS and check.name == "Credential health"


def test_doctor_names_a_marked_credential_by_fingerprint_status_and_age_and_never_the_secret():
    from empirica.cli.command_handlers import doctor

    ch.note_http_error(SECRET, _http_error(401, TERMINAL_BODY))
    check = doctor.check_credential_health()
    assert check.status == doctor.WARN
    assert "invalid_key" in check.detail and ch.fingerprint(SECRET)[:8] in check.detail
    assert SECRET not in json.dumps([check.detail, check.hint, check.data]), "fingerprints only"
    assert "auth login" in check.hint


def test_dead_marks_lists_each_marked_credential_once():
    ch.note_http_error(SECRET, _http_error(401, TERMINAL_BODY))
    ch.note_http_error(SECRET, _http_error(401, TERMINAL_BODY))
    ch.note_http_error(
        "tk_other_secret_000000000", _http_error(401, {"credential_status": "invalid_token", "retry": "reauthenticate"})
    )
    marks = ch.dead_marks()
    assert sorted(m["credential_status"] for m in marks) == ["invalid_key", "invalid_token"]
    assert all(SECRET not in json.dumps(m) for m in marks)
