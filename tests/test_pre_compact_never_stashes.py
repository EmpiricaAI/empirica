"""pre-compact no longer takes the user's working tree away.

The hook stashed uncommitted work "before snapshot" and popped it afterwards. Nothing consumed the stash (no reader of the
stash or its tag outside the hook; project-bootstrap ran AFTER the stash and so recorded a clean tree), it emptied the
tree for up to ~25 s of a shared checkout, and every way the work was ever lost in this hook (an exit path that skipped the
pop, a pop of someone else's stash, SIGKILL) existed only because of the stash. David, 2026-10-13: if it serves no purpose,
remove it. What stays is recovery of a stash an OLDER hook left behind, for one more release.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture
def hook():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("pre_compact_no_stash", _HOOK_DIR / "pre-compact.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, timeout=15
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "tracked.txt").write_text("committed\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    monkeypatch.chdir(r)
    return r


def _legacy_orphan(repo: Path, tag: str) -> None:
    """What an older hook left behind when it was killed between its push and its pop."""
    (repo / "tracked.txt").write_text("EDITED\n")
    (repo / "new.txt").write_text("untracked\n")
    _git(repo, "stash", "push", "-m", f"{tag} sess1234 - 2026-10-01 10:00:00", "--include-untracked")


def test_main_leaves_a_dirty_tree_exactly_as_it_found_it(hook, repo, monkeypatch):
    (repo / "tracked.txt").write_text("EDITED\n")
    (repo / "new.txt").write_text("untracked\n")
    seen = {}

    def _body(**kwargs):
        seen["tracked"] = (repo / "tracked.txt").read_text()
        seen["untracked"] = (repo / "new.txt").exists()
        seen["stash"] = _git(repo, "stash", "list")
        seen["kwargs"] = sorted(kwargs)
        sys.exit(0)

    monkeypatch.setattr(hook, "find_project_root", lambda **_kw: repo)
    monkeypatch.setattr(hook, "_write_compact_handoff", lambda *_a, **_kw: None)
    monkeypatch.setattr(hook, "_detect_empirica_session", lambda *_a, **_k: "sess1234")
    monkeypatch.setattr(hook, "_extract_last_task", lambda *_a, **_kw: "")
    monkeypatch.setattr(hook, "_main_guarded", _body)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"trigger": "auto", "session_id": "cc-1"})))

    with pytest.raises(SystemExit):
        hook.main()

    assert seen["tracked"] == "EDITED\n", "the body ran with the user's work still in the tree"
    assert seen["untracked"] is True
    assert seen["stash"] == ""
    assert "stash_sha" not in seen["kwargs"]
    assert (repo / "tracked.txt").read_text() == "EDITED\n" and (repo / "new.txt").exists()
    assert _git(repo, "stash", "list") == ""


def test_the_hook_has_no_way_left_to_create_a_stash(hook):
    source = (_HOOK_DIR / "pre-compact.py").read_text()
    assert '"stash", "push"' not in source
    assert not hasattr(hook, "_stash_uncommitted_work") and not hasattr(hook, "_install_stash_guard")


def test_recovery_restores_a_stash_an_older_run_left_behind(hook, repo):
    _legacy_orphan(repo, hook.STASH_TAG)
    assert (repo / "tracked.txt").read_text() == "committed\n"

    subject = hook._recover_orphaned_stashes()

    assert subject and hook.STASH_TAG in subject
    assert (repo / "tracked.txt").read_text() == "EDITED\n"
    assert (repo / "new.txt").read_text() == "untracked\n"
    assert _git(repo, "stash", "list") == ""


def test_recovery_never_touches_a_stash_we_did_not_create(hook, repo):
    (repo / "tracked.txt").write_text("USER WIP\n")
    _git(repo, "stash", "push", "-m", "my own work in progress")

    assert hook._recover_orphaned_stashes() is None
    assert "my own work in progress" in _git(repo, "stash", "list")


def test_recovery_never_overwrites_live_work(hook, repo):
    _legacy_orphan(repo, hook.STASH_TAG)
    (repo / "tracked.txt").write_text("NEWER WORK\n")

    assert hook._recover_orphaned_stashes() is None
    assert (repo / "tracked.txt").read_text() == "NEWER WORK\n"
    assert hook.STASH_TAG in _git(repo, "stash", "list")
