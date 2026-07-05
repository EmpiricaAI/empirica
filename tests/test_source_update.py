"""source-update — re-fetch + recompute content identity (source-lifecycle ACT half).

The verb closes David's check->act gap: sources-check detects a stale/broken
source, source-update re-fetches it (local canonical_path first, else http(s)
source_url), recomputes content_hash/size/mime, and appends a lifecycle_audit_log
event. A failed re-fetch must update NOTHING. These cover the fetch/hash helpers;
the handler's DB path is covered by a live smoke in the PR.
"""

from __future__ import annotations

import hashlib

from empirica.cli.command_handlers.sources_update_commands import (
    _content_hash,
    _fetch_content,
    _guess_mime,
)


def test_content_hash_is_sha256_prefixed_and_deterministic():
    h = _content_hash(b"hello world")
    assert h == "sha256:" + hashlib.sha256(b"hello world").hexdigest()
    assert _content_hash(b"hello world") == h  # deterministic


def test_fetch_reads_local_canonical_path(tmp_path):
    f = tmp_path / "src.txt"
    f.write_text("re-fetch me")
    content, err = _fetch_content(source_url=None, canonical_path=str(f))
    assert err is None
    assert content == b"re-fetch me"


def test_fetch_strips_file_uri_scheme(tmp_path):
    f = tmp_path / "src.md"
    f.write_bytes(b"body")
    content, err = _fetch_content(source_url=f"file://{f}", canonical_path=None)
    assert err is None and content == b"body"


def test_fetch_prefers_local_path_over_url(tmp_path):
    f = tmp_path / "local.txt"
    f.write_bytes(b"local wins")
    # source_url is http but a local canonical_path exists → no network, use file.
    content, err = _fetch_content(source_url="https://example.com/x", canonical_path=str(f))
    assert err is None and content == b"local wins"


def test_fetch_missing_file_falls_through_to_no_source(tmp_path):
    missing = tmp_path / "nope.txt"
    content, err = _fetch_content(source_url=None, canonical_path=str(missing))
    assert content is None
    assert err is not None and "no fetchable" in err


def test_fetch_no_source_at_all_errors():
    content, err = _fetch_content(source_url=None, canonical_path=None)
    assert content is None
    assert err is not None


def test_fetch_over_cap_refuses(tmp_path, monkeypatch):
    import empirica.cli.command_handlers.sources_update_commands as m

    monkeypatch.setattr(m, "_MAX_FETCH_BYTES", 8)
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 100)
    content, err = m._fetch_content(source_url=None, canonical_path=str(f))
    assert content is None
    assert err is not None and "cap" in err


def test_guess_mime_from_extension():
    assert _guess_mime("https://x/doc.html", None) == "text/html"
    assert _guess_mime(None, "/path/notes.md") in ("text/markdown", "text/x-markdown")
    assert _guess_mime(None, None) is None
