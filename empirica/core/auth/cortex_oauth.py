"""CLI-owned OAuth against the proprietary Cortex service — authorization_code
+ PKCE, RFC 8252 native flow.

Cortex (getempirica.com) is Empirica's PROPRIETARY serving layer, not part of
this open-source core; connecting requires a Cortex account. This module only
authenticates that account — Empirica core is fully functional without it.

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
- **No api_key.** Core carries only OAuth and time-based tokens (goal 739e47b2,
  David 2026-11-26); `cortex_bearer` returns a live token or none, with the reason.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
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
    # The listener reaches this on token refresh (cortex_bearer -> custody_refresh): one SSL context per process, as for
    # every other listener HTTPS call, not a new one per request.
    from empirica.core.loop_scheduler.http_shared import open_url

    with open_url(req, timeout) as resp:
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


#: Cortex's page that shows ``<code>#<state>`` for `auth login --remote`, under the deployment's issuer.
CODE_PAGE_PATH = "/v1/oauth/code"


def parse_pasted_code(pasted: str, expected_state: str) -> str:
    """The authorization code from what the person pasted back: the page's ``<code>#<state>`` line, or, if the page did not
    render, the address-bar URL (or just its query). Raises RuntimeError with the reason on an error, a missing state or a
    state that does not match the one this login sent."""
    text = (pasted or "").strip()
    if not text:
        raise RuntimeError("empty paste: copy the line the page shows (code#state)")
    if "code=" in text or "error=" in text:
        query = urllib.parse.urlparse(text).query if "?" in text else text.split("#", 1)[0]
        params = dict(urllib.parse.parse_qsl(query))
        if params.get("error"):
            raise RuntimeError(f"authorization failed: {params['error']}: {params.get('error_description', '')}")
        code, state = params.get("code", ""), params.get("state", "")
    elif "#" in text:
        code, _, state = text.partition("#")
    else:
        raise RuntimeError("no state in the paste: copy the whole line the page shows, code#state")
    if state != expected_state:
        raise RuntimeError("the pasted state does not match this login (a code from another sign-in?)")
    if not code:
        raise RuntimeError("no code in the paste")
    return code


def _read_pasted_code(authorize_url: str) -> str:
    """Default reader for --remote: the URL is already on stderr; ask for the code on stderr, read it from stdin."""
    print("Sign in there, then paste the code the page shows here: ", end="", file=sys.stderr, flush=True)
    try:
        return input()
    except EOFError:
        raise RuntimeError(
            "no input to paste the code into (stdin is closed): run auth login in an interactive terminal, or with --local"
        ) from None


def register_client(registration_endpoint: str, *, http=_http_json, extra_redirects: list[str] | None = None) -> dict:
    """DCR-register the CLI as a PUBLIC client (RFC 7591).

    `token_endpoint_auth_method: none` — a CLI cannot keep a secret; PKCE is
    the binding. The full loopback port block is registered because cortex
    matches redirect_uri exact-string (no §7.3 port flexibility).
    """
    payload = {
        "client_name": CLIENT_NAME,
        "redirect_uris": _redirect_uris() + list(extra_redirects or []),
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
    nonce: str | None = None,
) -> dict:
    """authorization_code exchange for a public client (PKCE, no secret). With ``nonce``, Cortex also returns a signed
    ``seat_assertion`` echoing it (seat_assertion.verify)."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if nonce:
        data["nonce"] = nonce
    return http(token_endpoint, data=data, form=True)


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
            v = float(token_response["expires_at"])
            # Absolute-expiry passthrough. Cortex only emits `expires_in`
            # (seconds) today, so this branch is dead for the cortex flow — but a
            # provider that returned an absolute expiry in JS milliseconds would
            # poison the clock exactly like the bridge did. Normalize ms→s here
            # too (extension prop_d44ipjuc) so no producer path can enter ms.
            return v / 1000.0 if v > 1e11 else v
        except (TypeError, ValueError):
            return None
    if token_response.get("expires_in"):
        try:
            return now + float(token_response["expires_in"])
        except (TypeError, ValueError):
            return None
    return None


