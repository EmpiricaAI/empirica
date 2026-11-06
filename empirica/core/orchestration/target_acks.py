"""Per-target acknowledgement for multi-target proposals.

A proposal addressed to several practices used to turn terminal on the FIRST completion call, closing the
others' work for everyone. Now each target acknowledges for itself and the proposal turns terminal only once
every target in ``target_claudes`` has. A single-target proposal is untouched: its one completion is its
acknowledgement.

REST ``handle_complete`` and MCP ``cortex_complete_proposal`` both call :func:`ack_or_complete`, so the rule
lives here once.

The call sequence is synchronous on purpose. ``store.update`` writes the whole payload, so an ack read, merged
and written across an ``await`` could revert a concurrent ack from another target; with no await between
``store.get`` and ``store.update`` the event loop cannot interleave them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from empirica.core.orchestration.audit import AuditEntry, append_audit
from empirica.core.orchestration.leak_guard import redact

# When targets disagree the proposal's overall result is the least good one: a failure is never hidden by a
# success elsewhere. The per-target detail stays in ``target_acks``.
_SEVERITY = {"shipped": 0, "wont_fix": 1, "failed": 2}


@dataclass
class AckOutcome:
    """What the caller does next.

    ``error`` set: refuse with ``status_code``. ``terminal``: apply the proposal-wide result below via the
    usual terminal path. Neither: the ack is recorded and the proposal stays open; ``payload`` is the reply.
    """

    error: str | None = None
    status_code: int = 400
    terminal: bool = False
    result: str | None = None
    note: str | None = None
    commit_sha: str | None = None
    artifact_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


def aggregate_result(acks: dict[str, dict[str, Any]]) -> str:
    return max((a["result"] for a in acks.values()), key=lambda r: _SEVERITY[r])


def _candidate_targets(proposal, tenant, tenant_db) -> list[str]:
    """Targets of ``proposal`` whose owning seat is the caller."""
    uid = getattr(tenant, "user_id", None)
    out = []
    for target in proposal.distinct_targets():
        try:
            owners = tenant_db.find_users_for_ai_id(target)
        except Exception:
            owners = []
        if any(getattr(u, "id", None) == uid for u in owners):
            out.append(target)
    return out


def resolve_acking_target(proposal, tenant, tenant_db, claimed: str | None) -> tuple[str | None, AckOutcome | None]:
    """Which target is the caller acknowledging for? Returns ``(target, None)`` or ``(None, refusal)``."""
    mine = _candidate_targets(proposal, tenant, tenant_db)
    claimed = (claimed or "").strip() or None
    if claimed:
        if claimed not in proposal.distinct_targets():
            return None, AckOutcome(
                error="ai_id is not a target of this proposal",
                status_code=400,
                extra={"targets": proposal.distinct_targets()},
            )
        if claimed not in mine:
            return None, AckOutcome(
                error="ai_id is a target of this proposal but not one of the calling seat's practices",
                status_code=403,
                extra={"targets": proposal.distinct_targets()},
            )
        return claimed, None
    if len(mine) == 1:
        return mine[0], None
    if not mine:
        return None, AckOutcome(
            error="the calling seat is not a target of this multi-target proposal",
            status_code=403,
            extra={"targets": proposal.distinct_targets()},
        )
    return None, AckOutcome(
        error="this seat owns several targets of this proposal: pass ai_id to say which one you are acknowledging",
        status_code=400,
        extra={"candidates": mine},
    )


def ack_or_complete(
    proposal,
    store,
    tenant,
    tenant_db,
    *,
    claimed_ai_id: str | None,
    result: str,
    note: str | None,
    commit_sha: str | None,
    artifact_ids: list[str],
    actor: str,
) -> AckOutcome | None:
    """Handle a completion call on a multi-target proposal. Returns None for a single-target one (the caller
    completes it as before)."""
    if not proposal.is_multi_target():
        return None
    target, refusal = resolve_acking_target(proposal, tenant, tenant_db, claimed_ai_id)
    if refusal is not None:
        refusal.payload = {"proposal_id": proposal.id, **refusal.extra}
        return refusal

    acks = dict(proposal.target_acks or {})
    if target in acks:
        return AckOutcome(
            payload={
                "ok": True,
                "proposal_id": proposal.id,
                "status": proposal.status,
                "ai_id": target,
                "target_acks": acks,
                "pending_targets": proposal.pending_targets(),
                "note": "already_acked",
            }
        )

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    note = redact(note)
    acks[target] = {
        "result": result,
        "note": note,
        "commit_sha": commit_sha,
        "artifact_ids": list(artifact_ids),
        "actor": actor,
        "at": now_iso,
    }
    proposal.target_acks = acks
    proposal.audit_log = append_audit(
        proposal.audit_log,
        AuditEntry(
            action="target_acked",
            actor=actor,
            note=note,
            details={
                "ai_id": target,
                "result": result,
                "commit_sha": commit_sha,
                "artifact_ids": artifact_ids,
                "pending": proposal.owed_targets(),
            },
        ),
    )

    # owed_targets, not pending_targets: the latter is a display rule and is empty for collab briefs.
    if proposal.owed_targets():
        store.update(proposal)
        return AckOutcome(
            payload={
                "ok": True,
                "proposal_id": proposal.id,
                "status": proposal.status,
                "ai_id": target,
                "target_acks": acks,
                "pending_targets": proposal.pending_targets(),
                "note": "acked_waiting_for_other_targets",
            }
        )

    notes = [f"{t}: {a['note']}" for t, a in acks.items() if a.get("note")]
    shas = [a["commit_sha"] for a in acks.values() if a.get("commit_sha")]
    arts = list(dict.fromkeys(i for a in acks.values() for i in a.get("artifact_ids", [])))
    return AckOutcome(
        terminal=True,
        result=aggregate_result(acks),
        note="\n".join(notes) or None,
        commit_sha=shas[0] if shas else None,
        artifact_ids=arts,
        payload={"target_acks": acks, "pending_targets": [], "ai_id": target},
    )
