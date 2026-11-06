"""Append-only audit log for orchestration proposals.

Each action on a proposal (create, acknowledge, respond, accept, reject,
reclassify, expire, dispatch, webhook-received) appends an AuditEntry to the
proposal's payload audit_log_json list. Entries are never mutated or deleted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class AuditEntry:
    """One row in a proposal's audit log."""

    action: str  # "created", "acknowledged", "accepted", "rejected", "reclassified", "dispatched", "webhook_received", "expired"
    actor: str  # "eco", "<claude-id>", "system", "autonomy"
    at: str = field(default_factory=_iso_now)
    note: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def append_audit(audit_log: list[dict[str, Any]], entry: AuditEntry) -> list[dict[str, Any]]:
    """Return a new list with the entry appended.

    We return a new list (not mutate in place) to make the append-only contract
    explicit — callers should rebind the field, never push into the old list.
    """
    return [*audit_log, entry.to_dict()]


def created_entry(actor: str, note: str | None = None, **details: Any) -> AuditEntry:
    """Convenience: audit entry for proposal creation."""
    return AuditEntry(action="created", actor=actor, note=note, details=details)


def trust_decision_entry(
    *,
    target: str,
    level: str,
    score: float,
    mode: str,
    routed_to: str,
) -> AuditEntry:
    """Audit entry recording a trust decision from the autonomy triage path.

    Records what ``trust_bridge.get_trust`` returned — either autonomy's
    real verdict (when ``ORCHESTRATION_AUTONOMY_URL`` is set) or the
    CONTROLLER_FALLBACK (autonomy unreachable / URL unset → everything
    ECO-gates). Actor is ``"autonomy-triage"`` in both cases — the
    controller-fallback IS an autonomy-triage decision (the intentional
    fail-safe posture).

    Renamed from ``stub_trust_decision_entry`` 2026-07-15 (goal 9972f5ae).
    Trust bridge shipped with real autonomy integration in T2b; the
    ``autonomy_stub`` actor literal was a T2a hangover misattributing
    real decisions to a stub that no longer exists. Historical audit
    rows keep the old actor for reflog integrity.
    """
    return AuditEntry(
        action="trust_decision",
        actor="autonomy-triage",
        note=f"target={target} → {routed_to}",
        details={"target": target, "level": level, "score": score, "mode": mode, "routed_to": routed_to},
    )


# Back-compat alias — the old name is still importable so a straggler
# import doesn't break, but new code should use trust_decision_entry.
# Retire this alias in a future sweep once no consumers reference it.
stub_trust_decision_entry = trust_decision_entry
