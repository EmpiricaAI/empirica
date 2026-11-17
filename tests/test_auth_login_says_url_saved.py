"""A first auth login says it set the Cortex URL, and nothing in its output reads as 'no URL' (goal 9a4eb934).

Onboarding a client, David saw the login appear to leave Cortex with no URL. The URL was saved; the output said
'No credentials config found' and '(the hosted Cortex: no URL was configured ...)'.
"""

from __future__ import annotations

import argparse
import logging

import yaml


def test_a_first_login_reports_the_url_it_saved(tmp_path, monkeypatch, capsys, caplog):
    import empirica.core.auth as auth
    from empirica.cli.command_handlers import auth_commands as ac
    from empirica.config.credentials_loader import CredentialsLoader
    from empirica.core.auth.cortex_oauth import DEFAULT_CORTEX_URL

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EMPIRICA_CREDENTIALS_PATH", raising=False)
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    monkeypatch.setattr(
        auth,
        "login",
        lambda **_kw: {"ok": True, "client_id": "c" * 20, "cortex_url": DEFAULT_CORTEX_URL, "url_source": "default",
                       "expires_at": None, "has_refresh_token": True, "org_id": None},
    )  # fmt: skip
    monkeypatch.setattr(ac, "_verify_token", lambda identity=None, org_id=None: None)
    try:
        with caplog.at_level(logging.WARNING):
            ac.handle_auth_login_command(
                argparse.Namespace(output="human", timeout=None, url=None, invite=None, org=None, remote=False)
            )
        out = capsys.readouterr().out
        saved = yaml.safe_load((tmp_path / ".empirica" / "credentials.yaml").read_text())
        assert saved["cortex"]["url"] == DEFAULT_CORTEX_URL
        assert f"Cortex URL set to {DEFAULT_CORTEX_URL}" in out and "credentials.yaml" in out
        assert "no URL" not in out and "No credentials config found" not in caplog.text + out
    finally:
        CredentialsLoader._instance = None
        CredentialsLoader._credentials_cache = None
