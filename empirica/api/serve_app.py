"""
FastAPI application for `empirica serve` — local daemon for Chrome extension.

Exposes profile operations as REST endpoints on localhost. The extension
extracts artifacts client-side (TypeScript) and POSTs them here for storage.

Security: Localhost-only by default. No authentication required for local use.
CORS allows chrome-extension:// origins for browser extension access.

API contract matches empirica-extension/src/api/empirica-client.ts:
- GET  /api/v1/health          → HealthResponse
- POST /api/v1/artifacts/import → ArtifactImportResponse
- GET  /api/v1/profile/status  → ProfileStatusResponse
- POST /api/v1/profile/sync    → SyncResponse
"""

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Request/Response Models ──────────────────────────────────────────
# These mirror the TypeScript interfaces in empirica-client.ts

class HealthResponse(BaseModel):
    """Matches extension's HealthResponse interface.

    Includes the daemon's active-project info (v0.5+) and, since v1.9.6,
    the registry of locally-known projects (`known_projects`) so the
    extension can offer all of them in its dropdown without round-tripping
    Cortex.
    """
    ok: bool = True
    version: str = "0.1.0"
    api_version: str = "v1"
    ollama: bool = False
    claude_mem: bool = False
    qdrant: bool = False

    # Active project info (v0.5+). All None if daemon launched outside any project.
    project_id: str | None = None
    project_path: str | None = None
    project_name: str | None = None
    project_slug: str | None = None
    repo_url: str | None = None

    # Registry of locally-known projects (v1.9.6+). Empty list when
    # ~/.empirica/registry.yaml is absent — single-project mode.
    known_projects: list[dict] = Field(default_factory=list)


class ArtifactPayload(BaseModel):
    """Single artifact from the extension's extraction pipeline."""
    type: str = Field(..., description="Artifact type: finding, decision, dead_end, mistake, unknown")
    content: str = Field(..., description="Artifact content text")
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    confidenceTier: str | None = None
    contentHash: str | None = None
    metadata: dict = Field(default_factory=dict)


class ArtifactImportRequest(BaseModel):
    """Matches what EmpiricaClient.importArtifacts() sends."""
    artifacts: list[ArtifactPayload] = Field(..., description="Pre-extracted artifacts from extension")


class ArtifactImportResponse(BaseModel):
    """Matches extension's ImportResponse interface."""
    ok: bool
    imported: int = 0
    duplicates_skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class CortexCredentialsRequest(BaseModel):
    """Set Cortex creds via the daemon. At least one field required.

    Extension flow: user enters cortexUrl + cortexApiKey in Settings,
    extension POSTs to this endpoint, daemon writes to
    ~/.empirica/credentials.yaml so the CLI sees the same creds.
    """
    url: str | None = None
    api_key: str | None = None


class CortexCredentialsResponse(BaseModel):
    """Cortex creds GET/POST response. NEVER returns the full key over
    the wire — `api_key_preview` is last-4-chars only, so even if CORS
    gets loosened the secret doesn't leak via read."""
    ok: bool
    url: str | None = None
    api_key_set: bool = False
    api_key_preview: str | None = None
    written_path: str | None = None
    error: str | None = None


class NtfyCredentialsRequest(BaseModel):
    """Set ntfy creds via the daemon. At least one of url/token required.

    Mirrors CortexCredentialsRequest. Extension flow: user enters
    ntfyUrl + ntfyToken in Settings (Notifications tab "Also save to CLI"
    toggle), extension POSTs here, daemon merges into the `ntfy:` block
    of ~/.empirica/credentials.yaml. `topic` is INTENTIONALLY not on this
    request shape — extension doesn't own the topic (cortex's channels
    endpoint dictates it), so partial-updates from this endpoint must
    preserve any existing `topic` key without clobbering."""
    url: str | None = None
    token: str | None = None


class NtfyCredentialsResponse(BaseModel):
    """ntfy creds GET/POST response. NEVER returns the full token over
    the wire — `token_preview` is last-4-chars only, same threat model
    as CortexCredentialsResponse."""
    ok: bool
    url: str | None = None
    topic: str | None = None
    token_set: bool = False
    token_preview: str | None = None
    written_path: str | None = None
    error: str | None = None


class ProfileStatusResponse(BaseModel):
    """Matches extension's ProfileStatus interface."""
    ok: bool = True
    artifact_counts: dict = Field(default_factory=dict)
    total_artifacts: int = 0
    last_sync: str | None = None


class SyncResponse(BaseModel):
    ok: bool
    message: str = ""
    fetched: int = 0
    imported: int = 0


