"""CLI-owned OAuth against cortex — authorization_code + PKCE, RFC 8252 native flow.

Design constraints, all measured (2026-08-12, OAuth thread):

- **Own DCR client, sole refresher.** Cortex rotates refresh tokens on every use
  and reuse-detection revokes the whole family; a *different* client attempting
  refresh REVOKES the presented token. The CLI therefore never touches the
  extension's tokens and nothing else may refresh the CLI's.
- **Port block, not ephemeral port.** Cortex's redirect_uri check is exact-string
  membership with no RFC 8252 §7.3 port flexibility (cortex prop_oji3v2gh), so an
  ephemeral-port loopback would be rejected at authorize time. We register a small
  fixed block of loopback URIs at DCR time and bind the first free one at login.
  If cortex later implements §7.3, this keeps working unchanged.
- **api_key stays valid throughout.** `cortex_bearer` prefers a live token and
  falls back to the api_key; retirement is a separate per-seat act gated on the
  survival-matrix lesson, never a side effect of this module.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import socket
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger(__name__)

DISCOVERY_PATH = "/.well-known/oauth-authorization-server"

# Registered as redirect URIs at DCR time; login binds the first free one.
# A fixed block (not port 0) because cortex matches redirect_uri exact-string.
LOOPBACK_PORTS = (43217, 43218, 43219, 43220, 43221, 43222, 43223, 43224)
CALLBACK_PATH = "/callback"

CLIENT_NAME = "empirica-cli"


def _http_json(
    url: str,
    *,
    data: dict | None = None,
    headers: dict | None = None,
    timeout: float = 15.0,
    form: bool = False,
) -> dict:
    """POST (when data) or GET a JSON endpoint. Raises on HTTP/parse errors."""
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            body = json.dumps(data).encode()
            hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_discovery(base_url: str, *, http=_http_json) -> dict:
    """Fetch the AS metadata (RFC 8414). `base_url` is the cortex root URL."""
    return http(base_url.rstrip("/") + DISCOVERY_PATH)


def build_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge) per RFC 7636."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _redirect_uris() -> list[str]:
    return [f"http://127.0.0.1:{p}{CALLBACK_PATH}" for p in LOOPBACK_PORTS]


def register_client(registration_endpoint: str, *, http=_http_json) -> dict:
    """DCR-register the CLI as a PUBLIC client (RFC 7591).

    `token_endpoint_auth_method: none` — a CLI cannot keep a secret; PKCE is
    the binding. The full loopback port block is registered because cortex
    matches redirect_uri exact-string (no §7.3 port flexibility).
    """
    payload = {
        "client_name": CLIENT_NAME,
        "redirect_uris": _redirect_uris(),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    out = http(registration_endpoint, data=payload)
    if not out.get("client_id"):
        raise RuntimeError("DCR registration returned no client_id")
    return out


def _bind_first_free_port() -> tuple[socket.socket, int]:
    """Bind the first free port of the registered block. The bound socket is
    handed to the HTTP server so there is no close-then-rebind race."""
    for port in LOOPBACK_PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return sock, port
        except OSError:
            sock.close()
    raise RuntimeError(
        f"no free loopback port in the registered block {LOOPBACK_PORTS} — close a stuck login and retry"
    )


class _CallbackHandler(BaseHTTPRequestHandler):
    """One-shot callback catcher. Result lands on the server object."""

    def do_GET(self):  # BaseHTTPRequestHandler contract name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.server.callback_params = params  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        ok = "code" in params
        self.wfile.write(
            b"<html><body><h2>empirica auth: %s</h2>You can close this tab.</body></html>"
            % (b"login complete" if ok else b"login FAILED - return to the terminal")
        )

    def log_message(self, *_args):  # silence default stderr access log
        return


def _prepare_callback_server(sock: socket.socket, port: int) -> HTTPServer:
    """Wrap the bound socket in a LISTENING server. Must be called BEFORE the
    browser is opened: a bound-but-not-listening socket refuses the redirect
    instantly, and the failure is a silent daemon-thread death followed by a
    timeout (a real race — it passed solo and failed under suite load)."""
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler, bind_and_activate=False)
    server.socket = sock
    server.server_activate()
    server.callback_params = None  # type: ignore[attr-defined]
    return server


def _wait_for_callback(server: HTTPServer, expected_state: str, timeout_s: float) -> str:
    """Serve until one callback arrives; return the code. Raises on state
    mismatch (CSRF guard), provider error, or timeout."""
    server.timeout = timeout_s
    try:
        server.handle_request()  # blocks for exactly one request (or timeout)
        params = server.callback_params  # type: ignore[attr-defined]
    finally:
        server.server_close()
    if params is None:
        raise TimeoutError(f"no OAuth callback within {timeout_s:.0f}s")
    if params.get("error"):
        raise RuntimeError(f"authorization failed: {params.get('error')}: {params.get('error_description', '')}")
    if params.get("state") != expected_state:
        raise RuntimeError("state mismatch on OAuth callback — possible CSRF, aborting login")
    code = params.get("code")
    if not code:
        raise RuntimeError("OAuth callback carried no code")
    return code


def exchange_code(
    token_endpoint: str,
    *,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    http=_http_json,
) -> dict:
    """authorization_code exchange for a public client (PKCE, no secret)."""
    return http(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        form=True,
    )


def refresh_access_token(refresh_token: str, token_endpoint: str, client_id: str, *, http=_http_json) -> dict:
    """refresh_token grant for a public client. Cortex ROTATES on every use —
    the caller must persist the returned refresh_token or the family dies on
    the next attempt (reuse detection)."""
    return http(
        token_endpoint,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        form=True,
    )


def _expires_at(token_response: dict, *, now: float) -> float | None:
    if token_response.get("expires_at"):
        try:
            return float(token_response["expires_at"])
        except (TypeError, ValueError):
            return None
    if token_response.get("expires_in"):
        try:
            return now + float(token_response["expires_in"])
        except (TypeError, ValueError):
            return None
    return None


def default_refresh(loader, *, http=_http_json) -> Callable[[str, str | None], dict]:
    """The refresh callable `cortex_access_token` wants, bound to this loader.

    Persists the ROTATED token set before returning (cortex rotates per use;
    an unpersisted rotation kills the family on the next refresh). client_id
    comes from the stored oauth block — the CLI's own client, sole refresher.
    """

    def _refresh(refresh_token: str, token_endpoint: str | None) -> dict:
        import time as _time

        oauth = loader.get_cortex_oauth()
        client_id = oauth.get("client_id")
        endpoint = token_endpoint or oauth.get("token_endpoint")
        if not client_id or not endpoint:
            raise RuntimeError("stored oauth block lacks client_id/token_endpoint — run `empirica auth login`")
        out = refresh_access_token(refresh_token, endpoint, client_id, http=http)
        now = _time.time()
        loader.save_cortex_oauth(
            access_token=out.get("access_token"),
            refresh_token=out.get("refresh_token"),  # rotation — persist or die
            expires_at=_expires_at(out, now=now),
        )
        return {
            "access_token": out.get("access_token"),
            "expires_at": _expires_at(out, now=now),
            "refresh_token": out.get("refresh_token"),
        }

    return _refresh


def cortex_bearer(loader=None, *, http=_http_json) -> dict[str, Any]:
    """{url, bearer, source} — OAuth-first, api_key fallback.

    source: 'oauth' | 'api_key' | 'none'. Never returns a stale token
    (cortex_access_token's contract); a failed refresh falls back to the
    api_key rather than sending a dead credential.
    """
    if loader is None:
        from empirica.config.credentials_loader import get_credentials_loader

        loader = get_credentials_loader()
    cfg = loader.get_cortex_config()
    token = None
    try:
        # Refresh custody: only the OWNER refreshes. A daemon-owned family is
        # kept fresh by the serve process's tick — the CLI reads access_token
        # ONLY (no refresh callable), or it would be a second refresher and
        # cortex would revoke the family. 'cli' / absent → this process refreshes
        # (headless-fallback mode).
        owner = (loader.get_cortex_oauth().get("refresh_owner") or "cli").lower()
        refresh_cb = None if owner == "daemon" else default_refresh(loader, http=http)
        token = loader.cortex_access_token(refresh=refresh_cb)
    except Exception as e:  # refresh machinery must never take down the api_key path
        logger.warning(f"oauth token resolution failed, falling back to api_key: {e}")
    if token:
        return {"url": cfg.get("url"), "bearer": token, "source": "oauth"}
    if cfg.get("api_key"):
        return {"url": cfg.get("url"), "bearer": cfg.get("api_key"), "source": "api_key"}
    return {"url": cfg.get("url"), "bearer": None, "source": "none"}


def login(
    *,
    loader=None,
    open_browser: Callable[[str], Any] | None = None,
    timeout_s: float = 300.0,
    http=_http_json,
) -> dict[str, Any]:
    """Full authorization_code + PKCE login. Returns a status dict.

    Steps: discovery → (reuse or DCR-register the CLI's client) → bind a
    registered loopback port → browser to authorize → exchange → persist
    token set + client_id. The api_key is never touched.
    """
    import time as _time

    if loader is None:
        from empirica.config.credentials_loader import get_credentials_loader

        loader = get_credentials_loader()

    cfg = loader.get_cortex_config()
    base_url = cfg.get("url")
    if not base_url:
        raise RuntimeError("no cortex url configured — set cortex.url in ~/.empirica/credentials.yaml first")

    disco = fetch_discovery(base_url, http=http)
    for required in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if not disco.get(required):
            raise RuntimeError(f"discovery document lacks {required}")

    # Reuse the stored client when present — re-registering on every login
    # would grow cortex's client table by one row per login forever.
    oauth = loader.get_cortex_oauth()
    client_id = oauth.get("client_id")
    if not client_id:
        client_id = register_client(disco["registration_endpoint"], http=http)["client_id"]

    sock, port = _bind_first_free_port()
    server = _prepare_callback_server(sock, port)  # listening BEFORE the browser opens
    redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
    verifier, challenge = build_pkce()
    state = secrets.token_urlsafe(24)

    authorize_url = (
        disco["authorization_endpoint"]
        + "?"
        + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )

    if open_browser is None:
        import webbrowser

        open_browser = webbrowser.open
    opener = threading.Thread(target=open_browser, args=(authorize_url,), daemon=True)
    opener.start()

    code = _wait_for_callback(server, state, timeout_s)
    tokens = exchange_code(
        disco["token_endpoint"],
        client_id=client_id,
        code=code,
        verifier=verifier,
        redirect_uri=redirect_uri,
        http=http,
    )
    if not tokens.get("access_token"):
        raise RuntimeError("token exchange returned no access_token")

    now = _time.time()
    loader.save_cortex_oauth(
        access_token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        expires_at=_expires_at(tokens, now=now),
        token_endpoint=disco["token_endpoint"],
        client_id=client_id,
        # auth login is the headless-fallback path: this shell process owns
        # its own client's family and refreshes it. The daemon-brokered path
        # writes refresh_owner='daemon' via the credentials route instead.
        refresh_owner="cli",
    )
    return {
        "ok": True,
        "client_id": client_id,
        "expires_at": _expires_at(tokens, now=now),
        "has_refresh_token": bool(tokens.get("refresh_token")),
        "authorize_url": authorize_url,
    }
