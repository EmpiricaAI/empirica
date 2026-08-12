"""`empirica auth` — CLI-owned cortex OAuth (login / status / logout).

The path that makes api_key retirement structurally possible on a CLI box:
the CLI holds its own DCR client and refreshes its own tokens. The api_key
is never touched here — retirement is a separate per-seat act gated on the
per-surface survival matrix (lesson
destructive-ops-need-per-surface-survival-signoff).
"""

from __future__ import annotations

import json
import time


def _loader():
    from empirica.config.credentials_loader import get_credentials_loader

    return get_credentials_loader()


def handle_auth_login_command(args) -> int:
    from empirica.core.auth import login

    output = getattr(args, "output", "human")
    timeout_s = float(getattr(args, "timeout", None) or 300)
    try:
        result = login(timeout_s=timeout_s)
    except Exception as e:
        if output == "json":
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"❌ auth login failed: {e}")
        return 1

    # Rung 4 inline: the credential is proven by a real authenticated call
    # from this seat, not by the token existing.
    verify = _verify_token()
    result["verified_against_api"] = verify
    if output == "json":
        print(json.dumps(result))
    else:
        exp = result.get("expires_at")
        exp_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp)) if exp else "unknown"
        print(f"✅ logged in (client {result['client_id'][:12]}…, token valid until {exp_s})")
        if not result.get("has_refresh_token"):
            print("⚠️  no refresh_token issued — token dies at expiry; api_key fallback covers")
        if verify is True:
            print("✅ verified: authenticated call succeeded with the new token")
        elif verify is False:
            print("❌ token stored but an authenticated call FAILED — do not retire this seat's api_key")
    return 0


def _verify_token() -> bool | None:
    """One real /v1/users/me call with the OAuth token ONLY (no api_key
    fallback) — 'stored' is not 'works'. None when the check itself errors."""
    import urllib.error
    import urllib.request

    try:
        loader = _loader()
        url = (loader.get_cortex_config().get("url") or "").rstrip("/")
        token = loader.cortex_access_token()  # no refresh: token was just minted
        if not url or not token:
            return None
        req = urllib.request.Request(f"{url}/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError:
        return False
    except Exception:
        return None


def handle_auth_status_command(args) -> int:
    output = getattr(args, "output", "human")
    loader = _loader()
    cfg = loader.get_cortex_config()
    oauth = loader.get_cortex_oauth()
    now = time.time()
    expires_at = oauth.get("expires_at")
    credential_lifecycle = "absent"
    if oauth.get("access_token"):
        try:
            credential_lifecycle = "valid" if expires_at and float(expires_at) > now else "expired"
        except (TypeError, ValueError):
            credential_lifecycle = "unknown-expiry"
    status = {
        "url": cfg.get("url"),
        "api_key_present": bool(cfg.get("api_key")),
        "oauth_token": credential_lifecycle,
        "expires_at": expires_at,
        "has_refresh_token": bool(oauth.get("refresh_token")),
        "client_id": (oauth.get("client_id") or "")[:12] or None,
        # The sentence that matters for retirement planning:
        "retirement_ready": credential_lifecycle == "valid" and bool(oauth.get("refresh_token")),
    }
    if output == "json":
        print(json.dumps(status))
    else:
        print(f"cortex url:        {status['url'] or '(none)'}")
        print(f"api_key:           {'present' if status['api_key_present'] else 'absent'}")
        print(f"oauth token:       {credential_lifecycle}")
        print(f"refresh token:     {'held (CLI is sole refresher)' if status['has_refresh_token'] else 'none'}")
        print(f"client:            {status['client_id'] or '(not registered)'}")
        print(f"retirement-ready:  {'yes' if status['retirement_ready'] else 'NO — api_key still load-bearing'}")
    return 0


def handle_auth_logout_command(args) -> int:
    """Revoke the refresh token at cortex, then drop the oauth block.
    The api_key is untouched — logout must never be a lockout."""
    output = getattr(args, "output", "human")
    loader = _loader()
    oauth = loader.get_cortex_oauth()
    if not oauth:
        if output == "json":
            print(json.dumps({"ok": True, "note": "no oauth block present"}))
        else:
            print("nothing to log out — no oauth block present")
        return 0

    revoked = _revoke_remote(loader, oauth)
    # Drop the block by rewriting the file without it (save_* only merges).
    _drop_oauth_block(loader)
    result = {"ok": True, "remote_revoked": revoked}
    if output == "json":
        print(json.dumps(result))
    else:
        print(f"✅ logged out (remote revocation: {'ok' if revoked else 'FAILED — token may live until expiry'})")
    return 0


def _revoke_remote(loader, oauth: dict) -> bool:
    import urllib.parse
    import urllib.request

    try:
        from empirica.core.auth import fetch_discovery

        url = (loader.get_cortex_config().get("url") or "").rstrip("/")
        token = oauth.get("refresh_token") or oauth.get("access_token")
        if not url or not token:
            return False
        endpoint = fetch_discovery(url).get("revocation_endpoint")
        if not endpoint:
            return False
        body = urllib.parse.urlencode({"token": token, "client_id": oauth.get("client_id") or ""}).encode()
        req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _drop_oauth_block(loader) -> None:
    """Remove cortex.oauth from the credentials file, preserving everything
    else byte-for-byte at the data level (same atomic write as the savers)."""
    target = loader._resolve_credentials_target(None)
    existing = loader._read_existing(target)
    cortex_block = existing.get("cortex")
    if isinstance(cortex_block, dict) and "oauth" in cortex_block:
        cortex_block.pop("oauth")
        existing["cortex"] = cortex_block
        loader._write_credentials(target, existing)


def handle_auth_group_command(args) -> int:
    action = getattr(args, "auth_action", None)
    if action == "login":
        return handle_auth_login_command(args)
    if action == "status":
        return handle_auth_status_command(args)
    if action == "logout":
        return handle_auth_logout_command(args)
    print("usage: empirica auth {login|status|logout}")
    return 2