class ListenerRow(BaseModel):
    """One (instance, listener) row for the extension's receive-path health
    indicator. Declarative + history fields come from the listener registry;
    `health_*` fields are merged from the per-instance heartbeat marker.
    `topic` is kept raw (`ntfy:<topic>?tags=<tag>`) — the extension parses it
    and owns the red/amber render logic.
    """
    instance_id: str
    name: str
    description: str = ""
    topic: str = ""
    wake_count: int = 0
    last_wake_at: str | None = None
    last_message: str | None = None
    registered_at: str | None = None
    health_status: str | None = None  # ok | degraded | None (no heartbeat yet)
    health_loop: str | None = None
    health_ts: str | None = None


class ListenersResponse(BaseModel):
    ok: bool = True
    listeners: list[ListenerRow] = Field(default_factory=list)


# ── FastAPI App ──────────────────────────────────────────────────────

def create_serve_app() -> FastAPI:
    """Create FastAPI app for the serve daemon."""

    app = FastAPI(
        title="Empirica Serve",
        description="Local daemon for Chrome extension integration",
        version="0.1.0",
    )

    # CORS: Allow chrome-extension:// and localhost origins.
    # NOTE: Starlette's `allow_origins` does exact-string match, NOT glob expansion.
    # The previous config (with "chrome-extension://*" as a literal) silently
    # rejected every real chrome-extension origin. Using `allow_origin_regex`
    # makes the intent work — confirmed by the E2E CORS preflight test.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(chrome-extension://.*|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?)$",
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # v0.5+ artifact endpoints (per-type lists, per spec docs/v0.5-LOCAL-ARTIFACTS.md)
    from empirica.api.routes.artifacts import router as artifacts_router
    app.include_router(artifacts_router)

    # Credential-grant flow (UI-prompted token, goal 167fc8d4) —
    # extracted into its own router so create_serve_app stays simple.
    from empirica.api.routes.credentials import router as credentials_router
    app.include_router(credentials_router)

    # Entity mint (workspace entity_registry write surface — idempotent
    # contact creation for same-box consumers like CRM MCP servers).
    from empirica.api.routes.entities import router as entities_router
    app.include_router(entities_router)

    @app.get("/api/v1/health", response_model=HealthResponse)
    async def health():  # pyright: ignore[reportUnusedFunction]
        """Health check — reports integrations, active project info, and
        the locally-known project registry (v1.9.6+)."""
        from empirica.api.daemon_project import get_cached_daemon_project
        from empirica.api.registry import list_known_projects
        project = get_cached_daemon_project() or {}
        return HealthResponse(
            ollama=_check_ollama(),
            qdrant=_check_qdrant(),
            project_id=project.get("project_id"),
            project_path=project.get("project_path"),
            project_name=project.get("project_name"),
            project_slug=project.get("project_slug"),
            repo_url=project.get("repo_url"),
            known_projects=list_known_projects(),
        )

    @app.post("/api/v1/artifacts/import", response_model=ArtifactImportResponse)
    async def import_artifacts(req: ArtifactImportRequest):  # pyright: ignore[reportUnusedFunction]
        """Import pre-extracted artifacts from the Chrome extension.

        The extension runs extraction client-side (TypeScript). This endpoint
        receives the results and stores them in the Empirica database.
        """
        try:
            result = _store_artifacts(req.artifacts)
            return ArtifactImportResponse(ok=True, **result)
        except Exception as e:
            logger.error(f"Import failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/v1/profile/status", response_model=ProfileStatusResponse)
    async def profile_status():  # pyright: ignore[reportUnusedFunction]
        """Get epistemic profile status — artifact counts and sync state."""
        try:
            result = _run_profile_status()
            return ProfileStatusResponse(ok=True, **result)
        except Exception as e:
            logger.error(f"Profile status failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/v1/profile/sync", response_model=SyncResponse)
    async def profile_sync():  # pyright: ignore[reportUnusedFunction]
        """Trigger profile sync (fetch notes, import to SQLite)."""
        try:
            result = _run_profile_sync()
            return SyncResponse(ok=True, **result)
        except Exception as e:
            logger.error(f"Profile sync failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/v1/listeners", response_model=ListenersResponse)
    async def listeners():  # pyright: ignore[reportUnusedFunction]
        """Registered mesh listeners + heartbeat freshness, merged from the
        on-disk registry + health markers. Lets the extension flag silent
        receive failures (seat alive but deaf) without reading ~/.empirica/
        directly. Read-only, localhost — same trust surface as /health."""
        return ListenersResponse(
            ok=True,
            listeners=[ListenerRow(**row) for row in _gather_listeners()],
        )

    _register_credentials_routes(app)

    return app


