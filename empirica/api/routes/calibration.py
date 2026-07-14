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


def _practice_has_open_transaction(practice_dir: Path | None) -> bool:
    """True when the practice has an OPEN transaction.

    A tuning override applied mid-transaction would shift the calibration signal
    under work already in flight, so the pane surfaces this to defer the change
    to the next transaction boundary (David's defer-to-boundary model, extension
    prop_kmnihczcx). Reads the workflow-owned ``active_transaction{suffix}.json``
    files at ``<practice>/.empirica/`` (same source the serve daemon + Sentinel
    firewall read). Best-effort; never raises — a read failure reports "no open
    transaction" (fail-open to the pane's default behavior)."""
    if practice_dir is None:
        return False
    try:
        import json as _json

        emp = Path(practice_dir) / ".empirica"
        for p in emp.glob("active_transaction*.json"):
            try:
                d = _json.loads(p.read_text())
            except Exception:
                continue
            if isinstance(d, dict) and d.get("status") == "open":
                return True
    except Exception:
        return False
    return False


def _effective(practice_id: str | None) -> dict[str, Any]:
    """Resolve the effective config: global override always applies; practice
    override layers on top when the practice_id resolves."""
    global_ov = cc.read_override(_global_dir())
    practice_ov: dict[str, Any] = {}
    practice_dir: Path | None = None
    if practice_id:
        practice_dir = _resolve_practice_dir(practice_id)
        if practice_dir is not None:
            practice_ov = cc.read_override(practice_dir)
    resolved = cc.resolve(global_ov, practice_ov)
    resolved["schema"] = cc.schema_json()
    # Two orthogonal preset axes (extension prop_aablfzw5): STANCE (how strictly
    # the practice gates — moves the gate meters) + PERSONA (domain focus — moves
    # the weight meters). resolve() already returns the effective `preset`
    # (persona) + `stance` names.
    resolved["presets"] = {"stance": sorted(cc.stance_names()), "persona": sorted(cc.preset_names())}
    resolved["overrides"] = {"global": global_ov, "practice": practice_ov}
    # active_transaction: true → the pane shows "applies at next boundary" and
    # (once A3 lands) a PATCH queues instead of applying live. Global scope has
    # no single practice, so it's always false there.
    resolved["active_transaction"] = _practice_has_open_transaction(practice_dir)
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
