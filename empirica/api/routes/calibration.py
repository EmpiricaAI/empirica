"""Calibration-config endpoints — the settable epistemic weights + Sentinel
thresholds surfaced to the extension's "Sentinel Tuning" tab.

    GET   /api/v1/calibration/config?practice_id=<id>
    PATCH /api/v1/calibration/config?scope=global|practice&practice_id=<id>

GET returns the effective (global→practice-layered) config for a practice, plus
the field schema, preset names, and the raw per-scope override blocks so the UI
can show what's set where. PATCH validates a sparse body ({weights?, thresholds?,
preset?}; a null value resets a key) and writes it to the requested scope's
``.empirica/calibration.yaml``.

This is a **FastAPI router** mounted in ``serve_app.py`` — the app ``empirica
serve`` actually runs — mirroring entities.py / engagements.py (APIRouter
prefix=/api/v1, ``verify_mint_bearer`` dep). It previously lived as a Flask
blueprint in the separate ``api/app.py``, which the daemon does NOT run, so
``GET /api/v1/calibration/config`` 404'd on the running daemon (the extension's
Sentinel Config tab showed "config API pending").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from empirica.api.entity_mint_auth import verify_mint_bearer
from empirica.core import calibration_config as cc

router = APIRouter(prefix="/api/v1", tags=["calibration"], dependencies=[Depends(verify_mint_bearer)])
logger = logging.getLogger(__name__)


def _global_dir() -> Path:
    return Path.home()


def _resolve_practice_dir(practice_id: str) -> Path | None:
    """Resolve a practice_id (project_id / name / ai_id) to its project dir via
    the daemon registry. Returns None if unresolved."""
    try:
        from empirica.api.registry import load_registry

        reg = load_registry()
    except Exception as e:
        logger.debug(f"calibration: registry load failed: {e}")
        return None
    for proj in reg.get("projects", []):
        if not isinstance(proj, dict):
            continue
        ids = {str(proj.get(k)) for k in ("project_id", "id", "name", "ai_id") if proj.get(k)}
        if practice_id in ids:
            path = proj.get("path") or proj.get("root") or proj.get("project_path") or proj.get("realpath")
            return Path(path) if path else None
    return None


def _effective(practice_id: str | None) -> dict[str, Any]:
    """Resolve the effective config: global override always applies; practice
    override layers on top when the practice_id resolves."""
    global_ov = cc.read_override(_global_dir())
    practice_ov: dict[str, Any] = {}
    if practice_id:
        d = _resolve_practice_dir(practice_id)
        if d is not None:
            practice_ov = cc.read_override(d)
    resolved = cc.resolve(global_ov, practice_ov)
    resolved["schema"] = cc.schema_json()
    resolved["presets"] = sorted(cc.preset_names())
    resolved["overrides"] = {"global": global_ov, "practice": practice_ov}
    return resolved


@router.get("/calibration/config")
async def get_config(practice_id: str | None = Query(None)):
    """Effective calibration config for a practice (or global-only if no id)."""
    try:
        return {"ok": True, **_effective(practice_id)}
    except Exception as e:
        logger.error(f"calibration GET failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="calibration read failed") from e


@router.patch("/calibration/config")
async def patch_config(
    body: dict[str, Any] | None = Body(default=None),
    scope: str = Query("practice"),
    practice_id: str | None = Query(None),
):
    """Write a sparse override to a scope's .empirica/calibration.yaml."""
    if scope == "global":
        scope_dir: Path | None = _global_dir()
    elif scope == "practice":
        if not practice_id:
            raise HTTPException(status_code=400, detail="practice scope requires practice_id")
        scope_dir = _resolve_practice_dir(practice_id)
        if scope_dir is None:
            raise HTTPException(status_code=404, detail=f"unknown practice_id: {practice_id}")
    else:
        raise HTTPException(status_code=400, detail=f"invalid scope: {scope!r} (global|practice)")

    # FastAPI already coerces/validates the body to a dict (422 otherwise); a
    # missing body is tolerated as an empty patch.
    clean, errors = cc.validate_patch(body or {})
    if errors:
        raise HTTPException(status_code=422, detail={"error": "validation failed", "details": errors})

    try:
        cc.apply_patch(scope_dir, clean)
    except Exception as e:
        logger.error(f"calibration PATCH failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="calibration write failed") from e
    return {"ok": True, "scope": scope, **_effective(practice_id if scope == "practice" else None)}
