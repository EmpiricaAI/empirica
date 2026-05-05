"""Stylometric fingerprint computation + voice profile drift detection.

Pure-function module. Computes 12 stylometric markers from prose and
compares them against a stored voice fingerprint to surface drift.

Background:
  van Nuenen et al. (April 2026) showed all frontier models drift prose
  toward formality regardless of voice-preservation prompts. Voice
  preservation belongs at the measurement layer, not the instruction
  layer. This module makes drift falsifiable.

Public API:
  compute_fingerprint(text: str) -> dict       # 12 markers from prose
  load_voice_fingerprint(name, project_root)   # JSON profile loader
  compute_drift(output_fp, voice_fp) -> dict   # per-marker + composite

Companion to PROPOSAL_STYLOMETRIC_DRIFT_COLLECTOR.md. Consumed by
prose_collector._collect_prose_stylometry which emits EvidenceItem
output that the goal-criterion EvidenceMetricEvaluator can gate on.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Sentence-ending punctuation followed by whitespace (regex split). Lightweight
# alternative to nltk; misses some edge cases (Mr. Smith, etc.) but adequate
# for stylometric averages where small noise doesn't move the signal.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")
_TOKEN_PATTERN = re.compile(r"\b[\w']+\b")
_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")
_EM_DASH_PATTERN = re.compile(r"—|--")


# Hardcoded contraction list — matches common English contractions. Match
# pattern: case-insensitive token boundaries. Apostrophe variants (curly vs
# straight) handled via NFKC-style normalization in compute_fingerprint.
_CONTRACTIONS = frozenset({
    "ain't", "aren't", "can't", "couldn't", "didn't", "doesn't", "don't",
    "hadn't", "hasn't", "haven't", "he'd", "he'll", "he's", "i'd", "i'll",
    "i'm", "i've", "isn't", "it'd", "it'll", "it's", "let's", "shan't",
    "she'd", "she'll", "she's", "shouldn't", "that'd", "that's", "there's",
    "they'd", "they'll", "they're", "they've", "wasn't", "we'd", "we'll",
    "we're", "we've", "weren't", "what's", "where's", "who's", "won't",
    "wouldn't", "y'all", "you'd", "you'll", "you're", "you've",
    # Common informal contractions
    "gonna", "wanna", "gotta", "kinda", "sorta", "lemme", "gimme", "dunno",
})

_FIRST_PERSON_TOKENS = frozenset({
    "i", "me", "my", "mine", "myself",
    "we", "us", "our", "ours", "ourselves",
})

# Function words: pronouns, determiners, prepositions, conjunctions, modal
# auxiliaries, common adverbs. ~150 entries — Universal POS function-word set
# adapted for English. These are the words a stylometric signature most
# strongly relies on (per Mosteller-Wallace, Pennebaker).
_FUNCTION_WORDS = frozenset({
    # Articles
    "a", "an", "the",
    # Pronouns (personal, possessive, reflexive, relative, demonstrative)
    "i", "me", "my", "mine", "myself", "we", "us", "our", "ours", "ourselves",
    "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves",
    "this", "that", "these", "those",
    "who", "whom", "whose", "which", "what", "whoever", "whomever",
    "whatever", "whichever",
    # Prepositions
    "of", "in", "on", "at", "by", "for", "with", "about", "against",
    "between", "into", "through", "during", "before", "after", "above",
    "below", "to", "from", "up", "down", "out", "over", "under", "again",
    "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "any", "both", "each", "few", "more", "most", "other",
    "some", "such", "than", "too", "very", "as", "since", "until", "across",
    "behind", "beside", "beyond", "near", "off", "onto", "toward", "upon",
    "within", "without", "along", "among", "around",
    # Conjunctions
    "and", "but", "or", "nor", "so", "yet", "if", "because", "although",
    "though", "while", "whereas", "unless", "however", "moreover",
    "therefore", "thus", "hence", "furthermore", "nonetheless", "nevertheless",
    # Modals & auxiliaries
    "is", "am", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having",
    "do", "does", "did", "doing", "done",
    "will", "would", "shall", "should", "may", "might", "must",
    "can", "could", "ought",
    # Negation
    "not", "no",
    # Quantifiers / determiners
    "every", "much", "many", "several", "either", "neither",
})


def _tokenize(text: str) -> list[str]:
    """Lowercase tokenization preserving apostrophes for contractions.

    Normalizes curly apostrophes to straight so contractions match the
    hardcoded list regardless of input quotation style.
    """
    normalized = text.replace("’", "'")
    return [m.group(0).lower() for m in _TOKEN_PATTERN.finditer(normalized)]


def _split_sentences(text: str) -> list[str]:
    """Light-weight sentence split. Returns non-empty sentences."""
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(text) if s.strip()]


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_BOUNDARY.split(text) if p.strip()]


def _mtld_factor_count(tokens: list[str], threshold: float = 0.72) -> float:
    """One-direction MTLD factor count (Measure of Textual Lexical Diversity).

    Walks tokens; tracks running TTR; each time TTR drops below threshold,
    increment factor count and reset window. Partial factor at end is
    proportionally counted. Returns total_tokens / factor_count.

    Reference: McCarthy & Jarvis (2010).
    """
    if not tokens:
        return 0.0
    factor_count = 0.0
    types: set[str] = set()
    n = 0
    last_ttr = 1.0
    for tok in tokens:
        types.add(tok)
        n += 1
        ttr = len(types) / n
        last_ttr = ttr
        if ttr <= threshold:
            factor_count += 1.0
            types = set()
            n = 0
            last_ttr = 1.0
    if n > 0:
        # Partial factor: proportional to how close to threshold the TTR ended
        if last_ttr < 1.0:
            partial = (1.0 - last_ttr) / (1.0 - threshold)
        else:
            partial = 0.0
        factor_count += min(1.0, partial)
    if factor_count <= 0:
        return float(len(tokens))
    return len(tokens) / factor_count


def compute_fingerprint(text: str) -> dict[str, Any]:
    """Compute 12 stylometric markers from prose.

    Returns a dict with `markers` (per-marker values) and `n` (token count).
    Markers with missing inputs (e.g., zero sentences) are omitted rather
    than reported as 0 — drift comparison should skip absent markers, not
    treat them as the lowest possible value.
    """
    tokens = _tokenize(text)
    sentences = _split_sentences(text)
    paragraphs = _split_paragraphs(text)

    n_tokens = len(tokens)
    n_sentences = len(sentences)
    n_paragraphs = len(paragraphs)

    markers: dict[str, float] = {}

    if n_tokens == 0:
        return {"markers": markers, "n": 0}

    # T1 markers (6) — content-distribution signals
    contractions = sum(1 for t in tokens if t in _CONTRACTIONS)
    markers["contractions_ratio"] = contractions / n_tokens

    first_person = sum(1 for t in tokens if t in _FIRST_PERSON_TOKENS)
    markers["first_person_ratio"] = first_person / n_tokens

    function_words = sum(1 for t in tokens if t in _FUNCTION_WORDS)
    markers["function_word_ratio"] = function_words / n_tokens

    markers["type_token_mtld"] = _mtld_factor_count(tokens)

    if n_sentences > 1:
        sentence_lengths = [len(_tokenize(s)) for s in sentences]
        markers["sentence_length_stdev"] = statistics.stdev(sentence_lengths)

    markers["avg_word_length"] = sum(len(t) for t in tokens) / n_tokens

    # T2 markers (6) — structural/punctuation signals
    # Per-100-words is a normalization, meaningful at any sample size; the
    # outer collector already gates the whole fingerprint at ≥200 tokens.
    per_100w = 100.0 / n_tokens
    punct_chars = sum(text.count(c) for c in (";", ":", "(", ")"))
    markers["punctuation_distribution"] = punct_chars * per_100w
    markers["em_dash_rate"] = len(_EM_DASH_PATTERN.findall(text)) * per_100w

    if n_sentences >= 1:
        per_100s = 100.0 / n_sentences
        markers["question_rate"] = text.count("?") * per_100s
        markers["exclamation_rate"] = text.count("!") * per_100s

        # Sentence-initial token diversity: unique first words / total sentences
        first_words = []
        for s in sentences:
            stoks = _tokenize(s)
            if stoks:
                first_words.append(stoks[0])
        if first_words:
            markers["sentence_initial_diversity"] = len(set(first_words)) / len(first_words)

    if n_paragraphs > 1:
        para_lengths = [len(_tokenize(p)) for p in paragraphs]
        if len(para_lengths) >= 2:
            markers["paragraph_rhythm"] = statistics.stdev(para_lengths)

    return {
        "markers": markers,
        "n": n_tokens,
        "n_sentences": n_sentences,
        "n_paragraphs": n_paragraphs,
    }


def load_voice_fingerprint(
    name: str, project_root: str | Path | None = None
) -> dict[str, Any] | None:
    """Load a voice fingerprint JSON. Returns None if not found.

    Resolution order (matches existing voice loader):
      1. <project_root>/.empirica/voice/<name>.fingerprint.json
      2. ~/.empirica/voice/<name>.fingerprint.json
    """
    candidates: list[Path] = []
    if project_root:
        candidates.append(
            Path(project_root) / ".empirica" / "voice" / f"{name}.fingerprint.json"
        )
    candidates.append(Path.home() / ".empirica" / "voice" / f"{name}.fingerprint.json")

    for path in candidates:
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                if "markers" in data:
                    return data
                logger.debug(f"Voice fingerprint at {path} has no 'markers' key")
            except (OSError, json.JSONDecodeError) as e:
                logger.debug(f"Failed to load voice fingerprint {path}: {e}")
    return None


# Markers where INCREASE indicates "formal pull" (output more academic than baseline)
_FORMAL_PULL_INCREASES = frozenset({
    "function_word_ratio", "avg_word_length", "type_token_mtld",
    "sentence_length_stdev",
})

# Markers where DECREASE indicates "formal pull"
_FORMAL_PULL_DECREASES = frozenset({
    "contractions_ratio", "first_person_ratio", "exclamation_rate",
})


def compute_drift(
    output_fingerprint: dict[str, Any],
    voice_fingerprint: dict[str, Any],
) -> dict[str, Any]:
    """Compare output fingerprint against a voice profile, return drift block.

    Voice fingerprint marker shape: {"target": float, "tolerance": float, "n": int}
    Output fingerprint marker shape: float (raw value from compute_fingerprint)

    Returns:
      drift_per_marker:    {marker: {target, output, drift_abs, drift_rel, exceeds_tolerance}}
      composite_drift:     mean of |drift_rel| over markers present in both
      drift_direction:     "formal_pull" | "informal_pull" | "mixed" | "no_signal"
      exceeds_tolerance:   list of marker names that exceeded tolerance
      claim_falsified:     None — caller computes from task_context

    Markers absent in either side are skipped, not penalized.
    """
    voice_markers = voice_fingerprint.get("markers", {})
    output_markers = output_fingerprint.get("markers", {})

    drift_per_marker: dict[str, dict[str, Any]] = {}
    rel_drifts: list[float] = []
    exceeds: list[str] = []
    formal_signal = 0
    informal_signal = 0

    for name, voice_entry in voice_markers.items():
        if name not in output_markers:
            continue
        target = voice_entry.get("target")
        tolerance = voice_entry.get("tolerance")
        if target is None:
            continue
        output_val = output_markers[name]
        drift_abs = output_val - target
        drift_rel = drift_abs / target if target != 0 else 0.0
        # Exceed-tolerance check: tolerance falls back to 5% of target if missing
        effective_tol = tolerance if tolerance is not None else max(abs(target) * 0.05, 1e-6)
        exceeds_tolerance = abs(drift_abs) > effective_tol

        drift_per_marker[name] = {
            "target": target,
            "output": output_val,
            "drift_abs": drift_abs,
            "drift_rel": drift_rel,
            "exceeds_tolerance": exceeds_tolerance,
        }
        rel_drifts.append(abs(drift_rel))
        if exceeds_tolerance:
            exceeds.append(name)

        # Direction tally — only count tolerance-exceeding drifts
        if exceeds_tolerance:
            if (name in _FORMAL_PULL_INCREASES and drift_abs > 0) or (name in _FORMAL_PULL_DECREASES and drift_abs < 0):
                formal_signal += 1
            elif (name in _FORMAL_PULL_INCREASES and drift_abs < 0) or (name in _FORMAL_PULL_DECREASES and drift_abs > 0):
                informal_signal += 1

    composite_drift = sum(rel_drifts) / len(rel_drifts) if rel_drifts else 0.0

    if not rel_drifts:
        direction = "no_signal"
    elif formal_signal > informal_signal and formal_signal >= 2:
        direction = "formal_pull"
    elif informal_signal > formal_signal and informal_signal >= 2:
        direction = "informal_pull"
    elif formal_signal == 0 and informal_signal == 0:
        direction = "within_tolerance"
    else:
        direction = "mixed"

    return {
        "drift_per_marker": drift_per_marker,
        "composite_drift": composite_drift,
        "drift_direction": direction,
        "exceeds_tolerance": exceeds,
    }
