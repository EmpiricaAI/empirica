"""A suppressed credential is named in the loop logs, and a terminal mark expires (goal b2b860d6, sweep D2 and D3)."""

from __future__ import annotations

import logging
import time

import pytest

from empirica.core.auth import credential_health as ch


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica").mkdir()
    ch.clear()
    return tmp_path


def test_a_mark_older_than_its_ttl_no_longer_suppresses(home, monkeypatch):
    ch.mark("secret-key", {"credential_status": "invalid_key"})
    assert ch.dead_reason("secret-key") == "invalid_key"
    later = time.time() + ch.MARK_TTL_SECONDS + 60
    monkeypatch.setattr(ch.time, "time", lambda: later)
    assert ch.dead_reason("secret-key") is None  # one fresh attempt; a terminal 401 marks it again
    assert ch.dead_marks() == []


def test_a_reason_is_logged_once_per_change(caplog):
    caplog.set_level(logging.WARNING)
    for _ in range(3):
        ch.say_suppressed("heartbeat-test", "api_key unusable (invalid_key)")
    ch.say_suppressed("heartbeat-test", None)
    ch.say_suppressed("heartbeat-test", "api_key unusable (invalid_key)")
    lines = [r.message for r in caplog.records if "heartbeat-test" in r.message]
    assert len(lines) == 3 and "invalid_key" in lines[0] and "usable again" in lines[1]


def test_the_liveness_probe_names_the_reason(monkeypatch):
    from empirica.core.loop_scheduler import liveness_probe as lp

    monkeypatch.setattr(
        "empirica.core.auth.cortex_bearer",
        lambda: {"url": "https://c", "bearer": None, "source": "none", "reason": "api_key unusable (invalid_key)"},
    )
    loaded = lp._default_cortex_loader()
    assert loaded is not None and loaded["api_key"] is None and "invalid_key" in loaded["reason"]
