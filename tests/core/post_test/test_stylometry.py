"""Tests for stylometric fingerprint computation + voice drift detection.

Covers compute_fingerprint, load_voice_fingerprint, compute_drift across
the 12 markers and direction inference.
"""

from __future__ import annotations

import json

from empirica.core.post_test.stylometry import (
    compute_drift,
    compute_fingerprint,
    load_voice_fingerprint,
)

# Sample prose snippets for fingerprint stability tests
INFORMAL_SAMPLE = """
I think we're missing the point here. It's not that the model can't
preserve voice — it's that voice preservation is a measurement problem,
not an instruction problem.

Look at what the paper shows: every model drifts toward formality.
Doesn't matter what the prompt says. We've been trying to fix this at
the wrong layer. I'd rather measure the drift than pretend prompts
solve it. Don't you think?

So here's my take: build the measurement first. Then we know which
configurations actually preserve voice and which just claim to.
"""

FORMAL_SAMPLE = """
The investigation revealed a fundamental misalignment between the
asserted capability and the empirical observation. Despite explicit
instructions regarding voice preservation, the language model
demonstrated systematic drift toward elevated formality.

Previous methodologies attempted to resolve this discrepancy through
prompt-level modifications. However, the underlying distributional
properties of post-training optimization render such interventions
structurally inadequate. Subsequent measurements confirmed the
hypothesis that voice preservation belongs at the measurement layer
rather than the instruction layer.
"""


# ---------------------------------------------------------------------------
# compute_fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_returns_markers_and_token_count():
    fp = compute_fingerprint(INFORMAL_SAMPLE)
    assert "markers" in fp
    assert "n" in fp
    assert fp["n"] > 0


def test_fingerprint_t1_markers_present():
    fp = compute_fingerprint(INFORMAL_SAMPLE)
    markers = fp["markers"]
    expected_t1 = {
        "contractions_ratio", "first_person_ratio", "function_word_ratio",
        "type_token_mtld", "sentence_length_stdev", "avg_word_length",
    }
    assert expected_t1.issubset(markers.keys())


def test_fingerprint_t2_markers_present_when_long_enough():
    """T2 markers (punctuation, structural) need ≥100 tokens for
    punctuation_distribution and ≥1 sentence for question/exclamation."""
    fp = compute_fingerprint(INFORMAL_SAMPLE)
    markers = fp["markers"]
    expected_t2 = {
        "punctuation_distribution", "question_rate", "exclamation_rate",
        "em_dash_rate", "sentence_initial_diversity",
    }
    assert expected_t2.issubset(markers.keys())


def test_fingerprint_empty_text_returns_empty_markers():
    fp = compute_fingerprint("")
    assert fp["n"] == 0
    assert fp["markers"] == {}


def test_fingerprint_paragraph_rhythm_skipped_for_single_paragraph():
    """paragraph_rhythm requires ≥2 paragraphs (stdev needs 2+ samples)."""
    single_para = (
        "This is one paragraph. It has multiple sentences. "
        "But there are no blank lines so it stays as one paragraph."
    )
    fp = compute_fingerprint(single_para)
    assert "paragraph_rhythm" not in fp["markers"]


def test_informal_sample_has_higher_contractions_than_formal():
    """Behavioural test: informal prose should have visibly more contractions
    (we're, can't, doesn't) than formal prose (no contractions at all)."""
    informal = compute_fingerprint(INFORMAL_SAMPLE)["markers"]
    formal = compute_fingerprint(FORMAL_SAMPLE)["markers"]
    assert informal["contractions_ratio"] > formal["contractions_ratio"]


def test_informal_sample_has_higher_first_person_than_formal():
    informal = compute_fingerprint(INFORMAL_SAMPLE)["markers"]
    formal = compute_fingerprint(FORMAL_SAMPLE)["markers"]
    # Informal has 'I think', 'we', 'I'd', 'my'
    # Formal is mostly third-person
    assert informal["first_person_ratio"] > formal["first_person_ratio"]


