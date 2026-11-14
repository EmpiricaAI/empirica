"""The Sentinel's mode and out-of-loop budget, read and set through the local daemon.

    GET /api/v1/sentinel/mode?project=<path or ai_id>
    PATCH /api/v1/sentinel/mode {mode?, max_act_without_gate?, scope: "practice"|"seat", project?}

The same answer as `empirica sentinel mode --output json`, built by the same function
(``sentinel_discipline.mode_report``), for the extension, which reaches only this daemon and Cortex
(prop_ycoxhq4nmzdrlhgjinyjei4sse; David's requirement that the budget be settable there, with the value in force and
its source shown). A PATCH writes the practice's ``.empirica/config.yaml`` or, with ``scope: "seat"``, ``~/.empirica/config.yaml``,
and answers with what is now in force; ``not_in_force`` names any value just written that an environment variable or a
higher-precedence file overrides.

``out_of_loop_acts_pending`` is null here: that ledger is kept per terminal instance, and the daemon has none.

An agent reaching this route runs a shell command, which the Sentinel gates like the CLI's own write; the route adds
no way around a transaction.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from empirica.api.entity_mint_auth import verify_mint_bearer
from empirica.core import sentinel_discipline as sd

router = APIRouter(prefix="/api/v1", tags=["sentinel"], dependencies=[Depends(verify_mint_bearer)])
logger = logging.getLogger(__name__)


class ModeUpdate(BaseModel):
    mode: str | None = None
    max_act_without_gate: int | None = None
    scope: Literal["practice", "seat"] = "practice"
    project: str | None = None


def _resolve_registered(key: str) -> Path | None:
    """A practice registered with the daemon, by project id, name or ai_id."""
    from empirica.api.routes.calibration import _resolve_practice_dir

    return _resolve_practice_dir(key)


def _practice(project: str | None) -> Path | None:
    """The practice root for ``project`` (a path or a registered id); None when no project is given.

    404 when one is given and it is not a practice: answering with the seat's values instead would show a setting for
    the wrong scope.
    """
    if not project:
        return None
    candidate = Path(project).expanduser()
    if not candidate.is_absolute() or not candidate.is_dir():
        # A relative path would resolve against the daemon's working directory: only an absolute one is a path here,
        # anything else is a registered id or name (1.16.3 sweep).
        candidate = _resolve_registered(project) or Path("/nonexistent")
    if not (candidate / ".empirica" / "project.yaml").is_file():
        raise HTTPException(status_code=404, detail=f"not a practice: {project}")
    return candidate


@router.get("/sentinel/mode")
def get_mode(project: str | None = Query(default=None)) -> dict:
    return sd.mode_report(_practice(project), pending=None)


@router.patch("/sentinel/mode")
def patch_mode(update: ModeUpdate) -> dict:
    if update.mode is None and update.max_act_without_gate is None:
        raise HTTPException(status_code=400, detail="nothing to set: give mode, max_act_without_gate or both")
    practice = _practice(update.project)
    if update.scope == "seat":
        target = Path.home() / ".empirica" / "config.yaml"
    elif practice is not None:
        target = practice / ".empirica" / "config.yaml"
    else:
        raise HTTPException(status_code=400, detail='scope "practice" needs a project (a path or ai_id)')
    try:
        sd.write_setting(target, mode=update.mode, budget=update.max_act_without_gate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    keys = tuple(
        k for k, v in (("mode", update.mode), ("max_act_without_gate", update.max_act_without_gate)) if v is not None
    )
    logger.info(f"sentinel mode written to {target}: {keys}")
    return sd.mode_report(practice, pending=None, written=target, written_keys=keys)
