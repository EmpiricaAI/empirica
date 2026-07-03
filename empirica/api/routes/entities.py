"""Entity mint endpoint — daemon HTTP surface over the workspace entity registry.

POST /api/v1/entities mints a contact into workspace.db entity_registry,
idempotently: re-creating with the same identity (email first, then the
deterministic name/company slug) returns the existing entity_id with
created=false. The returned id is the canonical contact_id that external
consumers (e.g. a CRM MCP server on the same box) carry as their FK and
that the knowledge-graph traversal resolves.

On a loopback daemon no bearer auth is enforced (transport security is the
loopback boundary, consistent with the other /api/v1 routes). When the per-org
daemon binds non-loopback (the hosted deployment), the route is guarded by a
service-token bearer — see ``empirica.api.entity_mint_auth``. The guard is a
route dependency, so the mint contract body is unchanged; auth rides the
``Authorization`` header (401 on missing/invalid token).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from empirica.api.entity_mint_auth import verify_mint_bearer

router = APIRouter(prefix="/api/v1", tags=["entities"])


class EntityCreateRequest(BaseModel):
    type: str = Field(description="Entity type — v1 mints contacts only")
    name: str = Field(min_length=1, description="Contact display name")
    email: str | None = None
    phone: str | None = None
    role: str | None = None
    company_name: str | None = None
    description: str | None = None
    metadata: dict | None = Field(
        default=None,
        description="Extra metadata merged into the registry row",
    )


@router.post("/entities", dependencies=[Depends(verify_mint_bearer)])
async def create_entity(req: EntityCreateRequest):
    """Idempotent contact mint. Returns the canonical entity_id.

    Response: ``{ok, entity_id, created, matched_by}`` — ``created`` is
    false when the identity resolved to an existing row (verified no-op).
    """
    if req.type != "contact":
        raise HTTPException(
            status_code=422,
            detail=f"entity create v1 mints contacts only (got {req.type!r}). "
            "Other entity types are written by their owning pipelines.",
        )

    from empirica.cli.command_handlers.entity_commands import mint_contact

    result = mint_contact(
        name=req.name,
        email=req.email,
        phone=req.phone,
        role=req.role,
        company_name=req.company_name,
        description=req.description,
        extra_metadata=req.metadata,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "mint failed"))
    return result


def _parse_metadata(raw: str | None) -> dict:
    """Parse a registry row's metadata TEXT column into a dict.

    Returns ``{}`` on absence or malformed JSON — the list projection must
    never 500 on a single garbage row.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list_subtitle(row: dict, meta: dict) -> str | None:
    """Cheap, projection-bound subtitle — no per-row queries.

    organization → domain; contact → company_name or role; engagement → status.
    Membership-transitive enrichment (e.g. engagement → org name) is deferred
    to v1.1 to keep the list endpoint single-query.
    """
    et = row.get("entity_type")
    if et == "organization":
        return meta.get("domain")
    if et == "contact":
        return meta.get("company_name") or meta.get("role")
    if et == "engagement":
        return row.get("status")
    return None


@router.get("/entities", dependencies=[Depends(verify_mint_bearer)])
async def list_entities(
    type: str | None = Query(None, description="Filter by entity_type (contact, organization, engagement, ...)"),
    status: str = Query("active", description="Status filter; 'all' returns every status"),
    parent_org: str | None = Query(
        None,
        description="Scope to CONTACTS affiliated with this organization id (active affiliation). "
        "Implies a contact scope; an unknown org returns []. Backed by entity_memberships.",
    ),
    limit: int = Query(100, ge=1, le=500),
):
    """List entities from the workspace registry (extension entity-list backing).

    Projection per the converged ERM contract: ``id``/``type``/``name`` are
    always present; ``subtitle``/``status``/``health``/``linked_artifact_count``/
    ``updated_at`` are projection-bound. ``health`` reads ``metadata.health``
    (decision A — promoted to a spine column later only if queried on).
    """
    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    out: list[dict] = []
    with WorkspaceDBRepository.open() as repo:
        # Org→org parentage for the org-tree render (extension). One query for
        # the whole org set (small: umbrella + brands), not per-row — preserves
        # the single-query intent that deferred the broader v1.1 enrichment.
        org_parents = repo.get_org_parent_map()
        # Contact→org affiliation (id + name + role) + richer CRM detail fields.
        # Same entity_memberships source as the parent_org filter, so filter and
        # enrichment agree.
        contact_org_details = repo.get_contact_org_details_map()
        contact_details = repo.get_contact_detail_map()
        # Manager (reports_to edge) → the extension Profile's "Reports-to" row.
        contact_reports_to = repo.get_contact_reports_to_map()
        for row in repo.list_entities(entity_type=type, status=status, parent_org=parent_org, limit=limit):
            et, eid = row["entity_type"], row["entity_id"]
            meta = _parse_metadata(row.get("metadata"))
            entry = {
                "id": eid,
                "type": et,
                "name": row.get("display_name"),
                "subtitle": _list_subtitle(row, meta),
                "status": row.get("status"),
                "health": meta.get("health"),
                "linked_artifact_count": repo.count_entity_artifacts(et, eid),
                "updated_at": row.get("updated_at"),
            }
            # parent_org_id carries the entity's owning org so the extension
            # tree/drill can key off it: org rows → parent ORG (umbrella, null for
            # roots); contact rows → affiliated org. Both resolve via
            # entity_memberships; other types omit the field.
            if et == "organization":
                entry["parent_org_id"] = org_parents.get(eid)
            elif et == "contact":
                cod = contact_org_details.get(eid) or {}
                cd = contact_details.get(eid) or {}
                entry["parent_org_id"] = cod.get("org_id")
                entry["parent_org_name"] = cod.get("org_name")
                entry["role"] = cod.get("role")
                entry["email"] = cd.get("email")
                entry["phone"] = cd.get("phone")
                entry["title"] = cd.get("title")
                entry["tags"] = cd.get("tags")
                entry["notes"] = cd.get("notes")
                entry["contact_type"] = cd.get("contact_type")
                entry["lifecycle_stage"] = cd.get("lifecycle_stage")
                # tier lives in the registry metadata (already parsed into meta);
                # reporting_to_name resolves the reports_to edge → manager's name.
                entry["tier"] = meta.get("tier")
                entry["reporting_to_name"] = contact_reports_to.get(eid)
            out.append(entry)
    return {"ok": True, "count": len(out), "entities": out}


@router.get("/entities/{entity_id}/artifacts", dependencies=[Depends(verify_mint_bearer)])
async def list_entity_artifacts(
    entity_id: str,
    type: str | None = Query(
        None, description="Optional entity_type to disambiguate (contact, organization, engagement, ...)"
    ),
    limit: int = Query(100, ge=1, le=500),
):
    """Scoped artifacts for a single entity (canonical-model Gap B).

    Returns the entity's DIRECT ``entity_artifacts`` pointers, newest first, each
    carrying ``artifact_type`` + ``artifact_source`` (§5 — so the caller can
    resolve + render the artifact). Unknown / empty entity → ``artifacts: []``
    (honest-empty; the board renders 0 rather than the old 404). Membership-
    transitive scoping (an entity's members' artifacts) is a deferred v2 pending
    the §5 relation/direction.
    """
    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    with WorkspaceDBRepository.open() as repo:
        artifacts = repo.get_artifacts_for_entity(entity_id, entity_type=type, limit=limit)
    return {"ok": True, "entity_id": entity_id, "count": len(artifacts), "artifacts": artifacts}
