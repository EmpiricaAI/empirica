"""Project identity migration — slug→UUID (the 1.12 single-UUID model).

``.empirica/project.yaml`` is the git-intrinsic source of truth for
``project_id`` (a UUID). Legacy projects init'd before the UUID switch carry
``project_id: <slug>`` (e.g. ``empirica``, ``empirica-outreach``). From 1.12 we
kill slug-as-id entirely: a project's identity is a single UUID, and the
practice/``ai_id`` identity stays the project *name*.

This module is the migration engine. It is deliberately self-contained and
dependency-injected (cortex resolution + minting are passed in) so it can be
unit-tested with zero network and wired into ``setup-claude-code --force`` (the
named migration path) and the session-init heal.

Resolution order for a slug/absent yaml id (project.yaml-authoritative):

1. ``project.yaml`` already holds a UUID → it wins, nothing to resolve.
2. ``workspace.db`` ``global_projects.trajectory_path`` → local, fast.
3. Cortex by slug+tenant → the public-release / no-workspace.db case.
4. Mint → never-registered, local-only.
5. Unresolved → a structured, actionable message (no guessing). A remote
   Claude — or the user running ``project-register`` once Cortex is reachable —
   finishes the job.

The re-key is **schema-introspection based**: every table carrying a
``project_id`` column, in every ``.db`` under ``<project>/.empirica/``, gets
``UPDATE … SET project_id = <new> WHERE project_id = <old>``. Complete by
construction — it does not drift as the schema grows new tables. ``workspace.db``
is keyed by ``trajectory_path`` (its ``global_projects.id`` is the UUID), so it
is handled by its own session-init heal, not re-keyed here.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

# Same shape the session-init healer uses (session-init.py:_PROJECT_ID_UUID_RE).
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Type alias: (slug, tenant) -> canonical UUID or None.
CortexResolver = Callable[[str | None, str | None], str | None]


def is_uuid(value: object) -> bool:
    """True iff value is a canonical UUID string (the 1.12 project_id shape)."""
    return bool(value) and bool(_UUID_RE.match(str(value)))


def _read_yaml_project_id(project_root: str | Path) -> str | None:
    import yaml

    yaml_path = Path(project_root) / ".empirica" / "project.yaml"
    if not yaml_path.exists():
        return None
    try:
        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        return cfg.get("project_id") or None
    except Exception:
        return None


def _write_yaml_project_id(project_root: str | Path, new_id: str) -> bool:
    """Atomically rewrite project.yaml's project_id, preserving key order.

    Returns True if a write happened, False if it was already the new value.
    """
    import yaml

    yaml_path = Path(project_root) / ".empirica" / "project.yaml"
    if not yaml_path.exists():
        return False
    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if cfg.get("project_id") == new_id:
        return False
    cfg["project_id"] = new_id
    yaml_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return True


def _lookup_workspace_uuid(project_root: str | Path, workspace_db: str | Path | None = None) -> str | None:
    """Canonical UUID for a project from workspace.db global_projects, by path."""
    ws = Path(workspace_db) if workspace_db else Path.home() / ".empirica" / "workspace" / "workspace.db"
    if not ws.exists():
        return None
    trajectory = str(Path(project_root) / ".empirica")
    try:
        conn = sqlite3.connect(str(ws))
        try:
            row = conn.execute(
                "SELECT id FROM global_projects WHERE trajectory_path = ?",
                (trajectory,),
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except Exception:
        return None


def resolve_canonical_uuid(
    project_root: str | Path,
    *,
    workspace_db: str | Path | None = None,
    cortex_resolver: CortexResolver | None = None,
    tenant: str | None = None,
    slug: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve a project's canonical UUID. Returns ``(uuid, source)`` or
    ``(None, None)``.

    ``source`` is one of ``yaml`` | ``workspace`` | ``cortex`` — the store that
    supplied the id. project.yaml wins when it already holds a UUID.
    """
    yaml_id = _read_yaml_project_id(project_root)
    if is_uuid(yaml_id):
        return yaml_id, "yaml"

    ws_uuid = _lookup_workspace_uuid(project_root, workspace_db)
    if is_uuid(ws_uuid):
        return ws_uuid, "workspace"

    if cortex_resolver is not None:
        lookup_slug = slug or yaml_id or Path(project_root).name
        try:
            cortex_uuid = cortex_resolver(lookup_slug, tenant)
            if is_uuid(cortex_uuid):
                return cortex_uuid, "cortex"
        except Exception:
            return None, None  # cortex unreachable / errored — unresolved, never guess

    return None, None


