"""`source-get` — consumer half of media-bearing sources (SER ser_a92b3a05).

Fetches a source's retained bytes from cortex (GET /v1/sources/{id}/raw),
verifies the SHA-256 content hash, and writes to --out. These tests mock the
HTTP fetch + cortex-config resolution so they run offline; they exercise the
verify-then-write contract, the error surfaces, and hash normalization.
"""

from __future__ import annotations

import base64
import hashlib
import types

import pytest

import empirica.cli.command_handlers.artifact_log_commands as alc
from empirica.cli.command_handlers.artifact_log_commands import (
    _normalize_hash,
    handle_source_get_command,
)

PAYLOAD = b"\x89PNG\r\n\x1a\n-fake-image-bytes"
GOOD_HASH = hashlib.sha256(PAYLOAD).hexdigest()


def _args(tmp_path, **over):
    d = {
        "id": "src-uuid-1",
        "out": str(tmp_path / "out.png"),
        "output": "json",
        "verbose": False,
        "cortex_url": None,
        "api_key": None,
    }
    d.update(over)
    return types.SimpleNamespace(**d)


@pytest.fixture(autouse=True)
def _cortex_configured(monkeypatch):
    # Pretend cortex creds resolve, so we exercise the fetch path.
    monkeypatch.setattr(
        alc,
        "_resolve_cortex_config",
        lambda args: ("https://cortex.example", "ctx_key"),
        raising=False,
    )
    # projects_commands._resolve_cortex_config is imported inside the handler;
    # patch there too.
    import empirica.cli.command_handlers.projects_commands as pc

    monkeypatch.setattr(pc, "_resolve_cortex_config", lambda args: ("https://cortex.example", "ctx_key"))


def _mock_fetch(monkeypatch, status, body):
    monkeypatch.setattr(alc, "_fetch_source_raw", lambda url, key, sid, timeout=30.0: (status, body))


def test_happy_path_writes_and_verifies(tmp_path, monkeypatch, capsys):
    _mock_fetch(
        monkeypatch,
        200,
        {
            "ok": True,
            "content": base64.b64encode(PAYLOAD).decode(),
            "content_hash": f"sha256:{GOOD_HASH}",
            "mime": "image/png",
            "size_bytes": len(PAYLOAD),
        },
    )
    rc = handle_source_get_command(_args(tmp_path))
    assert rc == 0
    assert (tmp_path / "out.png").read_bytes() == PAYLOAD


def test_bare_hash_also_verifies(tmp_path, monkeypatch):
    # cortex may return a bare hex hash (no algo prefix) — must still verify.
    _mock_fetch(
        monkeypatch,
        200,
        {"content": base64.b64encode(PAYLOAD).decode(), "content_hash": GOOD_HASH, "mime": "image/png"},
    )
    rc = handle_source_get_command(_args(tmp_path))
    assert rc == 0
    assert (tmp_path / "out.png").read_bytes() == PAYLOAD


def test_hash_mismatch_does_not_write(tmp_path, monkeypatch):
    _mock_fetch(
        monkeypatch,
        200,
        {"content": base64.b64encode(PAYLOAD).decode(), "content_hash": "sha256:" + "0" * 64, "mime": "image/png"},
    )
    rc = handle_source_get_command(_args(tmp_path))
    assert rc == 1
    assert not (tmp_path / "out.png").exists()  # never write unverified bytes


def test_not_retained_surfaces(tmp_path, monkeypatch):
    _mock_fetch(monkeypatch, 404, {"error": "not_retained"})
    rc = handle_source_get_command(_args(tmp_path))
    assert rc == 1
    assert not (tmp_path / "out.png").exists()


def test_source_not_found_surfaces(tmp_path, monkeypatch):
    _mock_fetch(monkeypatch, 404, {"error": "source_not_found"})
    rc = handle_source_get_command(_args(tmp_path))
    assert rc == 1


def test_cortex_not_configured(tmp_path, monkeypatch):
    import empirica.cli.command_handlers.projects_commands as pc

    monkeypatch.setattr(pc, "_resolve_cortex_config", lambda args: (None, None))
    rc = handle_source_get_command(_args(tmp_path))
    assert rc == 1


def test_missing_content_hash_still_writes(tmp_path, monkeypatch):
    # No content_hash to verify against → write, but flag hash_verified False.
    _mock_fetch(monkeypatch, 200, {"content": base64.b64encode(PAYLOAD).decode(), "mime": "image/png"})
    rc = handle_source_get_command(_args(tmp_path))
    assert rc == 0
    assert (tmp_path / "out.png").read_bytes() == PAYLOAD


def test_normalize_hash():
    assert _normalize_hash("sha256:ABC") == "abc"
    assert _normalize_hash("ABC") == "abc"
    assert _normalize_hash(None) is None
    assert _normalize_hash("  sha256:DeadBeef  ") == "deadbeef"
