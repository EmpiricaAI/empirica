"""Mesh sharing agreement mirror — derived-state cache of cortex's
authoritative `mesh_sharing_agreements` table into empirica's
`entity_registry`.

Cortex tenant.db is authoritative (every routing-decision read terminates
there). Empirica mirrors agreements as `entity_type='mesh_sharing_agreement'`
rows in workspace.db for narrative + discoverability surfaces (artifact
graph, dashboards, `empirica mesh-agreements list`). Sync is unidirectional:
empirica reads cortex, never writes back.

See docs/architecture/MESH_SHARING_AGREEMENTS.md (not in this repo) for the full model.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


ENTITY_TYPE = "mesh_sharing_agreement"
SOURCE_DB = "cortex"
SOURCE_TABLE = "mesh_sharing_agreements"

# Valid lifecycle states (mirrors cortex's CHECK constraint)
VALID_STATES = ("proposed", "active", "suspended", "revoked")

# Valid layer derivations
LAYER_L1 = "L1"  # same org + same tenant — should not appear (L1 doesn't need agreements)
LAYER_L2 = "L2"  # same org, different tenant
LAYER_L3 = "L3"  # different org


@dataclass(frozen=True)
class MeshSharingAgreement:
    """A bilateral admin-opt-in sharing contract between two parties.

    Mirrors cortex's row shape. `layer` is derived from the party pair
    (not stored on cortex side) for convenience in admission checks.
    """

    id: str
    party_a_org: str
    party_a_tenant: str | None  # None = org-wide
    party_b_org: str
    party_b_tenant: str | None
    state: str
    surfaces: list[str]
    direction: str  # 'a_to_b' / 'b_to_a' / 'bidirectional'
    eco_always: bool
    layer: str  # L2 or L3 (L1 doesn't need agreements)
    terms: dict[str, Any]
    created_at: float
    created_by_admin: str | None
    last_transition_at: float | None
    last_transition_actor: str | None
    expires_at: float | None

    @property
    def display_name(self) -> str:
        """Render a human-readable party-pair label for entity_registry."""
        a = self._party_label(self.party_a_org, self.party_a_tenant)
        b = self._party_label(self.party_b_org, self.party_b_tenant)
        return f"{a} ↔ {b}"

    @staticmethod
    def _party_label(org: str, tenant: str | None) -> str:
        return f"{org}.{tenant}" if tenant else org

    def to_metadata_json(self) -> str:
        """Serialize the row's metadata for entity_registry.metadata."""
        return json.dumps(
            {
                "party_a_org": self.party_a_org,
                "party_a_tenant": self.party_a_tenant,
                "party_b_org": self.party_b_org,
                "party_b_tenant": self.party_b_tenant,
                "surfaces_json": self.surfaces,
                "direction": self.direction,
                "eco_always": self.eco_always,
                "layer": self.layer,
                "terms_json": self.terms,
                "created_at": self.created_at,
                "created_by_admin": self.created_by_admin,
                "last_transition_at": self.last_transition_at,
                "last_transition_actor": self.last_transition_actor,
                "expires_at": self.expires_at,
            }
        )

    @classmethod
    def from_cortex_row(cls, row: dict[str, Any]) -> MeshSharingAgreement:
        """Build from cortex's GET /v1/orgs/me/mesh_sharing_agreements row.

        Handles two row shapes:
          - Current minimal shape (live as of 2026-06-03): party_a/party_b
            (user_id strings) + scope ('tenant_tenant' | 'org_org') + state +
            activated_at/revoked_at/initiator_user_id.
          - Future enriched shape (per cortex Q1 spec): party_a_org +
            party_a_tenant + party_b_org + party_b_tenant + surfaces +
            direction + eco_always + terms.

        Falls through to enriched shape when present; otherwise derives
        from the minimal shape with sensible defaults.
        """
        # Detect shape — enriched if explicit *_org fields present
        if "party_a_org" in row and "party_b_org" in row:
            party_a_org = row["party_a_org"]
            party_a_tenant = row.get("party_a_tenant")
            party_b_org = row["party_b_org"]
            party_b_tenant = row.get("party_b_tenant")
            layer = derive_layer(party_a_org, party_a_tenant, party_b_org, party_b_tenant)
        else:
            # Minimal shape: party_a/party_b are opaque user_id strings;
            # scope tells us layer. Treat user_id AS tenant identifier
            # until cortex enriches the response.
            party_a = row.get("party_a") or row.get("party_a_id")
            party_b = row.get("party_b") or row.get("party_b_id")
            if not party_a or not party_b:
                raise KeyError("party_a/party_b required")
            scope = row.get("scope", "").lower()
            if scope == "org_org":
                # party_a/party_b are org identifiers; no tenant narrowing
                party_a_org, party_a_tenant = str(party_a), None
                party_b_org, party_b_tenant = str(party_b), None
                layer = LAYER_L3
            else:
                # scope='tenant_tenant' (or unknown). Use a placeholder
                # org until cortex enriches the response; the user_id IS
                # the tenant identifier.
                party_a_org = row.get("party_a_org_id") or "cortex"
                party_b_org = row.get("party_b_org_id") or "cortex"
                party_a_tenant = str(party_a)
                party_b_tenant = str(party_b)
                layer = derive_layer(party_a_org, party_a_tenant, party_b_org, party_b_tenant)

        # Surfaces / direction / eco_always — defaults until cortex ships
        # the enriched fields
        surfaces = row.get("surfaces") or row.get("surfaces_json") or ["collab"]
        if isinstance(surfaces, str):
            try:
                surfaces = json.loads(surfaces)
            except (json.JSONDecodeError, TypeError):
                surfaces = [surfaces]
        terms = row.get("terms") or row.get("terms_json") or {}
        if isinstance(terms, str):
            try:
                terms = json.loads(terms)
            except (json.JSONDecodeError, TypeError):
                terms = {"raw": terms}

        # last_transition_at: use activated_at for active rows, revoked_at
        # for revoked rows, fall through to explicit field
        last_transition = row.get("last_transition_at") or row.get("revoked_at") or row.get("activated_at")
        last_transition_actor = (
            row.get("last_transition_actor") or row.get("revoked_by_user_id") or row.get("initiator_user_id")
        )

        return cls(
            id=row["id"],
            party_a_org=party_a_org,
            party_a_tenant=party_a_tenant,
            party_b_org=party_b_org,
            party_b_tenant=party_b_tenant,
            state=row.get("state", "proposed"),
            surfaces=list(surfaces),
            direction=row.get("direction", "bidirectional"),
            eco_always=bool(row.get("eco_always", layer == LAYER_L3)),
            layer=layer,
            terms=dict(terms),
            created_at=float(row.get("created_at", 0.0)),
            created_by_admin=row.get("created_by_admin") or row.get("initiator_user_id"),
            last_transition_at=last_transition,
            last_transition_actor=last_transition_actor,
            expires_at=row.get("expires_at"),
        )


