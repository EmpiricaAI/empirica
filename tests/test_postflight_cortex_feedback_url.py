"""POSTFLIGHT's cortex cache feedback reaches the Cortex the seat is signed in to, not a guessed localhost.

It read EMPIRICA_CORTEX_URL and defaulted to http://localhost:8420 with CORTEX_API_KEY, while every other call to Cortex takes
its URL and bearer from the shared credential contract. Cortex reports the route live, so on a signed-in seat the call went
nowhere. The env vars still override; with no URL at all there is no call.
"""

from __future__ import annotations

import json

import pytest

from empirica.cli.command_handlers import _workflow_postflight as wp
from empirica.cli.command_handlers import system_event

LOW = {"calibration_score": 0.1, "grounded_coverage": 0.5, "evidence_count": 3, "gaps": {}, "sources": []}


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None, **_):
        calls.append({"url": req.full_url, "auth": req.headers.get("Authorization"), "body": json.loads(req.data)})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.delenv("EMPIRICA_CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    return calls


def test_the_signed_in_cortex_is_used(monkeypatch, sent):
    monkeypatch.setattr(system_event, "resolve_cortex_config", lambda: ("https://cortex.example/", "tok"))

    wp._pipeline_cortex_cache_feedback("s1", {"know": 0.5}, LOW)

    assert [(c["url"], c["auth"]) for c in sent] == [("https://cortex.example/postflight", "Bearer tok")]
    assert sent[0]["body"]["session_id"] == "s1"


def test_the_environment_still_overrides(monkeypatch, sent):
    monkeypatch.setattr(system_event, "resolve_cortex_config", lambda: ("https://cortex.example", "tok"))
    monkeypatch.setenv("EMPIRICA_CORTEX_URL", "http://localhost:9999")
    monkeypatch.setenv("CORTEX_API_KEY", "envkey")

    wp._pipeline_cortex_cache_feedback("s1", {}, LOW)

    assert [(c["url"], c["auth"]) for c in sent] == [("http://localhost:9999/postflight", "Bearer envkey")]


def test_no_cortex_means_no_call(monkeypatch, sent):
    monkeypatch.setattr(system_event, "resolve_cortex_config", lambda: (None, None))

    wp._pipeline_cortex_cache_feedback("s1", {}, LOW)

    assert sent == []


def test_a_well_calibrated_session_sends_nothing(monkeypatch, sent):
    monkeypatch.setattr(system_event, "resolve_cortex_config", lambda: ("https://cortex.example", "tok"))

    wp._pipeline_cortex_cache_feedback("s1", {}, {**LOW, "calibration_score": 0.9})

    assert sent == []
