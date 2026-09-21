"""A finding gets one eidetic confidence, whichever writer reaches it first.

Three writers share a point id with first-writer-wins. Two formulas meant impact
0.9 became 0.68 at log time and 0.90 from a re-embed, and once the batch path
also wrote at log time (6982a9a6d) the lower number would have won everywhere,
under a memory-promotion threshold of 0.7.
"""

from __future__ import annotations

import inspect

import pytest

from empirica.core.fact_confidence import DEFAULT_FACT_CONFIDENCE, finding_fact_confidence


@pytest.mark.parametrize(
    ("impact", "expected"),
    [(0.9, 0.9), (0.3, 0.3), (None, 0.6), (0, 0.6), ("0.8", 0.8), ("high", 0.6), (1.7, 1.0), (-2, 0.0)],
)
def test_the_formula(impact, expected):
    assert finding_fact_confidence(impact) == expected


def test_a_high_impact_finding_still_clears_the_promotion_threshold():
    from empirica.core.memory_manager import PROMOTE_MIN_CONFIDENCE

    assert finding_fact_confidence(0.8) >= PROMOTE_MIN_CONFIDENCE
    assert DEFAULT_FACT_CONFIDENCE < PROMOTE_MIN_CONFIDENCE  # an unrated finding does not promote


def test_every_writer_calls_the_one_formula_and_none_keeps_its_own():
    from empirica.cli.command_handlers import artifact_log_commands, project_embed
    from empirica.core.qdrant import rebuild

    log_time = inspect.getsource(artifact_log_commands._ingest_finding_eidetic)
    assert "finding_fact_confidence(impact)" in log_time and "* 0.2" not in log_time
    for module in (project_embed, rebuild):
        source = inspect.getsource(module)
        assert "finding_fact_confidence(impact)" in source
        assert "float(impact) if impact else" not in source


def test_the_formula_module_does_not_load_the_vector_client():
    import subprocess
    import sys

    code = "import sys, empirica.core.fact_confidence; print('qdrant_client' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "False", out.stderr
