"""Tests for `CredentialsLoader.get_cortex_config()` — Cortex url resolution
via env-vars + ~/.empirica/credentials.yaml (1.9.7+).

Core no longer carries a Cortex api_key (goal 739e47b2, 2026-11-26): a key in
the file or CORTEX_API_KEY is ignored, never written, and only reported as a
leftover by `stored_cortex_api_key()`.
"""

from __future__ import annotations

import pytest

from empirica.config.credentials_loader import CredentialsLoader


@pytest.fixture(autouse=True)
def isolate_home_and_env(monkeypatch, tmp_path):
    """Universal isolation — every test gets a fake HOME and clean env.

    Without this, tests that fall through to `~/.empirica/credentials.yaml`
    can read OR overwrite the developer's real credentials file.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("EMPIRICA_CREDENTIALS_PATH", raising=False)


@pytest.fixture
def isolated_loader(monkeypatch, tmp_path):
    """Build a CredentialsLoader pointed at a tmp credentials.yaml."""

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
    """The url still resolves from the environment; CORTEX_API_KEY is ignored, because core carries no Cortex
    api_key any more (goal 739e47b2) and a key in the environment must not come back as a credential."""
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.example.com/")
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_env_key")

    loader = isolated_loader(None)
    cfg = loader.get_cortex_config()

    # Trailing slash stripped
    assert cfg["url"] == "https://cortex.example.com"
    assert cfg["api_key"] is None
    assert loader.stored_cortex_api_key() is True  # reported as a leftover, never returned


def test_cortex_url_alias_works(monkeypatch, isolated_loader):
    """CORTEX_URL is accepted as alias for CORTEX_REMOTE_URL."""
    monkeypatch.setenv("CORTEX_URL", "https://cortex.example.com")

    loader = isolated_loader(None)
    cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.example.com"


# ─── credentials.yaml resolution ──────────────────────────────────────


def test_credentials_yaml_resolves_cortex(isolated_loader):
    """With no env vars, the url comes from the `cortex:` block; a leftover api_key there is ignored (goal 739e47b2)
    and only reported by stored_cortex_api_key()."""
    yaml_content = """
version: 1.0
cortex:
  url: https://cortex.file.com/
  api_key: ctx_file_key
"""
    loader = isolated_loader(yaml_content)
    cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.file.com"
    assert cfg["api_key"] is None
    assert loader.stored_cortex_api_key() is True


def test_file_wins_over_env_per_field(monkeypatch, isolated_loader):
    """File is canonical (2026-05-28 flip): a set env url does NOT override the file url. The guard was written
    against a stale env value silently shadowing the file (the listener-deaf incident); with the key retired the
    url is the field it still protects."""
    yaml_content = """
version: 1.0
cortex:
  url: https://cortex.file.com
"""
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.stale-env.com")
    loader = isolated_loader(yaml_content)
    cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.file.com"  # file wins, env ignored


def test_env_fills_gap_file_lacks(monkeypatch, isolated_loader):
    """Env still fills the url when the file does not provide one (no shadowing risk when the file is silent)."""
    yaml_content = """
version: 1.0
cortex: {}
"""
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.env.com/")
    loader = isolated_loader(yaml_content)
    cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.env.com"  # env fills the missing url
    assert cfg["api_key"] is None


def test_env_file_mismatch_logs_warning(monkeypatch, isolated_loader, caplog):
    """When the env url differs from the file url, the env is ignored AND a warning is logged so the divergence is
    visible instead of silent."""
    import logging

    yaml_content = """
version: 1.0
cortex:
  url: https://cortex.file.com
