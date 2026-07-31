"""Per-claim grounding: CHECK declares, POSTFLIGHT adjudicates.

``know`` is a scalar over heterogeneous beliefs. A transaction rests on a dozen
claims at once, and one number averages them — so an *honest* ``know=0.82`` can be
the mean of eleven well-grounded claims and one pure guess, with nothing in the
record distinguishing that from twelve moderately-grounded ones. The average
conceals the outlier, and the outlier is what breaks.

Reported by cortex (2026-07-30) from a case that cost them four wrong design rules:
they were genuinely grounded on field distributions and ungrounded on show
identity, submitted an honest averaged ``know``, and built to a stale decision
record. David's refinement is what makes it a measurement rather than a nicer-
looking CHECK: **declare at CHECK, adjudicate at POSTFLIGHT.**

    CHECK       name 2-3 load-bearing claims + how each was grounded
    POSTFLIGHT  each claim → held | refuted | untested

The third verdict carries the value. ``refuted`` is rare, ``held`` is cheap, and
``untested`` is the state the scalar cannot express: *I acted on this and never
checked it.* A claim declared and never adjudicated is therefore recorded as
``untested`` and reported as a GAP — never silently passed, because "no verdict"
reads as "fine" in every reporting surface we have, which would give the mechanism
the exact defect it exists to catch.

**Advisory in v0.** Nothing here blocks a POSTFLIGHT. Two mechanisms in this
codebase died of over-firing — the artifact-breadth nudge whose predicate could
never be satisfied, and free-text ``resolution`` that nobody could aggregate — and
a gate that blocks on unadjudicated claims on day one would be the most obstructive
thing in the system before anyone has evidence it helps. Report first; gate only if
the data earns it.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

Grounding = Literal["read", "ran", "retrieved", "assumed"]
Verdict = Literal["held", "refuted", "untested"]

#: HOW a claim came to be believed, ordered strongest to weakest.
GROUNDINGS: tuple[str, ...] = ("read", "ran", "retrieved", "assumed")

GROUNDING_HELP: dict[str, str] = {
    "read": "opened the source and saw it — file, doc, schema, spec",
    "ran": "executed something and observed the result — strongest form",
    "retrieved": "came from OUR OWN prior artifact, decision or note — testimony, not observation",
    "assumed": "acting on it without checking — the honest label for a guess",
}

VERDICTS: tuple[str, ...] = ("held", "refuted", "untested")

VERDICT_HELP: dict[str, str] = {
    "held": "the work bore it out",
    "refuted": "it turned out false — the valuable one to record",
    "untested": "acted on it, never actually checked — the gap the scalar hides",
}


def normalize_grounding(value: str | None) -> str | None:
    """Return a valid grounding, or ``None`` when unrecognised.

    Unlike the verdict default, an unknown grounding does NOT fall back to
    ``assumed``: silently downgrading a claim the practitioner labelled would
    misreport their epistemic state in the pessimistic direction, and a wrong
    label is worse than an absent one for a mechanism whose whole output is
    labels.
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    return v if v in GROUNDINGS else None


