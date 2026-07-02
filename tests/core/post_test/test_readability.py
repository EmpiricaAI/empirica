"""Tests for the in-house readability module.

readability.py replaced textstat (which pulled nltk → PYSEC-2026-597). These
tests pin the tokenization contract and the Flesch/FK/Gunning-Fog formulas, and
verify graceful behavior when pyphen is unavailable (heuristic fallback).
"""

from __future__ import annotations

from empirica.core.post_test import readability

# ── tokenization ─────────────────────────────────────────────────────────────


def test_lexicon_count_strips_punctuation():
    assert readability.lexicon_count("The cat sat on the mat.") == 6
    # Contractions stay one token.
    assert readability.lexicon_count("Don't stop.") == 2


def test_sentence_count_splits_on_terminators():
    assert readability.sentence_count("One. Two! Three?") == 3
    # Collapsed terminators count once.
    assert readability.sentence_count("Wait... really?!") == 2


def test_sentence_count_unterminated_is_one():
    assert readability.sentence_count("no terminator here at all") == 1


def test_empty_text_is_zero():
    assert readability.sentence_count("") == 0
    assert readability.sentence_count("   \n  ") == 0
    assert readability.lexicon_count("") == 0


# ── analyze() ────────────────────────────────────────────────────────────────


def test_analyze_degenerate_input_yields_zeros_without_raising():
    stats = readability.analyze("")
    assert stats["word_count"] == 0
    assert stats["sentence_count"] == 0
    assert stats["flesch_reading_ease"] == 0.0
    assert stats["flesch_kincaid_grade"] == 0.0
    assert stats["gunning_fog"] == 0.0


def test_analyze_returns_all_metrics_typed():
    stats = readability.analyze("The quick brown fox jumps over the lazy dog. It was a fine day.")
    assert set(stats) == {
        "word_count",
        "sentence_count",
        "flesch_reading_ease",
        "flesch_kincaid_grade",
        "gunning_fog",
    }
    assert stats["word_count"] == 14
    assert stats["sentence_count"] == 2
    assert isinstance(stats["flesch_reading_ease"], float)
    assert isinstance(stats["gunning_fog"], float)


def test_simple_prose_reads_easier_than_dense_prose():
    """Higher Flesch Reading Ease = easier. Short monosyllabic sentences should
    score well above long polysyllabic ones."""
    easy = readability.analyze("The cat sat on the mat. The dog ran. We had fun.")
    hard = readability.analyze(
        "The multidisciplinary institutional infrastructure necessitated "
        "comprehensive reconceptualization of organizational methodologies."
    )
    assert easy["flesch_reading_ease"] > hard["flesch_reading_ease"]
    # Dense polysyllabic prose demands more formal education (Gunning Fog).
    assert hard["gunning_fog"] > easy["gunning_fog"]


def test_flesch_reading_ease_matches_formula():
    """Spot-check the arithmetic on an all-monosyllabic sentence where syllables
    are unambiguous (6 words, 1 sentence, 6 syllables)."""
    stats = readability.analyze("The cat sat on the mat.")
    assert stats["word_count"] == 6
    assert stats["sentence_count"] == 1
    # 206.835 - 1.015*(6/1) - 84.6*(syll/6); each monosyllable → syll==6 → factor 84.6.
    expected = 206.835 - 1.015 * 6 - 84.6 * 1.0
    assert abs(stats["flesch_reading_ease"] - expected) < 0.01


# ── syllables + fallback ─────────────────────────────────────────────────────


def test_syllables_at_least_one_per_word():
    for word in ("a", "cat", "readable", "epistemic", "calibration"):
        assert readability._syllables_in_word(word) >= 1


def test_multisyllabic_word_counts_multiple():
    # "calibration" = ca-li-bra-tion → 4 syllables (pyphen) or ≥3 (heuristic).
    assert readability._syllables_in_word("calibration") >= 3


def test_heuristic_fallback_when_pyphen_unavailable(monkeypatch):
    """With pyphen forced unavailable, syllable counting still returns sane
    values via the vowel-group heuristic rather than raising."""
    monkeypatch.setattr(readability, "_pyphen_dic", False)
    assert readability._syllables_in_word("readable") >= 1
    assert readability._syllables_in_word("the") == 1
    # analyze still produces a full metric set with pyphen absent.
    stats = readability.analyze("The cat sat on the mat. The dog ran fast.")
    assert stats["word_count"] > 0
    assert isinstance(stats["flesch_kincaid_grade"], float)
