"""Engagements routes — the daemon HTTP family for the X2 board.

- GET   /api/v1/engagements          — EngagementMin list feed (CCR prop_kiamoy5z)
- POST  /api/v1/engagements          — create a ticket (CCR prop_lp2t5gph / C3)
- PATCH /api/v1/engagements/{id}      — triage: lifecycle/stage + metadata (C3)

The extension X2 board is a Chrome MV3 worker with no filesystem/sqlite access —
it speaks HTTP to the daemon only. GET feeds the board; POST is its create form;
PATCH is the metadata-update path mesh-support's triage (M1) needs.

Auth + loopback boundary are identical to the entities route (shared
``verify_mint_bearer`` dependency). Contract: mesh-support prop_lp2t5gph.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from empirica.api.entity_mint_auth import verify_mint_bearer

router = APIRouter(prefix="/api/v1", tags=["engagements"])

# Severity vocab (metadata, validated route-side — not a sidecar column).
_SEVERITY = frozenset({"critical", "high", "normal", "low"})


def _require_severity(severity: str | None) -> None:
    if severity is not None and severity not in _SEVERITY:
        raise HTTPException(
            status_code=422, detail=f"invalid severity {severity!r} — must be one of {sorted(_SEVERITY)}"
        )


@router.get("/engagements", dependencies=[Depends(verify_mint_bearer)])
async def list_engagements(
    org: str | None = Query(None, description="Filter to engagements ticket_of this organization id"),
    contact: str | None = Query(
        None,
        description="Filter to engagements this contact id actively participates in "
        "(engagement_contacts edge). Composes with `org` (AND) when both are given.",
    ),
    domain: str | None = Query(None, description="Filter by engagement domain (support, sales, ...)"),
    lifecycle: str | None = Query(
        None,
        description="Filter by lifecycle_state (planned, open, in_progress, blocked, closed), "
        "or `all` for the full set (Engagements-area fetch-everything).",
    ),
    include_closed: bool = Query(
        False,
        description="Legacy sugar — adds terminal (closed) engagements back to the feed. Default "
        "false — the feed is active-by-default {open, in_progress, blocked} (SER#183 part-2); "
        "pre-active `planned` stays out unless requested explicitly or via `lifecycle=all`. "
        "Ignored when an explicit `lifecycle` is given.",
    ),
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
            rows = repo.list_engagements(
                org_id=org,
                contact_id=contact,
                domain=domain,
                lifecycle_state=lifecycle,
                include_closed=include_closed,
                limit=limit,
            )
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
                    # Pass the WHOLE entity_registry.metadata bag through (severity,
                    # assignee, tickets[], identifier, tenant, machine_state, …) — no
                    # per-key allowlist — with the synthesized org_display layered on
                    # top (derived from the ticket_of edge, not stored in metadata;
                    # wins on any key collision).
                    "metadata": {**proj["metadata"], "org_display": proj["org_display"]},
                }
            )
    return {"ok": True, "count": len(out), "engagements": out}


def _synthesize_title(severity: str | None, org_display: str | None) -> str:
    """Support fallback (mesh-support convention) when the form omits title —
    title is NOT NULL at the schema level, so the endpoint must always set one."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{severity or 'support'} · {org_display or 'unassigned'} · {date}"


class EngagementCreateRequest(BaseModel):
    domain: str = Field(description="Engagement domain — one of the 6 (support, sales, …)")
    title: str | None = Field(default=None, description="Ticket title; synthesized if omitted (NOT NULL in schema)")
    stage: str | None = Field(default=None, description="Full stage_id; validated against the domain")
    lifecycle_state: str | None = Field(
        default=None, description="planned|open|in_progress|blocked|closed (default open)"
    )
    engagement_type: str = Field(default="support")
    description: str | None = None
    org: str | None = Field(default=None, description="Organization id — sets the ticket_of edge atomically")
    # Writable metadata bag — NO org_display (that's read-synthesized from ticket_of).
    severity: str | None = None
    assignee_display: str | None = None
    assignee_id: str | None = None


