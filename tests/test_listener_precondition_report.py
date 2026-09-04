"""An OAuth-only seat was walked one gate at a time toward a cause neither gate named.

Measured by mesh-support on a real client box (facundo-wsl, empirica 1.13.27,
tenant facundo) while provisioning it. The listener crash-looped forever and
nothing in the chain stated the actual cause.

**Gate 1 — topic resolution.** `_cortex_creds()` returns `None` when `api_key` is
absent, and `fetch_notification_channels()` propagates that as a **silent** None —
no exception. So the listener's `except` never fired, no line was printed, topic
resolution fell through to the retired-topic refusal, and that message pointed at
cortex and at topics.

**Gate 2 — ntfy subscription auth.** Independent. `_ntfy_auth_header(None, None,
None)` refuses separately, with a different message.

The refusal at gate 1 said *set `ORCHESTRATION_NTFY_TOPIC` to override*. That
clears gate 1 and dies at gate 2. An operator following the tool's own advice is
marched through the gates one at a time, and the true cause — **this seat has no
api_key**, local, a credential, not a topic — is stated by neither.

Two properties fixed here:

1. **Fail soft AND say why.** The credentials layer stays non-raising (a cockpit
   that raises on a missing credential is worse than one that degrades) but now
   exposes the reason for the listener to print.
2. **Report every unmet precondition at once**, cause first. Serial reporting is
   what makes a fixed failure feel like a moving target.
"""

from __future__ import annotations

import pytest

from empirica.core.loop_scheduler.listener import _unmet_preconditions

NO_NTFY = {"user": None, "password": None, "token": None}


def test_the_measured_case_names_both_gates_and_the_cause_first():
    """The facundo-wsl shape: no api_key AND no ntfy credentials."""
    lines = _unmet_preconditions(NO_NTFY, "no cortex api_key configured")

    assert len(lines) == 2, "both gates, not one at a time"
    assert "api_key" in lines[0], "the credential comes first — it CAUSED the topic symptom"
    assert "ntfy" in lines[1]


def test_the_cortex_line_says_it_is_the_cause_not_a_separate_problem():
    """The old message let an operator read the topic failure as a cortex outage."""
    (cortex_line, _) = _unmet_preconditions(NO_NTFY, "no cortex api_key configured")
    assert "CAUSE of the unresolved topic" in cortex_line
    assert "auth login" in cortex_line or "credentials.yaml" in cortex_line, "and how to clear it"


def test_the_ntfy_line_says_the_topic_override_will_not_clear_it():
    """This is the sentence that stops the march: the printed remedy for gate 1
    provably does not satisfy gate 2, and now the message says so."""
    (_, ntfy_line) = _unmet_preconditions(NO_NTFY, "no cortex api_key configured")
    assert "INDEPENDENT" in ntfy_line
    assert "ORCHESTRATION_NTFY_TOPIC will not clear it" in ntfy_line


def test_only_the_unmet_gate_is_reported():
    """A report that lists satisfied preconditions trains skimming."""
    lines = _unmet_preconditions(NO_NTFY, None)
    assert len(lines) == 1 and "ntfy" in lines[0]

    lines = _unmet_preconditions({"token": "tk_x"}, "no cortex api_key configured")
    assert len(lines) == 1 and "api_key" in lines[0]


@pytest.mark.parametrize("ntfy", [{"token": "tk_x"}, {"user": "u", "password": "p"}])
def test_either_ntfy_credential_shape_satisfies_the_gate(ntfy):
    """Token OR user+password — a check that demanded both would refuse a valid seat."""
    assert _unmet_preconditions(ntfy, None) == [
        "no missing credentials detected — the topic itself is the problem; set ORCHESTRATION_NTFY_TOPIC to override."
    ]


def test_a_half_configured_basic_auth_is_not_credentials():
    """A user with no password authenticates nothing, and reporting it as
    configured would send the operator looking at cortex again."""
    assert len(_unmet_preconditions({"user": "u", "password": None}, None)) == 1


def test_with_everything_present_the_topic_advice_is_the_right_advice():
    """NEGATIVE CONTROL. The old message was not wrong in general — it was wrong
    for the seat that hit it. When no credential is missing, the topic override IS
    the remedy, and the report says exactly that."""
    (line,) = _unmet_preconditions({"token": "tk_x"}, None)
    assert "no missing credentials detected" in line
    assert "ORCHESTRATION_NTFY_TOPIC" in line


# ── the silence that caused it ───────────────────────────────────────────────


def test_the_credentials_layer_reports_why_it_returned_nothing(monkeypatch):
    """The silent None is the root cause: it is why the listener's except never
    fired and why nothing was printed at all."""
    from empirica.config import credentials_loader as cl
    from empirica.core.cockpit import notification_channels as nc

    class _Loader:
        def get_cortex_config(self):
            return {"url": "https://cortex.example", "api_key": None}

        def get_cortex_oauth(self):
            return {}

        def cortex_access_token(self, refresh=None):
            return None

    # Patch the SOURCE module: `_cortex_creds` imports the loader inside the
    # function body, so the name never exists on `nc` and patching there is a
    # no-op that leaves the real loader in place — a fake aimed at the wrong
    # target, which fails identically to the feature being broken.
    #
    # The fake grew two OAuth methods because `_cortex_creds` now resolves via
    # `cortex_bearer` — an api_key-only stub made the real resolver log a
    # fallback warning while still producing the right answer, which is a fake
    # too narrow for the contract it stands in for.
    monkeypatch.setattr(cl, "get_credentials_loader", lambda: _Loader())

    assert nc._cortex_creds() is None
    # Message widened deliberately with the OAuth fix: a seat with neither an
    # OAuth token nor an api_key used to be told only about the api_key, which
    # is exactly what sent an OAuth-only seat to provision the wrong credential.
    assert nc.last_credentials_error() == "no cortex credential configured (no OAuth token, no api_key)"


def test_a_missing_url_is_distinguished_from_a_missing_key(monkeypatch):
    """Two different fixes; one message for both would send half the seats wrong."""
    from empirica.config import credentials_loader as cl
    from empirica.core.cockpit import notification_channels as nc

    class _Loader:
        def get_cortex_config(self):
            return {"url": None, "api_key": "k"}

    monkeypatch.setattr(cl, "get_credentials_loader", lambda: _Loader())
    nc._cortex_creds()
    assert nc.last_credentials_error() == "no cortex url configured"


def test_a_successful_read_clears_the_error(monkeypatch):
    """POSITIVE CONTROL — a reason that never clears would report a stale failure
    forever, which is the same misdirection one layer along."""
    from empirica.config import credentials_loader as cl
    from empirica.core.cockpit import notification_channels as nc

    class _Bad:
        def get_cortex_config(self):
            return {"url": None, "api_key": None}

    class _Good:
        def get_cortex_config(self):
            return {"url": "https://cortex.example", "api_key": "k"}

    monkeypatch.setattr(cl, "get_credentials_loader", lambda: _Bad())
    nc._cortex_creds()
    assert nc.last_credentials_error() is not None

    monkeypatch.setattr(cl, "get_credentials_loader", lambda: _Good())
    assert nc._cortex_creds() == ("https://cortex.example", "k")
    assert nc.last_credentials_error() is None
