"""Core carries no Cortex api_key: OAuth and time-based tokens only (goal 739e47b2, David 2026-11-26).

Readers that used a stored or environment key with no OAuth path are cut first, then the writers, then the source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_doctor_no_longer_takes_a_key_from_the_environment(monkeypatch):
    from empirica.cli.command_handlers import doctor

    monkeypatch.setenv("CORTEX_URL", "https://cortex.test")
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_legacy")
    import empirica.core.auth.cortex_oauth as co

    monkeypatch.setattr(co, "cortex_bearer", lambda *a, **k: {"url": None, "bearer": None, "source": "none"})
    _url, key = doctor._resolve_cortex_creds()
    assert key != "ctx_legacy"


@pytest.mark.parametrize("hook", ["session-init.py", "session-end-postflight.py"])
def test_the_hooks_have_no_raw_key_fallback(hook):
    text = (ROOT / "empirica/plugins/claude-code-integration/hooks" / hook).read_text()
    assert 'os.environ.get("CORTEX_API_KEY"' not in text


def test_the_postflight_cache_feedback_sends_no_key():
    text = (ROOT / "empirica/cli/command_handlers/_workflow_postflight.py").read_text()
    assert 'os.environ.get("CORTEX_API_KEY"' not in text


def test_the_extension_grant_carries_no_key(monkeypatch):
    from empirica.cli.command_handlers import projects_commands as pc

    snap = pc._grant_credentials_snapshot({"url": "https://cortex.test", "api_key": "ctx_legacy"})
    assert snap == {"cortex": {"url": "https://cortex.test"}}


def _loader(tmp_path, monkeypatch, body):
    from empirica.config.credentials_loader import CredentialsLoader

    cred = tmp_path / "credentials.yaml"
    cred.write_text(body)
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(cred))
    monkeypatch.setattr(CredentialsLoader, "_instance", None, raising=False)
    loader = CredentialsLoader()
    monkeypatch.setattr(loader, "_find_config_file", lambda *a, **k: cred, raising=False)
    loader._credentials_cache = None
    loader._cache_signature = None
    return loader


def test_the_loader_supplies_no_key_from_the_file_or_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_env")
    loader = _loader(tmp_path, monkeypatch, "cortex:\n  url: https://cortex.test\n  api_key: ctx_file\n")
    cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.test" and not cfg.get("api_key")


def test_a_key_alone_is_no_credential():
    from empirica.core.auth import cortex_oauth

    class L:
        def get_cortex_config(self, *a, **k):
            return {"url": "https://cortex.test", "api_key": "ctx_legacy"}

        def get_cortex_oauth(self, *a, **k):
            return {}

        def cortex_oauth_sets(self):
            return []

    out = cortex_oauth.cortex_bearer(L())
    assert out.get("bearer") in (None, "") and out.get("source") != "api_key"


def test_the_writer_takes_no_key():
    import inspect

    from empirica.config.credentials_loader import CredentialsLoader

    assert "api_key" not in inspect.signature(CredentialsLoader.save_cortex_config).parameters