def _register_credentials_routes(app: FastAPI) -> None:
    """Register the cortex + ntfy credentials read/write endpoints.

    Extracted out of create_serve_app() to keep the parent function
    under the C901 complexity ceiling. Same threat model across both
    pairs: NEVER return the full secret over the wire — only a last-4
    preview — so even if CORS loosens later, the secret doesn't leak
    via a GET. Atomic write at the loader layer (tempfile + rename).
    """

    @app.post("/api/v1/credentials/cortex", response_model=CortexCredentialsResponse)
    async def set_cortex_credentials(  # pyright: ignore[reportUnusedFunction]
        req: CortexCredentialsRequest,
    ) -> CortexCredentialsResponse:
        """Write Cortex {url, api_key} into ~/.empirica/credentials.yaml.

        Companion to the extension's chrome.storage save — extension
        POSTs the user's entered creds here, daemon merges them into
        the existing `cortex:` block without touching other sections.

        At least one of url/api_key must be provided. Atomic write via
        tempfile + rename — never partial-corrupts the file."""
        if not req.url and not req.api_key:
            return CortexCredentialsResponse(
                ok=False, error="url or api_key required",
            )
        try:
            from empirica.config.credentials_loader import CredentialsLoader
            loader = CredentialsLoader()
            path = loader.save_cortex_config(
                url=req.url, api_key=req.api_key,
            )
            cfg = loader.get_cortex_config()
            key = cfg.get("api_key") or ""
            return CortexCredentialsResponse(
                ok=True,
                url=cfg.get("url"),
                api_key_set=bool(key),
                api_key_preview=f"...{key[-4:]}" if len(key) >= 4 else None,
                written_path=str(path),
            )
        except Exception as e:
            logger.error(f"set_cortex_credentials failed: {e}", exc_info=True)
            return CortexCredentialsResponse(ok=False, error=str(e))

    @app.get("/api/v1/credentials/cortex", response_model=CortexCredentialsResponse)
    async def get_cortex_credentials() -> CortexCredentialsResponse:  # pyright: ignore[reportUnusedFunction]
        """Read current Cortex creds from credentials.yaml (or env).

        Returns url + key-set flag + last-4-chars preview. NEVER returns
        the full key — exfiltration risk if CORS loosens in the future.
        Use for drift detection on the extension side (compare preview
        against the chrome.storage stored key)."""
        try:
            from empirica.config.credentials_loader import CredentialsLoader
            cfg = CredentialsLoader().get_cortex_config()
            key = cfg.get("api_key") or ""
            return CortexCredentialsResponse(
                ok=True,
                url=cfg.get("url"),
                api_key_set=bool(key),
                api_key_preview=f"...{key[-4:]}" if len(key) >= 4 else None,
            )
        except Exception as e:
            logger.error(f"get_cortex_credentials failed: {e}", exc_info=True)
            return CortexCredentialsResponse(ok=False, error=str(e))

    @app.post("/api/v1/credentials/ntfy", response_model=NtfyCredentialsResponse)
    async def set_ntfy_credentials(  # pyright: ignore[reportUnusedFunction]
        req: NtfyCredentialsRequest,
    ) -> NtfyCredentialsResponse:
        """Write ntfy {url, token} into ~/.empirica/credentials.yaml.

        Mirror of /credentials/cortex closing extension's round-trip
        credential model — extension's "Also save to CLI" toggle on the
        Notifications tab POSTs the user's ntfy bearer here, daemon
        merges into the existing `ntfy:` block via
        CredentialsLoader.save_ntfy_config (atomic tempfile+rename,
        preserves `topic` and other untouched keys).

        At least one of url/token must be provided. Topic is NOT on
        the request shape — cortex's channels endpoint owns topic
        derivation; this endpoint never touches it."""
        if not req.url and not req.token:
            return NtfyCredentialsResponse(
                ok=False, error="url or token required",
            )
        try:
            from empirica.config.credentials_loader import CredentialsLoader
            loader = CredentialsLoader()
            path = loader.save_ntfy_config(url=req.url, token=req.token)
            cfg = loader.get_ntfy_config()
            token_val = cfg.get("token") or ""
            return NtfyCredentialsResponse(
                ok=True,
                url=cfg.get("url"),
                topic=cfg.get("topic"),
                token_set=bool(token_val),
                token_preview=(
                    f"...{token_val[-4:]}" if len(token_val) >= 4 else None
                ),
                written_path=str(path),
            )
        except Exception as e:
            logger.error(f"set_ntfy_credentials failed: {e}", exc_info=True)
            return NtfyCredentialsResponse(ok=False, error=str(e))

    @app.get("/api/v1/credentials/ntfy", response_model=NtfyCredentialsResponse)
    async def get_ntfy_credentials() -> NtfyCredentialsResponse:  # pyright: ignore[reportUnusedFunction]
        """Read current ntfy creds from credentials.yaml (or env).

        Returns url + topic + token-set flag + last-4-chars preview.
        NEVER returns the full token — same exfiltration-risk threat
        model as the cortex creds endpoint. Use for drift detection on
        the extension side."""
        try:
            from empirica.config.credentials_loader import CredentialsLoader
            cfg = CredentialsLoader().get_ntfy_config()
            token_val = cfg.get("token") or ""
            return NtfyCredentialsResponse(
                ok=True,
                url=cfg.get("url"),
                topic=cfg.get("topic"),
                token_set=bool(token_val),
                token_preview=(
                    f"...{token_val[-4:]}" if len(token_val) >= 4 else None
                ),
            )
        except Exception as e:
            logger.error(f"get_ntfy_credentials failed: {e}", exc_info=True)
            return NtfyCredentialsResponse(ok=False, error=str(e))


