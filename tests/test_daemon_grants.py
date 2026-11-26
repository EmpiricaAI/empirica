"""Tests for the UI-prompted credential grant flow (goal 167fc8d4 / prop_b4si26t7c5).

Coverage:
  Codes:
    1. mint_device_code is 64-hex chars (32 bytes)
    2. mint_user_code is XXXX-XXXX format from the unambiguous alphabet
    3. normalize_user_code strips dashes/whitespace + upper-cases

  State lifecycle:
    4. create_grant writes a pending record
    5. list_records returns it
    6. find_by_user_code matches case-insensitively + with/without dash
    7. approve_grant transitions pending → approved with credentials snapshot
    8. deny_grant transitions pending → denied
    9. approve_grant fails for unknown user_code / non-pending state
   10. approve_grant fails past expires_at

  poll_grant terminal lifecycle:
   11. pending → pending (not consumed)
   12. approved → approved + credentials + deletes record (one-shot)
   13. denied → denied + deletes record
   14. past expires_at → expired + deletes record
   15. unknown device_code → not_found

  Reaper:
   16. reap_expired clears past-expiry records, leaves live ones

  Persistence safety:
   17. write_record is atomic (no partial file even on simulated failure)
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def isolate_home(monkeypatch, tmp_path):
    """Every test gets its own ~/.empirica/daemon_grants/ tree."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() reads HOME, so this propagates cleanly.


@pytest.fixture
def grants_module():
    """Re-import the module so any cached module-level state resets."""
    import empirica.api.daemon_grants as g

    return g


# ── Codes ──────────────────────────────────────────────────────────────


def test_mint_device_code_format(grants_module):
    code = grants_module.mint_device_code()
    assert len(code) == 64  # 32 bytes hex
    int(code, 16)  # parseable as hex (raises if not)


def test_mint_user_code_format(grants_module):
    code = grants_module.mint_user_code()
    assert len(code) == 9
    assert code[4] == "-"
    alphabet = set(grants_module.USER_CODE_ALPHABET)
    for ch in code.replace("-", ""):
        assert ch in alphabet
    # No ambiguous characters
    assert "I" not in code and "O" not in code
    assert "1" not in code and "0" not in code


def test_normalize_user_code(grants_module):
    assert grants_module.normalize_user_code("ab23-cdef") == "AB23CDEF"
    assert grants_module.normalize_user_code(" AB23 CDEF ") == "AB23CDEF"
    assert grants_module.normalize_user_code("ab23cdef") == "AB23CDEF"


# ── State lifecycle ────────────────────────────────────────────────────


def test_create_grant_writes_pending_record(grants_module):
    record = grants_module.create_grant(requesting_app="ext-test")
    assert record.status == "pending"
    assert record.requesting_app == "ext-test"
    assert record.expires_at > record.created_at
    assert grants_module._record_path(record.device_code).exists()


def test_list_records_returns_pending(grants_module):
    grants_module.create_grant(requesting_app="a")
    grants_module.create_grant(requesting_app="b")
    records = grants_module.list_records()
    assert len(records) == 2
    apps = {r.requesting_app for r in records}
    assert apps == {"a", "b"}


def test_find_by_user_code_normalises(grants_module):
    record = grants_module.create_grant(requesting_app="ext")
    # Stored format: XXXX-XXXX, alphabet upper-case. Search with lowercase
    # + missing dash → should still match.
    raw = record.user_code.replace("-", "").lower()
    found = grants_module.find_by_user_code(raw)
    assert found is not None
    assert found.device_code == record.device_code


def test_approve_grant_attaches_credentials(grants_module):
    record = grants_module.create_grant(requesting_app="ext")
    creds = {"cortex": {"url": "https://x", "api_key": "k123"}}
    approved = grants_module.approve_grant(record.user_code, creds)
    assert approved is not None
    assert approved.status == "approved"
    assert approved.granted_credentials == creds
    assert approved.approved_at is not None


def test_deny_grant_marks_denied(grants_module):
    record = grants_module.create_grant(requesting_app="ext")
    denied = grants_module.deny_grant(record.user_code)
    assert denied is not None
    assert denied.status == "denied"
    assert denied.denied_at is not None
    assert denied.granted_credentials is None


def test_approve_grant_unknown_user_code(grants_module):
    assert grants_module.approve_grant("NOPE-NOPE", {}) is None


