"""README's What's New and What's Coming are generated, and they are the section people read first (David, 2026-10-25).

The old sync pasted the first eight bold CHANGELOG bullets verbatim. Measured on 1.14.14: eight bullets, 4,905 characters, 420-865 each,
no headline, and everything past the eighth dropped without a word. A release with little novelty makes that worse, because the reader
has nothing to anchor on. The properties pinned here:

* the section is the release's author-written `### Highlights` (short lines), not the detail bullets;
* nothing is dropped silently: what is left out is counted and linked;
* the two releases before this one appear as one line each;
* What's Coming comes from `docs/ROADMAP.yaml`, a forecast whose every item carries a confidence, the evidence it rests on and the
  observation that would falsify it, and which is graded as items ship or drop;
* the roadmap is public text, so it must not carry internal ids.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "scripts" / "release.py"


def _mod():
    spec = importlib.util.spec_from_file_location("release_script_wn", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHANGELOG = """# Changelog

## [2.0.0] - 2026-02-01

### Highlights

- Wake service: idle sessions are nudged when mail arrives (opt-in).
- The ack on a multi-target proposal is per target.

### Added

- **A long detailed bullet about the wake service.** It goes on for a while and names flags, files and measurements that a reader of
  the README does not need on first read.
- **A second detailed bullet.** More detail.

### Fixed

- **A fix nobody scans for.** Detail.

## [1.9.0] - 2026-01-01

A correctness release for the notes-and-rebuild path. A second sentence that must not appear in the summary.

### Fixed

- **Rebuild restores ids.** Detail.

## [1.8.0] - 2025-12-01

### Added

- **Cockpit refresh.** Detail.
- **Another thing.** Detail.

## [1.7.0] - 2025-11-01

### Added

