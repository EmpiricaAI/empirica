"""The calibration bias block reaches the AI at session start and after compaction.

2026-10-02, checking the paper's account of what PREFLIGHT injects: the only injector,
post-compact.py `_load_calibration_from_breadcrumbs_yaml`, read `config["calibration"]`, a key the
writer no longer produces (`grounded_calibration`, `brier_calibration`, ...). On the real file it
returned 0 characters. session-init.py had no reader, though the system prompt promises one.

Files are built under tmp_path; the hooks are loaded by path (their names are hyphenated).
"""

from __future__ import annotations

import importlib.util
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"

sys.path.insert(0, str(LIB))
import calibration_block as cb  # noqa: E402


def _load_hook(name: str, filename: str):
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _cal(updated: str | None = None, **over):
    base = {
        "last_updated": updated or datetime.now().isoformat(),
        "observations": 34518,
        "divergence": {"uncertainty": -0.27, "state": 0.27, "completion": 0.25, "know": 0.02, "signal": -0.04},
        "insights": [
            {"vector": "impact", "severity": 0.63, "description": "impact has evidence in only 1/10 verifications"},
            {"vector": "density", "severity": 1.0, "description": "density underestimated in 9/9 calibrations"},
        ],
        "ungrounded": ["engagement"],
    }
    base.update(over)
    return {"grounded_calibration": base}


def _write(root: Path, config: dict) -> None:
    (root / ".breadcrumbs.yaml").write_text(yaml.safe_dump(config))


def test_the_real_shape_formats_with_its_sign_convention_and_provenance():
    """POSITIVE CONTROL: the structure the writer produces today."""
    out = cb.format_calibration_block(_cal())

    assert out.startswith("### Calibration (grounded, 34,518 observations, updated ")
    assert "not ground truth" in out
    assert "+ = you read higher than the evidence, - = lower" in out
    assert "uncertainty -0.27 · state +0.27 · completion +0.25" in out
    assert "know +0.02" not in out and "signal -0.04" not in out, "below the threshold is noise"
    assert out.index("density underestimated") < out.index("impact has evidence"), "insights by severity"
    assert "Not graded by evidence: engagement" in out


def test_the_old_loaders_key_is_not_what_the_file_has():
    """The defect, stated as data: a file shaped like the writer's has no `calibration` key."""
    config = _cal()

    assert "calibration" not in config and cb.format_calibration_block(config) != ""


@pytest.mark.parametrize(
    "config",
    [{}, {"calibration": {"x": 1}}, {"grounded_calibration": None}, {"grounded_calibration": {"divergence": {}}}],
)
def test_a_file_without_grounded_calibration_yields_nothing_not_a_guess(config):
    assert cb.format_calibration_block(config) == ""


def test_a_stale_profile_is_labelled_stale_not_presented_as_current():
    old = (datetime.now() - timedelta(days=30)).isoformat()

    out = cb.format_calibration_block(_cal(updated=old))

    assert "STALE (30 days old)" in out


def test_a_fresh_profile_is_not_labelled_stale():
    assert "STALE" not in cb.format_calibration_block(_cal())


def test_no_divergent_vector_says_so():
    out = cb.format_calibration_block(_cal(divergence={"know": 0.02}))

    assert "no vector differs by 0.10 or more" in out


def test_loading_reads_the_first_root_that_has_a_file(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _write(b, _cal())

    assert cb.load_calibration_block([a, b]).startswith("### Calibration")
    assert cb.load_calibration_block([a]) == "", "no file: nothing, not an error"


def test_a_malformed_file_yields_nothing(tmp_path):
    (tmp_path / ".breadcrumbs.yaml").write_text("grounded_calibration: [unclosed\n")

    assert cb.load_calibration_block([tmp_path]) == ""


# ── the two hooks ────────────────────────────────────────────────────────────


def test_post_compact_serves_the_block_from_a_file_shaped_like_the_writers(tmp_path, monkeypatch):
    """Fails on the old loader, which returned "" for this file."""
    _write(tmp_path, _cal())
    monkeypatch.chdir(tmp_path)
    hook = _load_hook("post_compact_under_test", "post-compact.py")

    out = hook._load_calibration_from_breadcrumbs_yaml()

    assert out.startswith("### Calibration (grounded") and "uncertainty -0.27" in out


def test_session_start_serves_the_same_block(tmp_path):
    _write(tmp_path, _cal())
    hook = _load_hook("session_init_under_test", "session-init.py")

    assert hook._calibration_block(tmp_path).startswith("### Calibration (grounded")


def test_session_start_never_raises_on_a_bad_project_root():
    hook = _load_hook("session_init_under_test2", "session-init.py")

    assert hook._calibration_block(Path("/nonexistent/project")) == ""
    assert hook._calibration_block(None) == ""


def test_age_is_computed_from_the_injected_clock_not_the_wall_clock():
    stamp = "2026-01-01T00:00:00"
    now = time.mktime(datetime(2026, 1, 20).timetuple())

    assert "STALE (19 days old)" in cb.format_calibration_block(_cal(updated=stamp), now=now)