def test_approve_grant_non_pending(grants_module):
    """Re-approving an already-approved grant must be a no-op."""
    record = grants_module.create_grant(requesting_app="ext")
    first = grants_module.approve_grant(record.user_code, {"k": "v"})
    assert first is not None
    second = grants_module.approve_grant(record.user_code, {"k": "different"})
    assert second is None  # Not re-approvable


def test_approve_grant_past_expiry(grants_module):
    record = grants_module.create_grant(requesting_app="ext")
    # Approve called with `now` past expires_at — should fail
    result = grants_module.approve_grant(
        record.user_code,
        {},
        now=record.expires_at + 1,
    )
    assert result is None


# ── poll_grant lifecycle ───────────────────────────────────────────────


def test_poll_pending(grants_module):
    record = grants_module.create_grant(requesting_app="ext")
    result = grants_module.poll_grant(record.device_code)
    assert result["status"] == "pending"
    assert result["expires_at"] == record.expires_at
    # Record still on disk
    assert grants_module._record_path(record.device_code).exists()


def test_poll_approved_delivers_once(grants_module):
    record = grants_module.create_grant(requesting_app="ext")
    creds = {"cortex": {"url": "https://x", "api_key": "k"}}
    grants_module.approve_grant(record.user_code, creds)
    first = grants_module.poll_grant(record.device_code)
    assert first["status"] == "approved"
    assert first["credentials"] == creds
    # Record consumed
    assert not grants_module._record_path(record.device_code).exists()
    second = grants_module.poll_grant(record.device_code)
    assert second["status"] == "not_found"


def test_poll_denied_delivers_once(grants_module):
    record = grants_module.create_grant(requesting_app="ext")
    grants_module.deny_grant(record.user_code)
    first = grants_module.poll_grant(record.device_code)
    assert first["status"] == "denied"
    assert not grants_module._record_path(record.device_code).exists()
    second = grants_module.poll_grant(record.device_code)
    assert second["status"] == "not_found"


def test_poll_past_expiry_returns_expired(grants_module):
    record = grants_module.create_grant(requesting_app="ext")
    result = grants_module.poll_grant(
        record.device_code,
        now=record.expires_at + 1,
    )
    assert result["status"] == "expired"
    assert not grants_module._record_path(record.device_code).exists()


def test_poll_unknown_device_code(grants_module):
    result = grants_module.poll_grant("a" * 64)
    assert result["status"] == "not_found"


# ── Reaper ─────────────────────────────────────────────────────────────


def test_reap_expired_clears_past_expiry(grants_module):
    live = grants_module.create_grant(requesting_app="live")
    expired = grants_module.create_grant(requesting_app="expired")
    # Make `expired` past its expires_at by rewriting the record with a
    # backdated expires_at
    expired.expires_at = expired.created_at - 1
    grants_module.write_record(expired)

    removed = grants_module.reap_expired()
    assert removed == 1
    remaining = grants_module.list_records()
    assert len(remaining) == 1
    assert remaining[0].device_code == live.device_code


# ── Persistence safety ────────────────────────────────────────────────


def test_write_record_atomic_no_partial(grants_module, monkeypatch):
    """Simulate a write failure mid-rename — the target file must be
    either fully-written-old-content or non-existent, never partial."""
    record = grants_module.create_grant(requesting_app="first")
    target = grants_module._record_path(record.device_code)
    original_content = target.read_text(encoding="utf-8")

    record.status = "approved"
    record.granted_credentials = {"k": "v"}

    # Patch os.replace to fail. The tempfile must NOT have overwritten target.
    import os as _os

    original_replace = _os.replace

    def boom(*_a, **_kw):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(_os, "replace", boom)
    with pytest.raises(OSError):
        grants_module.write_record(record)
    monkeypatch.setattr(_os, "replace", original_replace)

    # Target file is exactly as it was — neither partial-written nor truncated
    assert target.read_text(encoding="utf-8") == original_content

    # And no tempfile leftover
    tmp_remnants = list(target.parent.glob(".grant-*.json.tmp"))
    assert tmp_remnants == []


# ── CLI handlers (smoke) ───────────────────────────────────────────────


