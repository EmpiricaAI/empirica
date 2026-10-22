"""Every reader of `epistemic_sources.canonical_path` must resolve it (goal 8104e065, second item).

`canonical_path` is stored REPO-RELATIVE for a file inside the project (core/sources/canonical_path.py). `resolve()` is the
counterpart every reader needs, and its docstring says why: "a half-migrated column is worse than an un-migrated one, because
the rows that still work hide the rows that do not." Three readers opened the stored value as-is, so they worked from the repo
root and silently found nothing from anywhere else:

* `_maybe_push_small_body` (sources_reconcile_commands) skipped the body upload of every portable source,
* `_backfill_identity` hashed a relative path against the process cwd,
* `_upload_media_source` (artifact_log_commands) reported "media file unreadable".

Each test runs with the process cwd in an UNRELATED directory, which is the condition that exposed it; the absolute-path case
is the control that always worked.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core.sources import canonical_path as cp

BODY = b"the body of a portable source"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.txt").write_bytes(BODY)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(cp, "project_root", lambda explicit=None: root)
    monkeypatch.chdir(elsewhere)
    return root


def test_the_precondition_the_readers_failed_under(project):
    """Control: from this cwd the stored relative value does not exist, and resolve() finds it."""
    from pathlib import Path

    assert not Path("docs/a.txt").exists()
    assert cp.resolve("docs/a.txt").read_bytes() == BODY


# --- _maybe_push_small_body --------------------------------------------------------------------------------------------------


def _capture_push(monkeypatch):
    from empirica.cli.command_handlers import sources_reconcile_commands as src

    seen = {}

    def fake_push(url, key, uuid, content, mime):
        seen["content"] = content
        return {"pushed": True}

    monkeypatch.setattr(src, "_push_source_body_to_cortex", fake_push)
    return src, seen


@pytest.mark.parametrize("stored", ["docs/a.txt", "file://docs/a.txt"])
def test_the_small_body_of_a_portable_source_is_pushed_from_any_cwd(project, monkeypatch, stored):
    src, seen = _capture_push(monkeypatch)
    out = src._maybe_push_small_body("https://c", "k", "uuid-1", {"size_bytes": len(BODY), "canonical_path": stored})
    assert out is not None and seen["content"] == BODY


def test_an_absolute_canonical_path_still_works(project, monkeypatch):
    src, seen = _capture_push(monkeypatch)
    absolute = str(project / "docs" / "a.txt")
    out = src._maybe_push_small_body("https://c", "k", "uuid-1", {"size_bytes": len(BODY), "canonical_path": absolute})
    assert out is not None and seen["content"] == BODY


def test_a_missing_file_is_skipped_not_raised(project, monkeypatch):
    src, seen = _capture_push(monkeypatch)
    assert (
        src._maybe_push_small_body("https://c", "k", "u", {"size_bytes": 3, "canonical_path": "docs/gone.txt"}) is None
    )
    assert "content" not in seen


# --- _backfill_identity ------------------------------------------------------------------------------------------------------


def test_backfill_hashes_a_portable_source_from_any_cwd(project):
    from empirica.cli.command_handlers.sources_reconcile_commands import _backfill_identity

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "create table epistemic_sources (id text primary key, content_hash text, size_bytes integer, "
        "canonical_path text, mime_type text)"
    )
    conn.execute("insert into epistemic_sources (id, canonical_path) values ('s1', 'docs/a.txt')")
    db = type("DB", (), {"conn": conn})()
    rows = [
        {"id": "s1", "content_hash": None, "canonical_path": "docs/a.txt", "source_url": None, "source_metadata": None}
    ]

    assert _backfill_identity(db, rows) == 1
    stored = conn.execute("select content_hash, size_bytes, canonical_path from epistemic_sources").fetchone()
    assert stored[0].startswith("sha256:") and stored[1] == len(BODY)
    assert stored[2] == "docs/a.txt", "the locator stays repo-relative"


# --- _upload_media_source ----------------------------------------------------------------------------------------------------


def test_media_upload_finds_a_portable_source_from_any_cwd(project, monkeypatch):
    from empirica.cli.command_handlers import artifact_log_commands as alc

    sent = {}

    def fake_upsert(url, key, source_id, content, mime, title, project_id, visibility):
        sent["content"] = content
        return {"pushed": False, "stub": True}

    monkeypatch.setattr(alc, "_upsert_media_to_cortex", fake_upsert)
    monkeypatch.setattr(
        "empirica.cli.command_handlers.projects_commands._resolve_cortex_config", lambda args: ("https://c", "k")
    )
    out = alc._upload_media_source(
        object(), "docs/a.txt", "s1", {"canonical_path": "docs/a.txt", "mime_type": "text/plain"}, "t", "p", "shared"
    )
    assert "unreadable" not in str(out.get("error", "")), out
    assert sent.get("content") == BODY


def test_media_upload_still_reports_a_genuinely_missing_file(project, monkeypatch):
    from empirica.cli.command_handlers import artifact_log_commands as alc

    out = alc._upload_media_source(
        object(), "docs/gone.png", "s1", {"canonical_path": "docs/gone.png"}, "t", "p", "shared"
    )
    assert out["pushed"] is False and "unreadable" in out["error"]
