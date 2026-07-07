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
