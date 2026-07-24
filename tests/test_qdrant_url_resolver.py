"""Tests for the per-project URL resolver hook (prop_ure7rqfuon, 2026-07-24).

Adds ``set_url_resolver`` / ``get_url_resolver`` + a ``project_id`` kwarg
on ``_get_qdrant_client`` / ``_check_qdrant_available`` / ``_service_url``.
When a resolver is installed AND project_id is passed AND no explicit
qdrant_url is passed, the resolver's return wins.

Cover:
  - hook install / clear / get
  - _get_qdrant_client(project_id=X) uses resolver return
  - Explicit qdrant_url= still wins over resolver (priority 1)
  - No resolver installed → env fallback (backward compat)
  - Resolver returns None → env fallback (opt-in per-project)
  - Resolver raises → env fallback + warning (never let hook crash writes)
  - Default resolver factory: DB missing / env missing / project not in DB →
    None for all
"""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from empirica.core.qdrant import connection as conn_mod


@pytest.fixture(autouse=True)
def clear_resolver():
    """Reset resolver + env between tests so no leakage."""
    conn_mod.set_url_resolver(None)
    conn_mod._qdrant_available = None  # reset lazy availability check
    old_env = os.environ.pop("EMPIRICA_QDRANT_URL", None)
    yield
    conn_mod.set_url_resolver(None)
    if old_env is not None:
        os.environ["EMPIRICA_QDRANT_URL"] = old_env


# ── Hook install/get/clear ─────────────────────────────────────────


def test_set_and_get_url_resolver_round_trip():
    def resolver(pid):
        return "http://x:7335"

    conn_mod.set_url_resolver(resolver)
    assert conn_mod.get_url_resolver() is resolver


def test_clear_url_resolver_with_none():
    conn_mod.set_url_resolver(lambda pid: "http://x")
    assert conn_mod.get_url_resolver() is not None
    conn_mod.set_url_resolver(None)
    assert conn_mod.get_url_resolver() is None


# ── _get_qdrant_client wiring ───────────────────────────────────────


def test_resolver_used_when_project_id_and_no_explicit_url():
    """project_id + no qdrant_url + resolver installed → resolver URL used."""
    conn_mod.set_url_resolver(lambda pid: "http://per-org:7335")
    with (
        patch("empirica.core.qdrant.connection.QdrantClient", create=True) as MockClient,
        patch.object(
            conn_mod,
            "_get_qdrant_imports",
            return_value=(MockClient, None, None, None),
        ),
    ):
        conn_mod._get_qdrant_client(project_id="proj-a")
    MockClient.assert_called_once_with(url="http://per-org:7335")


def test_explicit_qdrant_url_wins_over_resolver():
    """Priority 1: explicit qdrant_url> resolver. Resolver never called."""
    resolver = MagicMock(return_value="http://resolver:7335")
    conn_mod.set_url_resolver(resolver)
    with (
        patch("empirica.core.qdrant.connection.QdrantClient", create=True) as MockClient,
        patch.object(
            conn_mod,
            "_get_qdrant_imports",
            return_value=(MockClient, None, None, None),
        ),
    ):
        conn_mod._get_qdrant_client(
            qdrant_url="http://explicit:9999",
            project_id="proj-a",
        )
    MockClient.assert_called_once_with(url="http://explicit:9999")
    resolver.assert_not_called()


def test_no_resolver_installed_falls_back_to_env(monkeypatch):
    """Backward compat: no resolver → env → localhost. project_id ignored."""
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://env:6333")
    with (
        patch("empirica.core.qdrant.connection.QdrantClient", create=True) as MockClient,
        patch.object(
            conn_mod,
            "_get_qdrant_imports",
            return_value=(MockClient, None, None, None),
        ),
    ):
        conn_mod._get_qdrant_client(project_id="proj-a")
    MockClient.assert_called_once_with(url="http://env:6333")


def test_resolver_returns_none_falls_through_to_env(monkeypatch):
    """Resolver returns None → treated as opt-out, env wins."""
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://env:6333")
    conn_mod.set_url_resolver(lambda pid: None)
    with (
        patch("empirica.core.qdrant.connection.QdrantClient", create=True) as MockClient,
        patch.object(
            conn_mod,
            "_get_qdrant_imports",
            return_value=(MockClient, None, None, None),
        ),
    ):
        conn_mod._get_qdrant_client(project_id="proj-a")
    MockClient.assert_called_once_with(url="http://env:6333")


