"""A private roadmap beside the public one (goal 29ac595b, David 2026-11-05).

`.empirica/ROADMAP.private.yaml` holds directions that must not reach the public repository: the same schema and confidence
caps as docs/ROADMAP.yaml, goal ids allowed, never rendered into README. The docs step validates it when it has items,
refuses an id present in both files, and refuses public text that quotes a private item.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
_ROOT = Path(__file__).resolve().parent.parent

ITEM = {"id": "pub-1", "title": "A public direction", "prediction": "Something public happens by the next release.",
        "confidence": 0.5, "evidence": "measured", "basis": "Measured.", "falsifier": "It does not happen.",
        "horizon": "1.18.0", "steered": False, "status": "predicted"}  # fmt: skip
PRIVATE = {**ITEM, "id": "priv-1", "title": "A private direction for the profile pilot",
           "prediction": "Three people run the private pilot for two weeks without a support call.",
           "basis": "Planned in goal 5d1e87f8."}  # fmt: skip


def _mod():
    spec = importlib.util.spec_from_file_location("release_private", _ROOT / "scripts" / "release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _doc(*items):
    return {"forecast": {"made_in": "1.17.11", "made_on": "2026-11-19"}, "items": list(items)}


def test_a_private_item_may_carry_goal_ids_the_public_file_refuses():
    mod = _mod()
    mod.validate_private_roadmap(_doc(PRIVATE))
    with pytest.raises(ValueError, match="internal id"):
        mod.validate_roadmap(_doc(PRIVATE))


def test_the_private_file_keeps_the_public_caps():
    with pytest.raises(ValueError, match="cannot carry confidence"):
        _mod().validate_private_roadmap(_doc({**PRIVATE, "evidence": "reported", "confidence": 0.9}))


def test_an_empty_private_file_needs_no_forecast_block():
    _mod().validate_private_roadmap({"forecast": {"moved_to": "elsewhere"}, "items": []})


def test_an_id_in_both_files_is_refused():
    problems = _mod().private_roadmap_leaks(_doc({**PRIVATE, "id": "pub-1"}), _doc(ITEM), readme="")
    assert any("pub-1" in p for p in problems)


@pytest.mark.parametrize("where", ["roadmap", "readme"])
def test_public_text_that_quotes_a_private_item_is_refused(where):
    leaked = {**ITEM, "basis": f"See: {PRIVATE['prediction']}"} if where == "roadmap" else ITEM
    readme = f"## What's Coming\n\n{PRIVATE['title']}\n" if where == "readme" else ""
    problems = _mod().private_roadmap_leaks(_doc(PRIVATE), _doc(leaked), readme=readme)
    assert problems and "priv-1" in problems[0]


def test_clean_files_have_no_leaks():
    assert _mod().private_roadmap_leaks(_doc(PRIVATE), _doc(ITEM), readme="# P\n") == []


def test_the_private_file_is_never_committed():
    out = subprocess.run(["git", "check-ignore", "-q", ".empirica/ROADMAP.private.yaml"], cwd=_ROOT, check=False)
    assert out.returncode == 0


def test_the_docs_step_never_renders_a_private_item(tmp_path, monkeypatch):
    mod = _mod()
    (tmp_path / "docs").mkdir()
    (tmp_path / ".empirica").mkdir()
    (tmp_path / "docs" / "ROADMAP.yaml").write_text(yaml.safe_dump(_doc(ITEM), sort_keys=False))
    (tmp_path / ".empirica" / "ROADMAP.private.yaml").write_text(yaml.safe_dump(_doc(PRIVATE), sort_keys=False))
    (tmp_path / "CHANGELOG.md").write_text("# C\n\n## [1.17.12] - 2026-11-20\n\n### Added\n\n- **Thing.** D.\n")
    (tmp_path / "README.md").write_text("# P\n\n## What's New in 0.0.0\n\n- old\n\n## Privacy\n")
    rm = mod.ReleaseManager()
    rm.repo_root, rm.version = tmp_path, "1.17.12"
    monkeypatch.setattr(rm, "_released_roadmap_text", lambda: None)
    rm.sync_readme_whats_new()
    readme = (tmp_path / "README.md").read_text()
    assert ITEM["title"] in readme and PRIVATE["title"] not in readme


def test_the_docs_step_stops_on_a_leak(tmp_path, monkeypatch):
    mod = _mod()
    (tmp_path / "docs").mkdir()
    (tmp_path / ".empirica").mkdir()
    (tmp_path / "docs" / "ROADMAP.yaml").write_text(yaml.safe_dump(_doc({**ITEM, "id": "priv-1"}), sort_keys=False))
    (tmp_path / ".empirica" / "ROADMAP.private.yaml").write_text(yaml.safe_dump(_doc(PRIVATE), sort_keys=False))
    (tmp_path / "CHANGELOG.md").write_text("# C\n\n## [1.17.12] - 2026-11-20\n\n### Added\n\n- **Thing.** D.\n")
    (tmp_path / "README.md").write_text("# P\n\n## What's New in 0.0.0\n\n- old\n\n## Privacy\n")
    rm = mod.ReleaseManager()
    rm.repo_root, rm.version = tmp_path, "1.17.12"
    monkeypatch.setattr(rm, "_released_roadmap_text", lambda: None)
    with pytest.raises(SystemExit):
        rm.sync_readme_whats_new()


def test_a_private_title_in_the_changelog_is_caught_before_it_becomes_the_readme(tmp_path, monkeypatch):
    """1.17.12 sweep: README's What's New is written from CHANGELOG, which the check never read."""
    mod = _mod()
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "ROADMAP.private.yaml").write_text(yaml.safe_dump(_doc(PRIVATE), sort_keys=False))
    (tmp_path / "CHANGELOG.md").write_text(f"# C\n\n## [1.17.12] - 2026-11-20\n\n- **{PRIVATE['title']}.** D.\n")
    (tmp_path / "README.md").write_text("# P\n")
    rm = mod.ReleaseManager()
    rm.repo_root, rm.version = tmp_path, "1.17.12"
    with pytest.raises(SystemExit):
        rm._check_private_roadmap(_doc(ITEM))
