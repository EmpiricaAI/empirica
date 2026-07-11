"""Tests for the injection measure-view cell in the cockpit status render (B3).

Pins the compact one-line format `empirica status` shows for the persisted
injection budget (prop_3por4fwg): injected volume · cap · dropped.
"""

from __future__ import annotations

from empirica.core.cockpit.render import _injection_cell


def test_none_budget_renders_nothing():
    assert _injection_cell(None) is None
    assert _injection_cell({}) is None


def test_uncapped_shows_volume():
    line = _injection_cell({"injected_total": 29, "cap_total": None, "cap_per_category": None})
    assert line == "29 injected · cap uncapped"


def test_per_category_cap_labelled():
    line = _injection_cell({"injected_total": 10, "cap_total": None, "cap_per_category": 3})
    assert line == "10 injected · cap 3/cat"


def test_total_cap_and_drops():
    line = _injection_cell({"injected_total": 8, "cap_total": 8, "capped_per_category": 2, "capped_total": 1})
    assert line == "8 injected · cap 8 · 3 dropped"


def test_no_drops_omits_dropped_segment():
    line = _injection_cell({"injected_total": 5, "cap_total": 20, "capped_per_category": 0, "capped_total": 0})
    assert "dropped" not in line
    assert line == "5 injected · cap 20"
