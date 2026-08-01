"""A truncated poll must be able to say it is truncated.

Cortex reports completeness on the orchestration envelope (`matched`,
`has_more`) as of prod acd536d6. Our CLI discarded it three frames below the
envelope, at `content_poll._fetch_orch`:

    proposals = body.get("proposals", [])
    return proposals if isinstance(proposals, list) else []

So `mailbox poll` returned N proposals with no way to tell whether N was
everything. "Have I replied to my whole backlog?" requires enumerating the
outbox, and a partial result that cannot say it is partial reads as complete —
which pushed backlog triage onto memory. Reported by a peer practice, verified
here in both directions against live cortex before accepting the framing:
limit=2 → matched 29 / has_more True; limit=200 → matched 29 / has_more False.

**The constraint that shaped the fix.** `_fetch_orch` is shared with the
LISTENER, so widening its return type would put a wake-path change in a
reporting fix. Completeness is threaded through an OPTIONAL out-dict instead:
the return type is untouched, and a caller that passes nothing — the listener —
is bit-for-bit unaffected. That property is what these tests pin.
"""

from __future__ import annotations

import json

import pytest

import empirica.core.loop_scheduler.content_poll as cp


class _Resp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def cortex(monkeypatch):
    """Stub the HTTP layer; body shape mirrors live cortex."""

    def _make(body):
        monkeypatch.setattr(cp.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
        monkeypatch.setattr(cp, "_resolve_canonical_ai_id", lambda *a, **k: "org.t.p", raising=False)

    return _make


TRUNCATED = {"proposals": [{"id": "a"}, {"id": "b"}], "matched": 29, "has_more": True, "returned": 2}
COMPLETE = {"proposals": [{"id": "a"}], "matched": 1, "has_more": False, "returned": 1}
LEGACY = {"proposals": [{"id": "a"}]}


def test_completeness_reaches_the_caller(cortex):
    """POSITIVE CONTROL — the reproduction. This was silently dropped."""
    cortex(TRUNCATED)
    meta: dict = {}

    cp.fetch_cortex_outbox("http://x", "k", "org.t.p", meta_out=meta)

    assert meta["matched"] == 29
    assert meta["has_more"] is True


def test_a_complete_poll_says_so(cortex):
    """NEGATIVE CONTROL: has_more must not be hardcoded truthy — a complete
    poll reporting has_more would be as useless as no signal at all."""
    cortex(COMPLETE)
    meta: dict = {}

    cp.fetch_cortex_outbox("http://x", "k", "org.t.p", meta_out=meta)

    assert meta["has_more"] is False


def test_the_listener_path_is_untouched(cortex):
    """THE LISTENER GUARANTEE, and the reason for an out-param over a widened
    return. A caller that passes no meta_out gets exactly what it always got:
    a plain list of proposals, same type, same contents."""
    cortex(TRUNCATED)

    result = cp.fetch_cortex_outbox("http://x", "k", "org.t.p")

    assert isinstance(result, list)
    assert result == [{"id": "a"}, {"id": "b"}]


def test_an_older_cortex_omits_the_keys_entirely(cortex):
    """Absent, not defaulted. A cortex that does not report completeness must
    not yield `has_more: False` — that would assert completeness it never
    claimed, which is the original bug wearing the fix's clothes."""
    cortex(LEGACY)
    meta: dict = {}

    cp.fetch_cortex_outbox("http://x", "k", "org.t.p", meta_out=meta)

    assert "has_more" not in meta
    assert "matched" not in meta


def test_the_inbox_path_takes_the_same_route(cortex):
    """Both wrappers share _fetch_orch; the fix must not be outbox-only."""
    cortex(TRUNCATED)
    meta: dict = {}

    cp.fetch_cortex_inbox("http://x", "k", "org.t.p", meta_out=meta)

    assert meta["matched"] == 29
