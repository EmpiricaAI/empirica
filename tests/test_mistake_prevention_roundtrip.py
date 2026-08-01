"""`prevention` persisted correctly and came back empty — or as the word "None".

#392 (FrancisFerrero). The load-bearing field on a mistake artifact is
`prevention`: the entire reason to record a mistake is that the prevention
resurfaces before you repeat it. A mistake that surfaces without it is close to
useless, which makes this a more severe bug than "a field is empty" suggests.

Three defects, all of them about one string being built in three places and read
in a fourth:

1. `m.get("prevention", "")` returns **None**, not `""`, when the key is present
   with a null value — the default only fires for a *missing* key. 27 of 176
   mistakes in this practice had a present-but-NULL prevention, so the f-string
   rendered the literal `"None"` into the embedded text and retrieval handed
   that back as content.
2. `artifact_log_commands` wrote `prevention or 'none specified'`;
   `project_embed` and `rebuild` wrote the raw value. Whether a mistake surfaced
   usefully depended on which path last embedded it.
3. The reader tested `"Prevention:" in text` but split on `"Prevention: "` with
   a trailing space, so a text truncated right after the colon passed the test
   and then raised IndexError.
"""

from __future__ import annotations

import pytest

from empirica.core.mistake_text import build_mistake_text, parse_mistake_text

# ── the None that looked like content ─────────────────────────────────


def test_a_null_prevention_does_not_become_the_word_None():
    """POSITIVE CONTROL — the 27 rows. `f"{None}"` is the four characters
    "None", which is worse than empty because it looks like a real answer."""
    text = build_mistake_text("shipped a broken hook", None)

    assert "None" not in text
    assert parse_mistake_text(text)[1] == ""


def test_a_missing_prevention_omits_the_marker_entirely():
    """Absent and 'the string None' are different claims. A reader that gets no
    marker knows the field is missing."""
    assert "Prevention:" not in build_mistake_text("did a thing", None)


@pytest.mark.parametrize("sentinel", ["None", "none", "none specified", "n/a", "  ", ""])
def test_historical_absent_sentinels_read_as_absent(sentinel):
    """Rows embedded before the shared builder existed still carry these, and a
    re-embed cannot be forced on every practice — so the reader treats them as
    absent rather than as text."""
    assert parse_mistake_text(f"did a thing Prevention: {sentinel}")[1] == ""


# ── the reader that could raise ───────────────────────────────────────


def test_a_text_truncated_after_the_colon_does_not_raise():
    """POSITIVE CONTROL for defect 3. The old reader tested for "Prevention:"
    and split on "Prevention: " — splitting on a string that is not present
    returns a one-element list, and [1] raised IndexError."""
    mistake, prevention = parse_mistake_text("a long mistake description Prevention:")

    assert mistake == "a long mistake description"
    assert prevention == ""


def test_text_with_no_marker_at_all_is_all_mistake():
    assert parse_mistake_text("just the mistake, nothing else") == ("just the mistake, nothing else", "")


@pytest.mark.parametrize("text", [None, "", "   "])
def test_degenerate_input_never_raises(text):
    assert parse_mistake_text(text) == ("", "")


# ── round-trip, both writer dialects ──────────────────────────────────


def test_the_round_trip_preserves_a_real_prevention():
    """NEGATIVE CONTROL: treating everything as absent would pass every test
    above while destroying the field this whole module exists to carry."""
    text = build_mistake_text("Ran narrow ruff", "run full-tree ruff before push")

    assert parse_mistake_text(text) == ("Ran narrow ruff", "run full-tree ruff before push")


def test_the_prefixed_dialect_round_trips_identically():
    """The live log path writes a `MISTAKE: ` prefix; the bulk re-embed paths do
    not. Both must parse the same, or the same artifact reads differently
    depending on which path last embedded it — which is what happened."""
    prefixed = build_mistake_text("Ran narrow ruff", "run full-tree ruff", prefix=True)
    plain = build_mistake_text("Ran narrow ruff", "run full-tree ruff")

    assert prefixed.startswith("MISTAKE: ")
    assert parse_mistake_text(prefixed) == parse_mistake_text(plain)


def test_a_prevention_containing_the_marker_word_survives():
    """Preventions are prose and may say "prevention". Splitting on the first
    marker must not truncate the rest of the sentence."""
    text = build_mistake_text("x", "the prevention is: always check Prevention: twice")

    assert parse_mistake_text(text)[1] == "the prevention is: always check Prevention: twice"


# ── the retrieval projection uses the shared parser ───────────────────


def test_retrieval_strips_the_prefix_from_the_mistake_half():
    """The inline split this replaced took text.split(" Prevention:")[0], which
    left `MISTAKE: ` in place for anything the live log path embedded."""
    import empirica.core.qdrant.pattern_retrieval as pr

    entry = pr._mistake_entry({"text": "MISTAKE: broke trunk Prevention: run CI", "score": 0.9}, "score")

    assert entry["mistake"] == "broke trunk"
    assert entry["prevention"] == "run CI"
    assert entry["score"] == 0.9


def test_retrieval_of_a_null_prevention_row_yields_empty_not_None():
    """End-to-end on the shape the 27 historical rows actually have."""
    import empirica.core.qdrant.pattern_retrieval as pr

    entry = pr._mistake_entry({"text": "did a thing Prevention: None", "score": 0.5}, "similarity")

    assert entry["prevention"] == ""
    assert entry["mistake"] == "did a thing"


def test_no_call_site_builds_the_string_by_hand_any_more():
    """Source guard. Three authors, two behaviours, one bug — this fails if a
    fourth dialect appears."""
    from pathlib import Path

    import empirica.cli.command_handlers.artifact_log_commands as alc
    import empirica.cli.command_handlers.project_embed as pe
    import empirica.core.qdrant.rebuild as rb

    for module in (alc, pe, rb):
        src = Path(module.__file__).read_text(encoding="utf-8")
        assert "Prevention: {" not in src, f"{module.__name__} is hand-building the mistake text again"
        assert "build_mistake_text" in src, f"{module.__name__} no longer uses the shared builder"