def derive_layer(
    party_a_org: str,
    party_a_tenant: str | None,
    party_b_org: str,
    party_b_tenant: str | None,
) -> str:
    """Derive the layer (L1/L2/L3) from a party pair.

    L1 = same org + same tenant (shouldn't appear in agreements — already
         inside the privacy boundary)
    L2 = same org, different tenant
    L3 = different org

    See docs/architecture/MESH_SHARING_AGREEMENTS.md (not in this repo) for the full
    ceiling/floor mapping.
    """
    if party_a_org != party_b_org:
        return LAYER_L3
    if party_a_tenant != party_b_tenant:
        return LAYER_L2
    return LAYER_L1


@dataclass
class SyncResult:
    """Outcome of a sync run — what changed in the mirror."""

    added: int = 0
    updated: int = 0
    marked_revoked: int = 0
    error: str | None = None

    @property
    def total_seen(self) -> int:
        return self.added + self.updated

    def summary_line(self) -> str:
        if self.error:
            return f"sync failed: {self.error}"
        return f"sync ok: {self.added} added, {self.updated} updated, {self.marked_revoked} marked-revoked"


def fetch_agreements_from_cortex(
    cortex_url: str,
    api_key: str,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """GET /v1/orgs/me/mesh_sharing_agreements. Returns raw cortex rows.

    Raises urllib.error.HTTPError / URLError on transport failure. Callers
    should treat any failure as 'mirror not refreshed, keep stale state'.
    """
    url = f"{cortex_url.rstrip('/')}/v1/orgs/me/mesh_sharing_agreements"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, list):
        return payload
    return list(payload.get("agreements") or payload.get("items") or [])


