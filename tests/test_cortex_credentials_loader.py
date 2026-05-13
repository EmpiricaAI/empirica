"""Tests for `CredentialsLoader.get_cortex_config()` — Cortex creds resolution
via env-vars + ~/.empirica/credentials.yaml (1.9.4+).

Mirrors the extension's chrome.storage save (`cortexUrl` + `cortexApiKey`)
for CLI users so they don't have to export env vars in every shell.
"""

from __future__ import annotations

import pytest

from empirica.config.credentials_loader import CredentialsLoader


@pytest.fixture
def isolated_loader(monkeypatch, tmp_path):
    """Build a CredentialsLoader pointed at a tmp credentials.yaml."""
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("EMPIRICA_CREDENTIALS_PATH", raising=False)

    # Reset singleton cache so the test gets a fresh load
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None

    def _make(yaml_content: str | None):
        if yaml_content is not None:
            creds_file = tmp_path / "credentials.yaml"
            creds_file.write_text(yaml_content)
            monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(creds_file))
        # Fresh instance per call
        CredentialsLoader._instance = None
        CredentialsLoader._credentials_cache = None
        return CredentialsLoader()
    return _make


# ─── env-var resolution ───────────────────────────────────────────────


def test_env_vars_resolve_cortex(monkeypatch, isolated_loader):
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.example.com/")
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_env_key")

    loader = isolated_loader(None)
    cfg = loader.get_cortex_config()

    # Trailing slash stripped
    assert cfg["url"] == "https://cortex.example.com"
    assert cfg["api_key"] == "ctx_env_key"


def test_cortex_url_alias_works(monkeypatch, isolated_loader):
    """CORTEX_URL is accepted as alias for CORTEX_REMOTE_URL."""
    monkeypatch.setenv("CORTEX_URL", "https://cortex.example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_env_key")

    loader = isolated_loader(None)
    cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.example.com"


# ─── credentials.yaml resolution ──────────────────────────────────────


def test_credentials_yaml_resolves_cortex(isolated_loader):
    """With no env vars, reads `cortex:` block from credentials.yaml."""
    yaml_content = """
version: 1.0
cortex:
  url: https://cortex.file.com/
  api_key: ctx_file_key
"""
    loader = isolated_loader(yaml_content)
    cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.file.com"
    assert cfg["api_key"] == "ctx_file_key"


def test_env_overrides_file_per_field(monkeypatch, isolated_loader):
    """Env per-field overrides file. Partial env still works."""
    yaml_content = """
version: 1.0
cortex:
  url: https://cortex.file.com
  api_key: ctx_file_key
"""
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_env_key_overrides")
    # No URL env — file's URL should win
    loader = isolated_loader(yaml_content)
    cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.file.com"  # from file
    assert cfg["api_key"] == "ctx_env_key_overrides"  # from env


def test_missing_cortex_block_returns_none(isolated_loader):
    """No cortex block + no env → (None, None)."""
    yaml_content = """
version: 1.0
providers:
  openai:
    api_key: sk-test
"""
    loader = isolated_loader(yaml_content)
    cfg = loader.get_cortex_config()
    assert cfg["url"] is None
    assert cfg["api_key"] is None


def test_partial_cortex_block(isolated_loader):
    """Only `url` configured → api_key is None, doesn't crash."""
    yaml_content = """
version: 1.0
cortex:
  url: https://cortex.example.com
"""
    loader = isolated_loader(yaml_content)
    cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.example.com"
    assert cfg["api_key"] is None


def test_completely_missing_credentials_file(monkeypatch, isolated_loader):
    """No env, no file → (None, None) without crashing."""
    loader = isolated_loader(None)
    cfg = loader.get_cortex_config()
    assert cfg["url"] is None
    assert cfg["api_key"] is None
