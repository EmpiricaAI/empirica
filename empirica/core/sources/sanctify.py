"""Source sanctification — classify the active source corpus + recommend actions.

Two new detectors on top of the existing lifecycle primitives (sources-check for
URL liveness, source-update to re-fetch, source-archive to retire):

- **zombie** — no incoming ``sourced_from`` edge; nothing references it (dead weight)
- **duplicate** — shares a ``content_hash`` with another active source (redundant)

plus **dead** (canonical_path missing) reused here. The pure ``classify_sources``
takes pre-computed inputs (no I/O) so the judgment is unit-testable; the DB/FS
gathering lives in the CLI handler.
"""

from __future__ import annotations

# Verdict precedence — most-retirable first. A source matching several rules is
# reported under the strongest one (dead beats duplicate beats zombie).
VERDICTS = ("dead", "duplicate", "zombie", "valid")

_RECOMMENDATION = {
    "dead": "archive (file_missing) — canonical_path no longer exists",
    "duplicate": "archive (superseded) — identical content to another source; keep one",
    "zombie": "review — no artifact references it (sourced_from); retire if truly unused",
    "valid": "keep",
}
# The subset a future auto-apply mode could safely archive (safe, reversible).
# Zombie stays manual: an unreferenced source may still be legitimately citable.
AUTO_SAFE = frozenset({"dead", "duplicate"})


def classify_sources(
    sources: list[dict],
    referenced_ids: set,
    hash_counts: dict,
    missing_paths: set,
) -> list[dict]:
    """Classify each active source.

    - ``sources`` — dicts with ``id`` / ``title`` / ``content_hash`` / ``canonical_path``
    - ``referenced_ids`` — source ids that have ≥1 incoming ``sourced_from`` edge
    - ``hash_counts`` — ``{content_hash: count}`` across the active corpus
    - ``missing_paths`` — canonical_paths that don't exist on disk

    Precedence: dead → duplicate → zombie → valid. Returns one classification per
    source with its recommended action and whether it's auto-safe to archive.
    """
    out: list[dict] = []
    for s in sources:
        sid = s.get("id")
        chash = s.get("content_hash")
        cpath = s.get("canonical_path")
        if cpath and cpath in missing_paths:
            verdict = "dead"
        elif chash and hash_counts.get(chash, 0) > 1:
            verdict = "duplicate"
        elif sid not in referenced_ids:
            verdict = "zombie"
        else:
            verdict = "valid"
        out.append(
            {
                "id": sid,
                "title": s.get("title") or "",
                "verdict": verdict,
                "recommendation": _RECOMMENDATION[verdict],
                "auto_safe": verdict in AUTO_SAFE,
            }
        )
    return out


def summarize(classifications: list[dict]) -> dict:
    """Roll up classifications into counts by verdict + the auto-safe total."""
    by_verdict: dict[str, int] = {}
    for c in classifications:
        by_verdict[c["verdict"]] = by_verdict.get(c["verdict"], 0) + 1
    return {
        "total": len(classifications),
        "by_verdict": by_verdict,
        "auto_safe": sum(1 for c in classifications if c["auto_safe"]),
    }


# ── Derived standing (Phase 4) ────────────────────────────────────────
#
# DERIVED ON READ, NEVER STORED. A stored score drifts from the evidence that
# produced it — the exact failure empirica exists to prevent — and keeping the
# computation here means the formula can change without migrating data.
#
# Every metric returns None when there is no evidence for it. "Unreviewed" is not
# "good" and "uncited" is not "inaccurate": absence of evidence is a first-class
# state, and collapsing it into a number is how a metric starts lying.

_POSITIVE_OUTCOMES = frozenset({"confirmed"})
_NEGATIVE_OUTCOMES = frozenset({"invalidated"})
_MOVED_OUTCOMES = frozenset({"superseded", "invalidated"})


