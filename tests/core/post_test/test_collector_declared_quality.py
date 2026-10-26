"""Code-quality evidence a practice declares itself (goal 792302ad, task 1; David 2026-10-26).

Core's code-quality evidence is ruff/radon/pyright on `.py` files, so a Go, Rust or Java transaction got an honest empty source. David ruled that
non-Python quality is defined by the practice that sets the project up (ecodex, all Rust, is the reference). The practice's own tooling writes
`.empirica/code_quality.json` and core reads it under the same freshness rule as the test report; nothing is run on the POSTFLIGHT hot path:

    {"runner": "broccoli.sh", "created": <epoch>, "scope": "owned crates",
     "metrics": {"rustfmt_ok": true, "clippy_warnings": 0}}

A metric is a boolean (a check that passed or failed) or a non-negative count (0 is clean). Core does not know what the names mean, so it
carries them as evidence with the runner and scope and does not invent a vector mapping beyond the lint analogue (clarity, coherence).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from empirica.core.post_test.collector import EvidenceBundle, EvidenceProfile, PostTestCollector
from empirica.core.post_test.mapper import summarize_evidence

T0 = 1_000_000.0


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".empirica").mkdir()
    return tmp_path


def _collector(root: Path, changed=("src/lib.rs",), **kw) -> PostTestCollector:
    c = PostTestCollector("s", preflight_timestamp=kw.pop("preflight_timestamp", T0), **kw)
    c._project_root = str(root)
    c._get_session_changed_files = lambda: list(changed)  # type: ignore[method-assign]
    return c


def _declare(root: Path, metrics, created=T0 + 100, **extra) -> Path:
    path = root / ".empirica" / "code_quality.json"
    path.write_text(json.dumps({"runner": "broccoli.sh", "created": created, "metrics": metrics, **extra}))
    return path


def _by_name(items):
    return {i.metric_name: i for i in items}


# ── reading the declaration ─────────────────────────────────────────────────────────────────────────────────────


def test_a_boolean_check_becomes_pass_or_fail_evidence(root):
    _declare(root, {"rustfmt_ok": True, "doc_ok": False})
    got = _by_name(_collector(root)._collect_code_quality_metrics())
    assert got["rustfmt_ok"].value == 1.0 and got["doc_ok"].value == 0.0
    assert got["rustfmt_ok"].raw_value["value"] is True and got["doc_ok"].raw_value["value"] is False


def test_a_count_is_clean_at_zero_and_falls_as_findings_rise(root):
    _declare(root, {"clippy_warnings": 0, "todo_markers": 3})
    got = _by_name(_collector(root)._collect_code_quality_metrics())
    assert got["clippy_warnings"].value == 1.0
    assert got["todo_markers"].value == pytest.approx(0.25), "1 / (1 + count)"
    assert got["todo_markers"].raw_value["value"] == 3


def test_every_item_carries_the_runner_the_scope_and_a_declared_marker(root):
    _declare(root, {"clippy_warnings": 2}, scope="owned crates: plugin, translator")
    (item,) = _collector(root)._collect_code_quality_metrics()
    assert item.source == "code_quality" and item.supports_vectors == ["clarity", "coherence"]
    assert item.raw_value == {
        "value": 2,
        "runner": "broccoli.sh",
        "scope": "owned crates: plugin, translator",
        "declared": True,
    }


def test_a_declaration_without_a_scope_carries_none(root):
    _declare(root, {"clippy_warnings": 0})
    (item,) = _collector(root)._collect_code_quality_metrics()
    assert item.raw_value["scope"] is None


def test_it_is_read_for_a_transaction_that_touches_no_python(root):
    """The Python tool path returns before running anything when no changed file is .py; the declaration must not be behind that return."""
    _declare(root, {"clippy_warnings": 0})
    assert _collector(root, changed=["main.go", "go.mod"])._collect_code_quality_metrics()


# ── freshness and absence ───────────────────────────────────────────────────────────────────────────────────────


def test_a_declaration_from_before_the_transaction_is_ignored_and_the_note_says_stale(root):
    _declare(root, {"clippy_warnings": 0}, created=T0 - 3600)
    c = _collector(root)
    assert c._collect_code_quality_metrics() == []
    assert c._source_notes["code_quality"] == "stale_code_quality_report"


def test_a_praxic_window_opens_at_check(root):
    _declare(root, {"clippy_warnings": 0}, created=T0 + 100)
    c = _collector(root, phase="praxic", check_timestamp=T0 + 500)
    assert c._collect_code_quality_metrics() == []
    assert c._source_notes["code_quality"] == "stale_code_quality_report"


def test_a_project_that_declares_nothing_is_noted_not_silent(root):
    c = _collector(root)
    assert c._collect_code_quality_metrics() == []
    assert c._source_notes["code_quality"] == "no_code_quality_report"


def test_a_second_call_does_not_inherit_the_first_calls_note(root):
    c = _collector(root)
    c._collect_code_quality_metrics()
    _declare(root, {"clippy_warnings": 0})
    assert c._collect_code_quality_metrics() and "code_quality" not in c._source_notes


@pytest.mark.parametrize("content", ["{not json", "[]", '{"metrics": 7}', '{"runner": "x"}', '{"metrics": {}}'])
def test_an_unusable_file_is_treated_as_absent_not_as_a_crash(root, content):
    (root / ".empirica" / "code_quality.json").write_text(content)
    c = _collector(root)
    assert c._collect_code_quality_metrics() == []
    assert c._source_notes["code_quality"] == "no_code_quality_report"


# ── per-metric validation ───────────────────────────────────────────────────────────────────────────────────────


def test_a_bad_entry_is_dropped_alone_and_its_neighbours_kept(root):
    _declare(
        root,
        {
            "good": 0,
            "negative": -1,
            "text": "clean",
            "nothing": None,
            "nested": {"a": 1},
            "nan": float("nan"),
            "ok_flag": True,
        },
    )
    assert set(_by_name(_collector(root)._collect_code_quality_metrics())) == {"good", "ok_flag"}


def test_an_enormous_metrics_map_is_capped(root):
    _declare(root, {f"m{i:03}": 0 for i in range(300)})
    assert len(_collector(root)._collect_code_quality_metrics()) <= 50


def test_a_metric_name_that_is_not_a_short_string_is_dropped(root):
    _declare(root, {"x" * 200: 0, "": 0, "fine": 0})
    assert set(_by_name(_collector(root)._collect_code_quality_metrics())) == {"fine"}


# ── what reaches the practitioner ───────────────────────────────────────────────────────────────────────────────


def _summary(root):
    return summarize_evidence(EvidenceBundle(session_id="s", items=_collector(root)._collect_code_quality_metrics()))


def test_a_clean_declaration_is_named_in_the_signals_with_its_runner(root):
    _declare(root, {"rustfmt_ok": True, "clippy_warnings": 0})
    assert "Code quality clean (broccoli.sh: clippy_warnings 0, rustfmt_ok true)" in _summary(root)["signals"]


def test_findings_are_named_with_their_figures_and_scope(root):
    _declare(root, {"rustfmt_ok": False, "clippy_warnings": 3}, scope="owned crates")
    assert (
        "Code quality findings (broccoli.sh, scope: owned crates): clippy_warnings 3, rustfmt_ok false"
        in _summary(root)["signals"]
    )


def test_the_python_tool_signal_is_not_disturbed_when_nothing_is_declared(root):
    assert not any(s.startswith("Code quality") for s in _summary(root)["signals"])


def test_collect_all_surfaces_the_note_for_a_non_python_project_that_declares_nothing(root, monkeypatch):
    c = _collector(root, changed=["main.go"])
    monkeypatch.setattr(c, "_resolve_profile", lambda: EvidenceProfile.CODE)
    for name in (
        "_collect_artifact_metrics",
        "_collect_noetic_metrics",
        "_collect_sentinel_metrics",
        "_collect_goal_metrics",
        "_collect_issue_metrics",
        "_collect_triage_metrics",
        "_collect_codebase_model_metrics",
        "_collect_non_git_file_metrics",
        "_collect_git_metrics",
        "_collect_test_results",
    ):
        monkeypatch.setattr(c, name, lambda: [])
    bundle = c.collect_all()
    assert "code_quality" in bundle.sources_empty and bundle.source_notes["code_quality"] == "no_code_quality_report"
    _declare(root, {"clippy_warnings": 0})
    bundle = c.collect_all()
    assert "code_quality" in bundle.sources_available and "code_quality" not in bundle.source_notes