"""
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.stale-env.com")
    loader = isolated_loader(yaml_content)
    with caplog.at_level(logging.WARNING):
        cfg = loader.get_cortex_config()
    assert cfg["url"] == "https://cortex.file.com"
    assert any("CORTEX_REMOTE_URL env var differs" in rec.message for rec in caplog.records)


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


def test_completely_missing_credentials_file(isolated_loader):
    """No env, no file → (None, None) without crashing."""
    loader = isolated_loader(None)
    cfg = loader.get_cortex_config()
    assert cfg["url"] is None
    assert cfg["api_key"] is None


# ─── save_cortex_config — write path ──────────────────────────────────


def test_save_cortex_creates_file_when_missing(tmp_path, monkeypatch):
    """No existing credentials.yaml → save creates it with a cortex: block holding the url and no api_key."""
    target = tmp_path / "credentials.yaml"
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    loader = CredentialsLoader()
    path = loader.save_cortex_config(
        url="https://cortex.example.com",
        config_path=target,
    )

    assert path == target
    assert target.exists()
    import yaml

    parsed = yaml.safe_load(target.read_text())
    assert parsed["cortex"]["url"] == "https://cortex.example.com"
    assert "api_key" not in parsed["cortex"]
    assert parsed["version"] == "1.0"


def test_save_cortex_preserves_providers_section(tmp_path, monkeypatch):
    """Existing providers.qwen.api_key must survive a cortex save."""
    target = tmp_path / "credentials.yaml"
    target.write_text("""
version: 1.0
providers:
  qwen:
    api_key: qwen_secret_unchanged
    base_url: https://qwen.example.com
cortex:
  url: https://old.example.com
""")
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    loader = CredentialsLoader()
    loader.save_cortex_config(url="https://new.example.com", config_path=target)

    import yaml

    parsed = yaml.safe_load(target.read_text())
    assert parsed["providers"]["qwen"]["api_key"] == "qwen_secret_unchanged"
    assert parsed["providers"]["qwen"]["base_url"] == "https://qwen.example.com"
    assert parsed["cortex"]["url"] == "https://new.example.com"


def test_save_cortex_partial_update_url_only(tmp_path, monkeypatch):
    """save(url=X) rewrites the url and drops a leftover api_key: core carries only OAuth details in the cortex block
    (goal 739e47b2, David 2026-11-26); other fields stay."""
    target = tmp_path / "credentials.yaml"
    target.write_text("""
version: 1.0
cortex:
  url: https://A.example.com
  api_key: B
""")
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    loader = CredentialsLoader()
    loader.save_cortex_config(url="https://C.example.com", config_path=target)

    import yaml

    parsed = yaml.safe_load(target.read_text())
    assert parsed["cortex"]["url"] == "https://C.example.com"
    assert "api_key" not in parsed["cortex"]  # the retired key is dropped on write


def test_save_cortex_invalidates_cache(tmp_path, monkeypatch):
    """After save, next get_cortex_config() reads the new value (not cached)."""
    target = tmp_path / "credentials.yaml"
    target.write_text("""
version: 1.0
cortex:
  url: https://A.example.com
