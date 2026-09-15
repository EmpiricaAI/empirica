"""The two severe members of the api_key-gate class.

Both are silent, and both were found by empirica-mesh-support sweeping for the
SHAPE (`get_cortex_config`, `.get("api_key")`, `if not api_key`) rather than for
the stack traces that happened to surface earlier instances.

  identity_migration  a False answer means "this project is purely local, minting
                      a fresh UUID is safe". On an OAuth-only seat it answered
                      False and MINTED — forking a practice from its own
                      artifacts and its roster row.

  listener            the canonical 3-form is what live mesh pushes are addressed
                      to. Without it the listener subscribes as the bare basename
                      and pushes are dropped. Catch-up polling still works, so the
                      seat looks functional and merely quiet.

The listener's own except branch already warned that this fallback drops pushes.
The api_key gate reached the same fallback with no message at all — the code knew
the danger and the gate walked around the warning.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from empirica.core.identity_migration import _cortex_installed


class _Loader:
    def __init__(self, cfg: dict, oauth: dict | None = None):
        self._cfg, self._oauth = cfg, oauth or {}

    def get_cortex_config(self) -> dict:
        return self._cfg

    def get_cortex_oauth(self) -> dict:
        return self._oauth


def _with_loader(loader):
    return patch("empirica.config.credentials_loader.get_credentials_loader", return_value=loader)


def test_an_oauth_seat_must_not_be_treated_as_purely_local():
    """The fork. False here means the migration mints a fresh UUID."""
    with _with_loader(_Loader({"url": "https://cortex.example"}, oauth={"access_token": "t"})):
        assert _cortex_installed() is True


def test_an_api_key_seat_is_still_recognised():
    with _with_loader(_Loader({"url": "https://cortex.example", "api_key": "k"})):
        assert _cortex_installed() is True


def test_a_genuinely_local_project_is_still_safe_to_mint():
    """The positive control. Without it both tests above would pass on a
    predicate that returned True unconditionally — and minting would never
    happen for anyone, which is a different bug wearing the same green."""
    with _with_loader(_Loader({})):
        assert _cortex_installed() is False


def _listener_tag(resolved: dict, canonical: str = "empirica.david.empirica"):
    """Call the real `resolve_tag_filter`, capturing what it writes to stderr.

    An earlier draft of these two tests reconstructed the resolution inline and
    asserted on its own expression — green whether or not the listener was fixed.
    That is the defect class this whole sweep is about, so the function was
    extracted to give the test something real to call.
    """
    import io

    from empirica.core.loop_scheduler.listener import resolve_tag_filter

    err = io.StringIO()
    with (
        patch("empirica.core.auth.cortex_oauth.cortex_bearer", return_value=resolved),
        patch(
            "empirica.core.loop_scheduler.content_poll._resolve_canonical_ai_id",
            return_value=canonical,
        ),
        patch("empirica.config.credentials_loader.get_credentials_loader", return_value=MagicMock()),
    ):
        return resolve_tag_filter("empirica", err), err.getvalue()


def test_the_listener_resolves_a_canonical_tag_for_an_oauth_seat():
    """It used to subscribe as the bare basename, and say nothing."""
    tag, err = _listener_tag({"url": "https://cortex.example", "bearer": "oauth_token", "source": "oauth"})

    assert tag == "empirica.david.empirica", "an OAuth seat must reach the canonical 3-form, not the basename"
    assert err == "", "a successful resolution must not warn"


def test_no_credential_falls_back_to_the_basename_and_says_so():
    """The fallback is correct and must survive — what was wrong is reaching it
    while holding a usable token, and reaching it in silence."""
    tag, err = _listener_tag({"url": None, "bearer": None, "source": "none", "reason": "no cortex credential"})

    assert tag == "empirica"
    assert "silently dropped" in err, "the fallback must announce itself — this path had no message at all"
    assert "no cortex credential" in err, "and it must carry the reason"