# ── Internal Handlers ────────────────────────────────────────────────

def _config_ollama_url() -> str | None:
    """Read `embeddings.ollama_url` from ~/.empirica/config.yaml DIRECTLY.

    Deliberately does NOT import empirica.core.qdrant.embeddings — that module
    pulls in the openai SDK (~0.5s+ import) and this runs in the /health request
    hot path (the daemon must answer health fast, e.g. for the extension's poll
    + the e2e startup probe). Mirrors the embeddings resolver's config read
    (env-over-config is applied by the caller).
    """
    try:
        import yaml
        cfg_path = os.path.expanduser("~/.empirica/config.yaml")
        if not os.path.exists(cfg_path):
            return None
        with open(cfg_path, encoding="utf-8") as f:
            full = yaml.safe_load(f) or {}
        return (full.get("embeddings") or {}).get("ollama_url")
    except Exception:
        return None


def _resolve_ollama_url() -> str:
    """Ollama URL the same way embeddings resolves it: env > config.yaml
    embeddings.ollama_url > localhost. So serve health reflects the ACTUAL
    configured backend instead of false-negating on a remote-Ollama setup.
    """
    return os.environ.get(
        "EMPIRICA_OLLAMA_URL", _config_ollama_url() or "http://localhost:11434",
    ).rstrip("/")


def _resolve_qdrant_url() -> str:
    """Qdrant URL honoring EMPIRICA_QDRANT_URL (the same env `_get_qdrant_client`
    uses) before falling back to localhost — so remote-Qdrant setups don't
    false-negative in serve health.
    """
    return os.environ.get("EMPIRICA_QDRANT_URL", "http://localhost:6333").rstrip("/")


def _check_ollama() -> bool:
    """Check if Ollama is reachable at the configured ollama_url."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{_resolve_ollama_url()}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False


def _check_qdrant() -> bool:
    """Check if Qdrant is reachable at the configured qdrant_url."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{_resolve_qdrant_url()}/collections", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False


# Instance ids that are dev/test fixtures, not canonical mesh seats — skipped
# from the listeners endpoint so the extension's Diagnostics tab stays clean.
_FIXTURE_INSTANCE_IDS = frozenset({"test_instance", "smoke_test", "custom"})
_FIXTURE_INSTANCE_PREFIXES = ("tmux_",)


def _is_fixture_instance(instance_id: str) -> bool:
    return (
        instance_id in _FIXTURE_INSTANCE_IDS
        or any(instance_id.startswith(p) for p in _FIXTURE_INSTANCE_PREFIXES)
    )