- **Too old to show.** Detail.
"""


def test_the_section_is_the_highlights_not_the_detail_bullets():
    out = _mod().render_whats_new(CHANGELOG, "2.0.0")
    assert out.startswith("## What's New in 2.0.0\n")
    assert "Wake service: idle sessions are nudged" in out
    assert "The ack on a multi-target proposal is per target." in out
    assert "A long detailed bullet" not in out


def test_what_was_left_out_is_counted_and_linked_not_dropped_silently():
    out = _mod().render_whats_new(CHANGELOG, "2.0.0")
    assert "All 3 entries" in out, "two Added bullets and one Fixed bullet are the detail behind the highlights"
    assert "(CHANGELOG.md)" in out


def test_an_earlier_release_with_highlights_is_summarised_by_its_first_highlight():
    text = CHANGELOG.replace(
        "A correctness release for the notes-and-rebuild path. A second sentence that must not appear in the summary.\n",
        "An intro.\n\n### Highlights\n\n- Rebuild restores ids.\n- A second highlight.\n",
    )
    out = _mod().render_whats_new(text, "2.0.0")
    assert (
        "**1.9.0** \u2014 Rebuild restores ids." in out and "An intro." not in out and "A second highlight" not in out
    )


def test_the_two_releases_before_this_one_get_one_line_each_and_no_older():
    out = _mod().render_whats_new(CHANGELOG, "2.0.0")
    assert "**1.9.0**" in out and "**1.8.0**" in out
    assert "1.7.0" not in out
    # an intro paragraph gives the summary when there are no highlights, and only its first sentence
    assert "A correctness release for the notes-and-rebuild path." in out
    assert "A second sentence that must not appear" not in out
    # with no intro paragraph the first bold title stands in
    assert "Cockpit refresh" in out


def test_a_release_without_highlights_falls_back_to_titles_and_still_says_what_it_skipped():
    bullets = "\n".join(f"- **Item {i}.** Detail {i}.\n" for i in range(9))
    text = f"# Changelog\n\n## [3.0.0] - 2026-03-01\n\n### Added\n\n{bullets}\n"
    out = _mod().render_whats_new(text, "3.0.0")
    shown = [ln for ln in out.splitlines() if ln.startswith("- Item ")]
    assert len(shown) == 6, "six titles at most"
    assert "Plus 3 more entries" in out, "the old sync dropped these without a word"
    assert "Detail 0" not in out, "titles only; the detail stays in the changelog"


def test_the_version_in_the_heading_is_the_one_asked_for():
    with pytest.raises(ValueError, match=r"2\.0\.1"):
        _mod().render_whats_new(CHANGELOG, "2.0.1")


def test_this_repos_whats_new_is_short_enough_to_scan():
    mod = _mod()
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text()
    version = re.search(r"^## \[([^\]]+)\]", changelog, re.MULTILINE).group(1)
    out = mod.render_whats_new(changelog, version)
    assert len(out) < 2500, f"What's New is {len(out)} characters; it was 4,905 and unreadable"
    assert max(len(ln) for ln in out.splitlines()) < 400, "no single line may be a wall of text"


# ── What's Coming ───────────────────────────────────────────────────────────

ITEM = {
    "id": "wake-default",
    "title": "Wake service on by default",
    "prediction": "The wake service stops being opt-in once two weeks of live use show no wrong-pane wakes.",
    "confidence": 0.6,
    "basis": "It passed a live trial on two seats and its open edge cases are small.",
    "falsifier": "Any wake that lands in a pane that was not idle.",
    "status": "predicted",
}


def _roadmap(*items):
    return {"forecast": {"made_in": "2.0.0", "made_on": "2026-02-01"}, "items": list(items)}


def test_a_valid_roadmap_renders_as_a_table_with_confidence_basis_and_falsifier():
    out = _mod().render_whats_coming(_roadmap(ITEM))
    assert out.startswith("## What's Coming\n")
    assert "60%" in out
    assert "Wake service on by default" in out
    assert "no wrong-pane wakes" in out
    assert "Any wake that lands in a pane that was not idle." in out


@pytest.mark.parametrize("missing", ["id", "title", "prediction", "confidence", "basis", "falsifier", "status"])
def test_an_item_without_one_of_its_fields_is_refused(missing):
    item = {k: v for k, v in ITEM.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        _mod().validate_roadmap(_roadmap(item))


@pytest.mark.parametrize("confidence", [-0.1, 1.5, "high", None, True])
def test_a_confidence_outside_zero_to_one_is_refused(confidence):
    with pytest.raises(ValueError, match="confidence"):
        _mod().validate_roadmap(_roadmap({**ITEM, "confidence": confidence}))


def test_an_unknown_status_and_a_repeated_id_are_refused():
    mod = _mod()
    with pytest.raises(ValueError, match="status"):
        mod.validate_roadmap(_roadmap({**ITEM, "status": "maybe"}))
    with pytest.raises(ValueError, match="duplicate"):
        mod.validate_roadmap(_roadmap(ITEM, {**ITEM, "title": "Same id again"}))


def test_a_resolved_item_needs_the_release_it_was_resolved_in():
    with pytest.raises(ValueError, match="resolved_in"):
        _mod().validate_roadmap(_roadmap({**ITEM, "status": "shipped"}))


def test_the_forecast_is_graded_as_items_resolve_and_resolved_items_leave_the_table():
    shipped = {**ITEM, "id": "a", "title": "Shipped thing", "status": "shipped", "resolved_in": "2.0.0"}
    dropped = {**ITEM, "id": "b", "title": "Dropped thing", "status": "dropped", "resolved_in": "2.0.0"}
    out = _mod().render_whats_coming(_roadmap(ITEM, shipped, dropped))
    assert "1 shipped" in out and "1 dropped" in out
    rows = [ln for ln in out.splitlines() if ln.startswith("| ") and "Confidence" not in ln and "---" not in ln]
    assert len(rows) == 1 and "Wake service on by default" in rows[0], "only open predictions are in the table"


def test_a_roadmap_with_no_open_items_renders_nothing_rather_than_an_empty_table():
    shipped = {**ITEM, "status": "shipped", "resolved_in": "2.0.0"}
    assert _mod().render_whats_coming(_roadmap(shipped)).count("| ") == 0


@pytest.mark.parametrize("leak", ["prop_abcdefghij", "ser_c2c87f7d", "goal 17d7ef80", "see 3ae3a502b"])
def test_internal_ids_in_a_roadmap_are_refused_because_the_text_is_public(leak):
    with pytest.raises(ValueError, match="internal id"):
        _mod().validate_roadmap(_roadmap({**ITEM, "basis": f"Tracked in {leak}."}))


def test_this_repos_roadmap_is_valid_and_public_safe():
    mod = _mod()
    path = REPO_ROOT / "docs" / "ROADMAP.yaml"
    assert path.exists(), "docs/ROADMAP.yaml is the source of README's What's Coming"
    data = mod.load_roadmap(path)
    assert any(i["status"] == "predicted" for i in data["items"]), "a forecast with nothing predicted is not a forecast"


# ── the sync end to end ─────────────────────────────────────────────────────


def _manager(tmp_path: Path, version="2.0.0"):
    mod = _mod()
    rm = mod.ReleaseManager()
    rm.repo_root = tmp_path
    rm.version = version
    return rm


def _write(tmp_path: Path, roadmap=True):
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    (tmp_path / "README.md").write_text(
        "# P\n\n## What's New in 1.9.0\n\n- **Old.** Thing.\n\n## What's Coming\n\nold forecast\n\n## Privacy\n\ntext\n"
    )
    if roadmap:
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs" / "ROADMAP.yaml").write_text(yaml.safe_dump(_roadmap(ITEM), sort_keys=False))


def test_the_sync_replaces_both_sections_and_keeps_a_blank_line_before_the_next_heading(tmp_path):
    _write(tmp_path)
    _manager(tmp_path).sync_readme_whats_new()
    readme = (tmp_path / "README.md").read_text()
    assert "## What's New in 2.0.0" in readme and "## What's New in 1.9.0" not in readme
    assert "old forecast" not in readme and "Wake service on by default" in readme
    assert readme.count("## What's Coming") == 1
    assert re.search(r"\n\n## Privacy\n", readme), "a blank line must separate the last section from the next heading"
    assert readme.index("## What's New") < readme.index("## What's Coming") < readme.index("## Privacy")


def test_running_the_sync_twice_changes_nothing(tmp_path):
    _write(tmp_path)
    rm = _manager(tmp_path)
    rm.sync_readme_whats_new()
    once = (tmp_path / "README.md").read_text()
    rm.sync_readme_whats_new()
    assert (tmp_path / "README.md").read_text() == once


def test_without_a_roadmap_a_stale_whats_coming_is_removed_not_left_to_mislead(tmp_path):
    _write(tmp_path, roadmap=False)
    _manager(tmp_path).sync_readme_whats_new()
    readme = (tmp_path / "README.md").read_text()
    assert "old forecast" not in readme and "## What's Coming" not in readme
    assert "## What's New in 2.0.0" in readme and "\n\n## Privacy\n" in readme


def test_an_invalid_roadmap_stops_the_release_instead_of_publishing_a_broken_forecast(tmp_path):
    _write(tmp_path)
    (tmp_path / "docs" / "ROADMAP.yaml").write_text(yaml.safe_dump(_roadmap({**ITEM, "confidence": 3})))
    with pytest.raises(SystemExit):
        _manager(tmp_path).sync_readme_whats_new()
