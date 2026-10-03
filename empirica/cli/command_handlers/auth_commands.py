"""`empirica auth` — OAuth for the proprietary Cortex service (login/status/logout).

Cortex (getempirica.com) is Empirica's PROPRIETARY serving layer — not part of
this open-source core. Connecting to it requires a Cortex account; these verbs
authenticate that account via OAuth. Empirica core runs fully without Cortex
(single-AI measurement); Cortex is what lights up the mesh.

The path that makes api_key retirement structurally possible on a CLI box:
the CLI holds its own DCR client and refreshes its own tokens. The api_key
is never touched here — retirement is a separate per-seat act gated on the
per-surface survival matrix (lesson
destructive-ops-need-per-surface-survival-signoff).
"""

from __future__ import annotations

import json
import sys
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


def handle_auth_token_command(args) -> int:
    """Print a valid access token for other tools to present.

    Cortex has one token shape: one audience, no subscription claims, and every
    endpoint authorizes per request from the user behind the token (cortex,
    2026-09-26). So the token `auth login` stored, refreshed when expired, is
    exactly what every ecosystem tool should send, rather than a static key of
    its own. Human output is the bare token, so `$(empirica auth token)` works.
    Nothing is printed to stdout on failure: a caller must never mistake an error
    message for a token.
    """
    from empirica.core.auth import cortex_oauth

    output = getattr(args, "output", "human")
    as_headers = bool(getattr(args, "headers", False))
    loader = _loader()
    oauth = loader.get_cortex_oauth()
    # Custody first: a daemon- or extension-owned family is refreshed by its owner, and a second
    # refresher makes cortex revoke it. For those this returns the stored token while it is valid
    # and never refreshes; the same gate `cortex_bearer` applies.
    refresh_cb, owner = cortex_oauth.custody_refresh(loader) if oauth else (None, "cli")
    token = loader.cortex_access_token(refresh=refresh_cb) if oauth else None
    if not token:
        if not oauth.get("access_token"):
            reason, hint = "no token set stored", "run `empirica auth login`"
        elif owner != "cli":
            reason = f"the stored token expired and its refresh belongs to the {owner}, not the CLI"
            hint = (
                f"wait for the {owner} to refresh it (is `empirica serve` running?), or run `empirica auth login` "
                "to take the family over"
            )
        else:
            reason, hint = "the stored token expired and could not be refreshed", "run `empirica auth login`"
        if as_headers:
            # A helper that prints a header on failure presents an empty bearer; printing nothing makes the
            # connection fail where someone can see it.
            sys.stderr.write(f"empirica auth token --headers: {reason}; {hint}\n")
        elif output == "json":
            print(json.dumps({"ok": False, "error": reason, "hint": hint, "refresh_owner": owner}))
        else:
            sys.stderr.write(f"empirica auth token: {reason}; {hint}\n")
        return 1
    if as_headers:
        print(json.dumps({"Authorization": f"Bearer {token}"}))
        return 0
    if output == "json":
        fresh = loader.get_cortex_oauth()
        print(
            json.dumps(
                {
                    "ok": True,
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_at": fresh.get("expires_at"),
                    "url": (loader.get_cortex_config().get("url") or None),
                }
            )
        )
    else:
        print(token)
    return 0


def _report_connector_plan(
    rows: list[dict], unknown: list[str], applied: dict | None, output: str, backup=None
) -> None:
    if output == "json":
        print(
            json.dumps(
                {
                    "ok": True,
                    "plan": rows,
                    "not_found": unknown,
                    "applied": applied,
                    "backup": str(backup) if backup else None,
                }
            )
        )
        return
    if not rows:
        print("no user-scope connector stores a static Authorization header")
    for r in rows:
        why = f" ({r['why']})" if r.get("why") else ""
        print(f"  {r['action']:7s} {r['name']:20s} {r['host'] or '(no host)'}{why}")
    for name in unknown:
        print(f"  not found: {name!r} has no static Authorization header to switch")
    if applied is not None:
        print(f"switched {applied['changed']} connector(s); backup {backup}")
        print("the backup holds the OLD bearer: delete it once the connector has reconnected")
        print("restart Claude Code (or /mcp reconnect) for the connector to pick up the helper")
    elif any(r["action"] == "switch" for r in rows):
        print("dry run: nothing written. Re-run with --apply to switch the rows marked `switch`.")