def test_cli_daemon_grant_approves_pending(grants_module, capsys, monkeypatch):
    """`empirica daemon-grant <user_code>` snapshots the Cortex url + approves.

    The snapshot carries the url only: the extension signs in with its own Cortex OAuth, and core hands over no
    api_key (goal 739e47b2), even when the loader still reports one."""
    from types import SimpleNamespace

    from empirica.cli.command_handlers.projects_commands import (
        handle_daemon_grant_command,
    )

    # Stub CredentialsLoader so we don't depend on a real ~/.empirica/credentials.yaml
    fake_cortex = {"url": "https://test.example", "api_key": "ctx_abc"}

    class FakeLoader:
        def get_cortex_config(self):
            return fake_cortex

    monkeypatch.setattr(
        "empirica.config.credentials_loader.CredentialsLoader",
        FakeLoader,
    )

    record = grants_module.create_grant(requesting_app="ext-test")
    args = SimpleNamespace(user_code=record.user_code, output="json")
    handle_daemon_grant_command(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["user_code"] == record.user_code

    # Verify the credentials were snapshotted into the record
    polled = grants_module.poll_grant(record.device_code)
    assert polled["status"] == "approved"
    assert polled["credentials"] == {"cortex": {"url": "https://test.example"}}
    assert "ctx_abc" not in json.dumps(polled)


def test_cli_daemon_grant_unknown_user_code(grants_module, capsys):
    from types import SimpleNamespace

    from empirica.cli.command_handlers.projects_commands import (
        handle_daemon_grant_command,
    )

    args = SimpleNamespace(user_code="ABCD-EFGH", output="json")
    handle_daemon_grant_command(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "No pending grant" in payload["error"]


def test_cli_daemon_deny_marks_denied(grants_module, capsys):
    from types import SimpleNamespace

    from empirica.cli.command_handlers.projects_commands import (
        handle_daemon_deny_command,
    )

    record = grants_module.create_grant(requesting_app="ext")
    args = SimpleNamespace(user_code=record.user_code, output="json")
    handle_daemon_deny_command(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    polled = grants_module.poll_grant(record.device_code)
    assert polled["status"] == "denied"


def test_cli_daemon_grants_list_includes_pending(grants_module, capsys):
    from types import SimpleNamespace

    from empirica.cli.command_handlers.projects_commands import (
        handle_daemon_grants_list_command,
    )

    record = grants_module.create_grant(requesting_app="ext-x")
    args = SimpleNamespace(output="json")
    handle_daemon_grants_list_command(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert len(payload["grants"]) == 1
    assert payload["grants"][0]["user_code"] == record.user_code
    assert payload["grants"][0]["status"] == "pending"


# ── FastAPI endpoint (smoke) ───────────────────────────────────────────


def test_endpoint_grant_request_and_poll(grants_module):
    """End-to-end: request → approve via state module → poll returns creds."""
    from fastapi.testclient import TestClient

    from empirica.api.serve_app import create_serve_app

    app = create_serve_app()
    client = TestClient(app)

    # Step 1: extension requests
    resp = client.post(
        "/api/v1/credentials/grant/request",
        json={"requesting_app": "extension"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["device_code"]
    assert body["user_code"]
    device_code = body["device_code"]
    user_code = body["user_code"]

    # Step 2: extension polls before approval — pending
    pending = client.post(
        "/api/v1/credentials/grant/poll",
        json={"device_code": device_code},
    )
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"

    # Step 3: user approves out-of-band (state module direct, simulating CLI)
    grants_module.approve_grant(
        user_code,
        {"cortex": {"url": "https://x", "api_key": "k"}},
    )

    # Step 4: next poll delivers creds
    approved = client.post(
        "/api/v1/credentials/grant/poll",
        json={"device_code": device_code},
    )
    body = approved.json()
    assert body["status"] == "approved"
    assert body["credentials"] == {"cortex": {"url": "https://x", "api_key": "k"}}

    # Step 5: replay — not_found
    replay = client.post(
        "/api/v1/credentials/grant/poll",
        json={"device_code": device_code},
    )
    assert replay.json()["status"] == "not_found"


def test_endpoint_poll_unknown_device_code(grants_module):
    from fastapi.testclient import TestClient

    from empirica.api.serve_app import create_serve_app

    app = create_serve_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/credentials/grant/poll",
        json={"device_code": "0" * 64},
    )
    assert resp.json()["status"] == "not_found"