def normalize_verdict(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    return v if v in VERDICTS else None


def is_weak(grounding: str | None) -> bool:
    """True when the claim rests on testimony or nothing.

    ``retrieved`` counts as weak deliberately. **Our own artifacts are testimony,
    not observation** — true when written and ageing exactly like any other prior.
    Retrieving our own decision record currently tags as ``search`` in
    ``epistemic_source``, indistinguishable from observing the live system, which
    is the hole cortex's four wrong rules went through. Marking it per-claim is the
    right level: the same artifact can be solid grounding for one claim and stale
    for another, so the distinction belongs to the claim, not to the artifact.
    """
    return normalize_grounding(grounding) in ("retrieved", "assumed")


def declare(
    db,
    *,
    session_id: str,
    transaction_id: str | None,
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Persist claims declared at CHECK. Returns the stored rows (with ids).

    Fail-soft by design: a malformed claim is skipped rather than failing the
    CHECK. Gating the noetic→praxic transition on the *shape of an advisory
    payload* would make a reporting feature capable of blocking real work.
    """
    stored: list[dict[str, Any]] = []
    if not claims:
        return stored
    now = time.time()
    for idx, raw in enumerate(claims, start=1):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("claim") or "").strip()
        if not text:
            continue
        cid = str(uuid.uuid4())
        grounding = normalize_grounding(raw.get("grounding"))
        ref = str(raw.get("ref") or "").strip() or None
        try:
            db.conn.execute(
                "INSERT INTO transaction_claims "
                "(id, session_id, transaction_id, claim_index, claim, grounding, ref, declared_timestamp) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cid, session_id, transaction_id, idx, text, grounding, ref, now),
            )
        except Exception:
            continue
        stored.append({"id": cid, "index": idx, "claim": text, "grounding": grounding, "ref": ref})
    if stored:
        db.conn.commit()
    return stored


def adjudicate(
    db,
    *,
    session_id: str,
    transaction_id: str | None,
    adjudications: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Apply POSTFLIGHT verdicts, then force every unadjudicated claim to ``untested``.

    The forcing is the load-bearing half. Leaving a declared claim with a NULL
    verdict would make "never adjudicated" and "nothing declared" identical in
    every downstream count — the same conflation as a skipped check that prints
    nothing, and the failure cortex explicitly asked be designed out.

    Matching accepts an explicit ``id`` (or 8+ char prefix) or a 1-based ``index``
    matching declaration order. Index is what a practitioner naturally has to
    hand; id is unambiguous when both are available.
    """
    rows = _open_claims(db, session_id, transaction_id)
    if not rows:
        return {"declared": 0, "held": 0, "refuted": 0, "untested": 0, "gaps": []}

    by_id = {r["id"]: r for r in rows}
    by_index = {r["claim_index"]: r for r in rows}
    now = time.time()

    for raw in adjudications or []:
        if not isinstance(raw, dict):
            continue
        verdict = normalize_verdict(raw.get("verdict"))
        if verdict is None:
            continue
        target = None
        ident = raw.get("id") or raw.get("claim_id")
        if ident:
            ident = str(ident)
            target = by_id.get(ident) or next((r for r in rows if len(ident) >= 8 and r["id"].startswith(ident)), None)
        if target is None and raw.get("index") is not None:
            try:
                target = by_index.get(int(raw["index"]))
            except (TypeError, ValueError):
                target = None
        if target is None:
            continue
        db.conn.execute(
            "UPDATE transaction_claims SET verdict = ?, verdict_evidence = ?, adjudicated_timestamp = ? WHERE id = ?",
            (verdict, str(raw.get("evidence") or "").strip() or None, now, target["id"]),
        )
        target["verdict"] = verdict

    # Anything still unadjudicated is a GAP, recorded — not left NULL.
    db.conn.execute(
        "UPDATE transaction_claims SET verdict = 'untested', adjudicated_timestamp = ? "
        "WHERE session_id = ? AND verdict IS NULL" + (" AND transaction_id = ?" if transaction_id else ""),
        ((now, session_id, transaction_id) if transaction_id else (now, session_id)),
    )
    db.conn.commit()

    final = _all_claims(db, session_id, transaction_id)
    counts = {"held": 0, "refuted": 0, "untested": 0}
    gaps = []
    for r in final:
        v = r.get("verdict") or "untested"
        if v in counts:
            counts[v] += 1
        if v == "untested":
            gaps.append(
                {
                    "claim": r.get("claim"),
                    "grounding": r.get("grounding"),
                    "note": (
                        "declared at CHECK, never adjudicated — acted on, never checked"
                        if not is_weak(r.get("grounding"))
                        else f"declared as {r.get('grounding') or 'ungrounded'} and never checked"
                    ),
                }
            )
    return {"declared": len(final), **counts, "gaps": gaps}


def _open_claims(db, session_id: str, transaction_id: str | None) -> list[dict[str, Any]]:
    return _query_claims(db, session_id, transaction_id, only_open=True)


def _all_claims(db, session_id: str, transaction_id: str | None) -> list[dict[str, Any]]:
    return _query_claims(db, session_id, transaction_id, only_open=False)


def _query_claims(db, session_id: str, transaction_id: str | None, only_open: bool) -> list[dict[str, Any]]:
    sql = "SELECT id, claim_index, claim, grounding, ref, verdict FROM transaction_claims WHERE session_id = ?"
    params: list[Any] = [session_id]
    if transaction_id:
        sql += " AND transaction_id = ?"
        params.append(transaction_id)
    if only_open:
        sql += " AND verdict IS NULL"
    sql += " ORDER BY claim_index"
    try:
        cur = db.conn.execute(sql, params)
    except Exception:
        return []
    return [
        {
            "id": r[0],
            "claim_index": r[1],
            "claim": r[2],
            "grounding": r[3],
            "ref": r[4],
            "verdict": r[5],
        }
        for r in cur.fetchall()
    ]


def summarize_for_check(stored: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The CHECK-side echo: what was declared, and how much of it is weak.

    Surfacing the weak count at CHECK — while the practitioner can still act — is
    the point of declaring rather than merely recording. Two of three load-bearing
    claims resting on `assumed` is a reason to keep investigating, and it is
    invisible in an averaged ``know``.
    """
    if not stored:
        return None
    weak = [c for c in stored if is_weak(c.get("grounding"))]
    ungrounded = [c for c in stored if c.get("grounding") is None]
    out: dict[str, Any] = {
        "declared": len(stored),
        "claims": stored,
        "weakly_grounded": len(weak),
    }
    if weak:
        out["note"] = (
            f"{len(weak)} of {len(stored)} load-bearing claims rest on retrieved-or-assumed "
            "grounding. Our own artifacts are testimony, not observation — they were true "
            "when written and age like any prior."
        )
    if ungrounded:
        out["unlabelled"] = len(ungrounded)
    return out