def default_refresh(loader, *, http=_http_json, org_id: str | None = None) -> Callable[[str, str | None], dict]:
    """The refresh callable `cortex_access_token` wants, bound to this loader.

    Persists the ROTATED token set before returning (cortex rotates per use;
    an unpersisted rotation kills the family on the next refresh). client_id
    comes from the stored oauth block — the CLI's own client, sole refresher.
    """

    def _refresh(refresh_token: str, token_endpoint: str | None) -> dict:
        import time as _time

        oauth = loader.get_cortex_oauth(org_id) if org_id else loader.get_cortex_oauth()
        client_id = oauth.get("client_id")
        endpoint = token_endpoint or oauth.get("token_endpoint")
        if not client_id or not endpoint:
            raise RuntimeError("stored oauth block lacks client_id/token_endpoint — run `empirica auth login`")
        out = refresh_access_token(refresh_token, endpoint, client_id, http=http)
        now = _time.time()
        slot = {"slot": loader.cortex_oauth_slot(org_id)} if org_id else {}  # the same org's block, never another's
        loader.save_cortex_oauth(
            **slot,
            rotated=True,  # core's own rotation keeps the team label bound
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


def custody_refresh(
    loader, *, http=_http_json, org_id: str | None = None
) -> tuple[Callable[[str, str | None], dict] | None, str]:
    """``(refresh callback or None, owner)`` for the stored OAuth family.

    The CLI refreshes ONLY a family it owns (`cli` / absent = the headless-fallback client it
    minted via `auth login`). A `daemon`-owned family is refreshed by the serve tick, an
    `extension`-owned one (daemonless Desktop seat) by the extension. For those the callback is
    None: the caller reads the access_token only, because a second refresher on one family makes
    cortex revoke it (rotation + reuse detection). Every path that would refresh asks here, so
    `cortex_bearer` and `empirica auth token` cannot disagree about who may.
    """
    raw = (loader.get_cortex_oauth(org_id) if org_id else loader.get_cortex_oauth()).get("refresh_owner")
    owner = (str(raw).strip().lower() if raw else "") or "cli"  # a non-string owner is a malformed one, not a crash
    extra = {"org_id": org_id} if org_id else {}
    return (default_refresh(loader, http=http, **extra) if owner == "cli" else None), owner


def cortex_bearer(loader=None, *, http=_http_json, org_id: str | None = None) -> dict[str, Any]:
    """{url, bearer, source} — the OAuth token, or none with a reason. Dead tokens are skipped.

    With ``org_id`` (a practice's project.yaml org_id), the sign-in for that org: a machine can hold several teams'
    (goal da0396f8). Without it, the default sign-in, as before.

    source: 'oauth' | 'none'. Never returns a stale token (cortex_access_token's contract). The api_key fallback that
    stood here was removed when core retired the key (goal 739e47b2, David 2026-11-26): it once minted 401 storms by
    handing back a revoked key forever, and a seat now signs in with `empirica auth login`.
    """
    if loader is None:
        from empirica.config.credentials_loader import get_credentials_loader

        loader = get_credentials_loader()
    cfg = loader.get_cortex_config()
    if org_id is None:
        # With several teams signed in here, the sign-in of the practice this runs in when it holds one; otherwise the
        # default, as before. A machine with one sign-in (every seat before teams) takes exactly the old path.
        org_id = preferred_practice_org(loader)
    token = None
    owner, failure = "", None
    try:
        # Refresh custody (see custody_refresh): only a family the CLI owns is refreshed here.
        org = {"org_id": org_id} if org_id else {}
        refresh_cb, owner = custody_refresh(loader, http=http, **org)
        token = loader.cortex_access_token(refresh=refresh_cb, **org)
        refresh_error = getattr(loader, "last_refresh_error", None)
        if not token and isinstance(refresh_error, str) and refresh_error:
            failure = f"oauth token expired and its refresh failed ({refresh_error})"
    except Exception as e:  # refresh machinery must never raise out of the bearer
        failure = f"the sign-in could not be read or renewed ({type(e).__name__}: {e})"
        logger.warning(f"oauth token resolution failed: {e}")
    from empirica.core.auth.credential_health import dead_reason

    token_reason = dead_reason(token) if token else None
    if token and not token_reason:
        return {"url": cfg.get("url"), "bearer": token, "source": "oauth"}
    # A token recorded as terminally dead is not offered again (it was, until it expired, re-presented every beat).

    return {
        "url": cfg.get("url"),
        "bearer": None,
        "source": "none",
        "reason": (
            f"oauth token unusable ({token_reason}) — re-authenticate with `empirica auth login`"
            if token_reason
            else _no_token_reason(loader, owner, failure, org_id)
        ),
    }


def preferred_practice_org(loader) -> str | None:
    """The org of the practice in play when this machine holds several sign-ins and one for that org; else None. The
    practice is the one a listener declared (`mesh_endpoint.acting_for`), else the working directory's."""
    try:
        if len(loader.cortex_oauth_sets()) < 2:
            return None
        from empirica.core.mesh_endpoint import acting_for_instance, practice_org_id

        org = practice_org_id(acting_for_instance())
        return org if org and loader.get_cortex_oauth(org) else None
    except Exception:
        return None


def _no_token_reason(loader, owner: str, failure: str | None, org_id: str | None = None) -> str:
    """Why no OAuth token came back. A stored set that could not give one must not read like a seat with nothing stored:
    that sent an operator looking for a missing credential while the real fault was a refresh (mesh-support, 2026-11-15)."""
    if failure:  # a refresh that raised, or a credentials file that could not be read: say so before anything else
        return f"{failure}; retry, and run `empirica auth login` if it persists"
    try:
        oauth = (loader.get_cortex_oauth(org_id) if org_id else loader.get_cortex_oauth()) or {}
        any_stored = bool(org_id) and any(block for _, block in loader.cortex_oauth_sets())
    except Exception as e:
        return f"the stored sign-in could not be read ({type(e).__name__}: {e})"
    if not (oauth.get("access_token") or oauth.get("refresh_token")):
        if any_stored:  # signed in to other teams, not this practice's
            return f"no sign-in for org {org_id} on this machine — run `empirica auth login --org {org_id}`"
        return "no Cortex sign-in on this machine — run `empirica auth login`"
    if not oauth.get("refresh_token"):
        return "oauth token expired and no refresh token is stored — re-authenticate with `empirica auth login`"
    if owner and owner != "cli":
        return (
            f"oauth token expired; its refresh belongs to the {owner}, which has not renewed it "
            "(is `empirica serve` running?)"
        )
    return "oauth token expired and the refresh returned no token — re-authenticate with `empirica auth login`"


def cortex_configured(loader=None) -> bool:
    """Is this seat cortex-capable? — PRESENCE, deliberately not resolution.

    Answers a different question from `cortex_bearer` and must not be confused
    with it. This one asks *could* this seat talk to cortex; that one asks *give
    me a credential to send*. Two consequences follow, and both matter:

    **It makes no network call.** `cortex_bearer` may refresh a token, and the
    callers of this predicate are on paths that run constantly —
    `setup-claude-code` fires on every session auto-heal. A refresh there would
    put an HTTP round-trip in front of every session start.

    **It does not care whether the credential still works.** A revoked key or an
    expired token still means the seat was SET UP for cortex, and the honest
    response to a dead credential is an auth error the operator can act on — not
    silently rendering a product that never mentions the mesh.

    Why it exists at all: `get_cortex_config()` returns `{url, api_key}` and has
    no OAuth path, so every gate written as `url and api_key` reads an
    OAuth-authenticated seat as unconfigured. That shape was fixed in 1.13.19 at
    the three sites that threw stack traces, and survived at the ones that fail
    SILENTLY — reported by empirica-mesh-support as a seat installing cleanly and
    receiving a system prompt with every mesh block stripped. One home for the
    question means the next fix is a grep for this function rather than for the
    traces that happened to surface it.
    """
    if loader is None:
        from empirica.config.credentials_loader import get_credentials_loader

        loader = get_credentials_loader()
    try:
        cfg = loader.get_cortex_config()
        if not cfg.get("url"):
            return False
        if cfg.get("api_key"):
            return True
        # `oauth` is file-only by design; presence of the stored block is the
        # signal, not its liveness.
        return bool(loader.get_cortex_oauth())
    except Exception:  # a credentials file we cannot read is not a configured seat
        return False


#: The hosted Cortex. `auth login` uses it when neither --url nor credentials.yaml names one (doctor and setup-claude-code
#: already default to it), so a machine that was never configured can still sign in (Philipp's Air, 2026-11-14).
DEFAULT_CORTEX_URL = "https://cortex.getempirica.com"


def resolve_cortex_url(loader, url: str | None = None) -> tuple[str, str]:
    """(url, where it came from): the --url flag, then cortex.url in credentials.yaml, then the hosted default."""
    if url:
        return url.rstrip("/"), "flag"
    configured = (loader.get_cortex_config() or {}).get("url")
    if configured:
        return configured.rstrip("/"), "config"
    return DEFAULT_CORTEX_URL, "default"


def invite_token(value: str) -> str:
    """The token of a Cortex invite, given the token itself or the whole link an admin was sent
    (``https://getempirica.com/install?invite=<token>``). Raises ValueError when there is none, before any browser opens."""
    text = (value or "").strip()
    if "://" in text or "?" in text or "invite=" in text:
        if "://" in text or "?" in text:
            query = urllib.parse.urlparse(text if "://" in text else f"https://x/{text}").query
        else:  # just the query part, e.g. `invite=<token>` copied from the address bar
            query = text.split("#", 1)[0]
        text = (urllib.parse.parse_qs(query).get("invite") or [""])[0].strip()
        if not text:
            raise ValueError("that link carries no invite= token")
    if not text:
        raise ValueError("empty invite")
    return text


def login(
    *,
    loader=None,
    open_browser: Callable[[str], Any] | None = None,
    timeout_s: float = 300.0,
    http=_http_json,
    url: str | None = None,
    invite: str | None = None,
    org: str | None = None,
    remote: bool = False,
    read_code: Callable[[str], str] | None = None,
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

    base_url, url_source = resolve_cortex_url(loader, url)
    # An invite rides the authorize request; Cortex redeems it in its callback once Google has verified the email, or refuses
    # the sign-in (access_denied) rather than signing in to another org.
    invite = invite_token(invite) if invite is not None else None
    # The org to sign in as (id, slug or name): Cortex skips its picker and refuses with access_denied if the person is not a
    # member there. Cortex reads at most 100 characters.
    if org is not None:
        org = org.strip()
        if not org:
            raise ValueError("empty --org")

    disco = fetch_discovery(base_url, http=http)
    for required in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if not disco.get(required):
            raise RuntimeError(f"discovery document lacks {required}")

    # Reuse the stored client ONLY when the stored family is OUR OWN
    # (cli/daemon-owned) — re-registering on every login would grow cortex's
    # client table by one row per login. But reusing it UNCONDITIONALLY is the
    # client_id-reuse trap (David, prop_4ikynx2k): run over a FOREIGN family
    # (e.g. the extension's), login would mint the CLI's tokens under the other
    # party's client_id → two clients presenting one rotating family → cortex
    # revokes it. And because incoming would then always equal stored, the
    # save_cortex_oauth replace-guard could never fire. So over a foreign or
    # unknown-owner block we register our OWN client and let the guard REPLACE
    # theirs.
    # With --org, the client of that team's own stored set (another team's would put two families under one client).
    oauth = loader.get_cortex_oauth(org) if org else loader.get_cortex_oauth()
    owner = (oauth.get("refresh_owner") or "").lower()
    same_cortex = oauth.get("token_endpoint") in (None, "", disco["token_endpoint"])
    # A client registered on ANOTHER Cortex is unknown here: reusing it left the browser on an error and the login waiting
    # out its timeout (1.17.0 sweep).
    client_id = oauth.get("client_id") if owner in ("cli", "daemon") and same_cortex else None
    code_page = f"{(disco.get('issuer') or base_url).rstrip('/')}{CODE_PAGE_PATH}"
    if remote and oauth.get("client_code_page") != code_page:
        # The browser is on another machine, so the redirect is Cortex's page, which shows the code to paste back. A
        # client registered by a local login lists only the loopback ports (Cortex matches redirect_uri exactly), so this
        # registers one with both, and records that it has the page: later logins, remote or local, reuse it.
        client_id = None
    client_code_page = code_page if remote else None
    if not client_id:
        extra = {"extra_redirects": [code_page]} if remote else {}  # a local login registers exactly as before
        client_id = register_client(disco["registration_endpoint"], http=http, **extra)["client_id"]

    server = None
    if remote:
        redirect_uri = code_page
    else:
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
                **({"invite": invite} if invite else {}),
                **({"org": org} if org else {}),
            }
        )
    )

    # Print the URL BEFORE any browser attempt, to STDERR. A browserless box
    # (WSL2 with Windows-interop off, headless server, container) can neither
    # open a browser NOR reliably signal that it failed — webbrowser.open returns
    # True for a BROWSER='echo %s' shim, and webbrowser.get() RAISES where no
    # browser exists at all. So NEVER gate on the opener: always surface the URL
    # so the user can paste it into any reachable browser, and the loopback
    # callback still returns. stderr (not stdout) keeps --output json parseable.
    if remote:
        print(f"\nOpen this URL in a browser on any machine:\n\n    {authorize_url}\n", file=sys.stderr, flush=True)
        code = parse_pasted_code((read_code or _read_pasted_code)(authorize_url), state)
    else:
        print(
            f"\nAuthorize this seat in your browser. If it does not open automatically, "
            f"open this URL:\n\n    {authorize_url}\n",
            file=sys.stderr,
            flush=True,
        )
        code = _open_and_wait(authorize_url, open_browser, server, state, timeout_s)

    from empirica.core.auth import seat_assertion

    nonce = seat_assertion.new_nonce()
    tokens = exchange_code(
        disco["token_endpoint"],
        client_id=client_id,
        code=code,
        verifier=verifier,
        redirect_uri=redirect_uri,
        http=http,
        nonce=nonce,
    )
    if not tokens.get("access_token"):
        raise RuntimeError("token exchange returned no access_token")
    seat = _check_seat(tokens.get("seat_assertion"), nonce)

    now = _time.time()
    # Refresh custody depends on whether a long-lived daemon is present. On a
    # box running `empirica serve`, the daemon's tick AND the listener AND the
    # CLI all reach cortex_bearer; if this family were 'cli'-owned, more than one
    # of them would refresh it and cortex would revoke it (two presenters of one
    # rotating credential). So a daemon-present box gets 'daemon' — the serve
    # tick is the sole refresher, everything else is read-only, and the token
    # stays warm without the browser (the retirement goal). A headless box has
    # no competing refresher, so 'cli' is correct there.
    owner = "daemon" if _serve_daemon_running() else "cli"
    # Which team the new set acts in, and so where it is stored: a second team's sign-in goes beside the first, never
    # over it (a token is bound to one user in one org; goal da0396f8).
    identity = _whoami(base_url, tokens["access_token"], http=http)
    # /v1/users/me can fail (it returns {} then); a verified seat proof is signed by Empirica's key and names the org.
    new_org = identity.get("org_id") or (seat.get("org_id") if seat["verified"] else None) or None
    if seat["present"] and seat.get("org_id") and new_org and seat["org_id"] != new_org:
        # The proof names another org than the account acts in: neither can be trusted to file this sign-in (3796fe5f).
        raise RuntimeError(
            f"seat assertion is for org {seat['org_id']}, but the account acts in {new_org}; nothing stored"
        )
    previous_org = loader.cortex_oauth_org(None)
    if not _same_cortex(loader, base_url) and len(loader.cortex_oauth_sets()) > 1:
        loader.clear_other_teams()  # they belong to the Cortex being left; never sent to the new one
    slot = _slot_for_new_sign_in(loader, new_org, base_url, http=http)
    note = None
    if slot is None and previous_org and new_org and previous_org != new_org and not loader.multi_team_allowed():
        note = (
            f"replaced the sign-in for {previous_org}: several teams on one machine need Empirica Cortex "
            "(getempirica.com)"
        )
    loader.save_cortex_oauth(
        slot=slot,
        access_token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        expires_at=_expires_at(tokens, now=now),
        token_endpoint=disco["token_endpoint"],
        client_id=client_id,
        client_code_page=client_code_page,
        refresh_owner=owner,
        # An unknown team clears the labels: a label left from the replaced set would name the wrong team.
        org_id=new_org or "",
        # A name only /v1/users/me gives: when the org came from the seat proof instead, clear it rather than keep
        # the previous team's (1.17.11 pass 2).
        org_name=(identity.get("org_name") or None) if identity.get("org_id") else "",
    )
    # The listeners' ntfy credentials, for the primary sign-in only: one ntfy block per machine, and it belongs to it.
    ntfy = (
        _fetch_ntfy_credentials(loader, base_url, tokens["access_token"], http=http)
        if slot is None
        else {"written": False, "reason": "another team's sign-in: the ntfy credentials stay the primary seat's"}
    )
    return {
        "ok": True,
        "slot": slot,
        "note": note,
        "ntfy": ntfy,
        "org_id": new_org,
        "org_name": identity.get("org_name"),
        "client_id": client_id,
        "expires_at": _expires_at(tokens, now=now),
        "has_refresh_token": bool(tokens.get("refresh_token")),
        "refresh_owner": owner,
        "authorize_url": authorize_url,
        "cortex_url": base_url,
        "url_source": url_source,
        "invite": bool(invite),
        "org_requested": org or None,
        "remote": remote,
        "seat": {k: seat.get(k) for k in ("present", "verified", "kid", "reason")},
    }


