"""Does B *contradict* A, or merely *resemble* it?

Core had write-time contradiction detection and switched it off. ``decay.py``
still carries ``decay_eidetic_by_finding`` and ``decay_related_lessons``; both
glue functions in ``artifact_log_commands`` have returned ``0`` / ``[]`` since
2026-05-28, because the trigger was **cosine similarity ≥ 0.85** and similarity
cannot separate agreement from contradiction. *"The gate blocks praxic tools"*
and *"the gate does NOT block praxic tools"* are near-identical vectors. So a
finding that CONFIRMED a fact decayed it — the inverse of confirmation. The
docstring there already named the guard it never implemented: *only near-exact
matches with opposing content should trigger decay*. Opposing content was never
checked.

This module is that check, and it is deliberately the smallest honest version.

**Two properties it must have, in this order.**

1. **Fails safe.** Returning "not opposed" leaves behaviour exactly as it is
   today — nothing fires, nothing decays, no one is worse off. Returning
   "opposed" when they are not is the autoimmune bug that caused the disable.
   So every ambiguity resolves to *not opposed*, and the tests pin that
   direction rather than recall.

2. **Warns, never mutates.** Even a correct predicate does not license the old
   behaviour. A contradiction between two artifacts is a fact about the
   PRACTITIONER's graph that they must adjudicate — one of the two is wrong, or
   the scope differs, and no automated confidence arithmetic can tell which.
   Nothing here writes; the caller prints.

**What it does not do, stated so nobody assumes otherwise.** This is lexical.
It sees polarity flips and a small closed set of antonym pairs on an otherwise
shared claim. It will miss contradictions expressed through different
vocabulary, through numbers, or across a chain of reasoning. Those need either
an LLM in the write path (which costs tokens per session and puts a
probabilistic judge on the write path) or a structured claim shape where
opposition is decidable — both live options, neither built here. Missing them
costs nothing relative to today; that is the whole argument for shipping the
lexical version first.
"""

from __future__ import annotations

import re

#: Markers that flip a claim's polarity. Multi-word forms are matched before
#: bare ``not`` so "does not" is consumed once, not twice.
_NEGATIONS: tuple[str, ...] = (
    "does not",
    "did not",
    "is not",
    "was not",
    "are not",
    "were not",
    "cannot",
    "can not",
    "never",
    "no longer",
    "not",
    "without",
    "fails to",
    "failed to",
    "fail to",
    "absent",
    "missing",
)

#: Closed antonym pairs. Small on purpose: every pair added is a chance to fire
#: on a claim that merely mentions both words, and a check that fires wrongly
#: teaches practitioners to ignore everything printed beside it.
_ANTONYMS: tuple[tuple[str, str], ...] = (
    ("enabled", "disabled"),
    ("present", "absent"),
    ("true", "false"),
    ("exists", "missing"),
    ("works", "broken"),
    ("live", "dead"),
    ("allows", "blocks"),
    ("succeeds", "fails"),
    ("supported", "unsupported"),
    ("reachable", "unreachable"),
    ("mutable", "immutable"),
    ("resolved", "unresolved"),
)

_WORD = re.compile(r"[a-z0-9_]+")

#: Below this shared-content overlap the two texts are not about the same claim,
#: so any difference between them means nothing. Opposition requires BOTH "same
#: subject" and "opposite sense"; similarity alone was the original bug, and
#: sense alone would fire on any two unrelated sentences where one happens to
#: contain "not".
MIN_OVERLAP = 0.5

#: Polarity needs a HIGHER bar than antonyms, and the reason is the one place
#: this module is not automatically fail-safe. Parity over a negation list is
#: only correct if the list is complete, and it never is — an UNLISTED negation
#: in one text makes the computed parities differ, which produces a false
#: POSITIVE, the dangerous direction. Antonyms have no such failure mode: they
#: come from a closed set, and a missing pair only ever costs recall.
#:
#: Requiring the two texts to be otherwise near-identical is the guard. When the
#: only material difference is a negation, the negation is what the difference
#: IS; at moderate overlap an unlisted negator is the likelier explanation for a
#: parity gap than a real contradiction.
POLARITY_MIN_OVERLAP = 0.8


def _polarity(text: str) -> int:
    """Count of polarity-flipping markers. Even/odd is what matters, not the total."""
    t = f" {text.lower()} "
    count = 0
    for marker in _NEGATIONS:
        # Consume matches so "does not" is not also counted as "not".
        hits = t.count(f" {marker} ")
        if hits:
            count += hits
            t = t.replace(f" {marker} ", " ")
    return count


def _content_words(text: str) -> set[str]:
    stop = {
        "the", "a", "an", "is", "was", "are", "were", "be", "been", "it", "its", "this", "that",
        "of", "to", "in", "on", "for", "and", "or", "but", "as", "at", "by", "with", "from",
        "does", "did", "do", "not", "no", "so", "then", "than", "when", "which", "who", "we",
    }  # fmt: skip
    return {w for w in _WORD.findall(text.lower()) if w not in stop and len(w) > 2}


def opposes(claim_a: str, claim_b: str) -> dict | None:
    """Whether ``claim_b`` contradicts ``claim_a``. None when it does not.

    Returns ``{"reason", "signal", "overlap"}`` on a positive, so the caller can
    show WHY rather than assert opposition and leave the practitioner to guess.
    An unexplained warning is one that gets dismissed.
    """
    if not claim_a or not claim_b:
        return None

    a_words, b_words = _content_words(claim_a), _content_words(claim_b)
    if not a_words or not b_words:
        return None
    overlap = len(a_words & b_words) / max(len(a_words), len(b_words))
    if overlap < MIN_OVERLAP:
        return None  # different claims; a polarity difference says nothing

    # Antonym pair split across the two texts, on the shared subject.
    for left, right in _ANTONYMS:
        a_l, a_r = left in a_words, right in a_words
        b_l, b_r = left in b_words, right in b_words
        if (a_l and b_r and not a_r and not b_l) or (a_r and b_l and not a_l and not b_r):
            return {
                "reason": f"antonym pair on a shared claim: {left!r} vs {right!r}",
                "signal": "antonym",
                "overlap": round(overlap, 2),
            }

    # Polarity flip: one asserts, the other denies, and the texts are otherwise
    # near-identical so the negation IS the difference (see POLARITY_MIN_OVERLAP
    # for why this bar is higher than the antonym one).
    if overlap >= POLARITY_MIN_OVERLAP and _polarity(claim_a) % 2 != _polarity(claim_b) % 2:
        return {
            "reason": "same claim, opposite polarity (one asserts what the other denies)",
            "signal": "polarity",
            "overlap": round(overlap, 2),
        }

    return None
