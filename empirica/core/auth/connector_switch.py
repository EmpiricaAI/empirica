"""Switch MCP connectors from a stored `Authorization` bearer to `headersHelper`.

A connector in `~/.claude.json` that stores `headers.Authorization` holds a credential that a key
rotation signs out (2026-09-29). `headersHelper: "empirica auth token --headers"` presents the seat's
own OAuth token, fresh, on every connection, so nothing is stored. Tested against cortex and
crm.getempirica.com (headless scratch connector: connected with the real token, failed with an invalid
one).

Rules this module enforces, each because the alternative is a way to hurt a seat or leak the token:

- **Plan first.** ``plan_switches`` is pure and changes nothing; the caller shows it and applies only on
  an explicit request.
- **Never present the seat's token to a host that is not ours.** Switching a connector makes the OAuth
  token travel to that connector's URL. Only the seat's own cortex host and ``*.getempirica.com``
  qualify; any other host is left exactly as it is.
- **Never switch a seat that cannot authenticate.** With no valid OAuth token now, nothing is switched.
- **Only what was named.** Applying needs explicit names; no connector is switched because it merely
  looks switchable.
- **No value is ever read into a plan or a report.** Names, scopes and hosts only.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

HELPER_COMMAND = "empirica auth token --headers"
OUR_DOMAIN = "getempirica.com"


def _host(url: object) -> str:
    try:
        return (urlparse(str(url)).hostname or "").lower()
    except ValueError:
        return ""


def host_is_ours(url: object, cortex_url: str | None = None) -> bool:
    """Is ``url`` served by the seat's cortex or by a ``*.getempirica.com`` host?"""
    host = _host(url)
    if not host:
        return False
    if host == OUR_DOMAIN or host.endswith("." + OUR_DOMAIN):
        return True
    return bool(cortex_url) and host == _host(cortex_url)


def _has_static_authorization(cfg: object) -> bool:
    if not isinstance(cfg, dict) or not isinstance(cfg.get("headers"), dict):
        return False
    return any(
        str(k).lower() == "authorization" and isinstance(v, str) and v and "${" not in v
        for k, v in cfg["headers"].items()
    )


def plan_switches(
    config: dict, names: list[str], token_valid: bool, cortex_url: str | None = None
) -> list[dict[str, Any]]:
    """One row per user-scope connector that stores a static Authorization header.

    Each row: ``name``, ``host``, ``action`` (``switch`` or ``leave``) and, for a leave, ``why``.
    A connector is switched only when it was named, the host is ours and the seat has a valid OAuth
    token now; every other candidate is listed and left alone.
    """
    servers = config.get("mcpServers") if isinstance(config.get("mcpServers"), dict) else {}
    rows: list[dict[str, Any]] = []
    for name, cfg in servers.items():
        if not _has_static_authorization(cfg):
            continue
        host = _host(cfg.get("url"))
        row: dict[str, Any] = {"name": name, "host": host or None}
        if not host_is_ours(cfg.get("url"), cortex_url):
            row.update(action="leave", why="host is not ours; the seat's token is never presented elsewhere")
        elif not token_valid:
            row.update(action="leave", why="no valid OAuth token on this seat (run `empirica auth login`)")
        elif name not in names:
            row.update(action="leave", why="not named; pass --name to switch it")
        else:
            row.update(action="switch", why=None)
        rows.append(row)
    return rows


def apply_switches(config: dict, rows: list[dict[str, Any]]) -> int:
    """Rewrite ``config`` in place for every ``switch`` row; return how many connectors changed.

    The Authorization header is removed (other headers stay) and ``headersHelper`` is set. A connector
    that already has a ``headersHelper`` of its own is not overwritten.
    """
    changed = 0
    servers = config.get("mcpServers", {})
    for row in rows:
        if row.get("action") != "switch":
            continue
        cfg = servers.get(row["name"])
        if not isinstance(cfg, dict) or cfg.get("headersHelper"):
            continue
        headers = {k: v for k, v in cfg["headers"].items() if str(k).lower() != "authorization"}
        if headers:
            cfg["headers"] = headers
        else:
            del cfg["headers"]
        cfg["headersHelper"] = HELPER_COMMAND
        changed += 1
    return changed
