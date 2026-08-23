"""The "nothing matched" signal cannot come from the score. Measured on two graphs.

`project-search` returned a full top-k with plausible scores for ANY input, so
every query looked answered. The obvious fix is a score floor. It does not work,
and that is not an opinion — it was measured once elsewhere and reproduced here
independently, on a different graph:

                          graph A   graph B
    memory noise floor    0.8148    0.8201
    weakest TRUE hit      0.6988    0.6828
    margin                -0.116   -0.1373     NEGATIVE, both times

True hits score BELOW pure gibberish. No cut admits every real answer while
excluding nonsense, because a dense embedding places nonsense *somewhere*, and
somewhere has neighbours at ordinary cosine distance. The score measures proximity
in the embedding; it never measured whether the query was about anything.

**Lexical agreement does separate them, at the level the answer is given.** The
question is asked of a RESULT SET — *did anything here match?* — and on that
question the final build scores, over the same fixture:

    nonsense query sets with a confirmed result     0 / 8
    true-hit query sets with a confirmed result    11 / 12

**The one miss is the honest limit and is not a bug.** *a saved location that
resolves for whoever wrote it and for nobody else* shares exactly one token
(`resolv`) with its own answer, giving 0.0653 — below the bar. And the bar must
sit above 0.1716, which is what two nonsense phrases score on a single real word
each (*indexing*, *auditing*). **A query sharing one word with its answer and a
nonsense phrase containing one real word are not distinguishable by this metric.**
Tuning `CONFIRM_MIN` into that overlap would be fitting to ten probes — the exact
move that misled the first attempt at a score threshold by 0.13.

The cost is bounded by design: an unconfirmed set still PRINTS its rows, captioned
as neighbours rather than answers. The failure mode is a misleading caption above
correct data, not withheld data.

**Why IDF over the CANDIDATE SET rather than a corpus.** A token's informativeness
is how well it distinguishes *these candidates from each other* — the question
actually being asked. It needs no corpus statistics, no second index, and cannot
go stale. A token in every candidate tells you nothing about which to pick,
whatever its global frequency.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not rerank. That was the original design and **measurement killed it.**

The second defect is that recall collapses under paraphrase — core measured 10/10
verbatim against 2/10 off-phrasing — and lexical rescue was the obvious fix, since
the sharpest miss on the other graph turned on a rare literal (`extra=allow`) that BM25 matches
and a dense embedding smears. Swept over core's graph: multiplicative fusion at
w ∈ {0.2, 0.5, 1.0} and reciprocal-rank fusion at w ∈ {0.5, 1.0, 2.0}, each at
candidate depths 50 / 100 / 200 / 400. **Dense alone scored 2/10; every hybrid
variant scored 1/10** — one configuration reached 2/10 and none beat dense. One
probe that had been a hit at rank 5 was pushed to rank 8 by the rerank.

The reason is categorical, not a tuning failure: those off-phrasings share **zero**
content tokens with their targets. A lexical method cannot rank text it has no
token in common with. It can still say *this query is about something in here* —
which is the confirmation signal, and which is why that half survived.

So: lexical is a LABEL here, never a ranking input. Dense order is returned
untouched. What the paraphrase misses actually need is measured in
`scripts/retrieval_calibration.py` — 3 of 10 are absent from a k=1000 dense sweep
entirely, so the remedy is at the embedding or query-expansion layer, not this one.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

#: Words carrying no discriminating power in a knowledge graph. Deliberately short
#: — an aggressive list strips the domain words that do the work, and every
#: surviving token is weighted by candidate-set rarity anyway, so a common word
#: costs little. This only has to remove tokens so ubiquitous that keeping them
#: would let a query confirm on grammar alone.
_STOPWORD_TEXT = (
    "a an and are as at be been being but by can could did do does doing done "
    "for from had has have having he her here hers him his how if in into is it "
    "its me my no nor not of off on once only or other our out over own same she "
    "should so some such than that the their them then there these they this "
    "those through to too under until up very was we were what when where which "
    "while who whom why will with would you your"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split(" "))

#: Below this, a token is punctuation or an article the splitter kept.
_MIN_TOKEN = 3

#: The confirmation bar.
#:
#:     0.1716   what nonsense scores on ONE real word (*indexing*, *auditing*)
#:     0.35     <- here: 2x that ceiling
#:     0.5698   weakest true hit that clears it
#:
#: Set from the shape of the distribution, not fitted to its edge. Everything
#: between 0.1716 and 0.5698 is arbitrary, and picking the edge of a ten-probe
#: fixture is how the first attempt at a score threshold went wrong: a
#: single-sample floor understated the real one by 0.13, which was the entire
#: apparent margin. Re-derive with `scripts/retrieval_calibration.py` over a fixture
#: you trust; never adjust from one example.
CONFIRM_MIN = 0.35

_SPLIT = re.compile(r"[^0-9a-z_=./-]+")
#: Inner separators of an identifier-shaped token, for emitting its parts too.
_PARTS = re.compile(r"[_=./-]+")


def _fold(token: str) -> str:
    """Crudest useful stemming: strip a trailing plural / participle, then a silent e.

    Not a real stemmer on purpose. The failure it guards against was measured
    elsewhere in this codebase — an overlap predicate whose own motivating example
    failed it because `blocks` and `block` were different tokens.

    **The trailing-e strip is not cosmetic and was earned by a false negative.** The
    first version stripped suffixes only, so `resolves` folded to `resolv` while
    `resolve` stayed whole — a mismatch on the SAME word. That cost the query *a
    saved location that resolves for whoever wrote it* its only shared token with
    its own answer, which contained `p.resolve()`, and the result set was reported
    as *nothing matched* with the correct answer sitting in it at rank 5. Exactly
    the loud-unhelpfulness failure this feature is supposed to avoid.

    Folding the `e` normalises the whole family at once: resolve / resolves /
    resolved / resolving, store / stored / storing. It over-folds occasionally
    (`core` and `cor`), which costs little here — a token is only ever evidence,
    weighted by rarity, never a filter on its own.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > _MIN_TOKEN + len(suffix) - 1 and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    if len(token) > _MIN_TOKEN and token.endswith("e"):
        token = token[:-1]
    return token


