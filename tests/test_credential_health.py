"""A 401 that retrying cannot fix must stop the retry — measured at 25h, ~10k requests.

Cortex now says on the wire whether a 401 is recoverable:

    {"error": "...", "credential_status": S, "retry": R}

`expired_token` -> `refresh` is the only retry-with-hope state. This file covers
core's side: remember the terminal ones so the NEXT process does not re-storm.

**The defect this closes named the wrong credential.** `cortex_bearer`'s docstring
claimed the api_key fallback avoided "sending a dead credential" — true of the
TOKEN, false of the KEY. `cortex_access_token` correctly returns None on
expiry-without-refresh, and the fallback then returned an api_key that might be
revoked, forever, with zero token-refresh attempts.

Three properties are load-bearing and each has a test that fails loudly if it is
ever traded away:

  1. the escape path is always open — writing credentials.yaml clears every mark
  2. absent fields fail OPEN — no verdict means no action, never a default of dead
  3. suppression is legible — a skipped credential must not look like an absent one
"""

from __future__ import annotations

import json

import pytest

from empirica.core.auth import credential_health as ch

TERMINAL_BODY = {"error": "nope", "credential_status": "invalid_key", "retry": "reauthenticate"}
EXPIRED_BODY = {"error": "nope", "credential_status": "expired_token", "retry": "refresh"}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every path this module touches hangs off Path.home().

    Built under tmp_path rather than mocked piecemeal, because a test that reads
    the real ~/.empirica passes for reasons it cannot control and fails in CI where
    that directory does not exist.
    """
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / ".empirica").mkdir(parents=True)
    (tmp_path / ".empirica" / "credentials.yaml").write_text("cortex:\n  api_key: k\n")
    return tmp_path


# ── the wire contract ────────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", ["dict", "text", "bytes"])
def test_the_verdict_is_read_whatever_shape_the_transport_gave_it(shape):
    """Callers hold the body in whatever their transport produced. Making each one
    normalise first is how a field ends up honoured on two paths out of five."""
    body = TERMINAL_BODY
    if shape == "text":
        body = json.dumps(TERMINAL_BODY)
    elif shape == "bytes":
        body = json.dumps(TERMINAL_BODY).encode()
    assert ch.parse_unauthorized(body) == {"credential_status": "invalid_key", "retry": "reauthenticate"}


def test_a_missing_retry_field_is_derived_not_discarded():
    """`retry` is a function of `credential_status`, so a body carrying one and not
    the other is still actionable."""
    assert ch.parse_unauthorized({"credential_status": "expired_token"})["retry"] == "refresh"
    assert ch.parse_unauthorized({"credential_status": "invalid_token"})["retry"] == "reauthenticate"


def test_the_rfc6750_challenge_header_is_read_when_the_body_is_empty():
    """The OAuth-challenge paths put the reason in WWW-Authenticate, not the body."""
    v = ch.parse_unauthorized("", {"WWW-Authenticate": 'Bearer error="invalid_token", error_description="x"'})
    assert v == {"credential_status": "invalid_token", "retry": "reauthenticate"}


@pytest.mark.parametrize(
    "body", [None, "", "not json at all", {}, {"error": "unauthorized"}, [1, 2, 3]], ids=lambda b: str(b)[:16]
)
def test_absent_fields_produce_no_verdict(body):
    """FAIL-OPEN, and it is deliberate. A server that has not shipped the contract,
    or a proxy that ate the body, must not brick a working seat."""
    assert ch.parse_unauthorized(body) is None


# ── marking, and what must NOT be marked ─────────────────────────────────────


def test_a_terminal_status_suppresses_the_credential():
    ch.mark("secret-key", ch.parse_unauthorized(TERMINAL_BODY))
    assert ch.dead_reason("secret-key") == "invalid_key"


def test_a_refreshable_status_is_never_marked():
    """NEGATIVE CONTROL on the one distinction the whole contract turns on. If
    `expired_token` marked, a healthy refresh path would brick itself on its own
    normal behaviour."""
    ch.mark("secret-key", ch.parse_unauthorized(EXPIRED_BODY))
    assert ch.dead_reason("secret-key") is None


def test_no_verdict_marks_nothing():
    ch.mark("secret-key", None)
    assert ch.dead_reason("secret-key") is None


def test_an_unrecognised_status_marks_nothing():
    """A status this version has never heard of is not evidence of death — it is
    evidence of a newer server."""
    ch.mark("secret-key", {"credential_status": "teapot", "retry": "reauthenticate"})
    assert ch.dead_reason("secret-key") is None


def test_marks_are_per_credential():
    ch.mark("dead-one", ch.parse_unauthorized(TERMINAL_BODY))
    assert ch.dead_reason("dead-one")
    assert ch.dead_reason("a-different-key") is None


# ── the escape path, which is the guard that matters most ────────────────────


def test_writing_credentials_clears_every_mark(isolated_home):
    """THE property. `empirica auth login` un-brickes a seat by writing the file,
    with no separate reset command to discover.

    A guard whose clear-path is itself gated turns a recoverable outage into a
    permanent one — and the practitioner hitting it is by definition the one who
    cannot authenticate to ask for help.
    """
    ch.mark("secret-key", ch.parse_unauthorized(TERMINAL_BODY))
    assert ch.dead_reason("secret-key")

    creds = isolated_home / ".empirica" / "credentials.yaml"
    creds.write_text("cortex:\n  api_key: brand-new\n")
    import os

    st = creds.stat()
    os.utime(creds, (st.st_atime + 10, st.st_mtime + 10))

    assert ch.dead_reason("secret-key") is None, "a re-login must clear the mark"


def test_a_mark_survives_the_process_that_made_it(isolated_home):
    """Persistence IS the feature: the CLI is short-lived, so a process-local cache
    would be empty on every invocation and every invocation would re-discover the
    death by making the request — which is the storm."""
    ch.mark("secret-key", ch.parse_unauthorized(TERMINAL_BODY))
    assert (isolated_home / ".empirica" / "credential_health.json").exists()
    assert ch.dead_reason("secret-key") == "invalid_key"


def test_the_secret_is_never_written_to_disk(isolated_home):
    """This practice already has a live admin key sitting verbatim in eleven of its
    own artifacts. A health file holding another copy would be that mistake with a
    new filename."""
    ch.mark("super-secret-value", ch.parse_unauthorized(TERMINAL_BODY))
    written = (isolated_home / ".empirica" / "credential_health.json").read_text()
    assert "super-secret-value" not in written


def test_clear_forgets_everything():
    ch.mark("secret-key", ch.parse_unauthorized(TERMINAL_BODY))
    ch.clear()
    assert ch.dead_reason("secret-key") is None


# ── the caller, where the storm actually happened ────────────────────────────


class _Loader:
    def __init__(self, api_key="live-key", url="https://cortex.example"):
        self._cfg = {"url": url, "api_key": api_key}

    def get_cortex_config(self):
        return dict(self._cfg)

    def get_cortex_oauth(self):
        return {}

    def cortex_access_token(self, refresh=None):
        return None  # the measured state: expiry without a usable refresh


def test_a_live_api_key_is_still_returned():
    """POSITIVE CONTROL, and not optional: without it, a fallback that returned
    None unconditionally would pass every suppression test below."""
    from empirica.core.auth import cortex_bearer

    out = cortex_bearer(loader=_Loader("live-key"))
    assert out["source"] == "api_key" and out["bearer"] == "live-key"


def test_a_dead_api_key_is_suppressed_rather_than_returned():
    """The 25h storm in one assertion."""
    from empirica.core.auth import cortex_bearer

    ch.mark("dead-key", ch.parse_unauthorized(TERMINAL_BODY))
    out = cortex_bearer(loader=_Loader("dead-key"))
    assert out["bearer"] is None
    assert out["source"] == "none"


def test_suppression_says_why_and_does_not_look_like_absence():
    """A suppressed credential returning a bare None is indistinguishable from a
    seat with none configured — which sends the operator to provision a credential
    they already have. That is the same unfalsifiable silence the wire contract was
    added to remove, one layer in."""
    from empirica.core.auth import cortex_bearer

    ch.mark("dead-key", ch.parse_unauthorized(TERMINAL_BODY))
    suppressed = cortex_bearer(loader=_Loader("dead-key"))
    absent = cortex_bearer(loader=_Loader(None))

    assert "invalid_key" in suppressed["reason"]
    assert "auth login" in suppressed["reason"]
    assert suppressed["reason"] != absent["reason"], "suppressed and absent must be distinguishable"
    assert "no cortex credential" in absent["reason"]


def test_an_unreadable_health_file_is_distinguishable_from_no_file(isolated_home, caplog):
    """Both fail open to no-marks, which is right — but a health file that has been
    unparseable for weeks would mean marks never stick, storms never stop, and
    nothing anywhere says why.

    Found by a broccoli pass over code written hours earlier: the first version
    caught bare `Exception` and returned `{}` with no log, so a corrupt file and a
    fresh install produced identical behaviour and identical silence.
    """
    import logging

    ch.mark("secret-key", ch.parse_unauthorized(TERMINAL_BODY))
    (isolated_home / ".empirica" / "credential_health.json").write_text("{not json")

    with caplog.at_level(logging.WARNING):
        assert ch.dead_reason("secret-key") is None, "still fails open"
    assert any("unreadable" in r.message for r in caplog.records), "and says so"


def test_no_health_file_at_all_is_silent(isolated_home, caplog):
    """NEGATIVE CONTROL. A fresh install is the common case and must not warn —
    a warning on every first run trains people to ignore the channel that carries
    the real one."""
    import logging

    with caplog.at_level(logging.WARNING):
        assert ch.dead_reason("never-marked") is None
    assert not caplog.records, "a missing file is normal, not a warning"
