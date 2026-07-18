"""Regression: an already-canonical ai_id must NOT be re-resolved via the roster.

The onboarding empty-inbox bug: `mailbox poll --ai-id <canonical>` for a freshly
registered practice returned empty because the canonical was round-tripped through
_resolve_canonical_ai_id, whose roster lookup can't match a canonical (or a not-yet-
synced practice) and fell back to a bare/mismatched id → cortex returns 0.
"""

from __future__ import annotations

import urllib.request

from empirica.core.loop_scheduler import content_poll
from empirica.core.loop_scheduler.content_poll import _resolve_canonical_ai_id


def test_canonical_ai_id_passthrough_skips_roster(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("roster must NOT be fetched for an already-canonical ai_id")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    # dotted == already canonical → returned unchanged, no network call
    assert (
        _resolve_canonical_ai_id("http://x.invalid", "key", "empirica.david.epistemic-dj")
        == "empirica.david.epistemic-dj"
    )
    assert (
        _resolve_canonical_ai_id("http://x.invalid", "key", "empirica.philipp.empirica-research")
        == "empirica.philipp.empirica-research"
    )


def test_bare_basename_still_resolves(monkeypatch):
    # A bare basename (no dot) still goes through the roster path.
    calls = {"n": 0}

    def _fake_urlopen(*_a, **_k):
        calls["n"] += 1
        raise OSError("unreachable")  # forces the documented fallback

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    content_poll._CANONICAL_AI_ID_CACHE.clear()
    out = _resolve_canonical_ai_id("http://x.invalid", "key", "epistemic-dj", timeout=0.1)
    assert calls["n"] == 1  # the roster WAS attempted for a bare basename
    assert out == "epistemic-dj"  # fallback to basename on failure (loud, not silent)
