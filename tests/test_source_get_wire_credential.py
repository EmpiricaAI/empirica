"""What `source-get` puts on the wire, against a fake cortex that honours visibility by credential (goal 41db4f02).

Reported (a peer's tenant): a source created with `--visibility shared` was fetched back by its creator, then refused with `source_not_found` by the
CLI from another path while the MCP tool `cortex_source_get_raw` returned the same bytes. It was not reproduced from a second tenant. Reading the
CLI at HEAD shows `_fetch_source_raw` sends `Authorization: Bearer <resolved credential>` and nothing else: no tenant, no project, no visibility. The
existing `test_source_get.py` mocks both the credential resolver and the fetch, so none of that was exercised. These tests run the real fetch over
localhost against a server that answers by token, and pin the property that decides the report: the verb is exactly as authorised as the credential
it was handed, so a verb that refuses where another path succeeds was holding a different credential, not mishandling visibility.
"""

from __future__ import annotations

import hashlib
import json
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from empirica.cli.command_handlers.artifact_log_commands import handle_source_get_command

PAYLOAD = b"\x89PNG\r\n\x1a\n-shared-image-bytes"
TOKENS = {  # token -> (tenant, org)
    "tok_creator": ("tenant-a", "org-x"),
    "tok_org_peer": ("tenant-b", "org-x"),
    "tok_outsider": ("tenant-c", "org-y"),
}
SOURCES = {  # source id -> (owner tenant, org, visibility)
    "src-shared": ("tenant-a", "org-x", "shared"),
    "src-private": ("tenant-a", "org-x", "local"),
    "src-public": ("tenant-a", "org-x", "public"),
}


def _may_read(tenant: str, org: str, source: tuple[str, str, str]) -> bool:
    owner, owner_org, visibility = source
    return tenant == owner or visibility == "public" or (visibility == "shared" and org == owner_org)


class _Cortex(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict]] = []

    def log_message(self, *_args):  # keep test output quiet
        return

    def _send(self, status: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        type(self).seen.append(
            {"path": self.path, "authorization": auth, "headers": sorted(k.lower() for k in self.headers)}
        )
        who = TOKENS.get(auth.removeprefix("Bearer "))
        if who is None:
            return self._send(401, json.dumps({"error": "unauthorized"}).encode())
        source_id = self.path.split("/")[3] if self.path.startswith("/v1/sources/") else ""
        source = SOURCES.get(source_id)
        if source is None or not _may_read(
            *who, source
        ):  # missing and forbidden answer the same, as the real server does
            return self._send(404, json.dumps({"error": "source_not_found"}).encode())
        digest = hashlib.sha256(PAYLOAD).hexdigest()
        return self._send(200, PAYLOAD, "image/png", {"X-Body-Hash": f"sha256:{digest}"})


@pytest.fixture
def cortex():
    _Cortex.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Cortex)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(tmp_path, url: str, token: str, source_id: str, capsys) -> tuple[int, dict, bytes | None]:
    out = tmp_path / f"{source_id}-{token}.png"
    args = types.SimpleNamespace(
        id=source_id, out=str(out), output="json", verbose=False, cortex_url=url, api_key=token
    )
    code = handle_source_get_command(args)
    printed = json.loads(capsys.readouterr().out)
    return code, printed, out.read_bytes() if out.exists() else None


def test_the_creator_fetches_its_own_shared_source_and_the_hash_verifies(cortex, tmp_path, capsys):
    code, printed, written = _get(tmp_path, cortex, "tok_creator", "src-shared", capsys)
    assert (code, printed["ok"], printed["hash_verified"], written) == (0, True, True, PAYLOAD)


def test_a_peer_in_the_same_org_fetches_a_shared_source_with_the_same_verb(cortex, tmp_path, capsys):
    """The cross-tenant read the verb exists for: a different tenant's credential, the same org, a shared source."""
    code, printed, written = _get(tmp_path, cortex, "tok_org_peer", "src-shared", capsys)
    assert (code, printed["ok"], written) == (0, True, PAYLOAD)


def test_a_credential_the_source_is_not_visible_to_is_refused_as_not_found_and_nothing_is_written(
    cortex, tmp_path, capsys
):
    for token, source_id in (("tok_outsider", "src-shared"), ("tok_org_peer", "src-private")):
        code, printed, written = _get(tmp_path, cortex, token, source_id, capsys)
        assert (code, printed["ok"], printed["error"], written) == (1, False, "source_not_found", None), (
            token,
            source_id,
        )


def test_the_same_source_gives_a_different_answer_to_a_different_credential_and_to_nothing_else(
    cortex, tmp_path, capsys
):
    """The report's shape: one source, one verb, two outcomes. Only the credential differs between them."""
    answers = {token: _get(tmp_path, cortex, token, "src-shared", capsys)[0] for token in TOKENS}
    assert answers == {"tok_creator": 0, "tok_org_peer": 0, "tok_outsider": 1}


def test_the_request_carries_the_bearer_and_no_tenant_project_or_visibility(cortex, tmp_path, capsys):
    _get(tmp_path, cortex, "tok_org_peer", "src-shared", capsys)
    (request,) = _Cortex.seen
    assert request["path"] == "/v1/sources/src-shared/raw"
    assert request["authorization"] == "Bearer tok_org_peer"
    assert not any(h for h in request["headers"] if "tenant" in h or "project" in h or "visib" in h), request["headers"]


def test_a_public_source_is_open_to_any_valid_credential_and_a_bad_one_is_not_a_not_found(cortex, tmp_path, capsys):
    assert _get(tmp_path, cortex, "tok_outsider", "src-public", capsys)[0] == 0
    code, printed, written = _get(tmp_path, cortex, "tok_unknown", "src-public", capsys)
    assert (code, printed["ok"], printed["status"], written) == (1, False, 401, None), (
        "a rejected credential is 401, which the verb must not paint as a missing source"
    )
