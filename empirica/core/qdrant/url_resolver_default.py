"""Default per-project URL resolver for standalone empirica CLI usage.

Ships with prop_ure7rqfuon (2026-07-24) as the standalone counterpart
to cortex's ``cortex.qdrant_routing.resolve_qdrant_url``.

When cortex is running, cortex-mcp installs its own resolver at startup
via ``connection.set_url_resolver(resolve_qdrant_url)`` and this module
is never consulted. When the empirica CLI runs standalone (root shell
on a Hetzner box, hooks, cron jobs, dev laptop) cortex isn't imported,
so callers can install THIS resolver instead — it reads the same
tenant DB + ``CORTEX_QDRANT_URLS_BY_ORG`` env cortex uses, so both
processes agree on where a project's data lives.

Install pattern:

    from empirica.core.qdrant.connection import set_url_resolver
    from empirica.core.qdrant.url_resolver_default import (
        make_default_resolver,
    )
    set_url_resolver(make_default_resolver())

The resolver is a closure so we can cache the parsed URL map + a
SQLite connection factory. Idempotent — call ``make_default_resolver()``
again to re-read the env (useful in tests that mutate CORTEX_QDRANT_URLS_BY_ORG).

Failure mode: any lookup failure (DB missing, project not in DB, env
unset, org not mapped) returns None, letting the caller fall through
to the ``EMPIRICA_QDRANT_URL`` env default. Never raises — a resolver
that blows up would break every write.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable

logger = logging.getLogger(__name__)


DEFAULT_TENANT_DB_PATHS = (
    "/root/.cortex/tenants.db",
    os.path.expanduser("~/.cortex/tenants.db"),
    os.path.expanduser("~/.empirica/tenants.db"),
)


def _parse_url_map(spec: str) -> dict[str, str]:
    """Same shape as cortex.qdrant_routing._parse_org_url_map."""
    out: dict[str, str] = {}
    for pair in (spec or "").split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        org, url = pair.split("=", 1)
        out[org.strip()] = url.strip().rstrip("/")
    return out


def _find_tenants_db(explicit: str | None = None) -> str | None:
    """Locate the tenants DB. Explicit path wins; then env; then well-known
    fallbacks. Returns None if none exist — resolver becomes a no-op then.
    """
    if explicit and os.path.exists(explicit):
        return explicit
    env_path = os.environ.get("CORTEX_TENANTS_DB")
    if env_path and os.path.exists(env_path):
        return env_path
    for candidate in DEFAULT_TENANT_DB_PATHS:
        if os.path.exists(candidate):
            return candidate
    return None


def make_default_resolver(
    tenants_db_path: str | None = None,
    *,
    url_map_env: str = "CORTEX_QDRANT_URLS_BY_ORG",
) -> Callable[[str], str | None]:
    """Build a project_id → URL resolver from tenant DB + env.

    Args:
        tenants_db_path: Optional explicit path to tenants.db. Falls
            back to ``CORTEX_TENANTS_DB`` env, then the well-known
            candidates in ``DEFAULT_TENANT_DB_PATHS``.
        url_map_env: Env var holding the comma-separated org=url map.
            Defaults to ``CORTEX_QDRANT_URLS_BY_ORG`` so this resolver
            matches cortex-mcp's routing exactly.

    Returns a callable ``(project_id: str) -> str | None`` suitable for
    ``connection.set_url_resolver``. All failures return None (fall
    through to EMPIRICA_QDRANT_URL env).
    """
    # Snapshot the URL map at resolver-build time. If ops rotate the map
    # under a running process, re-build the resolver.
    url_map = _parse_url_map(os.environ.get(url_map_env, ""))
    db_path = _find_tenants_db(tenants_db_path)

    if not url_map:
        logger.debug(
            f"url_resolver_default: {url_map_env} empty — resolver will "
            f"always return None (fall through to EMPIRICA_QDRANT_URL)",
        )
    if not db_path:
        logger.debug(
            "url_resolver_default: no tenants.db found — resolver will "
            "always return None (fall through to EMPIRICA_QDRANT_URL)",
        )

    def resolve(project_id: str) -> str | None:
        if not project_id or not url_map or not db_path:
            return None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT org_id FROM projects WHERE id = ?",
                    (project_id,),
                ).fetchone()
            finally:
                conn.close()
        except Exception as e:
            logger.debug(
                f"url_resolver_default: DB lookup for project_id={project_id[:8]} failed: {e}",
            )
            return None
        if not row:
            return None
        org_id = row[0]
        return url_map.get(org_id)

    return resolve


__all__ = ["DEFAULT_TENANT_DB_PATHS", "make_default_resolver"]