def content_tokens(text: str | None) -> set[str]:
    """Informative tokens of a string, folded and stopworded.

    `=`, `.`, `/`, `-` and `_` survive the split so identifier-shaped literals stay
    whole: `extra=allow`, `project.yaml`, `set_interval`. Those are the tokens a
    dense embedding loses, and keeping them intact is the difference between
    confirming on a distinctive symbol and confirming on the English around it.
    """
    if not text:
        return set()
    out: set[str] = set()
    for raw in _SPLIT.split(text.lower()):
        # BOTH the whole identifier and its parts. Keeping only the whole form was
        # measured to lose a true match: `str(p.resolve())` yielded the single token
        # `p.resolve`, which can never equal the bare `resolve` a question uses, so a
        # result set holding the right answer at rank 5 reported "nothing matched".
        # Keeping only the parts would lose the opposite case — `extra=allow` is
        # distinctive precisely as a unit, and split into `extra` and `allow` it is
        # two ordinary words.
        for candidate in {raw, *_PARTS.split(raw)} if _PARTS.search(raw) else {raw}:
            if len(candidate) < _MIN_TOKEN or candidate in _STOPWORDS or candidate.isdigit():
                continue
            folded = _fold(candidate)
            if len(folded) >= _MIN_TOKEN and folded not in _STOPWORDS:
                out.add(folded)
    return out


def _weights(query_tokens: set[str], doc_tokens: Sequence[set[str]]) -> dict[str, float]:
    """Candidate-set IDF for each query token.

    A token present in every candidate scores ~0 — it cannot discriminate between
    them. `df = 0` scoring maximum is deliberate: a query whose distinctive term
    appears nowhere in the pool must not reach the bar on its common words alone,
    which is precisely how gibberish sneaks past a naive overlap ratio.
    """
    n = max(len(doc_tokens), 1)
    df: Counter[str] = Counter()
    for toks in doc_tokens:
        for t in query_tokens & toks:
            df[t] += 1
    return {t: math.log(1.0 + n / (1.0 + df[t])) for t in query_tokens}


def annotate(
    query: str,
    candidates: list[dict[str, Any]],
    text_of: Callable[[dict[str, Any]], str],
    *,
    confirm_min: float = CONFIRM_MIN,
) -> list[dict[str, Any]]:
    """Stamp each candidate with `lexical` and `confirmed`. Order is NOT changed.

        lexical     0..1, the share of the query's informative weight this result
                    matches, weighted by candidate-set rarity
        confirmed   whether `lexical` clears `confirm_min`

    `score` is untouched. Reranking on `lexical` was measured to COST recall — see
    the module docstring — so this annotates and gets out of the way.

    A query of nothing but stopwords yields `None` for both, never `False`.
    Reporting "nothing matched" for a question that was never really asked is a
    different lie from the one this fixes, and a caller must be able to tell them
    apart.
    """
    if not candidates:
        return candidates

    qtoks = content_tokens(query)
    if not qtoks:
        for c in candidates:
            c["lexical"] = None
            c["confirmed"] = None
        return candidates

    doc_tokens = [content_tokens(text_of(c)) for c in candidates]
    weights = _weights(qtoks, doc_tokens)
    total = sum(weights.values()) or 1.0

    for cand, toks in zip(candidates, doc_tokens, strict=False):
        lex = sum(weights[t] for t in qtoks & toks) / total
        cand["lexical"] = round(lex, 4)
        cand["confirmed"] = lex >= confirm_min
    return candidates


def any_confirmed(results: dict[str, list[dict[str, Any]]]) -> bool:
    """True when any band holds a lexically-confirmed result — the set-level answer
    to *did anything here match?*, which the tool previously could not give."""
    return any(item.get("confirmed") for items in results.values() for item in items)