def _tables_with_project_id(conn: sqlite3.Connection) -> list[str]:
    tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    ]
    out = []
    for table in tables:
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if "project_id" in cols:
            out.append(table)
    return out


def rekey_project_id_in_db(db_path: str | Path, old_id: str, new_id: str) -> dict[str, int]:
    """Re-key ``project_id`` from ``old_id`` to ``new_id`` across every table in
    one SQLite db that carries a ``project_id`` column.

    Returns ``{table: rows_updated}`` for tables actually touched. A no-op (and
    empty dict) when the db is missing or ``old_id == new_id``.
    """
    result: dict[str, int] = {}
    if old_id == new_id or not Path(db_path).exists():
        return result
    conn = sqlite3.connect(str(db_path))
    try:
        for table in _tables_with_project_id(conn):
            cur = conn.execute(
                f'UPDATE "{table}" SET project_id = ? WHERE project_id = ?',
                (new_id, old_id),
            )
            if cur.rowcount:
                result[table] = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return result


def rekey_project_local_dbs(project_root: str | Path, old_id: str, new_id: str) -> dict[str, dict[str, int]]:
    """Re-key every ``.db`` under ``<project_root>/.empirica/`` (sessions +
    artifacts). Returns ``{db_relpath: {table: rows}}`` for dbs touched."""
    empirica_dir = Path(project_root) / ".empirica"
    out: dict[str, dict[str, int]] = {}
    if not empirica_dir.exists():
        return out
    for db_file in sorted(empirica_dir.rglob("*.db")):
        touched = rekey_project_id_in_db(db_file, old_id, new_id)
        if touched:
            out[str(db_file.relative_to(empirica_dir))] = touched
    return out


def _rekey_workspace_db(old_id: str, new_id: str, workspace_db: str | Path | None = None) -> dict[str, int]:
    """Re-key a project UUID in the user-level workspace.db: ``global_projects.id``
    and ``entity_registry.entity_id`` (project rows). Part of the completeness
    guard — the workspace stores are where the documented stranding bug
    (prop_xa6djztv5) left data behind when only some stores were corrected."""
    ws = Path(workspace_db) if workspace_db else Path.home() / ".empirica" / "workspace" / "workspace.db"
    out: dict[str, int] = {}
    if old_id == new_id or not ws.exists():
        return out
    conn = sqlite3.connect(str(ws))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "global_projects" in tables:
            cur = conn.execute("UPDATE global_projects SET id = ? WHERE id = ?", (new_id, old_id))
            if cur.rowcount:
                out["global_projects"] = cur.rowcount
        if "entity_registry" in tables:
            cur = conn.execute(
                "UPDATE entity_registry SET entity_id = ? WHERE entity_type = 'project' AND entity_id = ?",
                (new_id, old_id),
            )
            if cur.rowcount:
                out["entity_registry"] = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return out


def _rekey_registry_yaml(old_id: str, new_id: str) -> bool:
    """Re-key a project UUID in ``~/.empirica/registry.yaml`` (the daemon's served
    set). Returns True iff a row was rewritten."""
    if old_id == new_id:
        return False
    try:
        from empirica.api.registry import load_registry, save_registry
    except Exception:
        return False
    reg = load_registry()
    changed = False
    for p in reg.get("projects", []):
        if p.get("project_id") == old_id:
            p["project_id"] = new_id
            changed = True
    if changed:
        save_registry(reg)
    return changed


