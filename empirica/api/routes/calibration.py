"""Calibration-config endpoints — the settable epistemic weights + Sentinel
thresholds surfaced to the extension's "Sentinel Tuning" tab.

    GET   /api/v1/calibration/config?practice_id=<id>
    PATCH /api/v1/calibration/config?scope=global|practice&practice_id=<id>

GET returns the effective (global→practice-layered) config for a practice, plus
the field schema, preset names, and the raw per-scope override blocks so the UI
can show what's set where. PATCH validates a sparse body ({weights?, thresholds?,
preset?}; a null value resets a key) and writes it to the requested scope's
``.empirica/calibration.yaml``.
"""

import logging
import os
from pathlib import Path

from flask import Blueprint, jsonify, request

from empirica.core import calibration_config as cc

bp = Blueprint("calibration", __name__)
logger = logging.getLogger(__name__)

_DEBUG_MODE = os.environ.get("FLASK_DEBUG", "false").lower() == "true"


def _safe_error(error: Exception) -> str:
    return str(error) if _DEBUG_MODE else "An internal error occurred"


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


def _effective(practice_id: str | None) -> dict:
    """Resolve the effective config: global override always applies; practice
    override layers on top when the practice_id resolves."""
    global_ov = cc.read_override(_global_dir())
    practice_ov: dict = {}
    if practice_id:
        d = _resolve_practice_dir(practice_id)
        if d is not None:
            practice_ov = cc.read_override(d)
    resolved = cc.resolve(global_ov, practice_ov)
    resolved["schema"] = cc.schema_json()
    resolved["presets"] = sorted(cc.preset_names())
    resolved["overrides"] = {"global": global_ov, "practice": practice_ov}
    return resolved


@bp.route("/calibration/config", methods=["GET"])
def get_config():
    """Effective calibration config for a practice (or global-only if no id)."""
    try:
        practice_id = request.args.get("practice_id")
        return jsonify({"ok": True, **_effective(practice_id)})
    except Exception as e:
        logger.error(f"calibration GET failed: {e}", exc_info=True)
        return jsonify({"ok": False, "error": _safe_error(e)}), 500


@bp.route("/calibration/config", methods=["PATCH"])
def patch_config():
    """Write a sparse override to a scope's .empirica/calibration.yaml."""
    try:
        scope = request.args.get("scope", "practice")
        practice_id = request.args.get("practice_id")

        if scope == "global":
            scope_dir: Path | None = _global_dir()
        elif scope == "practice":
            if not practice_id:
                return jsonify({"ok": False, "error": "practice scope requires practice_id"}), 400
            scope_dir = _resolve_practice_dir(practice_id)
            if scope_dir is None:
                return jsonify({"ok": False, "error": f"unknown practice_id: {practice_id}"}), 404
        else:
            return jsonify({"ok": False, "error": f"invalid scope: {scope!r} (global|practice)"}), 400

        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "body must be a JSON object"}), 400

        clean, errors = cc.validate_patch(body)
        if errors:
            return jsonify({"ok": False, "error": "validation failed", "details": errors}), 422

        cc.apply_patch(scope_dir, clean)
        return jsonify({"ok": True, "scope": scope, **_effective(practice_id if scope == "practice" else None)})
    except Exception as e:
        logger.error(f"calibration PATCH failed: {e}", exc_info=True)
        return jsonify({"ok": False, "error": _safe_error(e)}), 500
