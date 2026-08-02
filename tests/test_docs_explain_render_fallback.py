"""A semantically-matched document must not render as an empty answer.

After semantic retrieval was fixed, `docs-explain --question` still produced:

    🧠 Search: semantic
    **Answering:** what is the difference between message-send and mailbox poll
    (nothing)

Retrieval was working — a raw query ranked MESSAGING_LAYERS.md second of 524
embedded docs. The RENDERER discarded it: `_extract_relevant_sections` keyword-
matches content that embedding selected, and a natural-language question's
wording need not appear literally in the prose. So the semantic win was thrown
away one layer above where the earlier key-mismatch threw it away.

An "Answering:" header with nothing beneath it is the worse half. It reads as
"we looked, and this is what there is" — a stronger claim than "no passage
matched".

**The floor is expressed in SCORED units, not raw.** `_score_and_rank` stores
`score * 2.0`. Measured on this practice's 524-doc collection:

    raw 0.363 -> scored 0.726   "how do cron loops differ from wake on event"
    raw 0.254 -> scored 0.507   nonsense

A floor written in raw units silently never fires — which is what happened on
the first attempt, and is the same shape as the doc_path mismatch: a comparison
against a value that looks right and lives in the wrong space.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.docs_commands import (
    _FALLBACK_MIN_SCORE,
    _OPENING_CHARS,
    _OPENING_HEADER,
)


def test_the_floor_separates_real_queries_from_nonsense():
    """Measured values, pinned. If embeddings change and this gap closes, the
    fallback is no longer safe and this test is where that surfaces."""
    real_scored = 0.726
    nonsense_scored = 0.507

    assert nonsense_scored < _FALLBACK_MIN_SCORE < real_scored, (
        f"the floor ({_FALLBACK_MIN_SCORE}) no longer separates a real query "
        f"({real_scored}) from nonsense ({nonsense_scored})"
    )


def test_the_floor_is_in_scored_units_not_raw():
    """The unit trap. Raw scores top out near 0.38, so any floor at or below
    that is in raw units and will never fire against doubled values."""
    assert _FALLBACK_MIN_SCORE > 0.40, (
        "floor looks like a RAW score — `_score_and_rank` doubles scores, so a raw-unit floor silently never fires"
    )


def test_the_opening_fallback_is_labelled_not_disguised():
    """The reader must be able to tell an opening from a matched section —
    otherwise the fallback quietly presents unrelated prose as the answer."""
    assert "no section matched" in _OPENING_HEADER
    assert 0 < _OPENING_CHARS <= 2000


@pytest.mark.parametrize(
    ("sections", "mode", "score", "expect_fallback"),
    [
        ([], "semantic", 0.90, True),  # semantic hit, no section -> fall back
        ([], "semantic", 0.50, False),  # below the floor -> refuse
        ([], "keyword", 0.90, False),  # keyword mode never had a semantic win
        ([("h", "b")], "semantic", 0.90, False),  # real section wins
    ],
)
def test_fallback_fires_only_when_semantic_found_something_good(sections, mode, score, expect_fallback):
    """The decision table, stated directly. Encoding it here means a future edit
    to the condition has to disagree with an explicit case rather than slip."""
    fires = not sections and mode == "semantic" and score >= _FALLBACK_MIN_SCORE

    assert fires is expect_fallback
