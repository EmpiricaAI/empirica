"""RETIRED — kept so the measurement that retired it has something to measure.

The tests below still pass: the predicate does what it was written to do. That is
precisely the finding. **Measured 2026-08-21 over this practice's whole corpus —
6,704 artifacts, every pair sharing a content word, 9,064,779 pairs — it fired
zero times.** A working instrument, pointed at real data, for its entire life,
yielding nothing.

And the case it was written for is a false negative of itself: the original
docstring argued from *"the gate blocks praxic tools"* vs *"...does NOT block..."*,
which is ``blocks`` vs ``block``, overlap 0.75 against a 0.8 bar. Pinned below, so
the next reader meets the limit before the thresholds.

The write path no longer calls it (also pinned below). The behavioural replacement
— a claim adjudicated ``refuted``, which is a contradiction established by running
the thing — lives in ``empirica/core/claims.py``.

Original docstring follows.

The predicate that had to exist before contradiction detection could come back.

Core's write-time contradiction detection was switched off on 2026-05-28 because
it fired on cosine similarity ≥ 0.85, and similarity cannot separate agreement
from contradiction — so a finding that CONFIRMED a fact decayed it. The feature
was never missing; the predicate was.

Nearly every test here pins a NEGATIVE. That is deliberate and it is the point:
a false positive is the exact autoimmune failure that caused the disable, while a
false negative leaves behaviour identical to today. Recall is cheap to improve
later; precision is what the mechanism was killed for lacking.
"""

from __future__ import annotations

from empirica.core.opposition import MIN_OVERLAP, POLARITY_MIN_OVERLAP, opposes

# ── the case the original predicate got backwards ────────────────────────────


def test_a_confirming_restatement_is_not_opposition():
    """The autoimmune bug, stated as a test.

    Two near-identical texts scored ≥ 0.85 cosine and the old code decayed on
    that alone. Agreement is the commonest form of high similarity.
    """
    a = "the sentinel gate blocks praxic tools before check"
    b = "the sentinel gate blocks praxic tool calls before the check gate"
    assert opposes(a, b) is None


def test_a_polarity_flip_on_the_same_claim_is_opposition():
    a = "the sentinel gate blocks praxic tools before check"
    b = "the sentinel gate does not block praxic tools before check"
    verdict = opposes(a, b)
    assert verdict is not None
    assert verdict["signal"] == "polarity"


def test_an_antonym_pair_on_the_same_claim_is_opposition():
    a = "the decay glue in artifact log commands is enabled for findings"
    b = "the decay glue in artifact log commands is disabled for findings"
    verdict = opposes(a, b)
    assert verdict is not None
    assert verdict["signal"] == "antonym"
    assert "enabled" in verdict["reason"]


# ── precision: everything that must NOT fire ─────────────────────────────────


def test_unrelated_claims_do_not_oppose_even_when_one_is_negative():
    """Polarity alone is not opposition.

    Without the overlap floor, any negative sentence would contradict any
    positive one — the check would fire constantly and be dismissed.
    """
    a = "qdrant embeddings are recomputed on every postflight"
    b = "the homebrew tap does not carry the published sha256"
    assert opposes(a, b) is None


def test_a_claim_mentioning_both_antonyms_does_not_oppose_itself():
    """A text that names both sides is describing a distinction, not taking one."""
    a = "the glue is disabled while the machinery in decay stays enabled"
    b = "the glue is disabled while the machinery in decay stays enabled and intact"
    assert opposes(a, b) is None


def test_double_negation_is_agreement_not_opposition():
    """Parity, not presence. Two flips return to the original polarity."""
    a = "the resolver does not fail to expand the prefix"
    b = "the resolver does not fail to expand the id prefix"
    assert opposes(a, b) is None


def test_empty_or_wordless_input_is_never_opposition():
    assert opposes("", "the gate blocks praxic tools") is None
    assert opposes("the gate blocks praxic tools", "") is None
    assert opposes("...", "!!!") is None


def test_the_overlap_floor_is_what_gates_polarity():
    """Named explicitly so a future tuner sees which knob trades precision away."""
    a = "the sentinel gate blocks praxic tools before check"
    b = "the sentinel gate does not block praxic tools before check"
    assert opposes(a, b) is not None
    assert MIN_OVERLAP >= 0.5, (
        "lowering the overlap floor buys recall by admitting unrelated claims — "
        "the failure mode that got this mechanism disabled the first time"
    )


def test_the_verdict_explains_itself():
    """An unexplained warning is one that gets dismissed."""
    verdict = opposes(
        "the listener service is reachable from the daemon",
        "the listener service is unreachable from the daemon",
    )
    assert verdict is not None
    assert verdict["reason"]
    assert 0.0 <= verdict["overlap"] <= 1.0


def test_opposition_is_symmetric():
    """A contradiction does not depend on which artifact was logged first."""
    a = "the tap formula carries the published sha"
    b = "the tap formula does not carry the published sha"
    assert (opposes(a, b) is None) == (opposes(b, a) is None)


def test_polarity_needs_a_higher_bar_than_antonyms_and_the_asymmetry_is_deliberate():
    """The one place this module is not automatically fail-safe.

    Parity over a negation list is only correct if the list is complete, and it
    never is: an UNLISTED negator in one text makes the parities differ and
    produces a false POSITIVE — the dangerous direction. Antonyms cannot fail
    that way; a missing pair only costs recall. Hence the higher floor, and
    hence this test, so a future tuner lowering it sees the reason first.
    """
    assert POLARITY_MIN_OVERLAP > MIN_OVERLAP

    # Same subject, moderate overlap, one side carries a negator the list does
    # not know ("scarcely"). Parity differs, and it must NOT be reported.
    a = "the listener forwards presence records for its own practice"
    b = "the listener scarcely forwards presence records for another practice today"
    assert opposes(a, b) is None


# ── the retirement, pinned ───────────────────────────────────────────────────


def test_its_own_motivating_example_does_not_fire():
    """`blocks` vs `block`. English inflection, no stemmer, and the bar is 0.8.

    NOT a bug to fix by loosening the threshold — that buys this one case and
    gives back the false-positive direction the autoimmune disable came from.
    It is the limit of lexical matching over natural-language text, which is why
    the replacement is behavioural rather than better-tuned.
    """
    assert opposes("the gate blocks praxic tools", "the gate does not block praxic tools") is None
    # POSITIVE CONTROL: identical but for the inflection, it fires. So the miss is
    # the stemming, measured — not the instrument being dead.
    assert opposes("the gate blocks praxic tools", "the gate does not blocks praxic tools") is not None


def test_the_write_path_no_longer_calls_it():
    """Retirement means detached, not merely deprecated in a docstring."""
    from pathlib import Path

    handler = (
        Path(__file__).resolve().parent.parent / "empirica" / "cli" / "command_handlers" / "artifact_log_commands.py"
    )
    src = handler.read_text()
    assert "from empirica.core.opposition import" not in src
    assert "_contradictions_safe" not in src

    # POSITIVE CONTROL for the grep: the same probe finds the import where it
    # genuinely lives, so the absence above is measured through a live instrument.
    ours = Path(__file__).read_text()
    assert "from empirica.core.opposition import" in ours


def test_the_behavioural_replacement_exists_and_names_its_referent():
    """The signal this module reached for, produced by observation instead of spelling."""
    from empirica.core import claims as C

    row = {"claim": "storage dedupes on hash", "grounding": "retrieved", "ref": "f-9c1", "verdict": "refuted"}
    out = C._refutation(row)
    assert out["ref"] == "f-9c1"
    assert "retracted" in out["note"]
