"""Release commits stage an explicit allowlist — never `git add -A`.

Regression guard for the 1.12.28 ERM-sweep: the release runs on the SHARED
develop working tree, so a broad `git add -A` sweeps a concurrent session's
uncommitted work into the release commit. release.py must only ever stage the
version/packaging allowlist (+ CHANGELOG for the bump commit).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

RELEASE_PY = Path(__file__).parent.parent / "scripts" / "release.py"


def _load_release_module():
    spec = importlib.util.spec_from_file_location("release_script", RELEASE_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_release_never_uses_git_add_dash_A():
    """No `git add -A` / `git add .` anywhere in the release script."""
    src = RELEASE_PY.read_text()
    # match a git "add" argv element followed by a broad "-A" or "." element
    assert not re.search(r'["\']add["\']\s*,\s*["\'](-A|\.|--all)["\']', src), (
        "release.py must stage an explicit allowlist, never `git add -A/./--all` (the shared-tree sweep hazard)"
    )


def test_version_commit_paths_are_explicit_and_include_pyproject():
    mod = _load_release_module()
    paths = mod.ReleaseManager._VERSION_COMMIT_PATHS
    assert isinstance(paths, tuple) and len(paths) >= 10
    assert "pyproject.toml" in paths
    assert "-A" not in paths and "." not in paths


def test_staged_release_paths_filters_to_existing(tmp_path, monkeypatch):
    mod = _load_release_module()
    mgr = mod.ReleaseManager(dry_run=True)
    monkeypatch.setattr(mgr, "repo_root", tmp_path)
    # only create two of the allowlisted files
    (tmp_path / "pyproject.toml").write_text("x")
    (tmp_path / "README.md").write_text("x")
    out = mgr._staged_release_paths("CHANGELOG.md")  # CHANGELOG absent → filtered out
    assert set(out) == {"pyproject.toml", "README.md"}
    assert "CHANGELOG.md" not in out  # doesn't exist → not staged (won't fail git add)


def test_commit_flag_stored_on_manager():
    """--commit wiring stores the flag on the manager."""
    mod = _load_release_module()
    mgr = mod.ReleaseManager(dry_run=True, commit_bump=True)
    assert mgr.commit_bump is True
    mgr_default = mod.ReleaseManager(dry_run=True)
    assert mgr_default.commit_bump is False


# ── the commit must be PROVED, not attempted ─────────────────────────────────


def test_the_version_bump_asserts_head_moved_before_reporting_success():
    """`git commit` runs with check=False so a no-op cannot fail the release — and
    that swallow printed the full success banner while HEAD had not moved.

    Hit live cutting 1.13.27: `--version-only` ran before the pyproject bump, so
    every file already read the current version, nothing staged, and the script
    announced a commit that git never made. A false "committed" is expensive here
    specifically — the next step builds and TAGS whatever is actually on disk.
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "scripts" / "release.py").read_text()
    tree = ast.parse(src)

    fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_git_head"),
        None,
    )
    assert fn is not None, "no HEAD reader — the outcome cannot be asserted without one"

    # Discriminate on whether the function actually RUNS `git commit`, not on
    # whether it mentions one. Two looser versions failed here first: matching the
    # words "version bump" found `verify_docs_ready`, and matching the commit
    # message found `run_docs`, which only PRINTS the command as advice. A probe
    # that finds something is not a probe that found the right thing.
    def _runs_git_commit(fn: ast.FunctionDef) -> bool:
        for node in ast.walk(fn):
            if not isinstance(node, ast.List):
                continue
            literals = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if literals[:2] == ["git", "commit"]:
                return True
        return False

    # NARROW, NAMED EXEMPTION. `create_git_tag`'s commit is legitimately optional
    # — the TAG is the deliverable, its success message says "Created and pushed
    # tag", and the tag's existence is verified independently by `--verify`. A
    # guard broad enough to catch it would demand a warning about something
    # harmless, and a guard that fires on correct code gets disabled rather than
    # obeyed. The exemption is by name and with the reason attached, so removing
    # it is a decision rather than an omission.
    EXEMPT_COMMIT_IS_NOT_THE_DELIVERABLE = {"create_git_tag"}

    committers = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and _runs_git_commit(n)]
    assert committers, "no function runs `git commit` — the probe is looking in the wrong place"
    checked = [n.name for n in committers if n.name not in EXEMPT_COMMIT_IS_NOT_THE_DELIVERABLE]
    assert len(checked) >= 2, (
        f"the probe checked {checked} — it must reach both the version bump and the homebrew tap, "
        "which are the two paths that report success ABOUT the commit"
    )

    for fn in committers:
        if fn.name in EXEMPT_COMMIT_IS_NOT_THE_DELIVERABLE:
            continue
        body = ast.dump(fn)
        assert "head_before" in body and "head_after" in body, (
            f"{fn.name} runs `git commit` without comparing HEAD before and after — "
            "check=False swallows a no-op, so success would be reported for a commit git never made"
        )


def test_the_no_op_case_is_reported_as_a_warning_not_a_success():
    """The message must name the cause the operator can act on: bump pyproject FIRST."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "scripts" / "release.py").read_text()
    assert "NO COMMIT WAS CREATED" in src
    assert "pyproject.toml" in src and "FIRST" in src, "and say what to do about it"
