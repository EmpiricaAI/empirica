"""Which artifact fields may be CORRECTED after the fact — one definition, two consumers.

The gardening triad is `log-artifacts` / `resolve-artifacts` / `delete-artifacts`:
create, close, remove. None of them can change a **field**. A gardener who finds a
finding scored `impact=0.9` that deserves 0.3, or an `epistemic_source=search` on
something a peer actually told them, has no move — resolution closes a row and
deletion destroys it, and neither is what a mis-scored-but-true artifact needs.

That is the gap `update-artifacts` fills, and David's framing is the right one: it
is not symmetry for its own sake, it is **load-bearing for gardening**.

**This map lives here, not in the CLI and not in the API, because it is consumed by
both.** The daemon has had `PATCH /artifacts/{id}` with a per-type whitelist since
v0.5; the CLI had nothing. Every defect fixed on 2026-07-31 came from exactly that
shape — two hand-maintained implementations of one contract, each looking complete
from inside itself:

  · the CLI could resolve findings while the API 422'd them
  · the API could correct provenance while the CLI had no verb
  · the API could correct — but only for the one project its daemon was bound to

Copying the whitelist into a second file would have scheduled the fourth.

**What is deliberately NOT correctable.** The claim text itself (`finding`,
`unknown`, `approach`, `mistake`, `choice`) is immutable here. Silently rewriting
what an artifact SAID would make the epistemic record unfalsifiable — a reader
could no longer distinguish "this was always the claim" from "someone edited it
after it was contradicted". A claim that turns out wrong gets `finding-resolve
--kind retracted`, which keeps the original wording and records that it failed.
Correct the *metadata*; retract the *claim*.
"""

from __future__ import annotations

#: Per-type correctable fields. Metadata only — never the claim text.
#:
#: `epistemic_source` is on every type because provenance is the field most often
#: wrong in practice: peer-derived artifacts get tagged `search` as though the
#: practitioner had observed the system themselves, which is the exact conflation
#: `prior_artifact` is meant to end. One practice measured 490 of 805 findings
#: carrying a contaminated `search` tag.
ARTIFACT_UPDATABLE_FIELDS: dict[str, set[str]] = {
    "finding": {"impact", "subject", "epistemic_source", "visibility"},
    "unknown": {"impact", "subject", "epistemic_source", "visibility"},
    "dead_end": {"impact", "subject", "epistemic_source", "visibility", "domain"},
    "mistake": {"prevention", "epistemic_source", "visibility"},
    "assumption": {"confidence", "status", "epistemic_source", "visibility"},
    # `reversibility` is metadata about the decision (how costly it is to undo), not
    # the claim — so it belongs here by this file's own rule. It earned the slot the
    # hard way: a truthy-default bug made `decision-log --reversibility` a no-op, and
    # the practice that found it had no supported way to correct a single row. The
    # enum stays enforced by the CHECK constraint on `decisions.reversibility`, so a
    # bad value fails the UPDATE rather than landing.
    "decision": {"outcome", "regret_score", "reversibility", "epistemic_source", "visibility"},
    "source": {"confidence", "description"},
    "goal": {"objective", "status"},
}

#: Table + id column per type, so a caller can locate the row without duplicating
#: the mapping that has already caused trouble elsewhere (`project_assumptions`
#: was referenced for the life of a verb; the real table is `assumptions`).
ARTIFACT_TABLES: dict[str, tuple[str, str]] = {
    "finding": ("project_findings", "id"),
    "unknown": ("project_unknowns", "id"),
    "dead_end": ("project_dead_ends", "id"),
    "mistake": ("mistakes_made", "id"),
    "assumption": ("assumptions", "id"),
    "decision": ("decisions", "id"),
    "source": ("epistemic_sources", "id"),
    "goal": ("goals", "id"),
}


def updatable_fields(artifact_type: str) -> set[str]:
    """Correctable fields for a type; empty set for an unknown type."""
    return ARTIFACT_UPDATABLE_FIELDS.get(artifact_type, set())


def filter_updates(artifact_type: str, body: dict) -> tuple[dict, list[str]]:
    """Split a request into (allowed updates, rejected field names).

    Rejected keys are RETURNED rather than silently dropped. The daemon's PATCH
    drops them quietly, which is defensible for a machine consumer but wrong for a
    practitioner typing a field name: a correction that reports success while
    changing nothing is the advertised-and-discarded pattern, and the whole reason
    this surface exists is that corrections were failing invisibly.
    """
    allowed = updatable_fields(artifact_type)
    updates = {k: v for k, v in body.items() if k in allowed}
    rejected = [k for k in body if k not in allowed]
    return updates, rejected
