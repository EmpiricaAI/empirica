"""`project-search` could not say "nothing here matched", and the obvious fix is
provably impossible.

Measured once elsewhere and reproduced here independently, on a different graph:

                          graph A   graph B
    memory noise floor    0.8148    0.8201
    weakest TRUE hit      0.6988    0.6828
    margin                -0.116   -0.1373      NEGATIVE, both times

True hits score BELOW pure gibberish, so **no score cut exists** that admits every
real answer while excluding nonsense. A dense embedding places nonsense somewhere,
and somewhere has neighbours at ordinary cosine distance.

The signal that does work is lexical, measured at the level the answer is given —
a result SET — where the final build scores 0/8 nonsense sets confirmed against
11/12 true-hit sets.

**Half the original design was killed by measurement and these tests protect the
corpse.** Reranking on the lexical signal was swept over multiplicative fusion
(w ∈ {0.2, 0.5, 1.0}) and RRF (w ∈ {0.5, 1.0, 2.0}) at depths 50/100/200/400:
dense alone scored 2/10 off-phrasing, every hybrid variant scored 1/10. So
`annotate` must never reorder — `test_annotate_does_not_reorder` is the guard, and
it is the most load-bearing test in the file.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.project_search import _unconfirmed_banner
from empirica.core.qdrant.lexical import CONFIRM_MIN, annotate, any_confirmed, content_tokens


def _text(item):
    return item.get("text") or ""


def _annotated(query, texts):
    cands = [{"text": t, "score": 1.0 - i * 0.01} for i, t in enumerate(texts)]
    return annotate(query, cands, _text)


# ── tokenisation ─────────────────────────────────────────────────────────────


def test_an_identifier_yields_both_the_whole_form_and_its_parts():
    """Measured both ways round.

    Whole-only lost a true match: `str(p.resolve())` produced the single token
    `p.resolve`, which can never equal the bare `resolve` a question uses, and a
    result set holding the right answer at rank 5 was captioned "nothing matched".
    Parts-only loses the opposite case: `extra=allow` is distinctive AS A UNIT, and
    split into `extra` and `allow` it is two ordinary words.
    """
    toks = content_tokens("ClientCapabilities has no extra=allow field")
    assert "extra=allow" in toks, "the rare literal must survive whole"
    assert {"extra", "allow"} <= toks, "and its parts must be matchable separately"


def test_a_word_and_its_inflections_fold_together():
    """An overlap predicate elsewhere in this codebase failed its own motivating
    example because `blocks` and `block` were different tokens."""
    forms = ["resolve", "resolves", "resolved", "resolving"]
    folded = {next(iter(content_tokens(f))) for f in forms}
    assert len(folded) == 1, f"same word, {len(folded)} tokens: {folded}"


def test_the_trailing_e_strip_is_what_makes_that_work():
    """NEGATIVE CONTROL on the specific bug. Stripping suffixes alone gives
    `resolves` -> `resolv` while `resolve` stays whole — a mismatch on the SAME
    word, which is what cost the real query its only shared token."""
    assert content_tokens("resolves") == content_tokens("p.resolve()") & content_tokens("resolves")


def test_stopwords_and_fragments_are_dropped():
    assert content_tokens("the and of it is a") == set()
    assert content_tokens("") == set()
    assert content_tokens(None) == set()


# ── confirmation ─────────────────────────────────────────────────────────────


def test_a_query_sharing_nothing_is_not_confirmed():
    """The measured case: `purple giraffe tessellation quarterly harmonica
    logistics` returned five memory results with scores up to 0.82."""
    out = _annotated(
        "purple giraffe tessellation quarterly harmonica logistics",
        [
            "the cockpit refresh scheduled aggregate_all on a fixed period",
            "entity_registry rows named a table absent from the database",
        ],
    )
    assert all(o["lexical"] == 0.0 for o in out)
    assert not any(o["confirmed"] for o in out)


def test_a_query_sharing_its_subject_is_confirmed():
    """POSITIVE CONTROL, and not optional: without it a `confirmed` that is always
    False passes the test above perfectly."""
    out = _annotated(
        "the cockpit refresh scheduled aggregate_all on a fixed period",
        ["the cockpit refresh scheduled aggregate_all on a fixed period", "unrelated text about lesson storage layers"],
    )
    assert out[0]["confirmed"] is True
    assert out[0]["lexical"] > CONFIRM_MIN


def test_one_incidental_word_does_not_confirm():
    """The bar exists for exactly this. Two nonsense phrases reached 0.1716 on a
    single real word each — *indexing* and *auditing* — and anything below the bar
    would have let them through."""
    out = _annotated(
        "corrugated marmalade indexing for equestrian sundials",
        [
            "a session narrative about indexing the eidetic collection for retrieval "
            "with confidence and phase and transaction and goal and impact"
        ],
    )
    assert not out[0]["confirmed"], f"one shared word scored {out[0]['lexical']}"


def test_a_token_in_every_candidate_is_worth_less_than_a_rare_one():
    """Candidate-set IDF, and why it is the right statistic: a token common to every
    candidate cannot tell you which to pick, whatever its global rarity.

    Asserted as a COMPARISON between two candidates rather than against the bar,
    because the first version of this test asserted the wrong thing and passing it
    would have meant the code was broken. A single-token query normalises to
    lexical 1.0 by construction — the weight is both numerator and denominator, so
    IDF cancels — which is correct behaviour: *qdrant* against three results about
    qdrant IS a match. The discrimination only exists BETWEEN tokens, so only a
    multi-token query can show it.

    Sized at the production candidate depth on purpose. Rarity is estimated FROM
    the pool, so a three-document pool barely discriminates at all — there, the
    everywhere-token still scored 0.3792 and cleared the bar. That is not a defect
    in the metric, it is what `_CANDIDATE_DEPTH = 50` exists to prevent, and a test
    at toy scale would have argued for changing the code to fix the fixture.
    """
    pool = [f"qdrant collection {i}" for i in range(49)] + ["qdrant tessellation gamma"]
    out = _annotated("qdrant tessellation", pool)
    common_only, has_rare = out[0]["lexical"], out[-1]["lexical"]
    assert has_rare > common_only, "the rare token must outweigh the ubiquitous one"
    assert common_only < CONFIRM_MIN, "matching only the everywhere-token is not confirmation"
    assert has_rare > CONFIRM_MIN, "and matching the rare one must clear the bar"


def test_a_stopword_only_query_is_unknown_not_unmatched():
    """None, never False. "nothing matched" for a question never really asked is a
    different lie from the one being fixed, and a caller must tell them apart."""
    out = _annotated("the and of it", ["some real content here"])
    assert out[0]["confirmed"] is None
    assert out[0]["lexical"] is None


def test_no_candidates_is_not_an_error():
    assert annotate("anything", [], _text) == []


# ── the rerank that measurement killed ───────────────────────────────────────


def test_annotate_does_not_reorder():
    """THE guard. Fusion was swept over two rules x three weights x four depths and
    every variant scored WORSE than dense on off-phrasing recall — 1/10 against
    2/10 — with one probe pushed from rank 5 to rank 8. Dense order ships."""
    texts = ["nothing in common at all", "cockpit refresh aggregate_all fixed period", "also unrelated"]
    out = annotate(
        "cockpit refresh aggregate_all fixed period",
        [{"text": t, "score": 1.0 - i} for i, t in enumerate(texts)],
        _text,
    )
    assert [o["text"] for o in out] == texts, "annotate reordered — the rerank is back"


def test_annotate_does_not_touch_score():
    """`score` is what every existing caller ranks on, including a POSTFLIGHT hook.
    Rewriting it is how the rerank would sneak back in through a side door."""
    cands = [{"text": "cockpit refresh aggregate_all", "score": 0.42}]
    annotate("cockpit refresh aggregate_all", cands, _text)
    assert cands[0]["score"] == 0.42


# ── the set-level answer, and the banner it drives ───────────────────────────


def test_any_confirmed_is_the_set_level_answer():
    confirmed = {"memory": [{"confirmed": False}, {"confirmed": True}]}
    assert any_confirmed(confirmed) is True
    assert any_confirmed({"memory": [{"confirmed": False}]}) is False
    assert any_confirmed({"memory": []}) is False


def test_the_banner_fires_only_when_the_signal_exists_and_says_no():
    assert _unconfirmed_banner({"memory": [{"confirmed": False}]}) is not None
    assert _unconfirmed_banner({"memory": [{"confirmed": True}, {"confirmed": False}]}) is None


@pytest.mark.parametrize(
    "results",
    [
        {"memory": [{"score": 0.9}]},
        {"memory": [{"confirmed": None}]},
        {"memory": []},
        {},
    ],
    ids=["no-field", "unknown", "empty-band", "no-bands"],
)
def test_the_banner_stays_silent_when_the_signal_is_unavailable(results):
    """An old server, a degraded annotator, a stopword query. Printing "nothing
    matched" because the checker never ran is the same defect one layer along —
    and it would be indistinguishable from a real negative."""
    assert _unconfirmed_banner(results) is None


def test_the_banner_says_the_rows_below_are_neighbours_not_answers():
    """The banner does not FILTER, and the wording is what makes that safe: the
    rows still print, so a reader can see what almost-matched and judge. One true
    hit in twelve is captioned this way — a misleading caption above correct data,
    never withheld data."""
    banner = _unconfirmed_banner({"memory": [{"confirmed": False}]})
    assert "nothing here matched" in banner
    assert "not answers" in banner
