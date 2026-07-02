"""In-house, dependency-light readability metrics.

Replaces ``textstat`` for the prose evidence collector. textstat pulled ``nltk``
transitively (``nltk`` was ``Required-by`` textstat only), and ``nltk`` carried
PYSEC-2026-597 (path-traversal file-read, no fix available). The readability
indices textstat gave us are public-domain arithmetic, so we compute them
directly here and drop the whole subtree.

Syllable counting uses ``pyphen`` — textstat's *own* nltk-free syllable backend,
so the numbers stay close to the old textstat output and existing calibration
baselines don't shift. When pyphen isn't installed (it ships in the ``[prose]``
extra), a vowel-group heuristic keeps the metrics working rather than failing —
the values feed soft ``clarity``/``density`` signals where approximation is fine.

Public API:
    analyze(text)             -> dict with all metrics in a single pass
    flesch_reading_ease(text) -> float
    flesch_kincaid_grade(text)-> float
    gunning_fog(text)         -> float
    lexicon_count(text)       -> int   (word count, punctuation stripped)
    sentence_count(text)      -> int
"""

from __future__ import annotations

import re

# Word = run of letters/digits/apostrophes; keeps contractions ("don't") intact.
_WORD_RE = re.compile(r"[A-Za-z0-9']+")
# Sentence boundary = run of ., !, or ? (collapses "?!" and "..." to one break).
_SENTENCE_END_RE = re.compile(r"[.!?]+")
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")

# pyphen dictionary is loaded lazily and cached. Sentinel values:
#   None  -> not yet attempted
#   False -> attempted, unavailable (use heuristic)
_pyphen_dic: object | None = None


def _get_pyphen():
    """Return a cached pyphen dictionary, or False if pyphen is unavailable."""
    global _pyphen_dic
    if _pyphen_dic is None:
        try:
            import pyphen  # pyright: ignore[reportMissingImports]

            _pyphen_dic = pyphen.Pyphen(lang="en_US")
        except Exception:
            _pyphen_dic = False
    return _pyphen_dic


def _syllables_in_word(word: str) -> int:
    """Syllable count for a single word (minimum 1 for any non-empty word)."""
    w = word.lower().strip("'")
    if not w:
        return 0
    dic = _get_pyphen()
    if dic:
        # pyphen inserts hyphens at valid break points; syllables = breaks + 1.
        return max(1, dic.inserted(w).count("-") + 1)  # pyright: ignore[reportAttributeAccessIssue]
    # Heuristic fallback: count vowel groups, drop a silent trailing "e".
    count = len(_VOWEL_GROUP_RE.findall(w))
    if w.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def lexicon_count(text: str) -> int:
    """Number of word tokens, punctuation stripped."""
    return len(_WORD_RE.findall(text))


def sentence_count(text: str) -> int:
    """Number of sentences. At least 1 for any non-empty text (unterminated
    prose still counts as one sentence)."""
    if not text.strip():
        return 0
    parts = [p for p in _SENTENCE_END_RE.split(text) if p.strip()]
    return max(1, len(parts))


def analyze(text: str) -> dict:
    """Compute all readability metrics in a single pass.

    Returns a dict with word_count, sentence_count, flesch_reading_ease,
    flesch_kincaid_grade, and gunning_fog. Degenerate input (no words or no
    sentences) yields zeroed indices rather than raising.
    """
    words = _WORD_RE.findall(text)
    n_words = len(words)
    n_sentences = sentence_count(text)

    if n_words == 0 or n_sentences == 0:
        return {
            "word_count": n_words,
            "sentence_count": n_sentences,
            "flesch_reading_ease": 0.0,
            "flesch_kincaid_grade": 0.0,
            "gunning_fog": 0.0,
        }

    syllables_per_word = [_syllables_in_word(w) for w in words]
    n_syllables = sum(syllables_per_word)
    complex_words = sum(1 for s in syllables_per_word if s >= 3)

    words_per_sentence = n_words / n_sentences
    syllables_per_word_avg = n_syllables / n_words

    return {
        "word_count": n_words,
        "sentence_count": n_sentences,
        # Flesch Reading Ease (higher = easier). 0.0-100+ typical range.
        "flesch_reading_ease": 206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word_avg,
        # Flesch-Kincaid Grade (U.S. school grade level).
        "flesch_kincaid_grade": 0.39 * words_per_sentence + 11.8 * syllables_per_word_avg - 15.59,
        # Gunning Fog (years of formal education needed).
        "gunning_fog": 0.4 * (words_per_sentence + 100.0 * (complex_words / n_words)),
    }


def flesch_reading_ease(text: str) -> float:
    return analyze(text)["flesch_reading_ease"]


def flesch_kincaid_grade(text: str) -> float:
    return analyze(text)["flesch_kincaid_grade"]


def gunning_fog(text: str) -> float:
    return analyze(text)["gunning_fog"]
