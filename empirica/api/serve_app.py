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

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Request/Response Models ──────────────────────────────────────────
# These mirror the TypeScript interfaces in empirica-client.ts

class HealthResponse(BaseModel):
    """Matches extension's HealthResponse interface.

    Includes the daemon's active-project info (v0.5+) so the extension can:
    - Match its dropdown's active project_id against the daemon's reported
      project_id to decide try-daemon-first vs Cortex-fallback dispatch.
    - Populate the project dropdown for Empirica-only users (no Cortex).
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


# ── FastAPI App ──────────────────────────────────────────────────────

def create_serve_app() -> FastAPI:
    """Create FastAPI app for the serve daemon."""

    app = FastAPI(
        title="Empirica Serve",
        description="Local daemon for Chrome extension integration",
        version="0.1.0",
    )

    # CORS: Allow chrome-extension:// and localhost origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "chrome-extension://*",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health", response_model=HealthResponse)
    async def health():  # pyright: ignore[reportUnusedFunction]
        """Health check — reports integrations + active project info."""
        from empirica.api.daemon_project import get_cached_daemon_project
        project = get_cached_daemon_project() or {}
        return HealthResponse(
            ollama=_check_ollama(),
            qdrant=_check_qdrant(),
            project_id=project.get("project_id"),
            project_path=project.get("project_path"),
            project_name=project.get("project_name"),
            project_slug=project.get("project_slug"),
            repo_url=project.get("repo_url"),
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

    return app


# ── Internal Handlers ────────────────────────────────────────────────

def _check_ollama() -> bool:
    """Check if Ollama is available locally."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False


def _check_qdrant() -> bool:
    """Check if Qdrant is available locally."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:6333/collections", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False


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
