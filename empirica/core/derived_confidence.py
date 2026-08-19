"""A claim is never worth more than the weakest premise it rests on.

Every artifact's confidence is stored independently of the artifacts it was
derived from. So a finding edged to a 0.55 assumption is retrieved at
finding-strength, and a consumer acting on it has no signal that an unverified
belief sits underneath. That is the failure empirica-outreach and
empirica-autonomy measured on 2026-08-18 (prop_ahcqqnb3): one artifact mixing an
observed behaviour with an inferred identity, consumed at the confidence of its
strongest part, five downstream effects including a customer-facing document
built under a fabricated name.

David ruled that the *representation* problem is already solved — the type
vocabulary separates observed (``finding``) from taken-for-granted
(``assumption``), and logging an inference as a finding is misuse, not a missing
field. This module is the other half of that ruling: once the two ARE separate
artifacts joined by an edge, make the edge load-bearing at retrieval, so
splitting them buys something mechanical rather than only tidiness.

**Read-time only.** Nothing here writes. No schema change, no new author burden,
no LLM — the discipline is already expressible, this just stops the graph from
discarding it on the way back out.

**Direction.** ``log-artifacts`` writes ``{from: dependent, to: premise}`` —
``{"from": "d1", "to": "f1", "relation": "evidence"}`` means *d1's evidence is
f1*. Premises are therefore reached by walking OUT along :data:`PREMISE_RELATIONS`.
``related`` and ``attached_to`` are excluded deliberately: they are association,
not derivation, and they are also the two most common relations in a real graph
(736 + 222 of 1330 edges in this practice), so admitting them would cap almost
everything on almost anything.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Relations that mean "this artifact RESTS ON that one". Association relations
#: (``related``, ``attached_to``) are not derivation and never cap.
PREMISE_RELATIONS: tuple[str, ...] = ("evidence", "grounded_by", "sourced_from", "caused_by")

#: Walk bound. Derivation chains are short in practice; the bound exists so a
#: cyclic or pathological graph cannot stall a retrieval on the PREFLIGHT hot path.
MAX_DEPTH = 4

#: Total premises visited before giving up, for the same reason. Reported as
#: ``truncated`` rather than silently dropped — a partial walk that looks
#: complete would understate risk, which is the exact failure this guards.
MAX_VISITED = 64

#: An artifact type carries confidence, or it does not. ``(table, id_column,
#: confidence_column_or_None)``.
_CONFIDENCE_SOURCES: dict[str, tuple[str, str, str | None]] = {
    "assumption": ("assumptions", "id", "confidence"),
    "decision": ("decisions", "id", "confidence_at_decision"),
    "source": ("epistemic_sources", "id", "confidence"),
    "finding": ("project_findings", "id", None),
    "unknown": ("project_unknowns", "id", None),
    "dead_end": ("project_dead_ends", "id", None),
    "mistake": ("mistakes_made", "id", None),
}

#: What a resolved finding is worth as a premise. `retracted` means it was FALSE
#: when written, so anything resting on it inherits that — a floor of 0.0 rather
#: than a discount. `stale` and `superseded` were true once; they are not
#: evidence for a live claim, but they are not lies either.
_RESOLVED_FINDING_WEIGHT: dict[str, float] = {
    "retracted": 0.0,
    "mistyped": 0.0,
    "stale": 0.5,
    "superseded": 0.5,
}


def _own_confidence(cursor, artifact_id: str) -> tuple[str, float] | None:
    """Resolve an id to ``(type, own_confidence)``, or None if it is not an artifact.

    A ``finding`` has no confidence column — ``impact`` is how much it MATTERS,
    not how sure we are — so an unresolved finding is treated as an assertion at
    1.0 and does not cap anything. That is the honest reading: a finding claims
    to be an observation. What DOES cap is a finding that has since been
    resolved, and especially one retracted as false.
    """
    for atype, (table, id_col, conf_col) in _CONFIDENCE_SOURCES.items():
        try:
            if atype == "finding":
                cursor.execute(
                    f"SELECT is_resolved, resolution_kind FROM {table} WHERE {id_col} = ?",
                    (artifact_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    continue
                if not row[0]:
                    return ("finding", 1.0)
                return ("finding", _RESOLVED_FINDING_WEIGHT.get(row[1] or "", 0.5))
            if conf_col is None:
                cursor.execute(f"SELECT 1 FROM {table} WHERE {id_col} = ?", (artifact_id,))
                if cursor.fetchone() is None:
                    continue
                return (atype, 1.0)
            if atype == "assumption":
                cursor.execute(
                    f"SELECT {conf_col}, status FROM {table} WHERE {id_col} = ?",
                    (artifact_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    continue
                status = (row[1] or "").lower()
                if status == "falsified":
                    return ("assumption", 0.0)
                if status == "verified":
                    return ("assumption", 1.0)
                return ("assumption", float(row[0]) if row[0] is not None else 0.5)
            cursor.execute(f"SELECT {conf_col} FROM {table} WHERE {id_col} = ?", (artifact_id,))
            row = cursor.fetchone()
            if row is None:
                continue
            return (atype, float(row[0]) if row[0] is not None else 1.0)
        except Exception:  # a missing table on an older db is not a reason to fail retrieval
            continue
    return None


def derived_confidence(cursor, artifact_id: str) -> dict[str, Any] | None:
    """The cap this artifact's premises place on it, or None if it rests on nothing.

    Returns ``{"value", "weakest": {"id", "type", "confidence", "relation"},
    "premises_walked", "truncated"}``. ``value`` is the MINIMUM own-confidence
    across the transitive premise closure — the weakest link, not an average,
    because averaging is exactly how a strong observation hides a weak inference.

    Returns None when the artifact has no premise edges: an unsupported claim is
    not thereby a weak one, and inventing a number for it would be the same
    error one level up.
    """
    try:
        placeholders = ",".join("?" * len(PREMISE_RELATIONS))
        visited: set[str] = {artifact_id}
        frontier: list[tuple[str, str]] = [(artifact_id, "")]
        weakest: dict[str, Any] | None = None
        walked = 0
        truncated = False

        for _depth in range(MAX_DEPTH):
            if not frontier:
                break
            next_frontier: list[tuple[str, str]] = []
            for node_id, _rel in frontier:
                cursor.execute(
                    f"SELECT to_id, relation FROM artifact_edges WHERE from_id = ? AND relation IN ({placeholders})",
                    (node_id, *PREMISE_RELATIONS),
                )
                for to_id, relation in cursor.fetchall():
                    if to_id in visited:
                        continue
                    if len(visited) >= MAX_VISITED:
                        truncated = True
                        break
                    visited.add(to_id)
                    resolved = _own_confidence(cursor, to_id)
                    if resolved is None:
                        continue
                    atype, conf = resolved
                    walked += 1
                    if weakest is None or conf < weakest["confidence"]:
                        weakest = {
                            "id": to_id,
                            "type": atype,
                            "confidence": conf,
                            "relation": relation,
                        }
                    next_frontier.append((to_id, relation))
                if truncated:
                    break
            if truncated:
                break
            frontier = next_frontier

        if weakest is None:
            return None
        return {
            "value": weakest["confidence"],
            "weakest": weakest,
            "premises_walked": walked,
            "truncated": truncated,
        }
    except Exception as e:  # retrieval must never fail because a cap could not be computed
        logger.debug(f"derived_confidence({artifact_id[:8]}) skipped: {e}")
        return None


def annotate(cursor, artifacts: list[dict], id_key: str = "id", budget: int = 25) -> list[dict]:
    """Attach ``derived_confidence`` to retrieved artifacts that have premises.

    Mutates and returns the list. Only artifacts with premise edges gain the key,
    so its PRESENCE is the signal — an absent key means "rests on nothing
    recorded", which is different from "rests on something solid" and must not
    look the same.

    ``budget`` bounds the work because this runs on the PREFLIGHT retrieval path.
    Artifacts past the budget are left unannotated rather than partially walked.
    """
    for art in artifacts[:budget]:
        aid = art.get(id_key)
        if not aid:
            continue
        cap = derived_confidence(cursor, str(aid))
        if cap is not None:
            art["derived_confidence"] = cap
    return artifacts


#: Labels for the dependent report — table, id column, text column, per type.
_DEPENDENT_LABELS: dict[str, tuple[str, str, str]] = {
    "finding": ("project_findings", "id", "finding"),
    "unknown": ("project_unknowns", "id", "unknown"),
    "dead_end": ("project_dead_ends", "id", "approach"),
    "mistake": ("mistakes_made", "id", "mistake"),
    "assumption": ("assumptions", "id", "assumption"),
    "decision": ("decisions", "id", "choice"),
}


def dependents_of(cursor, artifact_id: str, max_results: int = 25) -> list[dict[str, Any]]:
    """Artifacts in THIS graph that rest on ``artifact_id``. Read-only.

    The mirror of :func:`derived_confidence` — same relations, same table, keyed
    on ``to_id`` instead of ``from_id`` (``idx_artifact_edges_to`` already covers
    it). Retracting a premise otherwise leaves everything built on it at full
    retrieval weight, still being served as though nothing changed.

    **This graph only.** A peer's artifacts are theirs; a correction crossing a
    practice boundary is testimony they evaluate, never a write we perform. There
    is deliberately no cross-practice variant of this function.

    **Reports, never marks.** A dependent of a retracted premise is not thereby
    false — it is UNSUPPORTED, which is a different thing and only the
    practitioner can tell which of the two it is. Auto-resolving here would
    destroy true claims whose stated premise was merely the wrong one, and
    resolution is one-way.

    One hop, not transitive: the immediate dependents are the ones whose
    reasoning the practitioner still holds in mind. Their own dependents surface
    when each is judged in turn, which keeps the decision small enough to make.
    """
    out: list[dict[str, Any]] = []
    try:
        placeholders = ",".join("?" * len(PREMISE_RELATIONS))
        cursor.execute(
            f"SELECT from_id, relation FROM artifact_edges WHERE to_id = ? AND relation IN ({placeholders}) LIMIT ?",
            (artifact_id, *PREMISE_RELATIONS, max_results),
        )
        for from_id, relation in cursor.fetchall():
            entry = {"id": from_id, "relation": relation, "type": None, "text": None}
            for atype, (table, id_col, text_col) in _DEPENDENT_LABELS.items():
                try:
                    cursor.execute(
                        f"SELECT {text_col} FROM {table} WHERE {id_col} = ?",
                        (from_id,),
                    )
                    row = cursor.fetchone()
                except Exception:
                    continue
                if row is not None:
                    entry["type"] = atype
                    entry["text"] = (row[0] or "")[:120]
                    break
            out.append(entry)
    except Exception as e:
        logger.debug(f"dependents_of({artifact_id[:8]}) skipped: {e}")
    return out
