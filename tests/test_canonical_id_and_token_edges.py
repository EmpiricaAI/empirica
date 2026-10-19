"""Two edges an independent sweep found in the listener work (findings 4 and 8).

4. A roster row WITHOUT ``ai_id_mesh`` made `_resolve_canonical_ai_id` fall back to the basename and store it in the SUCCESS
   cache, which is consulted before ``fresh=True``. The bounded retry in `resolve_tag_filter` then re-read the same cache eight
   times, and every other caller in the process kept the bare basename for good: the deaf-listener failure the retry was meant to
   remove, reached by a different door.
8. A token with a trailing newline (a YAML block scalar, an env value) raised ValueError in `_curl_header_config`, which the
   generic handler turned into an exit with no fail heartbeat. The token is stripped.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from empirica.core.loop_scheduler import content_poll
from empirica.core.loop_scheduler.listener import _curl_header_config, _ntfy_auth_header


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
def _clean():
    content_poll.reset_canonical_cache()
    yield
    content_poll.reset_canonical_cache()


def _roster(mesh=None):
    row = {"ai_id_short": "proj"}
    if mesh:
        row["ai_id_mesh"] = mesh
    return {"self": {"tenant_slug": "t"}, "org": {"tenants": [{"tenant_slug": "t", "projects": [row]}]}}


def test_a_roster_row_without_a_mesh_id_is_not_cached_as_a_resolution(monkeypatch):
    body = _roster()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    assert content_poll._resolve_canonical_ai_id("http://c", "k", "proj") == "proj"

    body.update(_roster("o.t.proj"))  # the roster is repaired
    assert content_poll._resolve_canonical_ai_id("http://c", "k", "proj", fresh=True) == "o.t.proj"


def test_the_success_cache_never_holds_a_bare_basename(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(_roster()))
    content_poll._resolve_canonical_ai_id("http://c", "k", "proj")
    assert all("." in v for v in content_poll._CANONICAL_AI_ID_CACHE.values())


def test_a_real_resolution_is_still_cached(monkeypatch):
    calls = {"n": 0}

    def uo(*a, **k):
        calls["n"] += 1
        return _Resp(_roster("o.t.proj"))

    monkeypatch.setattr(urllib.request, "urlopen", uo)
    for _ in range(3):
        assert content_poll._resolve_canonical_ai_id("http://c", "k", "proj") == "o.t.proj"
    assert calls["n"] == 1


def test_a_token_with_a_trailing_newline_builds_a_header():
    header = _ntfy_auth_header(None, None, "tk_abcdefghijklmnopqrstuvwxyz\n")
    assert header == {"Authorization": "Bearer tk_abcdefghijklmnopqrstuvwxyz"}
    assert _curl_header_config(header).startswith('header = "Authorization: Bearer tk_')


def test_a_newline_inside_a_token_is_still_refused():
    header = _ntfy_auth_header(None, None, "tk_abc\nInjected: yes")
    with pytest.raises(ValueError):
        _curl_header_config(header)