def test_formal_sample_has_higher_avg_word_length():
    """Formal prose tends toward longer words ('investigation', 'methodology'
    vs informal 'think', 'point')."""
    informal = compute_fingerprint(INFORMAL_SAMPLE)["markers"]
    formal = compute_fingerprint(FORMAL_SAMPLE)["markers"]
    assert formal["avg_word_length"] > informal["avg_word_length"]


def test_curly_apostrophe_normalized():
    """Voice samples often have curly apostrophes from copy-paste; the
    contraction matcher must handle both straight and curly."""
    straight = compute_fingerprint("I can't do it. We won't try.")
    curly = compute_fingerprint("I can’t do it. We won’t try.")
    assert straight["markers"]["contractions_ratio"] == curly["markers"]["contractions_ratio"]


# ---------------------------------------------------------------------------
# load_voice_fingerprint
# ---------------------------------------------------------------------------


def test_load_voice_fingerprint_user_global(tmp_path, monkeypatch):
    """User-global ~/.empirica/voice/<name>.fingerprint.json resolves."""
    home = tmp_path / "home"
    voice_dir = home / ".empirica" / "voice"
    voice_dir.mkdir(parents=True)
    fp_data = {
        "name": "test",
        "version": "2026-05",
        "markers": {"contractions_ratio": {"target": 0.024, "tolerance": 0.008}},
    }
    (voice_dir / "test.fingerprint.json").write_text(json.dumps(fp_data))

    monkeypatch.setenv("HOME", str(home))
    result = load_voice_fingerprint("test")
    assert result is not None
    assert result["name"] == "test"