def derive_standing(
    outcome_events: list[dict],
    citation_count: int,
    last_reviewed_at: float | None,
    now: float,
    review_window_days: float = 90.0,
) -> dict:
    """Derive a source's standing from evidence. Pure — no I/O, no persistence.

    - ``relevance``  — is anything actually using it?
    - ``accuracy``   — did conclusions drawn from it hold? Only outcomes where the
      caller DECLARED the source implicated count against it; an artifact can fail
      because the reasoning was wrong, and inferring blame would slander good sources.
    - ``stability``  — how often do artifacts citing it get superseded/invalidated?
      A moving domain is not the same as a wrong source, so this is reported
      separately rather than folded into accuracy.
    - ``review_age_days`` / ``review_overdue`` — a source nobody has re-checked is an
      assertion with a date on it, not ground truth.
    """
    events = [e for e in (outcome_events or []) if e.get("event") == "source_outcome"]

    positives = [e for e in events if e.get("outcome") in _POSITIVE_OUTCOMES]
    # Only DECLARED implication counts against accuracy (spec §2.3).
    blamed = [e for e in events if e.get("outcome") in _NEGATIVE_OUTCOMES and e.get("implicated") is True]
    judged = len(positives) + len(blamed)
    accuracy = (len(positives) / judged) if judged else None

    moved = [e for e in events if e.get("outcome") in _MOVED_OUTCOMES]
    stability = (1.0 - (len(moved) / len(events))) if events else None

    age_days = ((now - last_reviewed_at) / 86400.0) if last_reviewed_at else None

    return {
        "relevance": {
            "citations": citation_count,
            "outcomes_observed": len(events),
            # Uncited is a real state, not a zero score.
            "status": "uncited" if citation_count == 0 else "cited",
        },
        "accuracy": accuracy,
        "accuracy_basis": {"confirmed": len(positives), "implicated_failures": len(blamed)},
        "stability": stability,
        "review_age_days": age_days,
        "review_overdue": (age_days is not None and age_days > review_window_days),
        "never_reviewed": last_reviewed_at is None,
        # What a consumer should NOT conclude — kept explicit so a caller cannot
        # mistake silence for a verdict.
        "unknown": [k for k, v in (("accuracy", accuracy), ("stability", stability)) if v is None],
    }


# ── Blindspot propagation (Phase 5) ───────────────────────────────────


def assess_blindspot_inputs(
    derived_from: list[str],
    invalidated_ids: set,
    stale_threshold: float = 0.5,
) -> dict:
    """Does a blindspot still stand, given what happened to its premises?

    A blindspot is not observed — it is INFERRED from a pattern across other
    artifacts. So it is a conclusion, and conclusions inherit the fate of their
    premises: if the artifacts it was derived from have been invalidated, the
    blindspot is suspect.

    Deliberately returns ``stale_inputs`` for RE-DERIVATION rather than
    auto-invalidating. A blindspot can remain true even when a supporting finding was
    wrong, and silently deleting an unknown-unknown is the worst available failure
    direction — the whole point of a blindspot is that nobody was looking there.

    Returns ``{verdict, invalidated_inputs, total_inputs, ratio, recommendation}``
    where verdict ∈ ``stands`` | ``stale_inputs`` | ``unknown_provenance``.
    """
    inputs = [i for i in (derived_from or []) if i]
    if not inputs:
        # Pre-migration blindspots recorded no premises. That is not "fine" — it is
        # unfalsifiable for a different reason, and must be visible as such.
        return {
            "verdict": "unknown_provenance",
            "invalidated_inputs": 0,
            "total_inputs": 0,
            "ratio": None,
            "recommendation": "re-scan — this blindspot predates provenance tracking, so its premises are unknown",
        }

    dead = [i for i in inputs if i in invalidated_ids or any(str(x).startswith(i) for x in invalidated_ids)]
    ratio = len(dead) / len(inputs)
    stale = ratio >= stale_threshold
    return {
        "verdict": "stale_inputs" if stale else "stands",
        "invalidated_inputs": len(dead),
        "total_inputs": len(inputs),
        "ratio": ratio,
        "recommendation": (
            "re-derive — enough premises were invalidated that the inference may no longer hold"
            if stale
            else "keep — premises still stand"
        ),
    }