@router.post("/engagements", dependencies=[Depends(verify_mint_bearer)])
async def create_engagement(req: EngagementCreateRequest):
    """Create a ticket: the engagements sidecar row + the entity_registry row
    (display_name + writable metadata bag) + the ticket_of edge, atomically.
    ``title`` is NOT NULL — synthesized when the form omits it."""
    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    _require_severity(req.severity)
    eid = f"e-{uuid.uuid4().hex[:12]}"
    with WorkspaceDBRepository.open() as repo:
        # org_display for the synthesized title is READ from the org entity — never
        # stored on the engagement (it stays read-synthesized per the GET spec).
        org_display = None
        if req.org:
            row = repo._execute(
                "SELECT display_name FROM entity_registry WHERE entity_type = 'organization' AND entity_id = ?",
                (req.org,),
            ).fetchone()
            org_display = row["display_name"] if row else None
        title = req.title or _synthesize_title(req.severity, org_display)
        try:
            eng = repo.create_engagement(
                eid,
                title,
                domain=req.domain,
                stage=req.stage,
                engagement_type=req.engagement_type,
                description=req.description,
            )
            if req.lifecycle_state and req.lifecycle_state != "open":
                eng = repo.update_engagement(eid, lifecycle_state=req.lifecycle_state) or eng
        except ValueError as e:  # unknown domain/stage/lifecycle → 422
            raise HTTPException(status_code=422, detail=str(e)) from e

        meta = {
            k: v
            for k, v in {
                "severity": req.severity,
                "assignee_display": req.assignee_display,
                "assignee_id": req.assignee_id,
            }.items()
            if v is not None
        }
        repo.upsert_entity(
            "engagement",
            eid,
            display_name=title,
            source_db="workspace",
            source_table="engagements",
            metadata=json.dumps(meta) if meta else None,
        )
        if req.org:
            repo.upsert_entity_membership("engagement", eid, "organization", req.org, role="ticket_of")
    return {"ok": True, "engagement_id": eid, "engagement": eng}


class EngagementPatchRequest(BaseModel):
    lifecycle_state: str | None = None
    stage: str | None = None
    outcome: str | None = None
    title: str | None = None
    description: str | None = None
    # metadata triage bag (merge; org_display excluded — read-synthesized).
    severity: str | None = None
    assignee_display: str | None = None
    assignee_id: str | None = None