""")
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    loader = CredentialsLoader()
    cached = loader.get_cortex_config()
    assert cached["url"] == "https://A.example.com"

    loader.save_cortex_config(url="https://B.example.com", config_path=target)
    fresh = loader.get_cortex_config()
    assert fresh["url"] == "https://B.example.com"


def test_save_cortex_requires_url(tmp_path, monkeypatch):
    """save() without a url raises ValueError: the url is the only field the writer takes (goal 739e47b2)."""
    target = tmp_path / "credentials.yaml"
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    loader = CredentialsLoader()
    with pytest.raises(ValueError, match="url required"):
        loader.save_cortex_config(config_path=target)
    assert not target.exists()


def test_save_cortex_atomic_no_tempfile_leak(tmp_path, monkeypatch):
    """After save, no `.credentials-*.yaml.tmp` files left behind."""
    target = tmp_path / "credentials.yaml"
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    loader = CredentialsLoader()
    loader.save_cortex_config(
        url="https://x.example.com",
        config_path=target,
    )

    tmp_files = list(tmp_path.glob(".credentials-*.tmp"))
    assert tmp_files == [], f"Tempfiles leaked: {tmp_files}"


# ─── Daemon endpoints ─────────────────────────────────────────────────


def _serve_client(tmp_path, monkeypatch, body: str | None = None):
    from fastapi.testclient import TestClient

    from empirica.api.serve_app import create_serve_app

    target = tmp_path / "credentials.yaml"
    if body is not None:
        target.write_text(body)
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    return target, TestClient(create_serve_app())


def test_endpoint_post_cortex_writes_url_and_never_stores_a_key(tmp_path, monkeypatch):
    """POST /api/v1/credentials/cortex with url+api_key → the url is written, the api_key is ignored and never
    stored (core carries no Cortex key, goal 739e47b2), and the response says no key is set."""
    target, client = _serve_client(tmp_path, monkeypatch)
    with client:
        resp = client.post(
            "/api/v1/credentials/cortex",
            json={"url": "https://cortex.example.com", "api_key": "ctx_abcdwxyz"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["url"] == "https://cortex.example.com"
    assert body["api_key_set"] is False
    assert body["api_key_preview"] is None
    assert "credentials.yaml" in body["written_path"]
    assert target.exists()
    assert "ctx_abcdwxyz" not in target.read_text()


def test_endpoint_post_refuses_a_key_alone(tmp_path, monkeypatch):
    """POST with only an api_key → ok=false naming the retirement, and nothing is written."""
    target, client = _serve_client(tmp_path, monkeypatch)
    with client:
        resp = client.post("/api/v1/credentials/cortex", json={"api_key": "ctx_abcdwxyz"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "api_key is no longer accepted" in body["error"]
    assert not target.exists()


def test_endpoint_get_cortex_never_returns_a_leftover_key(tmp_path, monkeypatch):
    """GET /api/v1/credentials/cortex with a leftover key in credentials.yaml: the key is not reported as set,
    and the raw key appears nowhere in the response."""
    _target, client = _serve_client(
        tmp_path,
        monkeypatch,
        """
version: 1.0
cortex:
  url: https://x.example.com
  api_key: ctx_dontleakme_zzz9
