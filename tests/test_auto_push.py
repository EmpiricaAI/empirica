"""auto_push: the four constraints, each from a defect this codebase actually shipped.

Nothing in empirica pushed code, while `sync-status` rendered a code remote as
configured. One seat accumulated several hundred commits existing only on a laptop
and no tooling said so. **A false label does not merely fail to inform, it
terminates the enquiry** — which is why the honest label shipped before the
automation, and why every path here reports rather than returns quietly.

Each test names the constraint it defends. The controls matter more than the
assertions: a feature whose whole point is "never silently no-op" can be faked by
any implementation that returns a dict, so the tests check that the dict says the
true thing about the world rather than merely that it exists.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from empirica.core import auto_push as ap


def _run(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repo with a real remote, so the push path is exercised rather than mocked."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    work = tmp_path / "work"
    work.mkdir()
    _run(work, "init", "-b", "main")
    _run(work, "config", "user.email", "t@example.com")
    _run(work, "config", "user.name", "t")
    _run(work, "remote", "add", "origin", str(origin))
    (work / "a.txt").write_text("one\n")
    _run(work, "add", "a.txt")
    _run(work, "commit", "-m", "first")
    return work


ON = {"auto_push_on": ["postflight"], "code_remote": "origin"}


# ── constraint 1: opt-in ─────────────────────────────────────────────────────


def test_it_does_nothing_when_not_enabled(repo):
    """Default is OFF. Auto-pushing work-in-progress publishes things nobody chose
    to publish, so the absence of configuration must mean absence of pushing."""
    out = ap.auto_push(repo, {"auto_push_on": [], "code_remote": "origin"}, "postflight")
    assert out["pushed"] is False
    assert out["outcome"] == ap.NOT_ENABLED


def test_a_different_trigger_does_not_fire_postflight(repo):
    """`session_end` is unimplemented, not aliased. A trigger that fires when a
    session crashes fires when state is least trustworthy."""
    out = ap.auto_push(repo, {"auto_push_on": ["session_end"], "code_remote": "origin"}, "postflight")
    assert out["pushed"] is False
    assert out["outcome"] == ap.NOT_ENABLED


# ── constraint 2: refuse on a dirty tree, and NAME the files ─────────────────


def test_a_dirty_tree_refuses_and_names_the_files(repo):
    """A push racing an uncommitted edit produces a remote state matching nothing the
    author ever had. Naming beats counting: the value is seeing WHICH file."""
    (repo / "scratch.txt").write_text("uncommitted\n")

    out = ap.auto_push(repo, ON, "postflight")

    assert out["pushed"] is False
    assert out["outcome"] == ap.REFUSED_DIRTY
    assert "scratch.txt" in out["dirty_files"], "the refusal must name what is dirty"
    assert "scratch.txt" in ap.render(out), "and the rendered line must too"


def test_the_refusal_happens_before_the_push_not_after(repo):
    """A check that runs after the push is not a guard. Proved by the remote ref
    never moving — the only observation that distinguishes the two orderings."""
    _run(repo, "push", "origin", "main")
    (repo / "b.txt").write_text("new\n")
    _run(repo, "add", "b.txt")
    _run(repo, "commit", "-m", "second")
    before = ap._rev(repo, "origin/main")
    (repo / "dirty.txt").write_text("x\n")

    ap.auto_push(repo, ON, "postflight")

    assert ap._rev(repo, "origin/main") == before, "the remote moved despite a dirty tree"


# ── constraint 3: never silently no-op ───────────────────────────────────────


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda r: None, ap.NOTHING_TO_PUSH),
        (lambda r: _run(r, "remote", "remove", "origin"), ap.NO_REMOTE),
        (lambda r: _run(r, "checkout", "--detach"), ap.DETACHED),
    ],
    ids=["already-up-to-date", "no-remote", "detached-head"],
)
def test_every_non_push_path_gives_a_reason(repo, mutate, expected):
    """THE constraint. Each of these is a way to push nothing; none may be silent.
    The original defect went unnoticed for months precisely because nothing spoke."""
    _run(repo, "push", "origin", "main")
    mutate(repo)

    out = ap.auto_push(repo, ON, "postflight")

    assert out["pushed"] is False
    assert out["outcome"] == expected
    assert out.get("reason"), f"{expected} produced no reason"
    assert ap.render(out).strip(), f"{expected} rendered an empty line"


def test_only_the_disabled_path_is_quiet(repo):
    """NEGATIVE CONTROL on constraint 3. Something must be allowed to be silent, or
    every postflight prints noise — and it has to be exactly the case the user chose."""
    out = ap.auto_push(repo, {"auto_push_on": []}, "postflight")
    assert ap.render(out) == ""


# ── constraint 5: no default destination ─────────────────────────────────────


def test_an_unset_code_remote_refuses_and_does_not_fall_back_to_origin(repo):
    """THE constraint, and the highest-consequence guess in the codebase.

    This read used to end in `or "origin"`. `origin` is a PUBLIC GitHub repo on some
    seats, so a practitioner enabling auto-push for a private backup would have
    published every commit instead — and `origin` exists here, so the push would have
    SUCCEEDED and reported success.

    Note the fixture repo HAS an `origin`: if the fallback returned, this test fails
    by pushing, not by erroring. That is deliberate — a guard whose negative case
    cannot happen in the fixture proves nothing.
    """
    before = ap._rev(repo, "origin/main")

    out = ap.auto_push(repo, {"auto_push_on": ["postflight"]}, "postflight")

    assert out["outcome"] == ap.NO_REMOTE
    assert out["pushed"] is False
    assert out["remote"] is None, "an unset remote must not be reported as a name"
    assert "sync-config code_remote" in out["reason"], "the refusal must name the fix"
    assert ap._rev(repo, "origin/main") == before, "it pushed to origin anyway"


def test_the_refusal_names_the_remotes_that_exist(repo):
    """Refusing without saying what may be chosen moves the invisibility one step
    along — the reader still has to go find out what this repo has."""
    out = ap.auto_push(repo, {"auto_push_on": ["postflight"]}, "postflight")

    assert "origin" in out["available_remotes"]
    assert "origin" in out["reason"]


# ── constraint 4: report VERIFIED-pushed, never attempted ────────────────────


def test_a_real_push_is_verified_against_the_remote_ref(repo):
    """POSITIVE CONTROL for the whole file. If pushing never worked, every assertion
    above would pass while the feature was inert."""
    (repo / "b.txt").write_text("new\n")
    _run(repo, "add", "b.txt")
    _run(repo, "commit", "-m", "second")
    head = ap._rev(repo, "HEAD")

    out = ap.auto_push(repo, ON, "postflight")

    assert out["pushed"] is True, out.get("reason")
    assert out["commit"] == head
    assert ap._rev(repo, "origin/main") == head, "claimed pushed but the remote ref disagrees"


def test_a_zero_exit_with_an_unmoved_ref_reports_NOT_pushed(repo, monkeypatch):
    """The distinction the label rests on. `git push` exiting 0 is a fact about a
    subprocess; the remote ref matching HEAD is a fact about the world. If they
    disagree the honest answer is NOT pushed — anything else is the false label
    this feature was built to retire, reintroduced at the last step.
    """
    (repo / "b.txt").write_text("new\n")
    _run(repo, "add", "b.txt")
    _run(repo, "commit", "-m", "second")

    real = ap._git

    def fake(root, *args, **kw):
        if args and args[0] == "push":
            return 0, "", ""  # claims success, moves nothing
        return real(root, *args, **kw)

    monkeypatch.setattr(ap, "_git", fake)

    out = ap.auto_push(repo, ON, "postflight")

    assert out["pushed"] is False
    assert out["outcome"] == ap.UNVERIFIED
    assert "verified" in out["reason"]
