"""The widened methods, each observed BOTH passing and failing on real shapes.

An evaluator never observed failing is not evidence — it is the
exemption-reports-clean-forever shape wearing a green badge. Every method here
has a test where it passes, a test where it FAILS, and a test where its evidence
is absent and it skips honestly.

The vocabulary was derived from the corpus, not invented: a regex pass over the
2,473 authored criteria found test/suite language in 18%, commit/ship language in
4%, and artifact-existence claims in 3%. Two derived axes were deliberately NOT
added — `count_threshold` is `quality_gate` under another name, and
`no_regression` needs baseline machinery that does not exist.
"""

from __future__ import annotations

from empirica.core.goals.types import SuccessCriterion
from empirica.core.goals.validation import VALID_VALIDATION_METHODS, validate_success_criteria
from empirica.core.post_test.collector import EvidenceBundle, EvidenceItem, EvidenceQuality
from empirica.core.post_test.criterion_evaluators.registry import _EVALUATORS, dispatch


def _crit(desc, method, threshold=None, required=True):
    return SuccessCriterion(
        id="c1", description=desc, validation_method=method, threshold=threshold, is_required=required
    )


def _bundle(**metrics):
    items = [
        EvidenceItem(
            source="test",
            metric_name=k,
            value=float(v),
            raw_value=v,
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=[],
        )
        for k, v in metrics.items()
    ]
    return EvidenceBundle(session_id="s1", items=items)


class _Goal:
    id = "g1"


class _Ctx:
    def __init__(self, crit, bundle=None):
        self.criterion = crit
        self.goal = _Goal()
        self.evidence = bundle if bundle is not None else _bundle()


# ── tests_pass ───────────────────────────────────────────────────────────────


def test_tests_pass_passes_on_a_green_suite():
    r = dispatch(_Ctx(_crit("all tests green", "tests_pass"), _bundle(test_pass_rate=1.0)))

    assert r.passed is True and r.skipped is False
    assert r.value == 1.0


def test_tests_pass_FAILS_on_a_red_suite():
    """THE control. A method that cannot fail is not a gate."""
    r = dispatch(_Ctx(_crit("all tests green", "tests_pass"), _bundle(test_pass_rate=0.83)))

    assert r.passed is False and r.skipped is False
    assert r.iteration_needed is True
    assert "test_pass_rate=0.83" in r.summary


def test_tests_pass_honours_a_declared_threshold():
    r = dispatch(_Ctx(_crit("most tests green", "tests_pass", threshold=0.8), _bundle(test_pass_rate=0.83)))

    assert r.passed is True


def test_tests_pass_skips_when_no_pytest_evidence_exists():
    """Absent evidence must surface as SKIPPED-with-reason, never as a pass and
    never as a confident failure — a suite that never ran is not a red suite."""
    r = dispatch(_Ctx(_crit("all tests green", "tests_pass"), _bundle()))

    assert r.skipped is True and r.passed is False
    assert "did not apply" in r.summary


# ── committed ────────────────────────────────────────────────────────────────


def test_committed_passes_when_a_commit_exists():
    r = dispatch(_Ctx(_crit("work committed", "committed"), _bundle(commit_count=2)))

    assert r.passed is True


def test_committed_FAILS_on_zero_commits():
    r = dispatch(_Ctx(_crit("work committed", "committed"), _bundle(commit_count=0)))

    assert r.passed is False and r.skipped is False


def test_committed_threshold_counts_commits():
    """threshold is a COUNT here, not a ratio — the 0-1 cap applies only to
    metric_threshold, and pinning that keeps a well-meant 'normalize all
    thresholds' refactor from silently capping commit counts."""
    validate_success_criteria([_crit("three commits", "committed", threshold=3.0)])
    r = dispatch(_Ctx(_crit("three commits", "committed", threshold=3.0), _bundle(commit_count=2)))

    assert r.passed is False


def test_committed_skips_outside_a_git_repo():
    r = dispatch(_Ctx(_crit("work committed", "committed"), _bundle()))

    assert r.skipped is True and r.passed is False


# ── artifact_exists ──────────────────────────────────────────────────────────


def test_artifact_exists_passes_on_a_real_file(tmp_path):
    p = tmp_path / "report.md"
    p.write_text("x")
    r = dispatch(_Ctx(_crit(str(p), "artifact_exists")))

    assert r.passed is True


def test_artifact_exists_FAILS_on_a_missing_file(tmp_path):
    r = dispatch(_Ctx(_crit(str(tmp_path / "never_written.md"), "artifact_exists")))

    assert r.passed is False and r.skipped is False
    assert "MISSING" in r.summary


def test_artifact_exists_skips_on_prose_instead_of_a_path():
    """The description IS the operand (same contract as quality_gate). A prose
    sentence stuffed in must skip, not be stat()ed as a filename — a stat on
    'the report exists' failing would read as a missing artifact."""
    r = dispatch(_Ctx(_crit("the final report exists somewhere", "artifact_exists")))

    assert r.skipped is True and r.passed is False


# ── the vocabulary boundary ──────────────────────────────────────────────────


def test_all_widened_methods_validate_and_dispatch():
    """Every method the validator accepts must reach a REGISTERED evaluator —
    except the two documented advisory ones. A validates-but-dispatches-to-
    nothing method is the metric_threshold trap; this pins the set so a future
    widening cannot reopen it silently."""
    advisory = {"metric_threshold", "prose", "undetermined"}
    for method in VALID_VALIDATION_METHODS:
        if method in ("prose", "undetermined"):
            continue  # registered, but evaluated as declared-uncheckable
        if method in advisory:
            continue  # metric_threshold: known unimplemented, pinned elsewhere
        assert method in _EVALUATORS, f"{method} validates but has no evaluator — the silent-pass trap"


def test_the_deliberate_absences_stay_absent():
    """count_threshold and no_regression were derived from the corpus and NOT
    added — one is a synonym for quality_gate, the other needs baseline
    machinery. If either appears, it should arrive with a design, not drift in."""
    assert "count_threshold" not in VALID_VALIDATION_METHODS
    assert "no_regression" not in VALID_VALIDATION_METHODS
    assert "count_threshold" not in _EVALUATORS
    assert "no_regression" not in _EVALUATORS


def test_metric_names_match_what_the_collector_actually_emits():
    """The evaluators are lookups against collector-emitted metric names. A
    typo'd metric name would make an evaluator skip forever — clean-looking and
    dead. Verified against the source, not assumed."""
    from pathlib import Path

    collector_src = (Path(__file__).parent.parent / "empirica" / "core" / "post_test" / "collector.py").read_text()

    assert 'metric_name="test_pass_rate"' in collector_src
    assert 'metric_name="commit_count"' in collector_src