def _gather_listeners() -> list[dict]:
    """Merge the on-disk listener registry + heartbeat-health files into a flat
    list of rows for the extension (which can't read ~/.empirica/ directly).

    One row per (instance, listener), built from
    ``~/.empirica/listeners_<inst>.json`` (declarative + history) with the
    ``health_*`` fields merged from ``~/.empirica/listener_health_<inst>.json``
    by instance. Dev/test fixtures are skipped. Read-only; never raises — a
    missing or malformed file is skipped, not fatal.
    """
    base = Path.home() / ".empirica"
    rows: list[dict] = []
    if not base.exists():
        return rows
    for reg_path in sorted(base.glob("listeners_*.json")):
        try:
            with open(reg_path, encoding="utf-8") as f:
                reg = json.load(f)
        except Exception:
            continue
        instance_id = reg.get("instance_id") or ""
        if not instance_id or _is_fixture_instance(instance_id):
            continue
        health: dict = {}
        hpath = base / f"listener_health_{instance_id}.json"
        if hpath.exists():
            try:
                with open(hpath, encoding="utf-8") as f:
                    health = json.load(f) or {}
            except Exception:
                health = {}
        for name, entry in (reg.get("listeners") or {}).items():
            entry = entry or {}
            rows.append({
                "instance_id": instance_id,
                "name": name,
                "description": entry.get("description", "") or "",
                "topic": entry.get("topic", "") or "",
                "wake_count": int(entry.get("wake_count", 0) or 0),
                "last_wake_at": entry.get("last_wake_at"),
                "last_message": entry.get("last_message"),
                "registered_at": entry.get("registered_at"),
                "health_status": health.get("status"),
                "health_loop": health.get("loop"),
                "health_ts": health.get("ts"),
            })
    return rows


def _store_artifacts(artifacts: list[ArtifactPayload]) -> dict:
    """Store pre-extracted artifacts in the Empirica database."""
    import uuid
    from datetime import datetime, timezone

    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    imported = 0
    duplicates_skipped = 0
    errors: list[str] = []

    for artifact in artifacts:
        artifact_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        content = artifact.content
        atype = artifact.type
        meta = artifact.metadata

        # Dedup by content hash if provided
        if artifact.contentHash:
            try:
                db.adapter.execute(
                    "SELECT id FROM project_findings WHERE finding = ? LIMIT 1",
                    (content,),
                )
                existing = db.adapter.fetchone()
                if existing:
                    duplicates_skipped += 1
                    continue
            except Exception:  # noqa: S110 — table schema may lack column; proceed with insert
                pass

        try:
            if atype == "finding":
                db.adapter.execute(
                    "INSERT INTO project_findings (id, project_id, session_id, finding, impact, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (artifact_id, "extension-import", None,
                     content, meta.get("impact", 0.5), now),
                )
            elif atype == "decision":
                db.adapter.execute(
                    "INSERT INTO project_findings (id, project_id, session_id, finding, impact, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (artifact_id, "extension-import", None,
                     f"[decision] {content}", meta.get("impact", 0.5), now),
                )
            elif atype == "dead_end":
                db.adapter.execute(
                    "INSERT INTO project_dead_ends (id, project_id, session_id, approach, why_failed, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (artifact_id, "extension-import", None,
                     content, meta.get("whyFailed", ""), now),
                )
            elif atype == "mistake":
                db.adapter.execute(
                    "INSERT INTO mistakes_made (id, project_id, session_id, mistake, why_wrong, prevention, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (artifact_id, "extension-import", None,
                     content, meta.get("whyFailed", ""), meta.get("prevention", ""), now),
                )
            elif atype == "unknown":
                db.adapter.execute(
                    "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (artifact_id, "extension-import", None,
                     content, now),
                )
            else:
                errors.append(f"Unknown artifact type: {atype}")
                continue

            imported += 1
        except Exception as e:
            errors.append(f"Failed to store {atype}: {e}")

    return {
        "imported": imported,
        "duplicates_skipped": duplicates_skipped,
        "errors": errors,
    }


def _run_profile_status() -> dict:
    """Get profile status — artifact counts from database."""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    counts: dict[str, int] = {}
    total = 0

    for table, label in [
        ("project_findings", "findings"),
        ("project_unknowns", "unknowns"),
        ("project_dead_ends", "dead_ends"),
        ("mistakes_made", "mistakes"),
        ("goals", "goals"),
    ]:
        try:
            db.adapter.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            row = db.adapter.fetchone()
            count = row["cnt"] if row else 0
            counts[label] = count
            total += count
        except Exception:
            counts[label] = 0

    return {
        "artifact_counts": counts,
        "total_artifacts": total,
    }


def _run_profile_sync() -> dict:
    """Run profile sync by invoking the existing sync logic."""
    import json
    import subprocess

    result = subprocess.run(
        ["empirica", "profile-sync", "--import-only", "--output", "json"],
        capture_output=True, text=True, timeout=60,
    )

    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return {
                "message": data.get("message", "Sync complete"),
                "fetched": data.get("fetched", 0),
                "imported": data.get("imported", 0),
            }
        except json.JSONDecodeError:
            return {"message": "Sync complete", "fetched": 0, "imported": 0}
    else:
        raise RuntimeError(f"Profile sync failed: {result.stderr[:200]}")
