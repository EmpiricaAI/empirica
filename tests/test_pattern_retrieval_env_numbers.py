"""Pattern retrieval's numeric environment knobs: a bad value must not raise out of PREFLIGHT/CHECK, and must not be ignored unsaid.

Found by the 2026-10-06 pipeline sweep (U2): `int(os.getenv(...))` at import and `float(os.getenv(...))` on every call raised
ValueError for a typo, taking the hook's retrieval down with it. The haiku draft fell back silently and accepted NaN and infinity;
this version warns by name and rejects both.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from empirica.core.qdrant import pattern_retrieval as pr


@pytest.mark.parametrize("raw", ["oops", "30s", "", "nan", "inf", "-inf", "-1", "1e999"])
def test_a_bad_value_falls_back_to_the_default_and_says_so(monkeypatch, caplog, raw):
    monkeypatch.setenv("EMPIRICA_TEST_KNOB", raw)
    with caplog.at_level("WARNING"):
        assert pr._env_number("EMPIRICA_TEST_KNOB", 30.0, float) == 30.0
    assert any("EMPIRICA_TEST_KNOB" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("raw", "cast", "expected"), [("12", int, 12), ("0", int, 0), ("2.5", float, 2.5), (" 7 ", int, 7)]
)
def test_a_good_value_is_used(monkeypatch, caplog, raw, cast, expected):
    monkeypatch.setenv("EMPIRICA_TEST_KNOB", raw)
    with caplog.at_level("WARNING"):
        assert pr._env_number("EMPIRICA_TEST_KNOB", 99, cast) == expected
    assert not caplog.records


def test_an_unset_value_is_the_default_without_a_warning(monkeypatch, caplog):
    monkeypatch.delenv("EMPIRICA_TEST_KNOB", raising=False)
    with caplog.at_level("WARNING"):
        assert pr._env_number("EMPIRICA_TEST_KNOB", 5, int) == 5
    assert not caplog.records


def test_importing_the_module_with_malformed_knobs_does_not_raise(tmp_path):
    """The constants are read at import: a typo used to make `import pattern_retrieval` itself fail."""
    code = "import empirica.core.qdrant.pattern_retrieval as p; print(p.MAX_ITEM_CHARS, p.MAX_PER_SECTION, p.MAX_TOTAL_CHARS)"
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "EMPIRICA_PATTERN_MAX_ITEM_CHARS": "wide",
        "EMPIRICA_PATTERN_MAX_PER_SECTION": "-3",
        "EMPIRICA_PATTERN_MAX_TOTAL_CHARS": "nan",
    }
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.split() == ["280", "5", "8000"]