""",
    )
    with client:
        resp = client.get("/api/v1/credentials/cortex")

    body = resp.json()
    assert body["ok"] is True
    assert body["url"] == "https://x.example.com"
    assert body["api_key_set"] is False
    # The raw key MUST NOT appear anywhere in the response
    assert "ctx_dontleakme_zzz9" not in resp.text


def test_endpoint_post_rejects_empty_payload(monkeypatch):
    """POST with no url + no api_key → 200 ok=false with hint."""
    from fastapi.testclient import TestClient

    from empirica.api.serve_app import create_serve_app

    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)

    app = create_serve_app()
    with TestClient(app) as client:
        resp = client.post("/api/v1/credentials/cortex", json={})
    assert resp.status_code == 200  # not 422 — surfaces as ok=false
    body = resp.json()
    assert body["ok"] is False
    assert "required" in body["error"].lower()


# ── save_ntfy_config (David, 2026-05-17 — first-run wizard) ──────────────


# Test-fixture token/password strings — obviously synthetic, not real
# secrets. Suppresses ruff S105/S106 false positives on hardcoded creds
# in test files. Defined module-level so all save_ntfy tests share them.
_FIXTURE_NTFY_TOKEN = "tk_test_abc123"  # noqa: S105
_FIXTURE_NTFY_TOKEN_XYZ = "tk_xyz"  # noqa: S105
_FIXTURE_NTFY_TOKEN_X = "tk_x"  # noqa: S105
_FIXTURE_NTFY_PASSWORD = "s3cret"  # noqa: S105


def test_save_ntfy_creates_file_when_missing(tmp_path, monkeypatch):
    """Fresh install: setup-claude-code wizard writes a complete ntfy block
    to a not-yet-existing credentials.yaml."""
    target = tmp_path / "credentials.yaml"
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))
    monkeypatch.delenv("ORCHESTRATION_NTFY_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_TOPIC", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_TOKEN", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_USER", raising=False)
    monkeypatch.delenv("ORCHESTRATION_NTFY_PASS", raising=False)

    from empirica.config.credentials_loader import CredentialsLoader

    loader = CredentialsLoader()
    written = loader.save_ntfy_config(
        url="https://ntfy.example.com",
        topic="orchestration-events",
        token=_FIXTURE_NTFY_TOKEN,
    )

    assert written == target
    assert target.exists()
    import yaml as _yaml

    data = _yaml.safe_load(target.read_text())
    assert data["ntfy"]["url"] == "https://ntfy.example.com"
    assert data["ntfy"]["topic"] == "orchestration-events"
    assert data["ntfy"]["token"] == _FIXTURE_NTFY_TOKEN
    # version key written by default
    assert data.get("version") == "1.0"


def test_save_ntfy_preserves_existing_cortex_block(tmp_path, monkeypatch):
    """The wizard runs after cortex is set (or vice versa). Writing the
    ntfy block must NEVER blow away the cortex block — atomic merge only."""
    target = tmp_path / "credentials.yaml"
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))
    import yaml as _yaml

    target.write_text(
        _yaml.dump(
            {
                "version": "1.0",
                "cortex": {"url": "https://cortex.example.com", "api_key": "ctx_keep_me"},
            }
        )
    )

    from empirica.config.credentials_loader import CredentialsLoader

    loader = CredentialsLoader()
    loader.save_ntfy_config(
        url="https://ntfy.example.com",
        topic="t",
        token=_FIXTURE_NTFY_TOKEN_XYZ,
    )

    data = _yaml.safe_load(target.read_text())
    # cortex block untouched
    assert data["cortex"] == {"url": "https://cortex.example.com", "api_key": "ctx_keep_me"}
    # ntfy block added
    assert data["ntfy"]["token"] == _FIXTURE_NTFY_TOKEN_XYZ


def test_save_ntfy_with_basic_auth_path(tmp_path, monkeypatch):
    """token is preferred but legacy user+password path must still work
    (some self-hosted ntfy installs predate access tokens)."""
    target = tmp_path / "credentials.yaml"
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))

    from empirica.config.credentials_loader import CredentialsLoader

    loader = CredentialsLoader()
    loader.save_ntfy_config(
        url="https://ntfy.example.com",
        topic="t",
        user="alice",
        password=_FIXTURE_NTFY_PASSWORD,
    )

    import yaml as _yaml

    data = _yaml.safe_load(target.read_text())
    assert data["ntfy"]["user"] == "alice"
    assert data["ntfy"]["password"] == _FIXTURE_NTFY_PASSWORD
    assert "token" not in data["ntfy"] or data["ntfy"].get("token") is None


def test_save_ntfy_requires_at_least_one_field(tmp_path, monkeypatch):
    """All-None call is misuse — surface as ValueError rather than silently
    writing an empty block."""
    target = tmp_path / "credentials.yaml"
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))

    from empirica.config.credentials_loader import CredentialsLoader

    loader = CredentialsLoader()
    import pytest

    with pytest.raises(ValueError, match="at least one field"):
        loader.save_ntfy_config()


def test_save_ntfy_strips_trailing_slash_on_url(tmp_path, monkeypatch):
    """Mirrors save_cortex_config — server convention is no trailing slash."""
    target = tmp_path / "credentials.yaml"
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(target))

    from empirica.config.credentials_loader import CredentialsLoader

    loader = CredentialsLoader()
    loader.save_ntfy_config(url="https://ntfy.example.com/", topic="t", token=_FIXTURE_NTFY_TOKEN_X)

    import yaml as _yaml

    data = _yaml.safe_load(target.read_text())
    assert data["ntfy"]["url"] == "https://ntfy.example.com"
