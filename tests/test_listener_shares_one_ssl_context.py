"""Every HTTPS call the listener makes reuses one SSL context for the life of the process.

Measured by mesh-support on 2026-11-06 (prop_2ufpbpincbcltiedfd7icbjvim): the liveness probe built a new SSL context and a new
opener every 60 seconds, about 0.77 MB of native memory each; 66 were still alive after three hours and a listener grew by
about 16 MB an hour, all of it outside Python's allocator. The other listener calls made http.client create a context per
connection. These tests hold the calls to one context, and keep the old per-call form from coming back.
"""

from __future__ import annotations

import ast
import ssl
import threading
import urllib.request
from pathlib import Path

import pytest

from empirica.core.loop_scheduler import http_shared

ROOT = Path(__file__).parent.parent
LISTENER_MODULES = [
    "empirica/core/loop_scheduler/liveness_probe.py",
    "empirica/core/loop_scheduler/heartbeat.py",
    "empirica/core/loop_scheduler/practitioner_heartbeat.py",
    "empirica/core/loop_scheduler/content_poll.py",
    "empirica/core/cockpit/notification_channels.py",
]


@pytest.fixture
def fresh(monkeypatch):
    """A process with no context yet, counting every context that gets built and every urlopen call."""
    monkeypatch.setattr(http_shared, "_CONTEXT", None)
    made = {"contexts": 0}
    real_context = ssl.create_default_context

    def counting_context(*a, **k):
        made["contexts"] += 1
        return real_context(*a, **k)

    monkeypatch.setattr(http_shared.ssl, "create_default_context", counting_context)
    calls: list = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout, context: calls.append(context) or "response")
    return made, calls


def test_180_calls_build_one_context_and_every_call_gets_it(fresh):
    made, calls = fresh
    for _ in range(180):  # three hours of 60-second ticks
        assert http_shared.open_url("https://example.invalid/x", timeout=5) == "response"
    assert made == {"contexts": 1}
    assert len(calls) == 180 and all(c is http_shared.shared_context() for c in calls)


def test_concurrent_first_use_still_builds_one(fresh):
    made, _calls = fresh
    results: list[int] = []

    def grab():
        results.append(id(http_shared.shared_context()))

    threads = [threading.Thread(target=grab) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1 and made == {"contexts": 1}


@pytest.mark.parametrize("path", LISTENER_MODULES)
def test_no_listener_module_builds_a_context_or_calls_urlopen_itself(path):
    tree = ast.parse((ROOT / path).read_text())
    calls = [
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("urlopen", "create_default_context")
    ]
    assert calls == [], f"{path} builds its own: {calls}"
