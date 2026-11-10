"""Where the mesh paths send their requests: a configured Cortex, else the local server (1.15.1, goal b4cdb5db).

David's ruling (2026-11-10): on a machine with no Cortex, practices hand work to each other through empirica-local, with no
setting to make. A configured Cortex always wins. With none, ``mesh_bearer()`` uses ``empirica serve`` when it answers
``GET /v1/health`` on loopback with ``"edition": "local"``; a development Cortex on the same port, an older serve without
the local routes, or nothing listening are all "no endpoint".

Only the mesh paths call this (mailbox, project registration, listener catch-up, heartbeats, system events). Everything
else keeps calling ``cortex_bearer()`` / ``get_cortex_config()``, which mean "a real Cortex seat": the reader audit found 11
readers that break if "no Cortex" starts returning a local URL (the engagement-write gate, the prompt render, the setup
wizard, identity minting, seat backfill, ``auth login`` among them).
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from typing import Any

PROBE_TIMEOUT = 0.5  # loopback: a refused connection is ~1 ms; this only bounds a port held by something that hangs


def _cortex_bearer(loader=None) -> dict[str, Any]:
    # The same import the moved callers used (`from empirica.core.auth import cortex_bearer`), at call time, so whatever
    # empirica.core.auth holds is what runs: a patched attribute, or a module replaced in sys.modules.
    from empirica.core.auth import cortex_bearer

    return cortex_bearer() if loader is None else cortex_bearer(loader)


def _probe_health(url: str) -> dict | None:
    """The health document at ``url``, or None for anything that is not one (refused, timed out, not JSON)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=PROBE_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def local_server_url() -> str:
    return f"http://127.0.0.1:{os.environ.get('EMPIRICA_SERVE_PORT', '8000')}"


def _local_token() -> str:
    """The token the local server checks, when one is configured; otherwise any bearer passes and "local" is sent."""
    try:
        from empirica.local_server.api import configured_token
    except Exception:  # the [api] extra is absent: no local server runs here anyway
        return os.environ.get("EMPIRICA_LOCAL_TOKEN") or "local"
    return configured_token() or "local"


def mesh_bearer(loader=None, *, probe: Callable[[str], dict | None] | None = None) -> dict[str, Any]:
    """``{url, bearer, source, edition}`` for the mesh paths, the shape of ``cortex_bearer()`` plus ``edition``.

    ``edition`` is ``"cortex"`` when a Cortex is configured (even one whose credential is unusable: that surfaces as its
    ``reason``, it does not quietly switch the practice to local), ``"local"`` for the local server, None for neither.
    """
    creds = dict(_cortex_bearer(loader))
    if creds.get("url"):
        return {**creds, "edition": "cortex"}
    base = local_server_url()
    doc = (probe or _probe_health)(f"{base}/v1/health")
    if isinstance(doc, dict) and doc.get("edition") == "local":
        return {"url": base, "bearer": _local_token(), "edition": "local", "source": "local"}
    return {**creds, "edition": None}
