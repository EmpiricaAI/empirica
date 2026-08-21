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

import logging
import time
import uuid
from typing import Any, Literal

logger = logging.getLogger(__name__)

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
    # Continue numbering from what this transaction already holds. Claims can be
    # declared at PREFLIGHT *and* at CHECK, and restarting at 1 each time put two
    # different claims at index 1 inside one transaction — `adjudicate` keys its
    # lookup on a dict, so one of them silently won and the other could never be
    # addressed, then got forced to `untested` and reported as a gap. That made the
    # mechanism understate its own coverage: three transactions reported phantom
    # gaps for claims that had in fact been verified.
    #
    # The index is the practitioner's addressing space, so it has to be unique
    # across the whole transaction, not per call.
    start = _next_claim_index(db, session_id, transaction_id)
    for idx, raw in enumerate(claims, start=start):
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


def _next_claim_index(db, session_id: str, transaction_id: str | None) -> int:
    """One past the highest index this transaction already uses (1 when empty).

    Fail-soft to 1 on any error, matching the rest of this module: a numbering
    problem must not be able to fail a CHECK.
    """
    try:
        sql = "SELECT MAX(claim_index) FROM transaction_claims WHERE session_id = ?"
        params: tuple = (session_id,)
        if transaction_id:
            sql += " AND transaction_id = ?"
            params = (session_id, transaction_id)
        row = db.conn.execute(sql, params).fetchone()
        return int(row[0]) + 1 if row and row[0] is not None else 1
    except Exception:
        return 1


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
        # Say when verdicts arrived with nothing to attach them to. Returning a
        # silent zero block here was the exact mirror of the forcing rule below:
        # that rule exists so "declared but never checked" cannot look like
        # "nothing declared", and this branch let "adjudicated but never declared"
        # look like "submitted nothing". Both are the same conflation.
        dropped = sum(1 for a in adjudications or [] if isinstance(a, dict) and normalize_verdict(a.get("verdict")))
        out: dict[str, Any] = {"declared": 0, "held": 0, "refuted": 0, "untested": 0, "gaps": []}
        if dropped:
            out["dropped_adjudications"] = dropped
            out["note"] = (
                f"{dropped} verdict(s) submitted but no claims were declared in this "
                "transaction — nothing was recorded. Declare claims at PREFLIGHT or CHECK "
                "for POSTFLIGHT to adjudicate."
            )
        return out

    by_id = {r["id"]: r for r in rows}
    by_index = {r["claim_index"]: r for r in rows}
    # Text index for the natural {claim: text, verdict} adjudication shape. A
    # text declared by ≥2 claims maps to None so it stays unmatched rather than
    # attaching a verdict to the wrong one.
    by_text: dict[str, dict[str, Any] | None] = {}
    for r in rows:
        key = _normalize_claim_text(r.get("claim"))
        by_text[key] = None if key in by_text else r
    now = time.time()

    # Every `continue` below used to be silent, and the silence was expensive in a
    # specific way: the unmatched entry falls through to the bulk `untested` sweep,
    # so POSTFLIGHT reports "declared at CHECK, never adjudicated — acted on, never
    # checked" about a claim the practitioner DID adjudicate, in the payload, with
    # evidence attached. A false "you didn't verify" is worse than no signal: it is
    # exactly the signal this mechanism exists to produce, so being wrong about it
    # trains the practitioner to distrust a working discipline.
    #
    # It also defeats diagnosis. Reported by mesh-support after three transactions
    # and two self-consistent WRONG root-causes (they theorised claim-index
    # bookkeeping; the real cause was sending `adjudication` where the contract
    # wants `verdict`, so the entry was dropped before matching was attempted).
    # A defect that survives two good-faith diagnoses from its own error message is
    # doing work to hide itself.
    applied = 0
    unmatched: list[dict[str, Any]] = []

    def _reject(raw_entry, reason: str) -> None:
        keys = sorted(raw_entry.keys()) if isinstance(raw_entry, dict) else []
        entry: dict[str, Any] = {"reason": reason, "keys_seen": keys}
        # Name the specific confusion when we can. These two are natural rather
        # than careless: the guidance prose calls the act "adjudication" while the
        # payload key is `verdict`, and calls the support "note" while the key is
        # `evidence`. `keys_seen` still carries the general case.
        # `note` is deliberately absent: it is now ACCEPTED as an alias for
        # `evidence`, so naming it as a confusion would send the reader to fix
        # a key that already works.
        # `claim` is now a valid matcher (text), so it is NOT a key confusion —
        # only flag `adjudication`→`verdict`. A `claim` present but unmatched
        # means the text matched no declared claim or was ambiguous (declared by
        # ≥2), which the reason string already conveys.
        near = {"adjudication": "verdict"}
        confusions = [f"{k} -> {v}" for k, v in near.items() if k in keys]
        if confusions:
            entry["likely_key_confusion"] = confusions
        unmatched.append(entry)

    for raw in adjudications or []:
        if not isinstance(raw, dict):
            unmatched.append({"reason": "not_an_object", "keys_seen": []})
            continue
        verdict = normalize_verdict(raw.get("verdict"))
        if verdict is None:
            # Distinguish absent from unrecognised — "you sent no verdict" and
            # "you sent a verdict I don't know" need different corrections.
            _reject(raw, "missing_verdict" if raw.get("verdict") is None else "unrecognized_verdict")
            continue
        target = _match_claim(raw, rows, by_id, by_index, by_text)
        if target is None:
            _reject(raw, "no_matching_claim")
            continue
        applied += 1
        db.conn.execute(
            "UPDATE transaction_claims SET verdict = ?, verdict_evidence = ?, adjudicated_timestamp = ? WHERE id = ?",
            # `note` accepted as an alias for `evidence`, because the MCP tool
            # description SHIPPED saying `note` while this line read `evidence` —
            # so a caller following the documented contract had their evidence
            # silently dropped. The description is fixed, but callers copied it,
            # and the guidance prose calls this "a note" regardless.
            #
            # Forgiving input where the ambiguity is ours, not theirs — the same
            # reason log-artifacts accepts `id` for `ref`.
            (verdict, str(raw.get("evidence") or raw.get("note") or "").strip() or None, now, target["id"]),
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
    counts, gaps, refutations = _tally(final)
    out = {"declared": len(final), **counts, "gaps": gaps}
    # A refuted claim was the OUTPUT of this mechanism and had no output surface:
    # it incremented a counter and stopped. Refutation is the strongest signal the
    # layer produces — a contradiction established by running the thing, owing
    # nothing to reading text — so where it points is worth more than that it
    # happened.
    if refutations:
        out["refutations"] = refutations
    if unmatched:
        out["adjudication"] = {
            "applied": applied,
            "unmatched": len(unmatched),
            "entries": unmatched,
            "hint": "each entry needs 'verdict' (held|refuted|untested) plus one of "
            "'claim' (the declared text), 'index' (1-based), or 'id'",
        }
        # Say it in the same breath as the gap note, because the gap note is the
        # thing the dropped entries are about to be misread as.
        out["adjudication_warning"] = (
            f"{len(unmatched)} of {len(unmatched) + applied} submitted verdict(s) were NOT applied — "
            "the claims they targeted are counted as 'untested' below, which understates what you "
            "actually checked. Fix the entry shape and the untested count will drop."
        )
    return out


def _tally(final: list[dict[str, Any]]) -> tuple[dict[str, int], list[dict], list[dict]]:
    """Verdict counts, the untested GAPS, and the refutations, in one pass."""
    counts = {"held": 0, "refuted": 0, "untested": 0}
    gaps: list[dict[str, Any]] = []
    refutations: list[dict[str, Any]] = []
    for r in final:
        v = r.get("verdict") or "untested"
        if v in counts:
            counts[v] += 1
        if v == "refuted":
            refutations.append(_refutation(r))
        elif v == "untested":
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
    return counts, gaps, refutations


def _refutation(row: dict[str, Any]) -> dict[str, Any]:
    """One refuted claim, rendered so it points at what it refutes.

    The `retrieved` case is the one that carries: the claim came FROM a prior
    artifact, so refuting it is direct behavioural evidence that the artifact is
    wrong — the practitioner is one `finding-resolve --kind retracted` away from
    correcting the graph, and every other route to that correction requires
    noticing the contradiction by reading. Without a `ref` the same refutation is
    inert, which is why CHECK asks for one while the id is still in hand.
    """
    ref = str(row.get("ref") or "").strip() or None
    grounding = row.get("grounding")
    out: dict[str, Any] = {"claim": row.get("claim"), "grounding": grounding, "ref": ref}
    if row.get("verdict_evidence"):
        out["evidence"] = row["verdict_evidence"]
    if grounding == "retrieved" and ref:
        out["note"] = (
            f"retrieved from {ref} and refuted by what you observed — that artifact asserted "
            "something this transaction contradicted. If it is wrong rather than merely aged: "
            f'`empirica finding-resolve {ref} --kind retracted --resolution "<why>"`.'
        )
    elif grounding == "retrieved":
        out["note"] = (
            "retrieved from a prior artifact that was not named, so the refutation cannot reach "
            "it. The source stays in retrieval asserting what this transaction just contradicted."
        )
    else:
        out["note"] = "refuted — check whether any artifact still asserts it."
    return out


def _normalize_claim_text(text: str | None) -> str:
    """Fold a claim string to a stable match key: strip + collapse inner
    whitespace + casefold. So an adjudication echoing the declared text matches
    even with trivial reformatting (a re-wrapped line, a case change)."""
    return " ".join(str(text or "").split()).casefold()


def _match_claim(
    raw: dict, rows: list[dict[str, Any]], by_id: dict, by_index: dict, by_text: dict
) -> dict[str, Any] | None:
    """Resolve one adjudication entry to the claim it targets, or None.

    Three keys, in order of precision: an explicit ``id`` (or 8+ char prefix); a
    1-based ``index`` matching declaration order; or the ``claim`` TEXT itself.
    Text is what a practitioner reaches for FIRST — the natural symmetric mirror
    of declaring ``{claim: "...", grounding: "..."}`` at CHECK is adjudicating
    ``{claim: "...", verdict: "..."}`` at POSTFLIGHT — so accepting it is the
    difference between the mechanism working as written and silently recording
    every verdict as ``untested`` (GH #409). Ambiguity is handled: a text that
    matches more than one declared claim is NOT resolved (the caller keeps it
    unmatched and reports it) rather than guessing.
    """
    ident = raw.get("id") or raw.get("claim_id")
    if ident:
        ident = str(ident)
        target = by_id.get(ident) or next((r for r in rows if len(ident) >= 8 and r["id"].startswith(ident)), None)
        if target is not None:
            return target
    if raw.get("index") is not None:
        try:
            return by_index.get(int(raw["index"]))
        except (TypeError, ValueError):
            return None
    if raw.get("claim"):
        # by_text maps normalized text → row, or → None when that text was
        # declared by ≥2 claims (ambiguous, don't guess).
        return by_text.get(_normalize_claim_text(raw.get("claim")))
    return None


def _open_claims(db, session_id: str, transaction_id: str | None) -> list[dict[str, Any]]:
    return _query_claims(db, session_id, transaction_id, only_open=True)


def _all_claims(db, session_id: str, transaction_id: str | None) -> list[dict[str, Any]]:
    return _query_claims(db, session_id, transaction_id, only_open=False)


def _query_claims(db, session_id: str, transaction_id: str | None, only_open: bool) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, claim_index, claim, grounding, ref, verdict, verdict_evidence "
        "FROM transaction_claims WHERE session_id = ?"
    )
    params: list[Any] = [session_id]
    if transaction_id:
        sql += " AND transaction_id = ?"
        params.append(transaction_id)
    if only_open:
        sql += " AND verdict IS NULL"
    sql += " ORDER BY claim_index"
    try:
        cur = db.conn.execute(sql, params)
    except Exception as e:
        # `[]` here is indistinguishable from "no claims were declared", so a
        # schema drift (a missing migration 062, a renamed column) disables the
        # whole claims mechanism and reports a clean zero forever. Degrading is
        # right — claims must never break POSTFLIGHT — but degrading QUIETLY is
        # what turns a fixable error into an invisible one. Say it, then degrade.
        logger.warning(f"claims query failed — reporting 0 claims, which is NOT the same as none declared: {e}")
        return []
    return [
        {
            "id": r[0],
            "claim_index": r[1],
            "claim": r[2],
            "grounding": r[3],
            "ref": r[4],
            "verdict": r[5],
            # Selected because `_refutation` reads it. A projection that omits a
            # column its own consumer reads is the shape where every test asserts
            # on the behaviour the field ENABLES and none reads it back off the row.
            "verdict_evidence": r[6],
        }
        for r in cur.fetchall()
    ]


