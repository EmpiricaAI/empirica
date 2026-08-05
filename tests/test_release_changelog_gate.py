"""A release with no CHANGELOG entry must not be publishable.

Regression guard for a defect found after 1.13.4 shipped. `release.py` derives
two surfaces from CHANGELOG.md and could silently skip both:

- Nothing checked that `## [<version>]` exists. **22 tagged releases have no
  CHANGELOG heading** (1.11.9, 1.12.19, 1.8.17, 1.9.7/8, 1.7.7/12, most of the
  1.0-1.6 era).
- `sync_readme_whats_new()` had four `warning()`-and-return paths and never
  raised. A warning in a 200-line release log is invisible: 1.13.4 published
  with README's What's New still reading "What's New in 1.13.3", because the
  bump commit (9051f063b) touched only the 5 regex-swept version strings.

The gate checks the TOP CHANGELOG heading is this version, which closes both
plus the third observed mechanism: a feature commit writing its bullets over
the previous release's heading (819e917f0 did this to 1.12.19, absorbing a
shipped release's notes into the next one).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

RELEASE_PY = Path(__file__).parent.parent / "scripts" / "release.py"
REPO_ROOT = Path(__file__).parent.parent

CHANGELOG_HEADING = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)


def _load_release_module():
    spec = importlib.util.spec_from_file_location("release_script", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manager(tmp_path: Path, version: str = "9.9.9"):
    mod = _load_release_module()
    rm = mod.ReleaseManager()
    rm.repo_root = tmp_path
    rm.version = version
    return rm


def test_gate_passes_when_top_entry_is_this_version(tmp_path: Path):
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [9.9.9] - 2026-01-01\n\n- **Something.** Shipped.\n\n## [9.9.8] - 2025-12-31\n"
    )
    _manager(tmp_path).verify_changelog_entry()  # must not raise


def test_gate_blocks_when_the_version_has_no_entry(tmp_path: Path):
    """The 22-gap case: released 9.9.9, CHANGELOG's newest entry is 9.9.8."""
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [9.9.8] - 2025-12-31\n\n- **Prior.** Shipped.\n")
    with pytest.raises(SystemExit):
        _manager(tmp_path).verify_changelog_entry()


def test_gate_blocks_an_unrenamed_unreleased_section(tmp_path: Path):
    """`## [Unreleased]` on top is how entries go missing — the release must
    rename it to the version, not ship with the heading absent."""
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- **Something.** Written but never versioned.\n"
    )
    with pytest.raises(SystemExit):
        _manager(tmp_path).verify_changelog_entry()


def test_gate_blocks_a_missing_changelog(tmp_path: Path):
    with pytest.raises(SystemExit):
        _manager(tmp_path).verify_changelog_entry()


def test_whats_new_sync_errors_instead_of_warning_past_a_missing_section(tmp_path: Path):
    """README with no `## What's New` section: previously a warning + return,
    leaving README advertising the previous release. Now a hard failure."""
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [9.9.9] - 2026-01-01\n\n### Fixed\n- **A thing.** Was broken, now is not.\n"
    )
    (tmp_path / "README.md").write_text("# Project\n\nNo What's New section here.\n")
    with pytest.raises(SystemExit):
        _manager(tmp_path).sync_readme_whats_new()


def test_whats_new_sync_errors_on_an_entry_with_no_bullets(tmp_path: Path):
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [9.9.9] - 2026-01-01\n\nProse only, no bullets.\n")
    (tmp_path / "README.md").write_text("# Project\n\n## What's New in 9.9.8\n\n- **Old.** Thing.\n\n---\n")
    with pytest.raises(SystemExit):
        _manager(tmp_path).sync_readme_whats_new()


def test_whats_new_sync_writes_the_released_version_heading(tmp_path: Path):
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [9.9.9] - 2026-01-01\n\n### Fixed\n- **A thing.** Was broken across\n  two physical lines.\n"
    )
    (tmp_path / "README.md").write_text("# Project\n\n## What's New in 9.9.8\n\n- **Old.** Thing.\n\n---\n\nrest\n")
    rm = _manager(tmp_path)
    rm.sync_readme_whats_new()
    readme = (tmp_path / "README.md").read_text()
    assert "## What's New in 9.9.9" in readme
    assert "## What's New in 9.9.8" not in readme
    # wrapped continuation joined, not truncated at the first physical line
    assert "Was broken across two physical lines." in readme


def test_whats_new_sync_survives_a_backslash_in_a_bullet(tmp_path: Path):
    """`re.sub` interprets escapes in a replacement STRING — a bullet with a
    backslash would be mangled or raise on the way into README."""
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [9.9.9] - 2026-01-01\n\n### Fixed\n- **Path handling.** `C:\\\\Users\\\\x` now parses.\n"
    )
    (tmp_path / "README.md").write_text("# Project\n\n## What's New in 9.9.8\n\n- **Old.** Thing.\n\n---\n")
    _manager(tmp_path).sync_readme_whats_new()
    assert "C:\\\\Users\\\\x" in (tmp_path / "README.md").read_text()


def test_both_release_flows_call_the_gate():
    """--publish is runnable without --prepare, so both must gate."""
    src = RELEASE_PY.read_text()
    for flow in ("def run_prepare", "def run_publish"):
        start = src.index(flow)
        end = src.index("\n    def ", start + 1)
        assert "self.verify_changelog_entry()" in src[start:end], (
            f"{flow} must call verify_changelog_entry() — a release path that skips it "
            f"can publish a version with no release notes"
        )


def test_the_sync_has_no_silent_skip_left():
    """Every abort inside sync_readme_whats_new must be fatal, not a warning."""
    src = RELEASE_PY.read_text()
    start = src.index("def sync_readme_whats_new")
    end = src.index("\n    def ", start + 1)
    # comments explain the defect and name warning() — only code counts
    body = "\n".join(ln for ln in src[start:end].splitlines() if not ln.lstrip().startswith("#"))
    assert "warning(" not in body, (
        "sync_readme_whats_new must fail the release rather than warn — a warning in a "
        "200-line release log is invisible (1.13.4 shipped a 1.13.3 What's New that way)"
    )


def test_this_repo_changelog_top_entry_matches_its_version():
    """The invariant that would have caught 1.13.4 before it shipped.

    Tolerates a leading `## [Unreleased]` so an in-flight contribution doesn't
    fail CI; the release-time gate above is the strict one.
    """
    version = re.search(r'^version\s*=\s*"([^"]+)"', (REPO_ROOT / "pyproject.toml").read_text(), re.MULTILINE)
    assert version, "pyproject.toml has no version"
    headings = [h for h in CHANGELOG_HEADING.findall((REPO_ROOT / "CHANGELOG.md").read_text()) if h != "Unreleased"]
    assert headings, "CHANGELOG.md has no versioned entries"
    assert headings[0] == version.group(1), (
        f"CHANGELOG.md's newest entry is [{headings[0]}] but pyproject.toml says "
        f"{version.group(1)} — a released version with no entry is how 22 of them went missing"
    )


def test_readme_whats_new_matches_the_shipped_version():
    """README's What's New heading is the one surface that says what changed."""
    version = re.search(r'^version\s*=\s*"([^"]+)"', (REPO_ROOT / "pyproject.toml").read_text(), re.MULTILINE)
    assert version
    readme = (REPO_ROOT / "README.md").read_text()
    match = re.search(r"^## What's New in (\S+)", readme, re.MULTILINE)
    assert match, "README.md has no `## What's New in …` section"
    assert match.group(1) == version.group(1), (
        f"README advertises What's New in {match.group(1)} but ships {version.group(1)}"
    )
