"""Tests for the linked-worktree .empirica/ self-heal.

Bug (observed 2026-07-30, a Claude Code worktree session): a fresh
`git worktree add` checkout has no `.empirica/` of its own even though the
main checkout is already an initialized Empirica project. `.empirica/` is
gitignored, per-project state — it isn't shared automatically the way
tracked files are. Every worktree session's first `empirica session-create`
failed with "Project not initialized", and `--auto-init` would instead mint
(or adopt-the-id-into) a SEPARATE `.empirica/` at the worktree path — a
second, divergent sessions.db/findings/unknowns for what should be one
project's shared state.

Fix: `find_worktree_main_empirica` detects a linked worktree (git-dir !=
git-common-dir) whose main checkout already has an initialized
`.empirica/config.yaml`, and both `_require_project_initialized` and
`_handle_auto_init` symlink to it instead of failing or minting.

These use REAL git repos and REAL `git worktree add` (no mocked git
plumbing) — the self-heal shells out to `git rev-parse --git-dir` /
`--git-common-dir`, and a mocked subprocess would just prove the mock, not
the actual worktree detection.
"""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace

import pytest

from empirica.cli.command_handlers.session_create import (
    _handle_auto_init,
    _require_project_initialized,
)
from empirica.config import path_resolver
from empirica.config.path_resolver import find_worktree_main_empirica


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_main_repo(main_root, initialized: bool = True):
    """Create a real git repo at main_root, optionally with a fake initialized
    .empirica/ (config.yaml + project.yaml — only what the code under test
    reads; a real `empirica project-init` isn't needed to exercise this)."""
    main_root.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=main_root)
    _git("config", "user.email", "test@example.com", cwd=main_root)
    _git("config", "user.name", "Test", cwd=main_root)
    (main_root / "README.md").write_text("hello\n")
    _git("add", "README.md", cwd=main_root)
    _git("commit", "-q", "-m", "init", cwd=main_root)

    if initialized:
        empirica_dir = main_root / ".empirica"
        empirica_dir.mkdir()
        (empirica_dir / "config.yaml").write_text("version: 2\n")
        (empirica_dir / "project.yaml").write_text(
            "project_id: 2273f11d-0000-4000-8000-000000000000\nai_id: main-project\n"
        )
    return main_root


def _add_worktree(main_root, worktree_root):
    _git("worktree", "add", "-q", "--detach", str(worktree_root), "HEAD", cwd=main_root)
    return worktree_root


@pytest.fixture(autouse=True)
def _reset_git_root_cache():
    """get_git_root() memoizes into a module-level cache; each test resolves
    a different tmp_path, so the cache from a previous test would leak in."""
    path_resolver._git_root_cache = None
    yield
    path_resolver._git_root_cache = None


class TestFindWorktreeMainEmpirica:
    def test_returns_main_empirica_for_a_linked_worktree_of_an_initialized_project(self, tmp_path):
        main_root = _init_main_repo(tmp_path / "main")
        wt_root = _add_worktree(main_root, tmp_path / "wt")

        result = find_worktree_main_empirica(wt_root)

        assert result == main_root / ".empirica"

    def test_returns_none_for_the_main_checkout_itself(self, tmp_path):
        main_root = _init_main_repo(tmp_path / "main")

        assert find_worktree_main_empirica(main_root) is None

    def test_returns_none_when_the_main_checkout_is_not_initialized(self, tmp_path):
        main_root = _init_main_repo(tmp_path / "main", initialized=False)
        wt_root = _add_worktree(main_root, tmp_path / "wt")

        assert find_worktree_main_empirica(wt_root) is None

    def test_returns_none_outside_a_git_repo(self, tmp_path):
        bare_dir = tmp_path / "not-a-repo"
        bare_dir.mkdir()

        assert find_worktree_main_empirica(bare_dir) is None


class TestRequireProjectInitializedHealsWorktrees:
    def test_symlinks_a_fresh_worktree_instead_of_exiting(self, tmp_path, monkeypatch):
        main_root = _init_main_repo(tmp_path / "main")
        wt_root = _add_worktree(main_root, tmp_path / "wt")
        monkeypatch.chdir(wt_root)

        # Must NOT sys.exit(1) — a raised SystemExit would fail the test.
        _require_project_initialized("some-ai-id", output_format="json")

        linked = wt_root / ".empirica"
        assert linked.is_symlink()
        assert linked.resolve() == (main_root / ".empirica").resolve()

    def test_still_fails_when_main_checkout_is_also_uninitialized(self, tmp_path, monkeypatch):
        main_root = _init_main_repo(tmp_path / "main", initialized=False)
        wt_root = _add_worktree(main_root, tmp_path / "wt")
        monkeypatch.chdir(wt_root)

        with pytest.raises(SystemExit):
            _require_project_initialized("some-ai-id", output_format="json")

        assert not (wt_root / ".empirica").exists()

    def test_never_touches_an_existing_empirica_dir_in_the_worktree(self, tmp_path, monkeypatch):
        """A worktree that already has SOME .empirica/ (even incomplete) is left
        to the normal not-initialized error path, not silently replaced."""
        main_root = _init_main_repo(tmp_path / "main")
        wt_root = _add_worktree(main_root, tmp_path / "wt")
        own_empirica = wt_root / ".empirica"
        own_empirica.mkdir()
        (own_empirica / "marker.txt").write_text("pre-existing, not a symlink\n")
        monkeypatch.chdir(wt_root)

        with pytest.raises(SystemExit):
            _require_project_initialized("some-ai-id", output_format="json")

        assert not own_empirica.is_symlink()
        assert (own_empirica / "marker.txt").exists()


class TestAutoInitLinksWorktreesInsteadOfMinting:
    def test_links_to_main_project_instead_of_minting_a_separate_one(self, tmp_path, monkeypatch):
        main_root = _init_main_repo(tmp_path / "main")
        wt_root = _add_worktree(main_root, tmp_path / "wt")
        monkeypatch.chdir(wt_root)
        args = Namespace(auto_init=True)

        performed, project_id, project_path = _handle_auto_init(args, output_format="json", project_id=None)

        linked = wt_root / ".empirica"
        assert linked.is_symlink()
        assert linked.resolve() == (main_root / ".empirica").resolve()
        # Adopted the MAIN checkout's canonical project_id, not a freshly minted one.
        assert project_id == "2273f11d-0000-4000-8000-000000000000"
        assert project_path == str(wt_root)
        # Nothing was "initialized" here — it was linked.
        assert performed is False

    def test_falls_back_to_mint_when_main_checkout_is_also_uninitialized(self, tmp_path, monkeypatch):
        """Sanity check: a worktree of a genuinely fresh (never-initialized) repo
        still goes through the ordinary mint path — this fix must not swallow it."""
        main_root = _init_main_repo(tmp_path / "main", initialized=False)
        wt_root = _add_worktree(main_root, tmp_path / "wt")
        monkeypatch.chdir(wt_root)
        args = Namespace(auto_init=True)

        init_result = {"project_id": "freshly-minted-uuid"}
        from unittest.mock import patch

        with patch(
            "empirica.cli.command_handlers.project_init.handle_project_init_command",
            return_value=init_result,
        ):
            performed, project_id, project_path = _handle_auto_init(args, output_format="json", project_id=None)

        assert not (wt_root / ".empirica").is_symlink()
        assert performed is True
        assert project_id == "freshly-minted-uuid"
        assert project_path == str(wt_root)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