#: Groundings whose referent is DEFINITIONALLY available at declaration time.
#: A `retrieved` claim came from a prior artifact, so the practitioner is holding
#: its id when they write the claim — an omission there is an oversight, not a
#: judgement call. `read` and `ran` also have referents (a file, a command) but
#: those are not artifact ids, so they are encouraged rather than expected.
REFERENT_EXPECTED: tuple[str, ...] = ("retrieved",)


def missing_referents(stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claims whose grounding expects a referent and that carry none.

    A refuted claim is a contradiction established by observation — the strongest
    signal this layer produces, and the only one that owes nothing to reading
    text. It is inert without a referent: measured 2026-08-21, of 17 refuted
    claims exactly ONE named what it refuted, so sixteen contradiction events
    existed that the graph could not attach to anything.

    `retrieved` is where that costs most and is cheapest to fix: the claim came
    FROM an artifact, so a refuted retrieved-claim is direct behavioural evidence
    that its source is wrong. Coverage at the time of writing: 8 of 36.
    """
    return [
        c for c in (stored or []) if (c.get("grounding") in REFERENT_EXPECTED) and not str(c.get("ref") or "").strip()
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
    # Referent coverage, echoed HERE because this is the last moment the
    # practitioner still holds the id. Reported, never rejected: this module is
    # advisory by design, and two mechanisms in this codebase died of over-firing.
    unreferenced = missing_referents(stored)
    if unreferenced:
        out["missing_referents"] = len(unreferenced)
        out["referent_note"] = (
            f"{len(unreferenced)} claim(s) grounded `retrieved` name no artifact. A retrieved "
            "claim came FROM a prior artifact, so its id is in your hand right now — and if this "
            "claim is later refuted, the referent is what turns that into evidence about the "
            "artifact rather than a verdict about nothing. Add `ref` to each."
        )
    return out
