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
        assert "self.verify_docs_ready()" in src[start:end], (
            f"{flow} must gate on verify_docs_ready() — which wraps the changelog check "
            f"plus README/CLI/version-sweep currency. A release path that skips it can "
            f"publish a version with no release notes"
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


# ---- the authoring/verification split (David, 2026-08-05) -------------------
#
# `--prepare` used to AUTHOR the release docs — version sweep, README What's New,
# CLI reference — after checking out main. That one choice produced three
# defects: main accumulated files develop never saw (every release merge
# conflicted on exactly README.md and CLI_COMMANDS_UNIFIED.md), the bump had to
# be committed before the sync could run (the window that shipped 1.13.4
# advertising 1.13.3), and the sync could warn-and-skip while the release
# continued. Authoring now lives in `--docs`, run on develop; `--prepare` only
# checks.


def _flow_body(src: str, name: str) -> str:
    """Source of one method. Falls back to end-of-file for the last method in the
    class — `run` is last today, and an index() that assumes a
    following `def` fails with a ValueError that reads like a missing method."""
    start = src.index(f"def {name}")
    nxt = src.find("\n    def ", start + 1)
    return src[start : nxt if nxt != -1 else len(src)]


def test_prepare_verifies_docs_instead_of_authoring_them():
    body = _flow_body(RELEASE_PY.read_text(), "run_prepare")
    assert "self.verify_docs_ready()" in body
    for authoring in ("self.sync_readme_whats_new()", "self.regenerate_cli_docs()", "self.update_version_strings()"):
        assert authoring not in body, (
            f"run_prepare must not call {authoring} — it runs after the checkout to main, so it "
            f"writes files develop never sees and conflicts on the next release merge"
        )


def test_publish_also_gates_on_docs():
    assert "self.verify_docs_ready()" in _flow_body(RELEASE_PY.read_text(), "run_publish")


def test_docs_mode_authors_all_three_and_commits_nothing():
    body = _flow_body(RELEASE_PY.read_text(), "run_docs")
    for authoring in ("self.update_version_strings()", "self.sync_readme_whats_new()", "self.regenerate_cli_docs()"):
        assert authoring in body, f"--docs must author {authoring}"
    # It PRINTS the commit command as guidance; it must not RUN one.
    assert "commit_version_bump" not in body and "run_command" not in body, (
        "--docs must leave the diff uncommitted — the review step is the point"
    )


def test_docs_mode_refuses_to_run_on_main():
    body = _flow_body(RELEASE_PY.read_text(), "run_docs")
    assert 'branch == "main"' in body and "error(" in body, (
        "authoring on main is the defect being removed; --docs must refuse there"
    )


def test_cli_docs_currency_ignores_the_generated_timestamp(tmp_path: Path):
    """A gate that always trips is as useless as one that never does — both stop
    being read. The generator stamps a UTC time on every render."""
    mod = _load_release_module()
    a = "**Generated:** 2026-08-05 09:34:49 UTC\n\n# CLI\n\n- foo\n"
    b = "**Generated:** 2026-08-05 12:08:40 UTC\n\n# CLI\n\n- foo\n"
    assert mod._strip_generated_stamp(a) == mod._strip_generated_stamp(b)
    c = "**Generated:** 2026-08-05 12:08:40 UTC\n\n# CLI\n\n- foo\n- bar\n"
    assert mod._strip_generated_stamp(b) != mod._strip_generated_stamp(c)


def test_docs_ready_gate_checks_every_release_facing_surface():
    body = _flow_body(RELEASE_PY.read_text(), "verify_docs_ready")
    assert "verify_changelog_entry" in body, "CHANGELOG entry"
    assert "What's New" in body, "README What's New version"
    assert "_cli_docs_stale" in body, "CLI reference currency"
    assert "__init__.py" in body, "version sweep landed"


# ---- publish is tag-and-push; CI owns the channels -------------------------
#
# Local --publish and release.yml both published every channel, so each release
# raced on the GitHub release ("a release with the same tag name already
# exists", recovered with --clobber — three times on 2026-08-05). CI_CD.md always
# framed the local path as transitional pending "verified for a release or two";
# 1.13.4/5/6 all published cleanly through CI.


def test_publish_leaves_ci_only_channels_to_ci():
    """PyPI and the GitHub release are CI's — publishing them locally too is what
    raced (`a release with the same tag name already exists`, three times in one
    day)."""
    src = RELEASE_PY.read_text()
    for flow in ("run_publish", "run(self)"):
        body = _flow_body(src, flow)
        assert "self.create_git_tag()" in body, f"{flow} must tag — the tag triggers release.yml"
        for channel in ("self.publish_to_pypi()", "self.create_github_release()", "self.build_and_push_docker()"):
            line = next(ln for ln in body.splitlines() if channel in ln)
            assert line.startswith("                "), (
                f"{flow}: {channel} must sit inside the --local-artifacts branch — CI owns it"
            )


def test_publish_still_does_the_channels_ci_cannot():
    """Docker and Homebrew gate on repo secrets that do not exist, and a gated
    skip concludes `success` — so CI has never published them. 1.13.7 shipped
    without both because the local path had been removed on the strength of that
    green tick. They stay local until the secrets land."""
    src = RELEASE_PY.read_text()
    for flow in ("run_publish", "run(self)"):
        body = _flow_body(src, flow)
        for channel in ("self.update_homebrew_tap()",):
            line = next(ln for ln in body.splitlines() if channel in ln)
            assert line.startswith("            ") and not line.startswith("                "), (
                f"{flow}: {channel} must run unconditionally — CI cannot publish it "
                f"(no DOCKERHUB_*/HOMEBREW_TAP_TOKEN secret), and its job still reports success"
            )


def test_ci_secret_gates_fail_loudly_rather_than_skip():
    """The defect that hid it: warning + skip, with the job still green."""
    wf = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "skip=true" not in wf, (
        "a secret-gated skip concludes `success`, making a job that published "
        "indistinguishable from one that did nothing — gates must exit 1"
    )
    assert wf.count("::error::DOCKERHUB_USERNAME") == 1


def test_local_artifacts_defaults_off():
    mod = _load_release_module()
    assert mod.ReleaseManager().local_artifacts is False
    assert mod.ReleaseManager(local_artifacts=True).local_artifacts is True


def test_ci_release_workflow_still_covers_every_channel():
    """The precondition for slimming: if a job is removed from release.yml, the
    local path is no longer redundant and this fails before a release does.

    Homebrew was pinned ABSENT here from 2026-08-05, on the condition "until a
    fine-grained PAT exists" — the tap credential at the time was a broad `gh`
    OAuth token, and putting that in a repo secret widens privilege rather than
    sharing a credential. David minted a PAT scoped to EmpiricaAI/homebrew-tap
    with Contents:write on 2026-08-07, which discharges the condition, so the
    assertion inverts rather than being deleted. The pin did its job: it made
    restoring the job a deliberate act with a stated reason instead of a quiet
    re-add.
    """
    wf = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    for job in ("pypi-empirica:", "pypi-empirica-mcp:", "docker:", "github-release:", "homebrew:"):
        assert job in wf, f"release.yml must still define {job} — --publish no longer does it locally"

    # A job that cannot authenticate must fail loudly. Skipping still concludes
    # `success` at the job level, which is how v1.13.7 reported a clean release
    # while publishing to two of six channels.
    for gate in ("::error::HOMEBREW_TAP_TOKEN not set", "::error::DOCKERHUB_USERNAME not set"):
        assert gate in wf, f"missing hard-fail gate: {gate}"
    assert "::warning::HOMEBREW_TAP_TOKEN" not in wf, "a warning gate lets the job pass having done nothing"
