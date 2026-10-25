"""Gardening for forecasts (David, 2026-10-25): the principle that keeps epistemic tagging honest, applied to predictions.

A forecast that can be edited after the fact, left open forever, or scored on work it caused is not a forecast. The same four moves that
keep findings honest apply:

* **retract, do not just close**: a prediction that was wrong when written (its basis was false) is `retracted`, distinct from `dropped`
  (a fair bet that did not pan out) and `superseded` (replaced by a better one). A record with no retractions is a record that cannot
  say it was wrong;
* **nothing stays open indefinitely**: a predicted item names a `horizon` (a release), and the docs step flags every one that has passed;
* **do not score a forecast on work it caused**: `steered` says a goal for the item already existed when it was predicted; those count
  separately, because delivering planned work is not foresight;
* **no silent edits**: against the last released roadmap, a changed confidence, prediction, falsifier or horizon needs a dated revision
  that says why, and a removed item must be resolved instead of deleted.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

RELEASE_PY = Path(__file__).parent.parent / "scripts" / "release.py"


def _mod():
    spec = importlib.util.spec_from_file_location("release_script_garden", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ITEM = {
    "id": "wake-default",
    "title": "Wake service on by default",
    "prediction": "The wake service stops being opt-in once two weeks of live use show no wrong-pane wakes.",
    "confidence": 0.6,
    "evidence": "measured",
    "basis": "It passed a live trial on two seats and its open edge cases are small.",
    "falsifier": "Any wake that lands in a pane that was not idle.",
    "horizon": "1.15.0",
    "steered": False,
    "status": "predicted",
}


def _roadmap(*items):
    return {"forecast": {"made_in": "1.14.14", "made_on": "2026-10-25"}, "items": list(items)}


def _resolved(status, **extra):
    return {**ITEM, "status": status, "resolved_in": "1.14.16", **extra}


# ── schema ───────────────────────────────────────────────────────────────────


def test_a_predicted_item_must_name_the_release_by_which_it_will_be_settled():
    item = {k: v for k, v in ITEM.items() if k != "horizon"}
    with pytest.raises(ValueError, match="horizon"):
        _mod().validate_roadmap(_roadmap(item))


@pytest.mark.parametrize("horizon", ["soon", "1.15", "v1.15.0", 1.15, "1.15.x"])
def test_a_horizon_is_a_dotted_release_number(horizon):
    with pytest.raises(ValueError, match="horizon"):
        _mod().validate_roadmap(_roadmap({**ITEM, "horizon": horizon}))


@pytest.mark.parametrize("steered", ["yes", 1, None])
def test_steered_is_a_yes_or_no_not_a_guess(steered):
    with pytest.raises(ValueError, match="steered"):
        _mod().validate_roadmap(_roadmap({**ITEM, "steered": steered}))


def test_a_retracted_item_says_why_it_was_wrong_when_written():
    mod = _mod()
    with pytest.raises(ValueError, match="reason"):
        mod.validate_roadmap(_roadmap(_resolved("retracted")))
    mod.validate_roadmap(
        _roadmap(_resolved("retracted", reason="Its basis described a gap fixed three weeks earlier."))
    )


def test_a_superseded_item_names_the_item_that_replaced_it_and_that_item_must_exist():
    mod = _mod()
    replacement = {**ITEM, "id": "wake-opt-in-docs", "title": "Wake docs"}
    old = _resolved("superseded", reason="Narrowed after the live trial.", superseded_by="wake-opt-in-docs")
    mod.validate_roadmap(_roadmap(old, replacement))
    with pytest.raises(ValueError, match="superseded_by"):
        mod.validate_roadmap(_roadmap(_resolved("superseded", reason="x")))
    with pytest.raises(ValueError, match="superseded_by"):
        mod.validate_roadmap(_roadmap(_resolved("superseded", reason="x", superseded_by="nope")))


def test_a_revision_records_when_what_changed_and_why_and_the_last_one_matches_the_current_confidence():
    mod = _mod()
    good = {"on": "2026-11-02", "from": 0.5, "to": 0.6, "reason": "Two more weeks of live use with no wrong-pane wake."}
    mod.validate_roadmap(_roadmap({**ITEM, "revisions": [good]}))
    with pytest.raises(ValueError, match="revisions"):
        mod.validate_roadmap(_roadmap({**ITEM, "revisions": [{k: v for k, v in good.items() if k != "reason"}]}))
    with pytest.raises(ValueError, match="revisions"):
        mod.validate_roadmap(_roadmap({**ITEM, "confidence": 0.7, "revisions": [good]}))


# ── what the reader sees ─────────────────────────────────────────────────────


def test_the_forecast_record_separates_planned_from_unplanned_work_and_always_shows_retractions():
    shipped_planned = _resolved("shipped", id="a", title="A", steered=True)
    shipped_unplanned = _resolved("shipped", id="b", title="B", steered=False)
    dropped = _resolved("dropped", id="c", title="C", reason="Superseded by events.")
    retracted = _resolved("retracted", id="d", title="D", reason="Basis was false when written.")
    out = _mod().render_whats_coming(_roadmap(ITEM, shipped_planned, shipped_unplanned, dropped, retracted))
    assert "2 shipped (1 of them not on the plan when predicted)" in out
    assert "1 dropped" in out and "1 retracted" in out and "0 superseded" in out


def test_an_open_item_that_was_already_on_the_plan_says_so_beside_its_confidence():
    out = _mod().render_whats_coming(_roadmap({**ITEM, "steered": True}))
    assert "60% (measured, planned)" in out
    assert "60% (measured)" in _mod().render_whats_coming(_roadmap(ITEM))


# ── nothing stays open indefinitely ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("version", "past"), [("1.14.16", False), ("1.15.0", False), ("1.15.1", True), ("1.16.0", True), ("2.0.0", True)]
)
def test_an_item_is_past_its_horizon_once_the_release_after_it_exists(version, past):
    flagged = _mod().items_past_horizon(_roadmap(ITEM), version)
    assert bool(flagged) is past


def test_resolved_items_are_never_past_their_horizon():
    assert _mod().items_past_horizon(_roadmap(_resolved("shipped")), "9.9.9") == []


# ── no silent edits ──────────────────────────────────────────────────────────


def test_an_unchanged_roadmap_has_no_silent_edits():
    assert _mod().roadmap_silent_edits(_roadmap(ITEM), _roadmap(ITEM)) == []


@pytest.mark.parametrize("field", ["confidence", "prediction", "falsifier", "horizon", "evidence"])
def test_changing_a_predicted_item_without_a_revision_is_refused(field):
    changed = {
        **ITEM,
        field: {
            "confidence": 0.5,
            "prediction": "Something easier to meet.",
            "falsifier": "Nothing at all.",
            "horizon": "1.16.0",
            "evidence": "reviewed",
        }[field],
    }
    problems = _mod().roadmap_silent_edits(_roadmap(ITEM), _roadmap(changed))
    assert problems and field in problems[0] and ITEM["id"] in problems[0]


def test_the_same_change_with_a_revision_that_says_why_is_accepted():
    revision = {"on": "2026-11-02", "from": 0.6, "to": 0.5, "reason": "A wrong-pane wake was reported on one seat."}
    changed = {**ITEM, "confidence": 0.5, "revisions": [revision]}
    assert _mod().roadmap_silent_edits(_roadmap(ITEM), _roadmap(changed)) == []


def test_removing_an_item_instead_of_resolving_it_is_refused():
    problems = _mod().roadmap_silent_edits(_roadmap(ITEM), _roadmap())
    assert problems and "wake-default" in problems[0] and "retract" in problems[0]


def test_a_new_item_and_an_item_that_moves_to_shipped_are_not_silent_edits():
    mod = _mod()
    other = {**ITEM, "id": "other", "title": "Other"}
    assert mod.roadmap_silent_edits(_roadmap(ITEM), _roadmap(ITEM, other)) == []
    assert mod.roadmap_silent_edits(_roadmap(ITEM), _roadmap(_resolved("shipped"))) == []


def test_a_resolved_item_cannot_be_rewritten_afterwards():
    shipped = _resolved("shipped")
    rewritten = {**shipped, "prediction": "A different claim that happened to come true."}
    assert _mod().roadmap_silent_edits(_roadmap(shipped), _roadmap(rewritten))


# ── the docs step uses all of it ─────────────────────────────────────────────


def _docs_step(tmp_path: Path, monkeypatch, version: str, baseline: str | None):
    mod = _mod()
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "ROADMAP.yaml").write_text(yaml.safe_dump(_roadmap(ITEM), sort_keys=False))
    (tmp_path / "CHANGELOG.md").write_text(
        f"# C\n\n## [{version}] - 2026-11-01\n\n### Highlights\n\n- One.\n\n### Added\n\n- **Thing.** D.\n"
    )
    (tmp_path / "README.md").write_text("# P\n\n## What's New in 0.0.0\n\n- old\n\n## Privacy\n")
    rm = mod.ReleaseManager()
    rm.repo_root = tmp_path
    rm.version = version
    monkeypatch.setattr(rm, "_released_roadmap_text", lambda: baseline)
    return rm


def test_the_docs_step_warns_about_every_item_past_its_horizon(tmp_path, monkeypatch, capsys):
    _docs_step(tmp_path, monkeypatch, "1.15.1", None).sync_readme_whats_new()
    assert "past its horizon" in capsys.readouterr().out


def test_the_docs_step_stops_on_a_silent_edit_against_the_last_released_roadmap(tmp_path, monkeypatch):
    released = yaml.safe_dump(_roadmap({**ITEM, "confidence": 0.4}), sort_keys=False)
    with pytest.raises(SystemExit):
        _docs_step(tmp_path, monkeypatch, "1.14.15", released).sync_readme_whats_new()


def test_with_no_released_roadmap_there_is_nothing_to_compare_and_drafts_are_free(tmp_path, monkeypatch):
    _docs_step(tmp_path, monkeypatch, "1.14.15", None).sync_readme_whats_new()
    assert "Wake service on by default" in (tmp_path / "README.md").read_text()