def test_load_voice_fingerprint_project_overrides_user(tmp_path, monkeypatch):
    """Project-local <project>/.empirica/voice/<name>.fingerprint.json wins."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home_voice = home / ".empirica" / "voice"
    project_voice = project / ".empirica" / "voice"
    home_voice.mkdir(parents=True)
    project_voice.mkdir(parents=True)

    home_data = {"name": "test", "version": "user", "markers": {}}
    project_data = {"name": "test", "version": "project", "markers": {}}
    (home_voice / "test.fingerprint.json").write_text(json.dumps(home_data))
    (project_voice / "test.fingerprint.json").write_text(json.dumps(project_data))

    monkeypatch.setenv("HOME", str(home))
    result = load_voice_fingerprint("test", project_root=project)
    assert result is not None
    assert result["version"] == "project"


def test_load_voice_fingerprint_returns_none_if_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert load_voice_fingerprint("nonexistent") is None


def test_load_voice_fingerprint_returns_none_for_invalid_json(tmp_path, monkeypatch):
    home = tmp_path / "home"
    voice_dir = home / ".empirica" / "voice"
    voice_dir.mkdir(parents=True)
    (voice_dir / "bad.fingerprint.json").write_text("{not json")
    monkeypatch.setenv("HOME", str(home))
    assert load_voice_fingerprint("bad") is None


# ---------------------------------------------------------------------------
# compute_drift
# ---------------------------------------------------------------------------


def _voice_fp(**markers):
    """Build a minimal voice fingerprint for testing."""
    return {
        "name": "test", "version": "test",
        "markers": {
            name: {"target": target, "tolerance": tol, "n": 1000}
            for name, (target, tol) in markers.items()
        },
    }


def _output_fp(**markers):
    return {"markers": dict(markers), "n": 1000}


def test_drift_zero_when_output_matches_target():
    voice = _voice_fp(contractions_ratio=(0.024, 0.008))
    output = _output_fp(contractions_ratio=0.024)
    drift = compute_drift(output, voice)
    assert drift["composite_drift"] == 0.0
    assert drift["exceeds_tolerance"] == []


def test_drift_within_tolerance_does_not_exceed():
    voice = _voice_fp(contractions_ratio=(0.024, 0.008))
    output = _output_fp(contractions_ratio=0.030)  # +0.006, within 0.008
    drift = compute_drift(output, voice)
    assert "contractions_ratio" not in drift["exceeds_tolerance"]


def test_drift_above_tolerance_flagged():
    voice = _voice_fp(contractions_ratio=(0.024, 0.008))
    output = _output_fp(contractions_ratio=0.050)  # +0.026, exceeds 0.008
    drift = compute_drift(output, voice)
    assert "contractions_ratio" in drift["exceeds_tolerance"]


def test_drift_direction_formal_pull_when_contractions_drop_and_function_words_rise():
    """Classic formal-pull pattern: fewer contractions + higher function word
    ratio + longer words."""
    voice = _voice_fp(
        contractions_ratio=(0.024, 0.005),
        function_word_ratio=(0.45, 0.02),
        avg_word_length=(4.6, 0.2),
    )
    output = _output_fp(
        contractions_ratio=0.005,    # below voice (formal)
        function_word_ratio=0.55,    # above voice (formal)
        avg_word_length=5.5,         # above voice (formal)
    )
    drift = compute_drift(output, voice)
    assert drift["drift_direction"] == "formal_pull"
    assert len(drift["exceeds_tolerance"]) == 3


def test_drift_direction_informal_pull_when_inverse():
    voice = _voice_fp(
        contractions_ratio=(0.005, 0.002),
        function_word_ratio=(0.55, 0.02),
        avg_word_length=(5.5, 0.2),
    )
    output = _output_fp(
        contractions_ratio=0.030,    # above voice (informal)
        function_word_ratio=0.40,    # below voice (informal)
        avg_word_length=4.4,         # below voice (informal)
    )
    drift = compute_drift(output, voice)
    assert drift["drift_direction"] == "informal_pull"


def test_drift_direction_within_tolerance_when_no_marker_exceeds():
    voice = _voice_fp(contractions_ratio=(0.024, 0.010))
    output = _output_fp(contractions_ratio=0.026)  # within tolerance
    drift = compute_drift(output, voice)
    assert drift["drift_direction"] == "within_tolerance"


def test_drift_direction_no_signal_when_no_overlapping_markers():
    voice = _voice_fp(contractions_ratio=(0.024, 0.008))
    output = _output_fp(avg_word_length=4.6)  # different marker
    drift = compute_drift(output, voice)
    assert drift["drift_direction"] == "no_signal"
    assert drift["composite_drift"] == 0.0


def test_drift_per_marker_includes_all_required_fields():
    voice = _voice_fp(contractions_ratio=(0.024, 0.008))
    output = _output_fp(contractions_ratio=0.012)
    drift = compute_drift(output, voice)
    pm = drift["drift_per_marker"]["contractions_ratio"]
    assert pm["target"] == 0.024
    assert pm["output"] == 0.012
    assert pm["drift_abs"] < 0
    assert pm["drift_rel"] < 0
    assert pm["exceeds_tolerance"] is True


def test_drift_skips_missing_markers_silently():
    """Marker present in voice but absent in output → skipped, not penalized."""
    voice = _voice_fp(
        contractions_ratio=(0.024, 0.008),
        avg_word_length=(4.6, 0.2),
    )
    output = _output_fp(contractions_ratio=0.024)  # missing avg_word_length
    drift = compute_drift(output, voice)
    assert "avg_word_length" not in drift["drift_per_marker"]
    assert "avg_word_length" not in drift["exceeds_tolerance"]
    assert drift["composite_drift"] == 0.0


def test_drift_default_tolerance_when_voice_omits_it():
    """Voice marker without explicit tolerance → 5% of target as fallback."""
    voice = {
        "name": "x", "markers": {
            "contractions_ratio": {"target": 0.020, "tolerance": None}
        }
    }
    output = _output_fp(contractions_ratio=0.022)  # +10% — exceeds 5% fallback
    drift = compute_drift(output, voice)
    assert "contractions_ratio" in drift["exceeds_tolerance"]
