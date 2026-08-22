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
    # A lesson's TEXT is immutable by the same rule as a finding's claim — a lesson
    # that turns out wrong is superseded, not edited. What is correctable is the
    # governance metadata, and `sharing_policy` is the one that matters: it decides
    # whether the lesson leaves the practice at all, and until this existed a lesson
    # authored under the `private` default could never be promoted.
    "lesson": {"sharing_policy", "abstraction_level", "abstract_pattern", "domain"},
}

#: Table + id column per type, so a caller can locate the row without duplicating
#: the mapping that has already caused trouble elsewhere (`project_assumptions`
#: was referenced for the life of a verb; the real table is `assumptions`).
#: Types whose rows do NOT live in `sessions.db`. `update-artifacts` resolves the
#: map above against the session database; anything here needs its own writer, and
#: the CLI dispatches on membership rather than on a hardcoded name.
#:
#: `lesson` is the whole reason this exists. Lessons live in
#: `.empirica/lessons/lessons.db`, so a lesson authored `private` — the default —
#: had NO path to become shared: measured 2026-08-21, 7 of this practice's 24
#: lessons were cross-practice patterns permanently invisible to every peer,
#: because the only way to change the policy was to re-author the lesson.
#: Federation can publish new knowledge; without this it cannot promote existing
#: knowledge.
FOREIGN_STORE_TYPES: frozenset[str] = frozenset({"lesson"})

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


#: The JSON blob column an artifact's edges live in, per type. Separate from
#: :data:`ARTIFACT_TABLES` because only the deletion path needs it — but IN THIS
#: FILE, because a private copy of a type map is exactly what this change removes.
#: `None` means the type stores no edge blob.
ARTIFACT_EDGE_DATA_COLUMNS: dict[str, str | None] = {
    "finding": "finding_data",
    "unknown": "unknown_data",
    "dead_end": "dead_end_data",
    "mistake": "mistake_data",
    "assumption": None,
    "decision": None,
    "source": None,
    "goal": "goal_data",
}


#: Types `delete-artifacts` may destroy. NOT the same set as the tables above, and
#: the difference is load-bearing rather than an oversight.
#:
#: `source` is excluded ON PURPOSE: sources are ARCHIVED, not deleted, because
#: archiving preserves the audit chain by design (`source-archive` says so). A
#: private seven-entry copy of the table map encoded this exclusion by OMITTING
#: source — which then made `_artifact_exists` answer False for every source id,
#: so `prune_dangling` judged every `sourced_from` edge dangling and a routine
#: gardening pass destroyed the practice's only two citation edges while both
#: endpoints sat on disk.
#:
#: That is one predicate answering two questions — *what may I delete?* and *what
#: exists?* — which agree everywhere except on things that are archived. They are
#: separate here for that reason: unify the TABLES, keep the POLICY explicit.
DELETABLE_TYPES: frozenset[str] = frozenset(ARTIFACT_TABLES) - {"source"}

#: Why a non-deletable type is refused, so the message names the alternative
#: rather than reading as an unknown type.
NON_DELETABLE_REASON: dict[str, str] = {
    "source": ("sources are ARCHIVED, not deleted — archiving preserves the audit chain. Use `source-archive <id>`."),
}


def artifact_table(artifact_type: str) -> tuple[str, str, str | None] | None:
    """(table, id column, edge-data column) for a type, or None if not a local type.

    ONE lookup for every consumer. Three divergent copies of this mapping existed
    — the canonical eight here, a private seven in `graph_commands` missing
    `source`, and a private five in `profile_commands` missing `assumption`,
    `decision` and `source` — while `update-artifacts`, in the same file as the
    seven-entry copy, already imported the canonical one. `delete-artifacts` on a
    `lesson` returned *Unknown artifact type* for a type this registry names on
    purpose. Reported by mesh-support, measured on installed 1.13.27.

    Returns None for a type stored outside `sessions.db` (see
    :data:`FOREIGN_STORE_TYPES`) as well as for a genuinely unknown one — callers
    that need to tell those apart check `FOREIGN_STORE_TYPES` and say so, because
    *we do not keep that here* and *we have never heard of it* deserve different
    messages.
    """
    spec = ARTIFACT_TABLES.get(artifact_type)
    if spec is None:
        return None
    table, id_col = spec
    return table, id_col, ARTIFACT_EDGE_DATA_COLUMNS.get(artifact_type)


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
