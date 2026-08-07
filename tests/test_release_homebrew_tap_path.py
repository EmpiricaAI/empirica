"""The Homebrew tap formula must be written where brew RESOLVES it.

Homebrew picks a third-party tap's formula directory as the first existing
entry of ``[Formula, HomebrewFormula, .]`` (brew's ``tap.rb``:
``potential_formula_dirs`` + ``find(&:directory?)``). EmpiricaAI/homebrew-tap
has had a ``Formula/`` directory since 2026-05-11, so a formula written to the
tap ROOT is never read.

Every empirica release from 1.12.x through 1.13.7 wrote ``empirica.rb`` to the
tap root. Each push succeeded, the file was present and carried the right
version, and ``brew install empiricaai/tap/empirica`` resolved nothing. The
push succeeded; the publish did not — the same shape as the CI jobs that
reported ``success`` while skipping every substantive step.

These tests pin the path, not the behaviour of ``brew``, because the path is
the whole defect.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_PY = REPO_ROOT / "scripts" / "release.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _load_release_module():
    spec = importlib.util.spec_from_file_location("_release_under_test", RELEASE_PY)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        pytest.skip("cannot load scripts/release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _releaser_class(mod):
    for obj in vars(mod).values():
        if isinstance(obj, type) and hasattr(obj, "TAP_FORMULA_RELPATH"):
            return obj
    pytest.fail("no class on scripts/release.py exposes TAP_FORMULA_RELPATH")


# ─── The path constant ─────────────────────────────────────────────────


def test_tap_formula_relpath_is_under_formula_dir():
    cls = _releaser_class(_load_release_module())
    assert cls.TAP_FORMULA_RELPATH == "Formula/empirica.rb"


def test_tap_formula_relpath_is_not_the_tap_root():
    """The regression itself: a bare ``empirica.rb`` is invisible to brew."""
    cls = _releaser_class(_load_release_module())
    assert cls.TAP_FORMULA_RELPATH != "empirica.rb"
    assert Path(cls.TAP_FORMULA_RELPATH).parent != Path(".")


# ─── The local publish path ────────────────────────────────────────────


def test_update_homebrew_tap_writes_under_formula_dir(tmp_path, monkeypatch):
    mod = _load_release_module()
    cls = _releaser_class(mod)

    repo_root = tmp_path / "empirica"
    (repo_root / "packaging" / "homebrew").mkdir(parents=True)
    (repo_root / "packaging" / "homebrew" / "empirica.rb").write_text('version "9.9.9"\n')

    tap = tmp_path / "homebrew-tap"
    (tap / ".git").mkdir(parents=True)

    rel = cls.__new__(cls)
    rel.repo_root = repo_root
    rel.version = "9.9.9"
    rel.dry_run = False
    rel.run_command = lambda *a, **k: None

    monkeypatch.setattr(mod, "log", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mod, "info", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mod, "success", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mod, "warning", lambda *a, **k: None, raising=False)

    rel.update_homebrew_tap()

    assert (tap / "Formula" / "empirica.rb").exists(), "formula not written where brew reads"
    assert not (tap / "empirica.rb").exists(), "formula written to the tap root — brew will not see it"


def test_tap_is_found_by_dot_git_not_by_a_root_formula(tmp_path, monkeypatch):
    """The candidate probe must not depend on the layout it is fixing.

    Keying the search on a root-level ``empirica.rb`` meant a correctly
    laid-out tap (formula under ``Formula/``) read as "not a tap at all", so
    the fix would have silently skipped publishing on every subsequent release.
    """
    mod = _load_release_module()
    cls = _releaser_class(mod)

    repo_root = tmp_path / "empirica"
    (repo_root / "packaging" / "homebrew").mkdir(parents=True)
    (repo_root / "packaging" / "homebrew" / "empirica.rb").write_text('version "9.9.9"\n')

    # An already-correct tap: Formula/ populated, nothing at the root.
    tap = tmp_path / "homebrew-tap"
    (tap / ".git").mkdir(parents=True)
    (tap / "Formula").mkdir()
    (tap / "Formula" / "empirica.rb").write_text('version "0.0.1"\n')

    rel = cls.__new__(cls)
    rel.repo_root = repo_root
    rel.version = "9.9.9"
    rel.dry_run = False
    rel.run_command = lambda *a, **k: None

    warnings: list[str] = []
    monkeypatch.setattr(mod, "log", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mod, "info", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mod, "success", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(mod, "warning", lambda m, *a, **k: warnings.append(str(m)), raising=False)

    rel.update_homebrew_tap()

    assert not any("not found" in w for w in warnings), f"correct tap read as missing: {warnings}"
    assert (tap / "Formula" / "empirica.rb").read_text() == 'version "9.9.9"\n'


# ─── The CI publish path ───────────────────────────────────────────────


def test_ci_homebrew_job_exists_and_targets_the_empiricaai_tap():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "\n  homebrew:\n" in text, "homebrew job absent from release.yml"
    assert "repository: EmpiricaAI/homebrew-tap" in text
    assert "repository: Nubaeon/homebrew-tap" not in text, "stale owner — Nubaeon redirects to EmpiricaAI"


def test_ci_homebrew_job_writes_under_formula_dir():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Formula/empirica.rb" in text
    assert "git add Formula/empirica.rb" in text


def test_ci_homebrew_secret_gate_fails_rather_than_skips():
    """A gated skip still concludes ``success`` — the 1.13.7 failure mode.

    Both channel gates must hard-fail, so a job that published is
    distinguishable from one that did nothing.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "::error::HOMEBREW_TAP_TOKEN not set" in text
    assert "::warning::HOMEBREW_TAP_TOKEN not set" not in text
    assert "skipping tap update" not in text


def test_ci_homebrew_job_asserts_the_rewrite_took():
    """``sed`` exits 0 when nothing matches, so a drifted formula commits clean."""
    text = WORKFLOW.read_text(encoding="utf-8")
    homebrew = text.split("\n  homebrew:\n", 1)[1].split("\n  github-release:", 1)[0]
    assert "url rewrite did not take" in homebrew
    assert "sha256 rewrite did not take" in homebrew


# ─── The verify path ───────────────────────────────────────────────────


def test_verify_checks_the_formula_not_just_reachability():
    """``--verify`` answers the question; it must not defer it.

    The previous check was ``git ls-remote HEAD`` whose success message said
    "reachable — check the formula version", i.e. it explicitly handed the only
    load-bearing question back to a human. It passed on 1.13.7 while the tap
    served no empirica formula at all.
    """
    text = RELEASE_PY.read_text(encoding="utf-8")
    assert "reachable — check the formula version" not in text
    assert "raw.githubusercontent.com/EmpiricaAI/homebrew-tap" in text