def reconcile_project_identity(
    project_root: str | Path,
    old_id: str,
    new_id: str,
    *,
    workspace_db: str | Path | None = None,
) -> dict:
    """Converge a project's local identity to ``new_id`` (the cortex-canonical
    UUID) across EVERY id-of-record store, in one pass.

    Cortex-is-authority reconcile (David's directive). The completeness is the
    load-bearing part: the documented stranding bug (prop_xa6djztv5) was an
    *incomplete* correction — one store moved while live sessions/artifacts
    stayed under the old id elsewhere. This rekeys them together:

      - local ``.empirica/*.db`` (sessions.db = local source of truth, artifacts…)
      - workspace.db ``global_projects.id`` + ``entity_registry.entity_id``
      - ``~/.empirica/registry.yaml``
      - ``.empirica/project.yaml``

    Qdrant collections are keyed by project_id but can't be renamed in place —
    the caller must run ``empirica rebuild --qdrant`` after (flagged in the
    return via ``qdrant_rebuild_needed``).

    Idempotent + a no-op when ``old_id == new_id``.
    """
    if old_id == new_id:
        return {"reconciled": False, "reason": "already_aligned", "old_id": old_id, "new_id": new_id}
    local_dbs = rekey_project_local_dbs(project_root, old_id, new_id)
    workspace = _rekey_workspace_db(old_id, new_id, workspace_db)
    registry = _rekey_registry_yaml(old_id, new_id)
    yaml_written = _write_yaml_project_id(project_root, new_id)
    return {
        "reconciled": True,
        "old_id": old_id,
        "new_id": new_id,
        "local_dbs": local_dbs,
        "workspace_db": workspace,
        "registry_yaml": registry,
        "project_yaml": yaml_written,
        "qdrant_rebuild_needed": True,
    }


def migrate_project_to_uuid(
    project_root: str | Path,
    *,
    workspace_db: str | Path | None = None,
    cortex_resolver: CortexResolver | None = None,
    mint: Callable[[], str] | None = None,
    tenant: str | None = None,
) -> dict:
    """Migrate one project's identity to a single canonical UUID (1.12 model).

    Idempotent. Returns a structured result with a ``status``:

    - ``no_project`` — no ``.empirica/project.yaml``.
    - ``already_uuid`` — project_id is already a UUID; nothing to do.
    - ``migrated`` — slug→UUID resolved + re-keyed; carries ``rekeyed`` counts.
    - ``unresolved`` — could not find/mint a UUID; carries an actionable
      ``message`` instead of guessing (a remote Claude / the user finishes it).
    """
    yaml_id = _read_yaml_project_id(project_root)
    if yaml_id is None:
        return {
            "status": "no_project",
            "message": (f"No .empirica/project.yaml at {project_root}. Run 'empirica project-init' to create it."),
        }
    if is_uuid(yaml_id):
        return {"status": "already_uuid", "project_id": yaml_id}

    slug = yaml_id
    new_id, source = resolve_canonical_uuid(
        project_root,
        workspace_db=workspace_db,
        cortex_resolver=cortex_resolver,
        tenant=tenant,
        slug=slug,
    )
    if new_id is None and mint is not None:
        try:
            minted = mint()
            if is_uuid(minted):
                new_id, source = minted, "minted"
        except Exception:
            new_id = None

    if new_id is None:
        return {
            "status": "unresolved",
            "slug": slug,
            "message": (
                f"Could not resolve a canonical UUID for legacy project_id "
                f"'{slug}'. It is not in workspace.db, not registered in Cortex "
                "(or Cortex is unreachable), and minting was unavailable. "
                "Fix: run 'empirica project-register .' once Cortex is reachable "
                "(it adopts or mints the UUID), then re-run the migration."
            ),
        }

    rekeyed = rekey_project_local_dbs(project_root, slug, new_id)
    yaml_updated = _write_yaml_project_id(project_root, new_id)
    return {
        "status": "migrated",
        "slug": slug,
        "project_id": new_id,
        "source": source,
        "yaml_updated": yaml_updated,
        "rekeyed": rekeyed,
    }