@router.patch("/engagements/{engagement_id}", dependencies=[Depends(verify_mint_bearer)])
async def patch_engagement(engagement_id: str, req: EngagementPatchRequest):
    """Triage: transition lifecycle/stage/outcome + merge the metadata bag on an
    existing engagement. 404 if it doesn't exist. This is the metadata-UPDATE
    path the board's triage (mesh-support M1) needs."""
    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    _require_severity(req.severity)
    with WorkspaceDBRepository.open() as repo:
        try:
            updated = repo.update_engagement(
                engagement_id,
                lifecycle_state=req.lifecycle_state,
                stage=req.stage,
                outcome=req.outcome,
                title=req.title,
                description=req.description,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if updated is None:
            raise HTTPException(status_code=404, detail=f"engagement {engagement_id!r} not found")
        patch = {
            k: v
            for k, v in {
                "severity": req.severity,
                "assignee_display": req.assignee_display,
                "assignee_id": req.assignee_id,
            }.items()
            if v is not None
        }
        if patch:
            repo.update_entity_metadata("engagement", engagement_id, patch)
    return {"ok": True, "engagement_id": engagement_id, "engagement": updated}


@router.get("/engagements/{engagement_id}/tasks", dependencies=[Depends(verify_mint_bearer)])
async def list_engagement_tasks(engagement_id: str):
    """List an engagement's tasks (workspace ``engagement_tasks``) for the board.

    Per row: task_id, title, description, status, assigned_to, due_at,
    completed_at, blocked_by, created_at (oldest first). Unknown/empty engagement
    → ``tasks: []`` (honest-empty; the board renders 0 rather than erroring).
    """
    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    with WorkspaceDBRepository.open() as repo:
        tasks = repo.get_engagement_tasks(engagement_id)
    return {"ok": True, "engagement_id": engagement_id, "count": len(tasks), "tasks": tasks}


# Source-link vocabulary. Deliberately NOT the entity_artifacts default 'about':
# a source attached to an engagement says something specific about direction —
# the engagement drew ON it, PRODUCED it, or CITED it.
_SOURCE_RELATIONSHIPS = frozenset({"sourced_from", "produced", "cited"})


class EngagementSourceLinkRequest(BaseModel):
    source_id: str = Field(..., description="epistemic_sources.id to attach")
    relationship: str = Field("sourced_from", description="sourced_from | produced | cited")
    relevance: float = Field(1.0, ge=0.0, le=1.0)
    artifact_source: str | None = Field(
        None, description="trajectory_path of the owning practice; defaults to the active project"
    )
    discovered_via: str | None = Field(None, description="cli | extension | agent | manual")


def _resolve_artifact_source(explicit: str | None) -> str:
    """Which practice owns the source. Explicit wins; otherwise the active project."""
    if explicit:
        return explicit
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        return str(R.project_path() or "")
    except Exception:
        return ""


@router.get("/engagements/{engagement_id}/sources", dependencies=[Depends(verify_mint_bearer)])
async def list_engagement_sources(engagement_id: str, limit: int = Query(50, ge=1, le=500)):
    """List the sources attached to an engagement.

    Unknown/empty engagement → ``sources: []`` (honest-empty, same contract as
    ``/tasks``: the board renders 0 rather than erroring). Note the empty here is
    genuinely "no links", not "looked in the wrong place" — the read is keyed on
    the engagement id the caller supplied, with no second scope to drift.
    """
    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    with WorkspaceDBRepository.open() as repo:
        links = [
            row
            for row in repo.get_entity_artifacts_by_entity("engagement", engagement_id, limit=limit)
            if row.get("artifact_type") == "source"
        ]
    return {"ok": True, "engagement_id": engagement_id, "count": len(links), "sources": links}


@router.post("/engagements/{engagement_id}/sources", dependencies=[Depends(verify_mint_bearer)])
async def attach_engagement_source(engagement_id: str, req: EngagementSourceLinkRequest):
    """Attach a source to an engagement — idempotent per source.

    **The uniqueness key is narrower than this route's vocabulary implies, and
    that is deliberate rather than hidden.** ``entity_artifacts`` enforces
    ``UNIQUE(artifact_type, artifact_id, entity_type, entity_id)`` — the
    relationship is an ATTRIBUTE of the one edge, not part of its identity. So an
    engagement can hold a given source once, not once per relationship.

    Goal 1aa0988d specified idempotency on ``(engagement_id, source_id,
    relationship)``, which the schema does not support. Rather than quietly
    delivering the narrower contract, a re-POST with a DIFFERENT relationship
    returns 409 naming the existing one, so the caller learns the model instead
    of wondering why nothing changed. Widening the key is workspace's call —
    ``entity_artifacts`` is their schema, not core's to assume.
    """
    if req.relationship not in _SOURCE_RELATIONSHIPS:
        raise HTTPException(
            status_code=422,
            detail=f"invalid relationship {req.relationship!r} — must be one of {sorted(_SOURCE_RELATIONSHIPS)}",
        )

    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    with WorkspaceDBRepository.open() as repo:
        for row in repo.get_entity_artifacts_by_entity("engagement", engagement_id, limit=500):
            if row.get("artifact_type") != "source" or row.get("artifact_id") != req.source_id:
                continue
            if row.get("relationship") == req.relationship:
                return {
                    "ok": True,
                    "engagement_id": engagement_id,
                    "link_id": row.get("id"),
                    "created": False,
                    "reason": "already_linked",
                }
            # Same source, different relationship. The unique key excludes
            # relationship, so the insert below would fail with a bare integrity
            # error — say WHY instead, and name what is already there.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"source {req.source_id} is already linked to engagement {engagement_id} as "
                    f"{row.get('relationship')!r}. entity_artifacts is unique on "
                    f"(artifact_type, artifact_id, entity_type, entity_id) — relationship is an "
                    f"attribute of the single edge, not part of its identity."
                ),
            )

        link_id = repo.add_entity_artifact(
            artifact_id=req.source_id,
            artifact_type="source",
            artifact_source=_resolve_artifact_source(req.artifact_source),
            entity_type="engagement",
            entity_id=engagement_id,
            relationship=req.relationship,
            relevance=req.relevance,
            discovered_via=req.discovered_via,
            engagement_id=engagement_id,
        )

    if not link_id:
        raise HTTPException(status_code=409, detail="link not created (integrity conflict)")
    return {"ok": True, "engagement_id": engagement_id, "link_id": link_id, "created": True}
