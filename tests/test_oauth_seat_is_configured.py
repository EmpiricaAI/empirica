"""An OAuth-only seat is a configured seat.

`get_cortex_config()` returns `{url, api_key}` and has no OAuth path, so every
gate written as `url and api_key` reads a seat authenticated by
`empirica auth login` as unconfigured. 1.13.19 fixed the three sites that threw
stack traces. The ones that fail SILENTLY survived — and the worst of them strips
every `{% if cortex %}` block from the rendered system prompt, so the seat
installs cleanly, reports no error, and is never told the mesh exists.

Reported by empirica-mesh-support as the fourth instance of the class.

The distinction these tests pin: **presence is not resolution.**
`cortex_configured` answers *could this seat talk to cortex* without a network
call, because it runs on every session auto-heal. `cortex_bearer` answers *give
me a credential to send* and may refresh a token, so it belongs only where an
HTTP call is imminent.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.setup_claude_code import _is_cortex_configured, _strip_cortex_blocks
from empirica.core.auth.cortex_oauth import cortex_configured

_PROMPT = "always\n{% if cortex %}mesh addressing, mailbox skills, ECO routing{% endif %}\ntail"


class _Loader:
    """Only the two accessors the predicate is allowed to touch."""

    def __init__(self, cfg: dict, oauth: dict | None = None, explode: bool = False):
        self._cfg, self._oauth, self._explode = cfg, oauth or {}, explode
        self.calls: list[str] = []

    def get_cortex_config(self) -> dict:
        if self._explode:
            raise OSError("credentials file unreadable")
        self.calls.append("config")
        return self._cfg

    def get_cortex_oauth(self) -> dict:
        self.calls.append("oauth")
        return self._oauth


def test_an_oauth_only_seat_is_configured():
    """The reported defect, as a test. This returned False."""
    loader = _Loader({"url": "https://cortex.example"}, oauth={"access_token": "t", "refresh_token": "r"})
    assert cortex_configured(loader) is True


def test_an_api_key_seat_is_still_configured():
    """The fix must not trade one credential for the other."""
    loader = _Loader({"url": "https://cortex.example", "api_key": "k"})
    assert cortex_configured(loader) is True


def test_a_seat_with_neither_is_not_configured():
    """The positive control for the two above — without it they could both pass on a
    predicate that returns True unconditionally."""
    assert cortex_configured(_Loader({"url": "https://cortex.example"})) is False


def test_no_url_is_not_configured():
    assert cortex_configured(_Loader({"api_key": "k"})) is False


def test_an_unreadable_credentials_file_is_not_a_configured_seat():
    assert cortex_configured(_Loader({}, explode=True)) is False


def test_presence_never_resolves_or_refreshes_a_token():
    """The hot-path constraint, pinned.

    This predicate runs on every session auto-heal. If it ever reached for
    `cortex_bearer` it would put a token refresh — an HTTP round-trip — in front
    of every session start, and nothing in the output would say so.
    """
    loader = _Loader({"url": "https://cortex.example"}, oauth={"access_token": "t"})
    cortex_configured(loader)
    assert loader.calls == ["config", "oauth"], "only the two file-only accessors may be touched"


def test_an_api_key_seat_does_not_even_read_the_oauth_block():
    """Short-circuit: the cheaper answer wins, and the oauth read never happens."""
    loader = _Loader({"url": "https://cortex.example", "api_key": "k"})
    cortex_configured(loader)
    assert "oauth" not in loader.calls


@pytest.mark.parametrize(
    "configured,expect_mesh",
    [(True, True), (False, False)],
    ids=["cortex-on keeps the mesh blocks", "cortex-off drops them"],
)
def test_the_prompt_blocks_follow_the_predicate(configured, expect_mesh):
    """End to end: what the seat actually receives.

    The predicate matters only because of this. An OAuth seat reading False here
    got a prompt with no mesh guidance at all, which is indistinguishable from
    the product not having a mesh.
    """
    rendered = _PROMPT if configured else _strip_cortex_blocks(_PROMPT)
    assert ("mesh addressing" in rendered) is expect_mesh
    assert "always" in rendered and "tail" in rendered, "non-cortex content survives either way"


def test_the_installer_predicate_delegates_to_the_shared_one(monkeypatch):
    """One home for the question, so the next fix is a grep for one name.

    The installer's own wrapper must not reimplement the test — that is how the
    three sites fixed in 1.13.19 drifted apart from the two that were not.
    """
    seen = {}

    def _fake(*a, **kw):
        seen["called"] = True
        return True

    monkeypatch.setattr("empirica.core.auth.cortex_oauth.cortex_configured", _fake)
    assert _is_cortex_configured() is True
    assert seen.get("called"), "_is_cortex_configured must delegate, not re-derive"
