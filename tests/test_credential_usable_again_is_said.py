"""A credential that recovers says so, and a second death with the same reason is logged again (goal 4c935e3b, sweep C1).

say_suppressed remembers the last reason per component and logs only on a change. The heartbeats called it only when a beat
was skipped, never when credentials were present again, so the memory never cleared: "usable again" never printed, and a
second death with the same fixed reason text was silent, the failure the 24 h expiry work was meant to remove.
"""

from __future__ import annotations

import inspect
import logging

import pytest

from empirica.core.auth import credential_health as ch


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ch, "_last_said", {})


@pytest.mark.parametrize("module", ["heartbeat", "practitioner_heartbeat"])
def test_a_beat_with_credentials_clears_the_remembered_reason(module, caplog):
    import importlib

    mod = importlib.import_module(f"empirica.core.loop_scheduler.{module}")
    with caplog.at_level(logging.WARNING):
        ch.say_suppressed("c", "api_key unusable (invalid_key)")
        mod._say_usable("c")
        ch.say_suppressed("c", "api_key unusable (invalid_key)")
    lines = [r.getMessage() for r in caplog.records]
    assert sum("no usable cortex credential" in m for m in lines) == 2
    assert any("usable again" in m for m in lines)


@pytest.mark.parametrize("module", ["heartbeat", "practitioner_heartbeat"])
def test_the_send_path_says_usable_once_credentials_are_present(module):
    import importlib

    source = inspect.getsource(importlib.import_module(f"empirica.core.loop_scheduler.{module}"))
    skip = source.index("_say_why_skipped(")  # the definition
    call = source.index("_say_why_skipped(", skip + 1)  # the call in the send path
    assert "_say_usable(" in source[call : call + 400], "the creds-present path must clear the remembered reason"