def sync_from_cortex(
    repo,
    cortex_url: str,
    api_key: str,
    *,
    fetcher=fetch_agreements_from_cortex,
) -> SyncResult:
    """Sync agreements from cortex into the workspace.db entity_registry.

    For each agreement in cortex's response: upsert into entity_registry
    keyed by (entity_type='mesh_sharing_agreement', entity_id=agr_id).
    For each row in our mirror that is NOT in cortex's response: mark
    status='revoked' locally (canonical "removed from cortex" signal).

    Args:
        repo: WorkspaceDBRepository — anything with `upsert_entity`,
            `list_entities`, and `mark_entity_status` methods.
        cortex_url: Cortex base URL (e.g. https://cortex.getempirica.com).
        api_key: Bearer token for the tenant.
        fetcher: Override for the cortex fetch — injected for testing.

    Returns:
        SyncResult with counts. On transport failure, `error` is set and
        the mirror is unchanged.
    """
    result = SyncResult()

    try:
        rows = fetcher(cortex_url, api_key)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        result.error = f"fetch failed: {exc}"
        logger.warning("mesh-agreements sync: %s", result.error)
        return result

    # Build set of ids cortex returned — anything in mirror not in this set
    # gets marked revoked locally.
    seen_ids: set[str] = set()
    for row in rows:
        try:
            agr = MeshSharingAgreement.from_cortex_row(row)
        except (KeyError, ValueError) as exc:
            logger.warning(
                "mesh-agreements sync: skipping malformed row %r: %s",
                row.get("id", "?"),
                exc,
            )
            continue
        seen_ids.add(agr.id)

        existing = repo.get_entity(ENTITY_TYPE, agr.id)
        repo.upsert_entity(
            entity_type=ENTITY_TYPE,
            entity_id=agr.id,
            display_name=agr.display_name,
            source_db=SOURCE_DB,
            source_table=SOURCE_TABLE,
            description=str(agr.terms.get("description") or "") or None,
            status=agr.state if agr.state in VALID_STATES else "proposed",
            metadata=agr.to_metadata_json(),
        )
        if existing is None:
            result.added += 1
        else:
            result.updated += 1

    # Mark-revoked sweep: anything previously mirrored but not in this response.
    existing_rows = repo.list_entities(entity_type=ENTITY_TYPE, status="all", limit=10000)
    for existing in existing_rows:
        if existing["entity_id"] in seen_ids:
            continue
        if existing.get("status") == "revoked":
            continue  # already revoked, no-op
        if repo.mark_entity_status(ENTITY_TYPE, existing["entity_id"], "revoked"):
            result.marked_revoked += 1

    return result


def is_agreement_active(
    repo,
    party_a_org: str,
    party_a_tenant: str | None,
    party_b_org: str,
    party_b_tenant: str | None,
    surface: str | None = None,
) -> bool:
    """Check whether an active agreement exists between two parties.

    Mirror-side admission check — DOES NOT replace cortex's authoritative
    enforcement. Used by empirica's --visibility ladders-into-agreement
    decision at write time (warns + downgrades on negative; cortex still
    enforces on the wire).

    Args:
        repo: WorkspaceDBRepository.
        party_a_org / party_a_tenant: First party (tenant=None matches org-wide).
        party_b_org / party_b_tenant: Second party.
        surface: Optional surface (e.g. 'collab', 'eco'). When provided,
            requires the agreement's `surfaces_json` to include this surface
            (or 'all'). When None, any agreement state matches.

    Returns:
        True if an active agreement between the parties (in either
        direction) exists locally. Pairs that are L1 (same org + same
        tenant) always return True — no agreement needed for local
        traffic.
    """
    layer = derive_layer(party_a_org, party_a_tenant, party_b_org, party_b_tenant)
    if layer == LAYER_L1:
        return True

    rows = repo.list_entities(entity_type=ENTITY_TYPE, status="active", limit=1000)
    for row in rows:
        try:
            meta = json.loads(row.get("metadata") or "{}")
        except json.JSONDecodeError:
            continue
        if not _parties_match(meta, party_a_org, party_a_tenant, party_b_org, party_b_tenant):
            continue
        if surface is not None:
            surfaces = meta.get("surfaces_json") or []
            if "all" not in surfaces and surface not in surfaces:
                continue
        return True
    return False


def _parties_match(
    meta: dict[str, Any],
    party_a_org: str,
    party_a_tenant: str | None,
    party_b_org: str,
    party_b_tenant: str | None,
) -> bool:
    """Check if metadata's parties match the query pair (either direction)."""
    pa_org, pa_tenant = meta.get("party_a_org"), meta.get("party_a_tenant")
    pb_org, pb_tenant = meta.get("party_b_org"), meta.get("party_b_tenant")
    forward = (
        pa_org == party_a_org and pa_tenant == party_a_tenant and pb_org == party_b_org and pb_tenant == party_b_tenant
    )
    reverse = (
        pa_org == party_b_org and pa_tenant == party_b_tenant and pb_org == party_a_org and pb_tenant == party_a_tenant
    )
    return forward or reverse