def _check_seat(token: str | None, nonce: str) -> dict:
    """Verify Cortex's seat assertion (goal 3796fe5f). Absent: {present: False} and today's host pin decides, as Cortex
    sends none when its signer is not configured. Present and failing: the login is refused before anything is stored,
    because a sign-in whose proof does not verify must not replace or sit beside a team's tokens."""
    from empirica.core.auth import seat_assertion

    if not token:
        return {
            "present": False,
            "verified": False,
            "kid": None,
            "reason": "Cortex sent no seat assertion (unverified)",
        }
    try:
        claims = seat_assertion.verify(token, nonce=nonce)
    except seat_assertion.SeatAssertionError as e:
        raise RuntimeError(f"seat assertion did not verify ({e}); nothing stored") from e
    return {
        "present": True,
        "verified": True,
        "kid": claims.get("_kid"),
        "reason": None,
        "org_id": claims.get("org_id"),
    }


def _claim_stamp(stamp: Path, min_interval: float, now: float) -> bool:
    """Claim this attempt so listeners starting together almost always make one call between them (1.17.18 sweep). A
    stale stamp is moved aside by an atomic rename and the new one created exclusively; a caller whose stale read predates
    another's claim can still win a second rename, so the bound is one extra call against the 5-an-hour limit, not zero.
    False when another attempt is recent or another caller won the claim."""
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        if stamp.exists() and now - stamp.stat().st_mtime >= min_interval:
            aside = stamp.with_name(f"{stamp.name}.{os.getpid()}.{time.time_ns()}")
            try:
                os.rename(stamp, aside)
            except FileNotFoundError:
                return False  # another caller moved it first: theirs is the attempt
            aside.unlink(missing_ok=True)
        os.close(os.open(stamp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return True
    except FileExistsError:
        return False
    except OSError:
        return True  # a stamp that cannot be written must not block the fetch for good


def refetch_ntfy_credentials(
    loader=None, *, stamp_path: Path | None = None, min_interval: float = 1800, http=_http_json
) -> dict:
    """Fetch the ntfy credentials outside a sign-in, at most once per ``min_interval`` seconds on this machine.

    For the listener: auth login fetches them once, and a seat Cortex answered 404 for (no notification account yet) then
    restarted its listeners forever until the person signed in again (prop_z2qcr27 #5, goal 489c7fa0). The endpoint allows
    5 calls an hour and the supervisor restarts every few minutes, so the attempt is stamped machine-wide and skipped while
    the stamp is fresh. Never raises. Returns {written, reason}.
    """
    import time as _time

    stamp = stamp_path or (Path.home() / ".empirica" / "ntfy_fetch_attempt")
    try:
        creds = cortex_bearer(loader)
    except Exception as e:
        return {"written": False, "reason": f"the Cortex sign-in could not be read ({type(e).__name__})"}
    if not (creds.get("url") and creds.get("bearer")):
        return {"written": False, "reason": "no Cortex sign-in on this machine; run `empirica auth login`"}
    # Claimed after the sign-in check: an attempt with no sign-in does not use up the window (1.17.18 second pass).
    if not _claim_stamp(stamp, min_interval, _time.time()):
        try:
            age = int((_time.time() - stamp.stat().st_mtime) // 60)
        except OSError:
            age = 0
        return {
            "written": False,
            "reason": f"throttled: last tried {age} min ago (at most every {int(min_interval // 60)} min)",
        }
    if loader is None:
        from empirica.config.credentials_loader import get_credentials_loader

        loader = get_credentials_loader()
    return _fetch_ntfy_credentials(loader, creds["url"], creds["bearer"], http=http)


def _fetch_ntfy_credentials(loader, base_url: str, token: str, *, http=_http_json) -> dict:
    """Write the seat's ntfy url and token into credentials.yaml so the listeners can subscribe (mesh-support prop_ajiz272z).

    Cortex: GET /v1/users/me/ntfy-credentials with the seat's own bearer gives {ntfy_url, bearer, created_at}, 404
    bearer_not_provisioned when it has no notification account for the person, at most 5 calls an hour. A token already in
    the file is kept and the endpoint not called: it may be one set by hand, and re-signing in should not spend the limit.
    Never raises; the sign-in stands whatever happens here. Returns {written, reason}, never the token.
    """
    env = [v for v in ("ORCHESTRATION_NTFY_URL", "ORCHESTRATION_NTFY_TOKEN", "ORCHESTRATION_NTFY_PASS") if os.getenv(v)]
    if env:
        # The listener prefers these over the file; Cortex's bearer written beside a self-hosted URL would go to that host.
        return {"written": False, "reason": f"ntfy is set in the environment ({', '.join(env)}); left as it is"}
    try:
        existing = loader._read_existing(loader._resolve_credentials_target(None)).get("ntfy") or {}
    except Exception:
        existing = {}
    if isinstance(existing, dict) and (existing.get("token") or existing.get("password")):
        return {"written": False, "reason": "kept the ntfy credentials already in credentials.yaml"}
    try:
        out = http(f"{base_url.rstrip('/')}/v1/users/me/ntfy-credentials", headers={"Authorization": f"Bearer {token}"})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {
                "written": False,
                "reason": "this seat has no notification account yet (Cortex: bearer_not_provisioned); listeners stay off until it has one",
            }
        return {
            "written": False,
            "reason": f"could not fetch the ntfy credentials (HTTP {e.code}); run auth login again later",
        }
    except Exception as e:
        return {"written": False, "reason": f"could not fetch the ntfy credentials ({type(e).__name__})"}
    url, bearer = (out or {}).get("ntfy_url"), (out or {}).get("bearer")
    if not (isinstance(url, str) and url and isinstance(bearer, str) and bearer):
        return {"written": False, "reason": "Cortex answered without an ntfy url and token"}
    try:
        loader.save_ntfy_config(url=url, token=bearer)
    except Exception as e:
        return {"written": False, "reason": f"could not write credentials.yaml ({type(e).__name__}: {e})"}
    return {"written": True, "reason": None, "url": url.rstrip("/")}


def _whoami(base_url: str, token: str, *, http=_http_json) -> dict:
    """GET /v1/users/me with ``token``: {org_id, org_name, ...}, or {} when it cannot say (the sign-in still stands)."""
    try:
        out = http(f"{base_url.rstrip('/')}/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        return out if isinstance(out, dict) else {}
    except Exception as e:
        logger.debug("auth login: /v1/users/me failed: %s", e)
        return {}


def _cortex_key(url: str | None) -> tuple:
    """A Cortex URL compared as scheme, host (lowercase, no trailing dot), port (the scheme's default when absent), path."""
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
        port = parsed.port or {"https": 443, "http": 80}.get(parsed.scheme.lower())
    except ValueError:
        return (str(url or ""),)
    host = (parsed.hostname or "").lower().rstrip(".")
    return (parsed.scheme.lower(), host, port, parsed.path.rstrip("/"))


def _same_cortex(loader, base_url: str) -> bool:
    """Whether a sign-in to ``base_url`` is to the configured Cortex (a first sign-in, with none configured, is)."""
    configured = (loader.get_cortex_config() or {}).get("url") or ""
    return not configured or _cortex_key(configured) == _cortex_key(base_url)


def _slot_for_new_sign_in(loader, new_org: str | None, base_url: str, *, http=_http_json) -> str | None:
    """Where a new sign-in for ``new_org`` is stored: None (the default `cortex.oauth`) or an org id (`cortex.orgs`).

    The default when there is none, when it is the same team, or when the new org is unknown (as before). Beside it when
    the default records another team. A default from before orgs were recorded is asked which team it is, once, with its
    own token; when it cannot say (its token is dead and will not renew), nothing usable would be lost, so it is replaced.
    """
    if not loader.multi_team_allowed() or not _same_cortex(loader, base_url):
        # Several teams need Empirica Cortex, and the sign-in must be to the Cortex the stored ones belong to: a login to
        # another server neither probes the stored token there nor files its token beside ours (1.17.2 sweep).
        return None
    if new_org and any(slot == new_org for slot, _ in loader.cortex_oauth_sets()):
        return new_org  # that team already has its own block: update it, never a second copy
    default = loader.get_cortex_oauth()
    if not default or not new_org:
        return None
    from empirica.config.credentials_loader import effective_org

    known = effective_org(default)
    if not known:
        try:
            refresh_cb, owner = custody_refresh(loader, http=http)
            token = loader.cortex_access_token(refresh=refresh_cb)
        except Exception:
            token, owner = None, "cli"
        if token:
            known = _whoami(base_url, token, http=http).get("org_id")
            if not known:
                return new_org  # a live default that could not say which team it is stays; the new one goes beside it
        elif default.get("refresh_token") and owner != "cli":
            return new_org  # expired, but its owner (the daemon or the extension) renews it: keep it
        else:
            return None  # dead and nobody renews it: nothing usable is lost by replacing it, as before
        loader.save_cortex_oauth(org_id=known)  # recorded, so the next sign-in need not ask
    return None if known == new_org else new_org


def _open_and_wait(authorize_url: str, open_browser, server, state: str, timeout_s: float) -> str:
    """Open the browser in the background (best effort) and wait for the loopback callback."""

    def _best_effort_open() -> None:
        try:
            opener_fn = open_browser
            if opener_fn is None:
                import webbrowser

                opener_fn = webbrowser.open  # may itself raise on a browserless box
            opener_fn(authorize_url)
        except Exception as e:
            # browserless (WSL2 interop-off, headless) — expected; the URL was
            # already printed above, so this is a degraded path, not a failure.
            logger.debug("auth login: browser open failed (%s); URL printed to stderr", e)

    threading.Thread(target=_best_effort_open, daemon=True).start()
    return _wait_for_callback(server, state, timeout_s)


def _serve_daemon_running() -> bool:
    """True iff the local `empirica serve` daemon answers its health endpoint.
    Best-effort: any failure means 'assume no daemon' → cli-owned, the safe
    default (a lone shell refreshing its own family can't collide)."""
    import os

    port = os.getenv("EMPIRICA_SERVE_PORT", "8000")
    try:
        from empirica.core.loop_scheduler.http_shared import open_url

        with open_url(f"http://127.0.0.1:{port}/api/v1/health", 2) as r:
            return 200 <= r.status < 300
    except Exception:
        return False