def _cortex_installed() -> bool:
    """True when Cortex is configured locally (a cortex api_key in
    ~/.empirica/credentials.yaml). Mirrors the codebase convention
    (liveness_probe / loop_scheduler read the same shape).

    The signal matters for the migration's mint policy: registering a project
    in Cortex always requires this key, so its presence means the project may
    already have a canonical Cortex UUID — minting a fresh one would *fork* the
    identity. Its absence (public-facing / no-mesh) means the project is purely
    local, so minting is safe.
    """
    try:
        import yaml

        cred = Path.home() / ".empirica" / "credentials.yaml"
        if not cred.exists():
            return False
        data = yaml.safe_load(cred.read_text(encoding="utf-8")) or {}
        cortex = data.get("cortex") or {}
        return bool(cortex.get("api_key"))
    except Exception:
        return False


def _make_cortex_slug_resolver(timeout: float = 8.0) -> CortexResolver:
    """Build a (slug, tenant) -> canonical UUID resolver hitting cortex
    ``GET /v1/projects/by-slug/{slug}`` (caller-scope; the caller's org+user is
    derived from the bearer, so it returns *this* owner's project). Reads cortex
    ``url`` + ``api_key`` from ~/.empirica/credentials.yaml. Returns None on
    miss/unconfigured/error — never raises, so the migration degrades to the
    route-to-``project-register`` path rather than guessing.

    Endpoint contract (cortex a61587c): 200 ``{ok, project: {id, ...}}`` |
    404 ``{ok: false, error: "not_found"}``. Archived projects are excluded.
    """

    def resolver(slug: str | None, tenant: str | None) -> str | None:
        if not slug:
            return None
        try:
            import json
            import urllib.parse
            import urllib.request

            import yaml

            cred = Path.home() / ".empirica" / "credentials.yaml"
            if not cred.exists():
                return None
            cortex = (yaml.safe_load(cred.read_text(encoding="utf-8")) or {}).get("cortex") or {}
            url, key = cortex.get("url"), cortex.get("api_key")
            if not url or not key:
                return None
            endpoint = url.rstrip("/") + "/v1/projects/by-slug/" + urllib.parse.quote(str(slug), safe="")
            req = urllib.request.Request(endpoint, headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok") and isinstance(body.get("project"), dict):
                return body["project"].get("id")
        except Exception:
            return None
        return None

    return resolver


def run_force_migration(
    project_root: str | Path,
    *,
    cortex_installed_fn: Callable[[], bool] = _cortex_installed,
    cortex_resolver: CortexResolver | None = None,
    mint: Callable[[], str] | None = None,
    tenant: str | None = None,
) -> dict:
    """Migrate one project to a single canonical UUID, choosing the resolution
    policy by whether Cortex is installed (David's 1.12 rule).

    - **Cortex installed** → the project may be Cortex-registered, so resolve
      the canonical UUID (workspace.db, then the injected Cortex resolver) and
      **never mint** — an unresolvable case routes the user to
      ``project-register`` (the one correct adopt-or-mint path).
    - **Cortex not installed** (public-facing / no-mesh) → no remote identity
      exists, so **minting locally is safe**.

    The Cortex slug→UUID ``cortex_resolver`` is dependency-injected: a
    ``GET project by slug+tenant`` endpoint is the forward-looking follow-on
    (existing Cortex users on a CLI harness expanding to multi-project). Until
    it lands the resolver is ``None`` and that case routes to ``project-register``.
    """
    import uuid

    installed = cortex_installed_fn()
    if installed:
        # Default to the live cortex slug→UUID resolver; an explicit injected
        # resolver (incl. a stub) overrides it (tests / custom flows).
        resolver = cortex_resolver if cortex_resolver is not None else _make_cortex_slug_resolver()
        mint_fn = None  # never fork a possibly-registered identity
    else:
        resolver = None
        mint_fn = mint or (lambda: str(uuid.uuid4()))  # safe: purely local

    result = migrate_project_to_uuid(
        project_root,
        cortex_resolver=resolver,
        mint=mint_fn,
        tenant=tenant,
    )
    result["cortex_installed"] = installed
    return result
