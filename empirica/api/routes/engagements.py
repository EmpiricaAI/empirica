"""Engagements list endpoint — daemon HTTP feed for the X2 board.

GET /api/v1/engagements?org=&domain=&lifecycle= returns the EngagementMin
projection per row: the engagement sidecar fields (stage / lifecycle / domain /
outcome), counts (member / goal / linked-artifact), and synthesized metadata
(org_display via the ticket_of edge + severity/assignee pass-through). The
extension X2 board is a Chrome MV3 worker with no filesystem/sqlite access — it
speaks HTTP to the daemon only, and its listEngagements already hits this route.

Auth + loopback boundary are identical to the entities route (shared
``verify_mint_bearer`` dependency). CCR prop_kiamoy5z, ratified by David.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from empirica.api.entity_mint_auth import verify_mint_bearer

router = APIRouter(prefix="/api/v1", tags=["engagements"])


@router.get("/engagements", dependencies=[Depends(verify_mint_bearer)])
async def list_engagements(
    org: str | None = Query(None, description="Filter to engagements ticket_of this organization id"),
    domain: str | None = Query(None, description="Filter by engagement domain (support, sales, ...)"),
    lifecycle: str | None = Query(None, description="Filter by lifecycle_state (open, in_progress, blocked, closed)"),
    limit: int = Query(100, ge=1, le=500),
):
    """List engagements as EngagementMin[] for the board daemon feed.

    Per row: sidecar fields (id, title, engagement_type, domain, stage,
    lifecycle_state, status, outcome, started_at, ended_at, updated_at), counts
    (member_count, goal_count, linked_artifact_count), and a ``metadata`` object
    (org_display synthesized via the ticket_of edge + pass-through severity /
    assignee_id / assignee_display from the engagement entity's registry
    metadata).
    """
    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    out: list[dict] = []
    with WorkspaceDBRepository.open() as repo:
        try:
            rows = repo.list_engagements(org_id=org, domain=domain, lifecycle_state=lifecycle, limit=limit)
        except ValueError as e:
            # invalid lifecycle_state — surface as a 422, never a 500.
            raise HTTPException(status_code=422, detail=str(e)) from e
        for e in rows:
            eid = e["engagement_id"]
            proj = repo.get_engagement_projection(eid)
            out.append(
                {
                    "id": eid,
                    "title": e.get("title"),
                    "engagement_type": e.get("engagement_type"),
                    "domain": e.get("domain"),
                    "stage": e.get("stage"),
                    "lifecycle_state": e.get("lifecycle_state"),
                    "status": e.get("status"),
                    "outcome": e.get("outcome"),
                    "started_at": e.get("started_at"),
                    "ended_at": e.get("ended_at"),
                    "updated_at": e.get("updated_at"),
                    "member_count": proj["member_count"],
                    "goal_count": proj["goal_count"],
                    "linked_artifact_count": proj["linked_artifact_count"],
                    "metadata": {
                        "org_display": proj["org_display"],
                        "severity": proj["severity"],
                        "assignee_id": proj["assignee_id"],
                        "assignee_display": proj["assignee_display"],
                    },
                }
            )
    return {"ok": True, "count": len(out), "engagements": out}
