"""The pre-compact stash and its pop must be a single unbreakable lifecycle.

Between `_stash_uncommitted_work` and the pop, the user's working tree exists
ONLY inside that stash. Every one of these tests is a negative control for a way
the tree was actually lost:

- the pop lived on the happy path only, so three `sys.exit(2)` branches and the
  harness timeout each left the tree empty and the stash orphaned
- the pop took `stash@{0}`, so in a shared checkout it could restore someone
  else's stash instead of ours
- SIGKILL runs no handler, so recovery cannot live only in the run that stashed
- the report said "saved+restored" whenever a stash was *created*, so a failed
  restore was indistinguishable from a successful one

Real git repos in tmp_path — the operations under test ARE git operations, so
stubbing git would test the stub. Nothing here touches the developer's repo:
every subprocess is pinned to `cwd=repo`.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


def _load_hook():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("pre_compact_hook", _HOOK_DIR / "pre-compact.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hook():
    return _load_hook()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, timeout=15
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with one commit, made the process cwd (the hook reads os.getcwd())."""
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


def test_a_clean_tree_produces_no_stash(hook, repo):
    assert hook._stash_uncommitted_work("sess1234") is None
    assert _git(repo, "stash", "list") == ""


def test_round_trip_restores_tracked_and_untracked(hook, repo):
    (repo / "tracked.txt").write_text("EDITED\n")
    (repo / "new.txt").write_text("untracked\n")

    sha = hook._stash_uncommitted_work("sess1234")
    assert sha, "a dirty tree must produce a stash sha, not a bool"
    assert (repo / "tracked.txt").read_text() == "committed\n"
    assert not (repo / "new.txt").exists()

    assert hook._restore_stash(sha) is True
    assert (repo / "tracked.txt").read_text() == "EDITED\n"
    assert (repo / "new.txt").read_text() == "untracked\n"
    assert _git(repo, "stash", "list") == ""


def test_restore_pops_OUR_stash_when_another_landed_on_top(hook, repo):
    """NEGATIVE CONTROL for the position bug: bare `git stash pop` takes the top.

    A shared checkout (several instances, one working tree) can push a stash
    between our push and our pop. Popping by position restores their work over
    this tree and buries ours.
    """
    (repo / "tracked.txt").write_text("OURS\n")
    ours = hook._stash_uncommitted_work("sess1234")

    (repo / "tracked.txt").write_text("SOMEONE ELSE\n")
    _git(repo, "stash", "push", "-m", "another instance", "--include-untracked")

    assert hook._find_stash_ref(ours) == "stash@{1}"
    assert hook._restore_stash(ours) is True
    assert (repo / "tracked.txt").read_text() == "OURS\n"
    assert "another instance" in _git(repo, "stash", "list")


def test_the_old_bare_pop_really_did_restore_the_wrong_stash(hook, repo):
    """The control, kept rather than run once and thrown away.

    Performs the ORIGINAL operation — `git stash pop` with no ref — in the same
    scenario as the test above, and asserts it does the wrong thing. If this
    ever starts passing "correctly", git's stash-ordering semantics changed and
    the identity lookup above needs re-examining, so the guard is still earning
    its place after the bug is fixed.
    """
    (repo / "tracked.txt").write_text("OURS\n")
    hook._stash_uncommitted_work("sess1234")

    (repo / "tracked.txt").write_text("SOMEONE ELSE\n")
    _git(repo, "stash", "push", "-m", "another instance", "--include-untracked")

    _git(repo, "stash", "pop")  # what the hook used to do

    assert (repo / "tracked.txt").read_text() == "SOMEONE ELSE\n"
    assert hook.STASH_TAG in _git(repo, "stash", "list"), "ours is still buried"


def test_restore_is_idempotent(hook, repo):
    """The finally-block runs after the happy path already popped. Not a failure."""
    (repo / "tracked.txt").write_text("EDITED\n")
    sha = hook._stash_uncommitted_work("sess1234")

    assert hook._restore_stash(sha) is True
    assert hook._restore_stash(sha) is True
    assert (repo / "tracked.txt").read_text() == "EDITED\n"


