"""Tests for the slug→UUID project identity migration (1.12 single-UUID model).

The migration engine is project.yaml-authoritative: a UUID yaml wins; a slug
yaml is resolved (workspace.db → cortex → mint) and the legacy slug is re-keyed
across every project-local db so history doesn't orphan. Unresolvable cases
return an actionable message rather than guessing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from empirica.core.identity_migration import (
    is_uuid,
    migrate_project_to_uuid,
    rekey_project_id_in_db,
    rekey_project_local_dbs,
    resolve_canonical_uuid,
    run_force_migration,
)

_UUID_A = "11111111-2222-3333-4444-555555555555"
_UUID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _make_project(tmp_path: Path, project_id: str, *, name: str = "empirica-outreach") -> Path:
    """A project root with .empirica/project.yaml carrying the given id."""
    root = tmp_path / name
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(
        yaml.safe_dump({"project_id": project_id, "ai_id": name}, sort_keys=False)
    )
    return root


def _seed_sessions_db(root: Path, project_id: str) -> Path:
    """A project-local sessions.db with two project_id-bearing tables seeded."""
    db = root / ".empirica" / "sessions" / "sessions.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sessions (session_id TEXT, project_id TEXT)")
    conn.execute("CREATE TABLE findings (id TEXT, project_id TEXT, finding TEXT)")
    conn.execute("CREATE TABLE unrelated (id TEXT, note TEXT)")  # no project_id
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (project_id,))
    conn.execute("INSERT INTO findings VALUES ('f1', ?, 'hi')", (project_id,))
    conn.execute("INSERT INTO findings VALUES ('f2', 'other-project', 'no')")
    conn.commit()
    conn.close()
    return db


# ── is_uuid ─────────────────────────────────────────────────────────────


def test_is_uuid():
    assert is_uuid(_UUID_A)
    assert is_uuid(_UUID_A.upper())
    assert not is_uuid("empirica-outreach")
    assert not is_uuid("empirica")
    assert not is_uuid("")
    assert not is_uuid(None)


# ── resolve_canonical_uuid ──────────────────────────────────────────────


def test_resolve_yaml_uuid_wins(tmp_path):
    root = _make_project(tmp_path, _UUID_A)
    uuid, source = resolve_canonical_uuid(root)
    assert uuid == _UUID_A
    assert source == "yaml"


def test_resolve_from_workspace(tmp_path):
    root = _make_project(tmp_path, "empirica-outreach")
    ws = tmp_path / "workspace.db"
    conn = sqlite3.connect(str(ws))
    conn.execute("CREATE TABLE global_projects (id TEXT, trajectory_path TEXT)")
    conn.execute(
        "INSERT INTO global_projects VALUES (?, ?)",
        (_UUID_A, str(root / ".empirica")),
    )
    conn.commit()
    conn.close()
    uuid, source = resolve_canonical_uuid(root, workspace_db=ws)
    assert uuid == _UUID_A
    assert source == "workspace"


def test_resolve_from_cortex_when_no_workspace(tmp_path):
    root = _make_project(tmp_path, "empirica-outreach")
    calls = {}

    def fake_cortex(slug, tenant):
        calls["slug"], calls["tenant"] = slug, tenant
        return _UUID_B

    uuid, source = resolve_canonical_uuid(
        root, workspace_db=tmp_path / "nope.db", cortex_resolver=fake_cortex, tenant="david"
    )
    assert uuid == _UUID_B
    assert source == "cortex"
    assert calls == {"slug": "empirica-outreach", "tenant": "david"}


def test_resolve_unresolved(tmp_path):
    root = _make_project(tmp_path, "empirica-outreach")
    uuid, source = resolve_canonical_uuid(root, workspace_db=tmp_path / "nope.db")
    assert uuid is None and source is None


# ── re-key ──────────────────────────────────────────────────────────────


def test_rekey_updates_all_matching_tables(tmp_path):
    root = _make_project(tmp_path, "empirica-outreach")
    db = _seed_sessions_db(root, "empirica-outreach")
    touched = rekey_project_id_in_db(db, "empirica-outreach", _UUID_A)
    assert touched == {"sessions": 1, "findings": 1}  # not the 'other-project' row

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT project_id FROM sessions").fetchone()[0] == _UUID_A
    assert conn.execute("SELECT project_id FROM findings WHERE id='f1'").fetchone()[0] == _UUID_A
    # the unrelated project's row is untouched
    assert conn.execute("SELECT project_id FROM findings WHERE id='f2'").fetchone()[0] == "other-project"
    conn.close()


def test_rekey_noop_when_same(tmp_path):
    root = _make_project(tmp_path, _UUID_A)
    db = _seed_sessions_db(root, _UUID_A)
    assert rekey_project_id_in_db(db, _UUID_A, _UUID_A) == {}


def test_rekey_local_dbs_walks_empirica_dir(tmp_path):
    root = _make_project(tmp_path, "empirica-outreach")
    _seed_sessions_db(root, "empirica-outreach")
    out = rekey_project_local_dbs(root, "empirica-outreach", _UUID_A)
    assert "sessions/sessions.db" in out
    assert out["sessions/sessions.db"] == {"sessions": 1, "findings": 1}


# ── migrate_project_to_uuid ─────────────────────────────────────────────


def test_migrate_no_project(tmp_path):
    result = migrate_project_to_uuid(tmp_path / "empty")
    assert result["status"] == "no_project"


def test_migrate_already_uuid_is_noop(tmp_path):
    root = _make_project(tmp_path, _UUID_A)
    result = migrate_project_to_uuid(root)
    assert result["status"] == "already_uuid"
    assert result["project_id"] == _UUID_A


def test_migrate_slug_via_cortex_rekeys_and_rewrites_yaml(tmp_path):
    root = _make_project(tmp_path, "empirica-outreach")
    _seed_sessions_db(root, "empirica-outreach")

    result = migrate_project_to_uuid(
        root,
        workspace_db=tmp_path / "nope.db",
        cortex_resolver=lambda slug, tenant: _UUID_B,
        tenant="david",
    )
    assert result["status"] == "migrated"
    assert result["source"] == "cortex"
    assert result["project_id"] == _UUID_B
    assert result["yaml_updated"] is True
    assert result["rekeyed"]["sessions/sessions.db"] == {"sessions": 1, "findings": 1}

    # yaml now carries the UUID
    cfg = yaml.safe_load((root / ".empirica" / "project.yaml").read_text())
    assert cfg["project_id"] == _UUID_B
    assert cfg["ai_id"] == "empirica-outreach"  # name/practice identity preserved


def test_migrate_slug_via_mint(tmp_path):
    root = _make_project(tmp_path, "empirica-outreach")
    result = migrate_project_to_uuid(root, workspace_db=tmp_path / "nope.db", mint=lambda: _UUID_A)
    assert result["status"] == "migrated"
    assert result["source"] == "minted"
    assert result["project_id"] == _UUID_A


def test_migrate_unresolved_leaves_yaml_untouched(tmp_path):
    root = _make_project(tmp_path, "empirica-outreach")
    result = migrate_project_to_uuid(root, workspace_db=tmp_path / "nope.db")
    assert result["status"] == "unresolved"
    assert result["slug"] == "empirica-outreach"
    assert "project-register" in result["message"]
    # yaml is NOT guessed/rewritten
    cfg = yaml.safe_load((root / ".empirica" / "project.yaml").read_text())
    assert cfg["project_id"] == "empirica-outreach"


# ── run_force_migration (cortex-gated policy) ───────────────────────────


def test_force_no_cortex_mints_locally(tmp_path):
    """No Cortex installed → purely local → minting a UUID is safe."""
    root = _make_project(tmp_path, "empirica-outreach")
    _seed_sessions_db(root, "empirica-outreach")
    result = run_force_migration(
        root,
        cortex_installed_fn=lambda: False,
        mint=lambda: _UUID_A,
    )
    assert result["cortex_installed"] is False
    assert result["status"] == "migrated"
    assert result["source"] == "minted"
    assert result["project_id"] == _UUID_A
    assert result["rekeyed"]["sessions/sessions.db"] == {"sessions": 1, "findings": 1}


def test_force_cortex_installed_never_mints_routes_to_register(tmp_path):
    """Cortex installed + unresolvable → never mint (fork risk); route to register."""
    root = _make_project(tmp_path, "empirica-outreach")
    minted = []
    result = run_force_migration(
        root,
        cortex_installed_fn=lambda: True,
        cortex_resolver=lambda slug, tenant: None,  # cortex lookup misses
        mint=lambda: minted.append(1) or _UUID_A,  # must NOT be called
    )
    assert result["cortex_installed"] is True
    assert result["status"] == "unresolved"
    assert "project-register" in result["message"]
    assert minted == []  # mint was not invoked


def test_force_cortex_installed_resolves_via_cortex(tmp_path):
    """Cortex installed → the injected resolver supplies the canonical UUID."""
    root = _make_project(tmp_path, "empirica-outreach")
    _seed_sessions_db(root, "empirica-outreach")
    result = run_force_migration(
        root,
        cortex_installed_fn=lambda: True,
        cortex_resolver=lambda slug, tenant: _UUID_B,
    )
    assert result["cortex_installed"] is True
    assert result["status"] == "migrated"
    assert result["source"] == "cortex"
    assert result["project_id"] == _UUID_B


def test_force_already_uuid_is_noop(tmp_path):
    root = _make_project(tmp_path, _UUID_A)
    result = run_force_migration(root, cortex_installed_fn=lambda: False)
    assert result["status"] == "already_uuid"


# ── live cortex slug resolver (mocked transport) ────────────────────────


def test_cortex_slug_resolver_parses_200_and_404(tmp_path, monkeypatch):
    import io
    import json as _json

    from empirica.core import identity_migration as im

    # The resolver now goes through the SHARED credential contract rather than
    # hand-parsing ~/.empirica/credentials.yaml, so the double moves with it.
    #
    # The old setup wrote a credentials.yaml under tmp_path and patched
    # `im.Path.home`. That worked against a hand-parser and is order-dependent
    # against the loader, which caches a singleton — the test passed alone and
    # failed in a group where an earlier test had already populated the cache.
    #
    # Patching `cortex_bearer` is also what this test MEANS: the subject is
    # "does the slug resolver parse 200 and 404", and the credentials were only
    # ever scaffolding to get past the guard.
    monkeypatch.setattr(
        "empirica.core.auth.cortex_bearer",
        lambda *a, **k: {"url": "https://cortex.example", "bearer": "ctx_x", "source": "test"},
    )

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        slug = req.full_url.rsplit("/", 1)[-1]
        assert req.headers.get("Authorization") == "Bearer ctx_x"
        if slug == "found-slug":
            return _Resp(_json.dumps({"ok": True, "project": {"id": _UUID_B}}).encode())
        raise Exception("404 not_found")  # urllib raises HTTPError on 404

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    resolver = im._make_cortex_slug_resolver()
    assert resolver("found-slug", "david") == _UUID_B
    assert resolver("missing-slug", "david") is None  # 404 → None, no raise
    assert resolver("", "david") is None  # empty slug short-circuits


# ─── the "is Cortex installed" check fails toward NOT minting ─────────────


def test_cortex_installed_check_that_cannot_run_answers_yes(monkeypatch, caplog):
    """The question is "might this project already have a Cortex UUID". A check
    that cannot run must answer YES: True routes to the resolver/register path
    (worst case an actionable 'unresolved'); False routes to MINT (worst case
    the identity fork this module exists to prevent, reached through a broken
    auth import instead of a missing key)."""
    import logging
    import sys

    from empirica.core import identity_migration as im

    monkeypatch.setitem(sys.modules, "empirica.core.auth.cortex_oauth", None)  # import raises ImportError
    with caplog.at_level(logging.WARNING, logger=im.logger.name):
        assert im._cortex_installed() is True
    assert "assuming INSTALLED" in caplog.text


def test_a_broken_installed_check_never_mints(tmp_path, monkeypatch):
    """End to end through run_force_migration: with the check broken, no mint
    function is wired, so a slug project resolves to 'unresolved', not a fresh
    UUID."""
    import sys

    from empirica.core import identity_migration as im

    root = tmp_path / "proj"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text("ai_id: proj\nproject_id: proj\n")
    monkeypatch.setitem(sys.modules, "empirica.core.auth.cortex_oauth", None)

    minted = []
    result = im.run_force_migration(
        root,
        cortex_resolver=lambda slug, tenant: None,
        mint=lambda: minted.append("x") or "11111111-2222-4333-8444-555555555555",
    )
    assert result["cortex_installed"] is True
    assert minted == []
    assert result.get("source") != "minted"
