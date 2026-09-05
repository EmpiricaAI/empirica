"""mesh-agreements command — sync / list cortex mesh sharing agreements
into empirica's entity_registry mirror.

Read-only from cortex's perspective (no admin verbs here — sharing
governance happens via cortex REST direct or the extension System tab
admin UI). See docs/architecture/MESH_SHARING_AGREEMENTS.md (not in this repo).
"""

from __future__ import annotations

import json
from typing import Any

from empirica.core.mesh_sharing import (
    ENTITY_TYPE,
    SyncResult,
    sync_from_cortex,
)


def _load_cortex_credentials() -> tuple[str | None, str | None]:
    """Read cortex URL + bearer through the SHARED credential contract.

    Returns (None, None) if either is missing — caller surfaces the error.

    Hand-parsed `~/.empirica/credentials.yaml` before this, which was blind
    twice: to OAuth (an OAuth-only seat has no api_key to find) and to the
    loader's precedence — a repo-local credentials file, which the loader
    honours, was invisible to a reader that only opened HOME.
    """
    try:
        from empirica.core.auth import cortex_bearer

        creds = cortex_bearer()
        return creds.get("url"), creds.get("bearer")
    except Exception:
        return None, None


def _open_workspace_repo():
    """Open the workspace.db repository. Returns the repo instance."""
    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    return WorkspaceDBRepository.open(ensure_schema=True)


def _emit(payload: dict[str, Any], output: str) -> None:
    if output == "json":
        print(json.dumps(payload, indent=2))
    else:
        ok = payload.get("ok")
        line = payload.get("summary") or payload.get("message") or ""
        prefix = "✅" if ok else "❌"
        print(f"{prefix} {line}")


def handle_mesh_agreements_group_command(args) -> int:
    """Dispatch `empirica mesh-agreements <action>`."""
    import sys

    action = getattr(args, "action", None)
    if action == "sync":
        return handle_mesh_agreements_sync_command(args)
    if action == "list":
        return handle_mesh_agreements_list_command(args)
    sys.stderr.write("Usage: empirica mesh-agreements <sync|list>\n")
    return 1


def handle_mesh_agreements_sync_command(args) -> int:
    """Trigger a sync of cortex's mesh_sharing_agreements into entity_registry."""
    output = getattr(args, "output", "human")

    cortex_url = getattr(args, "cortex_url", None)
    api_key = getattr(args, "api_key", None)
    if not (cortex_url and api_key):
        loaded_url, loaded_key = _load_cortex_credentials()
        cortex_url = cortex_url or loaded_url
        api_key = api_key or loaded_key

    if not (cortex_url and api_key):
        _emit(
            {
                "ok": False,
                "error": "cortex credentials not found",
                "hint": "Set cortex.url + cortex.api_key in ~/.empirica/credentials.yaml "
                "or pass --cortex-url and --api-key",
            },
            output,
        )
        return 1

    repo = _open_workspace_repo()
    result: SyncResult = sync_from_cortex(repo, cortex_url, api_key)

    if result.error:
        _emit(
            {
                "ok": False,
                "error": result.error,
                "added": 0,
                "updated": 0,
                "marked_revoked": 0,
                "summary": result.summary_line(),
            },
            output,
        )
        return 1

    _emit(
        {
            "ok": True,
            "added": result.added,
            "updated": result.updated,
            "marked_revoked": result.marked_revoked,
            "total_seen": result.total_seen,
            "summary": result.summary_line(),
        },
        output,
    )
    return 0


def handle_mesh_agreements_list_command(args) -> int:
    """List mirrored mesh sharing agreements from entity_registry."""
    output = getattr(args, "output", "human")
    status_filter = getattr(args, "status", "active")
    limit = getattr(args, "limit", 100)

    repo = _open_workspace_repo()
    rows = repo.list_entities(entity_type=ENTITY_TYPE, status=status_filter, limit=limit)

    if output == "json":
        print(
            json.dumps(
                {
                    "ok": True,
                    "count": len(rows),
                    "agreements": [
                        {
                            "id": r["entity_id"],
                            "display_name": r["display_name"],
                            "status": r.get("status"),
                            "updated_at": r.get("updated_at"),
                            "metadata": _safe_json_load(r.get("metadata")),
                        }
                        for r in rows
                    ],
                },
                indent=2,
            )
        )
        return 0

    if not rows:
        print(f"No mesh sharing agreements (status={status_filter}).")
        return 0

    print(f"{len(rows)} mesh sharing agreement(s) (status={status_filter}):\n")
    for r in rows:
        meta = _safe_json_load(r.get("metadata")) or {}
        layer = meta.get("layer", "?")
        surfaces = meta.get("surfaces_json") or []
        direction = meta.get("direction", "?")
        print(f"  {r['entity_id'][:16]}…  [{layer}]  {r['display_name']}")
        print(f"    status={r.get('status')}  surfaces={surfaces}  direction={direction}")
        if meta.get("eco_always"):
            print("    eco_always=true (cross-org floor)")
    return 0


def _safe_json_load(raw: Any) -> dict | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