def test_recovery_restores_a_stash_a_killed_run_left_behind(hook, repo):
    """NEGATIVE CONTROL for SIGKILL: no handler runs, so the NEXT run must recover."""
    (repo / "tracked.txt").write_text("EDITED\n")
    (repo / "new.txt").write_text("untracked\n")
    hook._stash_uncommitted_work("sess1234")  # sha dropped on the floor, as a kill would

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
    """A dirty tree means someone is working. Popping into it could conflict."""
    (repo / "tracked.txt").write_text("EDITED\n")
    hook._stash_uncommitted_work("sess1234")
    (repo / "tracked.txt").write_text("NEWER WORK\n")

    assert hook._recover_orphaned_stashes() is None
    assert (repo / "tracked.txt").read_text() == "NEWER WORK\n"
    assert hook.STASH_TAG in _git(repo, "stash", "list")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (lambda **kw: sys.exit(2), SystemExit),  # bootstrap returncode != 0, and the generic except
        (lambda **kw: sys.exit(0), SystemExit),  # the happy path
        (lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")), RuntimeError),  # anything unforeseen
    ],
    ids=["exit-2", "exit-0", "unhandled-exception"],
)
def test_the_tree_comes_back_however_the_hook_leaves(hook, repo, monkeypatch, body, expected):
    """NEGATIVE CONTROL for the original bug: the pop was on the happy path only.

    Drives the REAL `main()` so the restore under test is the hook's own
    `finally`, not one written by this test. Only the surrounding I/O is stubbed
    — the stash, the exits and the restore are all live.
    """
    import io

    (repo / "tracked.txt").write_text("EDITED\n")
    (repo / "new.txt").write_text("untracked\n")

    monkeypatch.setattr(hook, "find_project_root", lambda **_kw: repo)
    monkeypatch.setattr(hook, "_write_compact_handoff", lambda *_a, **_kw: None)
    monkeypatch.setattr(hook, "_detect_empirica_session", lambda: "sess1234")
    monkeypatch.setattr(hook, "_extract_last_task", lambda *_a, **_kw: "")
    monkeypatch.setattr(hook, "_main_guarded", body)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"trigger": "auto", "session_id": "cc-1"})))

    with pytest.raises(expected):
        hook.main()

    assert (repo / "tracked.txt").read_text() == "EDITED\n"
    assert (repo / "new.txt").read_text() == "untracked\n"
    assert _git(repo, "stash", "list") == ""


def test_main_really_does_stash_before_the_guarded_body(hook, repo, monkeypatch):
    """Guards the test above from passing vacuously.

    If main() ever stopped stashing, every assertion in that test would still
    hold — the tree would be intact because it was never taken away. This pins
    the precondition: inside the guarded body, the tree IS empty.
    """
    import io

    (repo / "tracked.txt").write_text("EDITED\n")
    seen = {}

    def _body(**kwargs):
        seen["tracked"] = (repo / "tracked.txt").read_text()
        seen["sha"] = kwargs.get("stash_sha")
        sys.exit(0)

    monkeypatch.setattr(hook, "find_project_root", lambda **_kw: repo)
    monkeypatch.setattr(hook, "_write_compact_handoff", lambda *_a, **_kw: None)
    monkeypatch.setattr(hook, "_detect_empirica_session", lambda: "sess1234")
    monkeypatch.setattr(hook, "_extract_last_task", lambda *_a, **_kw: "")
    monkeypatch.setattr(hook, "_main_guarded", _body)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"trigger": "auto", "session_id": "cc-1"})))

    with pytest.raises(SystemExit):
        hook.main()

    assert seen["tracked"] == "committed\n", "the body must run with the tree stashed away"
    assert seen["sha"], "the guarded body receives the stash identity, not a bool"
    assert (repo / "tracked.txt").read_text() == "EDITED\n"


def test_sigterm_guard_restores_before_dying(hook, repo):
    """NEGATIVE CONTROL for the harness hook-timeout kill (SIGTERM)."""
    import os
    import signal

    (repo / "tracked.txt").write_text("EDITED\n")
    sha = hook._stash_uncommitted_work("sess1234")
    hook._install_stash_guard(sha)

    with pytest.raises(SystemExit) as excinfo:
        os.kill(os.getpid(), signal.SIGTERM)

    assert excinfo.value.code == 143
    assert (repo / "tracked.txt").read_text() == "EDITED\n"
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def test_the_report_is_keyed_on_the_pop_not_the_push():
    """A status that reads the same whether or not the tree came back is no status.

    Source-level because the string is built inside the bootstrap happy path.
    """
    source = (_HOOK_DIR / "pre-compact.py").read_text()

    assert 'stash_msg = " (stash: saved+restored)" if stash_created else ""' not in source
    assert "elif restored:" in source
    assert "ORPHANED" in source and "git stash pop" in source
