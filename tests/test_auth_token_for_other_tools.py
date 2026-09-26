"""`empirica auth token` hands other tools the credential `auth login` stored.

Cortex has one token shape (one audience, no subscription claims, authorization
per request), so the stored token, refreshed when expired, is what every
ecosystem tool should present (cortex, 2026-09-26; David's go the same day).
Because many tools may now ask at once and cortex rotates the refresh token on
every use, a refresh is serialized across processes.
"""

from __future__ import annotations

import threading
import time
import types

import pytest

from empirica.cli.command_handlers import auth_commands
from empirica.config.credentials_loader import CredentialsLoader

_LIVE = "LIVE-TOKEN"
_OLD = "OLD-TOKEN"
_NEW = "NEW-TOKEN"
_RT = "RT1"


@pytest.fixture
def loader(monkeypatch, tmp_path):
    p = tmp_path / "credentials.yaml"
    p.write_text('version: "1.0"\ncortex:\n  url: https://c.test\n')
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(p))
    ld = CredentialsLoader()
    ld._credentials_cache = None
    monkeypatch.setattr(auth_commands, "_loader", lambda: ld)
    return ld


def _run(output="human"):
    return auth_commands.handle_auth_token_command(types.SimpleNamespace(output=output))


def test_a_live_token_is_printed_bare_for_command_substitution(loader, capsys):
    loader.save_cortex_oauth(access_token=_LIVE, refresh_token=_RT, expires_at=time.time() + 3600)
    assert _run() == 0
    assert capsys.readouterr().out.strip() == _LIVE


def test_json_carries_the_token_and_its_type(loader, capsys):
    import json

    loader.save_cortex_oauth(access_token=_LIVE, refresh_token=_RT, expires_at=time.time() + 3600)
    assert _run("json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["access_token"] == _LIVE and out["token_type"] == "Bearer"  # noqa: S105


def test_no_token_set_exits_1_and_prints_nothing_to_stdout(loader, capsys):
    assert _run() == 1
    captured = capsys.readouterr()
    assert captured.out == "", "a caller must never mistake an error message for a token"
    assert "auth login" in captured.err


def test_an_expired_token_that_cannot_refresh_is_not_handed_out(loader, capsys, monkeypatch):
    loader.save_cortex_oauth(access_token=_OLD, refresh_token=_RT, expires_at=time.time() - 10)

    def failing_refresh(_loader, **_k):
        def _r(*_a):
            raise RuntimeError("token endpoint unreachable")

        return _r

    monkeypatch.setattr("empirica.core.auth.cortex_oauth.default_refresh", failing_refresh)
    assert _run() == 1
    assert capsys.readouterr().out == ""


def test_concurrent_callers_refresh_once(loader):
    """Two callers with an expired token: the second waits on the lock, re-reads
    the renewed set, and does not spend the rotated refresh token again."""
    loader.save_cortex_oauth(access_token=_OLD, refresh_token=_RT, expires_at=time.time() - 10)
    calls = []

    def refresh(refresh_token, _endpoint):
        calls.append(refresh_token)
        time.sleep(0.3)  # widen the window in which the second caller would race
        return {"access_token": _NEW, "expires_at": time.time() + 3600, "refresh_token": "RT2"}

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(loader.cortex_access_token(refresh=refresh))) for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [_NEW, _NEW]
    assert calls == [_RT], "the refresh token was spent twice"