def test_resolver_raises_falls_through_to_env(monkeypatch, caplog):
    """A resolver that blows up MUST NOT block writes — falls through + warns."""
    monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://env:6333")

    def boom(pid):
        raise RuntimeError("resolver exploded")

    conn_mod.set_url_resolver(boom)
    with (
        patch("empirica.core.qdrant.connection.QdrantClient", create=True) as MockClient,
        patch.object(
            conn_mod,
            "_get_qdrant_imports",
            return_value=(MockClient, None, None, None),
        ),
        caplog.at_level("WARNING"),
    ):
        conn_mod._get_qdrant_client(project_id="proj-a")
    MockClient.assert_called_once_with(url="http://env:6333")
    assert any("resolver exploded" in r.message for r in caplog.records)


def test_no_project_id_skips_resolver_even_when_installed():
    """Backward compat: caller without project_id gets the pre-hook flow.
    Resolver stays untouched — no accidental fires."""
    resolver = MagicMock(return_value="http://resolver:7335")
    conn_mod.set_url_resolver(resolver)
    with (
        patch("empirica.core.qdrant.connection.QdrantClient", create=True) as MockClient,
        patch.object(
            conn_mod,
            "_get_qdrant_imports",
            return_value=(MockClient, None, None, None),
        ),
        patch("urllib.request.urlopen") as urlopen,
    ):
        urlopen.side_effect = OSError()  # localhost unavailable
        result = conn_mod._get_qdrant_client()  # no project_id
    resolver.assert_not_called()
    assert result is None  # no server available


# ── _service_url wiring ────────────────────────────────────────────


def test_service_url_uses_resolver_when_project_id_and_no_url():
    conn_mod.set_url_resolver(lambda pid: "http://per-org:7335")
    assert conn_mod._service_url(project_id="proj-a") == "http://per-org:7335"


def test_service_url_explicit_url_wins():
    conn_mod.set_url_resolver(lambda pid: "http://resolver:7335")
    assert conn_mod._service_url(qdrant_url="http://explicit:8000", project_id="proj-a") == "http://explicit:8000"


# ── _check_qdrant_available accepts project_id kwarg ────────────────


def test_check_qdrant_available_accepts_project_id():
    """API parity — project_id must be accepted (currently unused but
    part of the signature contract so callers can thread uniformly)."""
    import inspect

    sig = inspect.signature(conn_mod._check_qdrant_available)
    assert "project_id" in sig.parameters


# ── Default resolver factory ───────────────────────────────────────


def test_default_resolver_returns_none_when_env_unset(monkeypatch, tmp_path):
    from empirica.core.qdrant.url_resolver_default import make_default_resolver

    monkeypatch.delenv("CORTEX_QDRANT_URLS_BY_ORG", raising=False)
    resolver = make_default_resolver(tenants_db_path=str(tmp_path / "no.db"))
    assert resolver("some-project-id") is None


def test_default_resolver_returns_none_when_project_missing(monkeypatch, tmp_path):
    from empirica.core.qdrant.url_resolver_default import make_default_resolver

    db_path = tmp_path / "tenants.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, org_id TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setenv("CORTEX_QDRANT_URLS_BY_ORG", "org-nle=http://localhost:7335")
    resolver = make_default_resolver(tenants_db_path=str(db_path))
    assert resolver("nonexistent-project") is None


def test_default_resolver_resolves_when_project_in_db_and_org_mapped(
    monkeypatch,
    tmp_path,
):
    from empirica.core.qdrant.url_resolver_default import make_default_resolver

    db_path = tmp_path / "tenants.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, org_id TEXT)")
    conn.execute(
        "INSERT INTO projects (id, org_id) VALUES ('proj-nle-1', 'org-nle')",
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CORTEX_QDRANT_URLS_BY_ORG", "org-nle=http://localhost:7335")
    resolver = make_default_resolver(tenants_db_path=str(db_path))
    assert resolver("proj-nle-1") == "http://localhost:7335"


def test_default_resolver_returns_none_when_org_not_mapped(
    monkeypatch,
    tmp_path,
):
    """Project exists but org has no URL entry → None (fall through to env)."""
    from empirica.core.qdrant.url_resolver_default import make_default_resolver

    db_path = tmp_path / "tenants.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, org_id TEXT)")
    conn.execute(
        "INSERT INTO projects (id, org_id) VALUES ('proj-empirica-1', 'org-empirica')",
    )
    conn.commit()
    conn.close()

    # url_map has only org-nle. org-empirica falls through.
    monkeypatch.setenv("CORTEX_QDRANT_URLS_BY_ORG", "org-nle=http://localhost:7335")
    resolver = make_default_resolver(tenants_db_path=str(db_path))
    assert resolver("proj-empirica-1") is None
