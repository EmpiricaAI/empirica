"""A typed criterion's operator applies to the quantity the metric's NAME promises, in every method that compares a number.

1. `ruff_violation_density` (and `proselint_violations`) store an inverted higher-is-better score as `value` and the real
   density in `raw_value`. Applying the author's `<=` to the score failed clean code (score 1.0) and passed bad code.
   The items here are built by the collector's own `_collect_ruff_evidence`, with only ruff's stdout faked, so the test
   sees the shape the producer really emits.
6. `goals-create` stored an operator for every method but only `quality_gate` read it, so `completion:subtask_ratio@<=0.5`
   was evaluated as `>=`.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from empirica.cli.command_handlers.goal_commands import _parse_criterion_entry
from empirica.core.goals.types import Goal
from empirica.core.post_test import collector as collector_mod
from empirica.core.post_test.collector import EvidenceBundle, EvidenceItem, EvidenceQuality, PostTestCollector
from empirica.core.post_test.criterion_evaluators.registry import dispatch


class _Ctx:
    def __init__(self, crit, bundle, goal=None):
        self.criterion = crit
        self.goal = goal or Goal.create(objective="o", success_criteria=[crit])
        self.evidence = bundle


def _ruff_items(tmp_path, monkeypatch, *, lines: int, violations: int) -> list[EvidenceItem]:
    """Run the collector's ruff path over a real file with ruff's JSON output faked (no ruff binary is spawned)."""
    (tmp_path / "m.py").write_text("x = 1\n" * lines)
    out = json.dumps([{"code": "E501"} for _ in range(violations)]) if violations else ""

    def fake_run(cmd, *a, **kw):
        assert cmd[0] == "ruff"
        return subprocess.CompletedProcess(cmd, 1 if violations else 0, stdout=out, stderr="")

    monkeypatch.setattr(collector_mod.subprocess, "run", fake_run)
    c = PostTestCollector(session_id="s1")
    c._project_root = str(tmp_path)
    return c._collect_ruff_evidence(["m.py"], str(tmp_path))


def _bundle(items):
    return EvidenceBundle(session_id="s1", items=items)


def test_clean_code_passes_an_at_most_density_gate(tmp_path, monkeypatch):
    items = _ruff_items(tmp_path, monkeypatch, lines=100, violations=0)
    assert items[0].value == 1.0  # the producer's clean-code score
    crit = _parse_criterion_entry("quality_gate:ruff_violation_density@<=0.25")
    r = dispatch(_Ctx(crit, _bundle(items)))
    assert r.skipped is False
    assert r.passed is True
    assert r.value == 0.0


def test_bad_code_fails_an_at_most_density_gate(tmp_path, monkeypatch):
    items = _ruff_items(tmp_path, monkeypatch, lines=10, violations=5)  # 50 per 100 lines, score clamps to 0.0
    assert items[0].value == 0.0
    crit = _parse_criterion_entry("quality_gate:ruff_violation_density@<=0.25")
    r = dispatch(_Ctx(crit, _bundle(items)))
    assert r.skipped is False
    assert r.passed is False


@pytest.mark.parametrize(("violations", "passes"), [(2, True), (4, False)])
def test_the_threshold_is_read_in_the_density_the_raw_value_carries(tmp_path, monkeypatch, violations, passes):
    items = _ruff_items(tmp_path, monkeypatch, lines=1000, violations=violations)  # 0.2 / 0.4 per 100 lines
    crit = _parse_criterion_entry("quality_gate:ruff_violation_density@<=0.25")
    assert dispatch(_Ctx(crit, _bundle(items))).passed is passes


def test_at_least_on_a_density_compares_the_density(tmp_path, monkeypatch):
    items = _ruff_items(tmp_path, monkeypatch, lines=10, violations=5)
    crit = _parse_criterion_entry("quality_gate:ruff_violation_density@>=0.25")
    assert dispatch(_Ctx(crit, _bundle(items))).passed is True


def test_proselint_violations_compares_violations_per_100_words():
    """Same inverted shape as prose_collector._run_proselint emits."""
    item = EvidenceItem(
        source="prose_quality",
        metric_name="proselint_violations",
        value=0.96,  # 1 - 0.2/5
        raw_value={"violations": 1, "words": 500, "per_100_words": 0.2},
        quality=EvidenceQuality.OBJECTIVE,
        supports_vectors=["coherence", "signal"],
    )
    assert dispatch(_Ctx(_parse_criterion_entry("quality_gate:proselint_violations@<=0.5"), _bundle([item]))).passed
    assert not dispatch(_Ctx(_parse_criterion_entry("quality_gate:proselint_violations@<=0.1"), _bundle([item]))).passed


def _goal_with_tasks(crit, done: int, total: int) -> Goal:
    """Goal.calculate_progress reads subtasks from the task DB; stub its return shape instead of touching one."""
    g = Goal.create(objective="o", success_criteria=[crit])
    g.calculate_progress = lambda: {  # type: ignore[method-assign]
        "total_subtasks": total,
        "completed": done,
        "in_progress": 0,
        "pending": total - done,
        "blocked": 0,
        "skipped": 0,
        "completion_percentage": done / total * 100.0,
    }
    return g


@pytest.mark.parametrize(
    ("done", "op", "passes"), [(1, "<=", True), (3, "<=", False), (1, ">=", False), (3, ">=", True)]
)
def test_completion_honours_the_operator(done, op, passes):
    crit = _parse_criterion_entry(f"completion:subtask_ratio@{op}0.5")
    goal = _goal_with_tasks(crit, done, 4)
    r = dispatch(_Ctx(crit, _bundle([]), goal=goal))
    assert r.skipped is False
    assert r.passed is passes


def _commit_item(commits: int) -> EvidenceItem:
    """The shape _collect_git_metrics emits: a maturity-normalised score as value, the count in raw_value."""
    return EvidenceItem(
        source="git",
        metric_name="commit_count",
        value=min(1.0, commits / 5.0),
        raw_value={"commits": commits, "maturity": "mature"},
        quality=EvidenceQuality.OBJECTIVE,
        supports_vectors=["do", "change"],
    )


def _pass_rate_item(passed: int, total: int) -> EvidenceItem:
    """The shape _collect_test_results emits: the rate as value, the counts dict as raw_value."""
    return EvidenceItem(
        source="pytest",
        metric_name="test_pass_rate",
        value=passed / total,
        raw_value={"passed": passed, "failed": total - passed, "total": total, "runner": "junit", "scope": None},
        quality=EvidenceQuality.OBJECTIVE,
        supports_vectors=["know", "do", "state"],
    )


@pytest.mark.parametrize(("op", "passes"), [("<=", True), (">=", False)])
def test_committed_honours_the_operator(op, passes):
    crit = _parse_criterion_entry(f"committed:commit_count@{op}2")
    assert dispatch(_Ctx(crit, _bundle([_commit_item(1)]))).passed is passes


def test_committed_counts_commits_not_the_normalised_score():
    """One commit in a mature repo scores 0.2; `committed` with its default threshold asks for at least one commit."""
    crit = _parse_criterion_entry({"description": "x", "validation_method": "committed"})
    r = dispatch(_Ctx(crit, _bundle([_commit_item(1)])))
    assert r.passed is True
    assert r.value == 1.0


@pytest.mark.parametrize(("op", "passes"), [("<=", True), (">=", False)])
def test_tests_pass_honours_the_operator(op, passes):
    crit = _parse_criterion_entry(f"tests_pass:test_pass_rate@{op}0.9")
    assert dispatch(_Ctx(crit, _bundle([_pass_rate_item(1, 2)]))).passed is passes


@pytest.mark.parametrize("metric", ["radon_avg_complexity", "test_coverage_percent", "mistake_ratio"])
def test_an_operator_on_a_score_valued_metric_is_skipped_not_misread(metric):
    """Second review pass: `radon_avg_complexity@<=10` passed at complexity 25 because the value is a normalised score."""
    item = EvidenceItem(source="s", metric_name=metric, value=0.9, raw_value=None, quality=EvidenceQuality.OBJECTIVE,
                        supports_vectors=["know"])  # fmt: skip
    r = dispatch(_Ctx(_parse_criterion_entry(f"quality_gate:{metric}@<=0.5"), _bundle([item])))
    assert r.skipped is True and "score" in (r.summary or "")
    legacy = {"description": metric, "validation_method": "quality_gate", "threshold": 0.5}  # no operator: the score
    assert dispatch(_Ctx(_parse_criterion_entry(legacy), _bundle([item]))).skipped is False
