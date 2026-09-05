"""Adopting shared criteria: translate at the boundary, never degrade silently.

Cortex is registry-and-transport of criteria, never the evaluator — the evidence
a criterion grades against is seat-local by nature. So a shared criterion is
checked by nothing until a local seat adopts it, and adoption crosses a
vocabulary boundary: cortex stores free text, core validates a closed set.

Two rules, both argued for by cortex and both load-bearing:

1. **Never map an unparsed criterion to `completion`** — that is the defect the
   widening removed, where one method applied to everything and produced a
   verdict on every criterion.
2. **Two labels.** `undetermined` (the AUTHOR did not grade it → re-run the
   protocol) and `untranslated` (CORE'S PARSER failed → extend the mapping) have
   different remediations, so one label makes the second untriggerable: nobody
   sweeps `undetermined` asking which rows are their own parser's fault.
"""

from __future__ import annotations

from empirica.core.goals.adoption import UNTRANSLATED, adopt, translate, untranslated_backlog
from empirica.core.goals.validation import VALID_VALIDATION_METHODS


def test_a_typed_criterion_translates_to_the_real_method():
    r = translate("completion:subtask_ratio@>=1.0")

    assert r["validation_method"] == "completion"
    assert r["description"] == "subtask_ratio"
    assert r["threshold"] == 1.0


def test_the_authors_non_claim_survives_as_a_non_claim():
    """Upgrading `undetermined` to something checkable would invent a grading
    the author explicitly declined to make."""
    r = translate("undetermined: cannot state a done-condition until the schema lands")

    assert r["validation_method"] == "undetermined"
    assert "schema lands" in r["description"]


def test_an_unknown_method_is_UNTRANSLATED_not_completion():
    """THE rule. A peer on a newer vocabulary writes `tests_pass:...`; a silent
    fallback to `completion` would grade it by subtask ratio and report a verdict
    — unfalsifiable success, the exact defect the widening removed."""
    r = translate("some_future_method:some_metric@>=0.9")

    assert r["validation_method"] == UNTRANSLATED
    assert "some_future_method" in r["reason"]
    assert r["original"] == "some_future_method:some_metric@>=0.9"


def test_free_text_is_UNTRANSLATED_not_dropped():
    """Lossless in the direction that matters: a parser that did not recognise
    something must not discard what a peer wrote."""
    r = translate("the export flow stops confusing first-time users")

    assert r["validation_method"] == UNTRANSLATED
    assert r["original"] == "the export flow stops confusing first-time users"


def test_UNTRANSLATED_and_UNDETERMINED_are_DIFFERENT_labels():
    """Cortex's pushback, in one assertion. Same-label collapse would make the
    translator-gap remediation permanently untriggerable."""
    author_gap = translate("undetermined: nobody has decided what done means")
    parser_gap = translate("wat:thing@>=1.0")

    assert author_gap["validation_method"] == "undetermined"
    assert parser_gap["validation_method"] == UNTRANSLATED
    assert author_gap["validation_method"] != parser_gap["validation_method"]


def test_untranslated_is_NOT_authorable():
    """A practitioner hand-writing `untranslated:` would be claiming core's
    parser failed on something core's parser never saw — not a claim they are
    positioned to make. It is produced at translation, never accepted at
    authoring."""
    assert UNTRANSLATED not in VALID_VALIDATION_METHODS


def test_a_structured_peer_criterion_passes_through():
    """A peer already speaking the vocabulary must not be re-parsed out of it."""
    r = translate({"description": "ruff_violation_density", "validation_method": "quality_gate", "threshold": 0.0})

    assert r["validation_method"] == "quality_gate"
    assert r["threshold"] == 0.0


def test_adopt_reports_the_SPLIT_not_just_a_success():
    """A run where every criterion failed to translate must not read as a
    successful adoption — which a bare `adopted` count would allow."""
    result = adopt(
        [
            "completion:subtask_ratio@>=1.0",
            "undetermined: not decided yet",
            "wat:thing@>=1.0",
            "some free text nobody can parse",
        ]
    )

    assert result["total"] == 4
    assert result["evaluable"] == 1
    assert result["declared_unclaimable"] == 1
    assert result["untranslated"] == 2


def test_untranslated_reasons_are_NAMED_not_counted():
    """A count says something is wrong; the reason says which vocabulary entry
    would fix it. The backlog is only actionable with the second."""
    result = adopt(["wat:thing@>=1.0"])

    assert result["untranslated_reasons"]
    assert "wat" in result["untranslated_reasons"][0]


def test_the_backlog_is_QUERYABLE():
    """The whole point of the second label. Without a way to ask which criteria
    the parser failed on, the mapping never grows and the gap becomes permanent
    while looking like ordinary author uncertainty."""
    result = adopt(["completion:subtask_ratio@>=1.0", "wat:thing@>=1.0", "undetermined: tbd"])

    backlog = untranslated_backlog(result["criteria"])

    assert len(backlog) == 1
    assert backlog[0]["original"] == "wat:thing@>=1.0"
    assert "wat" in backlog[0]["reason"]


def test_an_empty_criterion_is_untranslated_not_silently_skipped():
    r = translate("")

    assert r["validation_method"] == UNTRANSLATED
    assert "empty" in r["reason"]


def test_adopting_nothing_is_not_an_error():
    """NEGATIVE CONTROL: a shared goal with no criteria is the common case
    cortex deliberately does not auto-fill."""
    result = adopt(None)

    assert result["total"] == 0
    assert result["untranslated"] == 0


def test_no_criterion_ever_becomes_completion_by_accident():
    """THE invariant, swept over shapes rather than asserted once. `completion`
    may only appear when the author asked for it."""
    for text in ("wat:thing@>=1.0", "free text", "", "unknown_method:m@>=1", "  "):
        assert translate(text)["validation_method"] != "completion", text