def handle_auth_connectors_command(args) -> int:
    """Plan, and on --apply perform, the switch of named connectors to `headersHelper`."""
    import os
    import shutil
    import stat as _stat
    from pathlib import Path

    from empirica.cli.command_handlers.setup_claude_code import (
        ConcurrentlyModified,
        _read_json_with_stamp,
        _write_json_file,
    )
    from empirica.config.credentials_loader import _as_epoch_seconds
    from empirica.core.auth.connector_switch import apply_switches, plan_switches

    output = getattr(args, "output", "human")
    names = list(getattr(args, "name", None) or [])
    path = Path.home() / ".claude.json"
    try:
        config, stamp = _read_json_with_stamp(path, {}) if path.is_file() else ({}, None)
    except (OSError, ValueError) as exc:
        sys.stderr.write(
            f"empirica auth connectors: {path} could not be read ({type(exc).__name__}); nothing changed\n"
        )
        return 1
    if not isinstance(config, dict):
        sys.stderr.write(f"empirica auth connectors: {path} is not a JSON object; nothing changed\n")
        return 1

    loader = _loader()
    oauth = loader.get_cortex_oauth()
    try:
        # The loader's own normaliser: an extension bridge has stored expires_at in JS milliseconds,
        # which as seconds reads as year ~58,600, so a dead token looked valid here and the connector was
        # switched to a helper that then refused to serve it (broccoli, 2026-10-03).
        valid = bool(oauth.get("access_token")) and float(_as_epoch_seconds(oauth.get("expires_at")) or 0) > time.time()
    except (TypeError, ValueError):
        valid = False
    rows = plan_switches(config, names, valid, loader.get_cortex_config().get("url"))
    listed = {r["name"] for r in rows}
    unknown = [n for n in names if n not in listed]
    to_switch = [r for r in rows if r["action"] == "switch"]

    if not getattr(args, "apply", False) or not to_switch:
        _report_connector_plan(rows, unknown, None, output)
        return 0

    stem = f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    backup = path.with_name(stem)
    n = 1
    while (
        backup.exists()
    ):  # two applies in one second must not overwrite the first backup, which holds the original header
        n += 1
        backup = path.with_name(f"{stem}-{n}")
    try:
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    except OSError as exc:
        sys.stderr.write(
            f"empirica auth connectors: could not write the backup ({type(exc).__name__}); nothing changed\n"
        )
        return 1
    changed = apply_switches(config, rows)
    try:
        _write_json_file(path, config, expect_stamp=stamp)
    except ConcurrentlyModified:
        sys.stderr.write(
            f"empirica auth connectors: {path} changed while this command was preparing its write (Claude Code "
            f"writes it continuously). Nothing was written. Re-run it. The backup {backup.name} was kept and holds "
            "the old header; delete it when you no longer need it.\n"
        )
        return 1
    except OSError as exc:
        sys.stderr.write(
            f"empirica auth connectors: could not write {path} ({type(exc).__name__}); nothing changed. "
            f"The backup {backup.name} was kept.\n"
        )
        return 1
    _report_connector_plan(rows, unknown, {"changed": changed}, output, backup)
    try:
        if _stat.S_IMODE(os.stat(path).st_mode) & 0o077:
            sys.stderr.write(
                f"empirica auth connectors: {path} is readable by other users and holds credentials; "
                f"run `chmod 600 {path}`.\n"
            )
    except OSError:
        pass
    return 0


def handle_auth_group_command(args) -> int:
    action = getattr(args, "auth_action", None)
    if action == "connectors":
        return handle_auth_connectors_command(args)
    if action == "token":
        return handle_auth_token_command(args)
    if action == "login":
        return handle_auth_login_command(args)
    if action == "status":
        return handle_auth_status_command(args)
    if action == "logout":
        return handle_auth_logout_command(args)
    print("usage: empirica auth {login|token|status|connectors|logout}")
    return 2
