"""Handler for `empirica practice-context` — Ambassador addressbook.

Lane 2 of David's Ambassador design-of-record (cortex prop_7r5tihxyqr,
ratified 2026-06-02). Unblocked by cortex Lane 1 substrate field
(`projects.substrate`, ac47e66 + ace05e4, deploy 2026-06-02).

The command projects the caller's roster (`GET /v1/users/me/roster`)
into a per-practitioner addressbook with:

| ai_id            | tenant   | substrate | role |
|------------------|----------|-----------|------|
| empirica         | david    | cortex    | self |
| extension        | david    | cortex    | peer |
| cortex           | david    | cortex    | peer |
| empirica         | philipp  | cortex    | peer |

Used by autonomy's Ambassador to know who exists in the mesh + how to
reach them (substrate determines transport: cortex/git/local).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

CORTEX_ROSTER_PATH = "/v1/users/me/roster"


def handle_practice_context_command(args) -> int:
    """Emit the practice-context addressbook (human table or JSON)."""
    cortex_url, api_key = _resolve_cortex_config(args)
    if not cortex_url or not api_key:
        print(
            "Error: cortex config missing. Pass --cortex-url + --api-key, "
            "set CORTEX_URL + CORTEX_API_KEY env, or configure "
            "`cortex:` block in ~/.empirica/credentials.yaml.",
            file=sys.stderr,
        )
        return 2

    timeout = float(getattr(args, "timeout", 10.0))
    try:
        roster = _fetch_roster(cortex_url, api_key, timeout)
    except urllib.error.HTTPError as e:
        print(
            f"Error: cortex roster fetch failed ({e.code} {e.reason}).",
            file=sys.stderr,
        )
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(
            f"Error: cortex unreachable ({e}). Roster endpoint requires cortex.",
            file=sys.stderr,
        )
        return 1

    rows = _project_roster_to_addressbook(roster, self_ai_id=_resolve_self_ai_id())

    ai_id_filter = getattr(args, "ai_id", None)
    if ai_id_filter:
        rows = [r for r in rows if r["ai_id"] == ai_id_filter]

    output = getattr(args, "output", "human")
    if output == "json":
        print(json.dumps({"practices": rows, "count": len(rows)}, indent=2, default=str))
    else:
        _render_human(rows)
    return 0


def _resolve_cortex_config(args) -> tuple[str | None, str | None]:
    """Same precedence chain as projects_commands._resolve_cortex_config."""
    arg_url = getattr(args, "cortex_url", None)
    arg_key = getattr(args, "api_key", None)
    if arg_url and arg_key:
        return arg_url.rstrip("/"), arg_key

    from empirica.config.credentials_loader import get_credentials_loader
    cfg = get_credentials_loader().get_cortex_config()
    url = arg_url or cfg.get("url")
    key = arg_key or cfg.get("api_key")
    return (url.rstrip("/") if url else None, key or None)


def _resolve_self_ai_id() -> str | None:
    """Return the caller's canonical ai_id, or None if unresolvable."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        return R.ai_id()
    except Exception:
        return None


def _fetch_roster(cortex_url: str, api_key: str, timeout: float) -> dict[str, Any]:
    """GET /v1/users/me/roster. Returns the parsed body dict."""
    req = urllib.request.Request(
        f"{cortex_url}{CORTEX_ROSTER_PATH}",
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _project_roster_to_addressbook(
    roster: dict[str, Any],
    self_ai_id: str | None,
) -> list[dict[str, Any]]:
    """Flatten the roster into addressbook rows.

    Roster shape (per cortex/roster.py compute_roster, verified 2026-06-02):
      {
        "self": {"user_id", "tenant_slug", "ai_ids": [...]},
        "org": {
          "id", "slug", "name",
          "tenants": [
            {
              "tenant_slug", "user_name", "is_admin", "governance_mode",
              "projects": [
                {"id", "slug", "display_name",
                 "ai_id_short", "ai_id_tenant", "ai_id_mesh",
                 "substrate"}
              ]
            },
            ...   # one entry per tenant in the same org
          ]
        },
        "version", "etag"
      }

    Permissive parsing — missing fields default sensibly so the addressbook
    keeps rendering even when cortex's shape evolves.
    """
    rows: list[dict[str, Any]] = []

    self_meta = roster.get("self") or {}
    self_tenant_slug = self_meta.get("tenant_slug") or "unknown"

    org = roster.get("org") or {}
    org_slug = org.get("slug") or "unknown"

    for tenant in org.get("tenants", []) or []:
        tenant_slug = tenant.get("tenant_slug") or "unknown"
        is_self_tenant = (tenant_slug == self_tenant_slug)
        for proj in tenant.get("projects", []) or []:
            ai_id = proj.get("ai_id_short") or proj.get("slug") or "?"
            substrate = proj.get("substrate") or "cortex"
            role = "self" if (is_self_tenant and self_ai_id and ai_id == self_ai_id) else "peer"
            rows.append({
                "ai_id": ai_id,
                "tenant": tenant_slug,
                "substrate": substrate,
                "role": role,
                "ai_id_tenant": proj.get("ai_id_tenant"),
                "ai_id_mesh": proj.get("ai_id_mesh"),
                "project_id": proj.get("id"),
                "org": org_slug,
            })

    return rows


def _render_human(rows: list[dict[str, Any]]) -> None:
    """Render rows as an aligned table."""
    if not rows:
        print("(empty roster — no practitioners reachable)")
        return

    headers = ["ai_id", "tenant", "substrate", "role"]
    widths = {
        h: max(len(h), max(len(str(r.get(h, ""))) for r in rows))
        for h in headers
    }

    sep_line = "  ".join("-" * widths[h] for h in headers)
    header_line = "  ".join(h.ljust(widths[h]) for h in headers)
    print(header_line)
    print(sep_line)
    for r in rows:
        print("  ".join(str(r.get(h, "")).ljust(widths[h]) for h in headers))
    print(f"\n{len(rows)} practitioner(s)")
