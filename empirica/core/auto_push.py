"""Auto-push CODE on a postflight boundary, opt-in, and never silently.

Nothing in empirica has ever pushed code. `sync-push` carries two refspecs, both
notes; `projects-sync` performs no git operation; `forgejo-publish` creates a repo
and sets a remote rather than delivering to it. Meanwhile `sync-status` rendered
``Code: <remote> (public)`` beside the notes line, so a practitioner auditing their
configuration saw code listed as configured and stopped looking. One seat
accumulated several hundred commits that existed only on a laptop, and no tooling
ever said so.

**A false label does not merely fail to inform, it terminates the enquiry.** That
is why the honest label shipped first and this shipped second: the gap had to stop
being invisible before it was worth automating away.

THE FIVE CONSTRAINTS, EACH FROM A DEFECT
----------------------------------------
1. **Opt-in per project.** Auto-pushing work-in-progress publishes things nobody
   chose to publish — half-finished branches, client names in commit messages,
   experiments. Enabling is a deliberate act through a validating path, never a
   default and never a hand-edit that silently does nothing.
2. **Refuse on a dirty tree, and name the files.** A push racing an uncommitted
   edit produces a remote state matching nothing the author ever had.
3. **Never silently no-op.** No remote, no upstream, nothing to push, detached
   HEAD — every one of those is SAID. The whole reason this exists is that a
   silent gap went unnoticed for months; a trigger that fires and pushes nothing
   without saying so reproduces the original defect inside its own fix, and is
   harder to catch the second time because now there is a feature everyone
   believes is handling it.
4. **Report verified-pushed, never attempted.** The remote ref is re-read after
   the push and compared to local HEAD. "I ran git push and it exited 0" is a
   claim about a subprocess; "the remote ref now equals my HEAD" is a claim about
   the world, and only the second is what the label promises.
5. **No default destination.** `code_remote` has no fallback. `origin` is a public
   GitHub repo on some seats and absent on others, so the same literal publishes
   on one box and no-ops on another — and neither says which. Unset means refuse.

WHY POSTFLIGHT AND NOT SESSION_END
----------------------------------
A closed transaction is a coherent unit, which is what a backup boundary wants.
Sessions also end by crashing, so a trigger firing on abnormal termination fires
exactly when state is least trustworthy. `session_end` is left UNIMPLEMENTED
rather than half-implemented — a half-implemented trigger is indistinguishable
from a working one until the day it matters.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

#: The only trigger implemented. `session_end` is deliberately absent — see module
#: docstring. Validation rejects anything else by name rather than ignoring it.
SUPPORTED_TRIGGERS: frozenset[str] = frozenset({"postflight"})

#: Outcomes. Every one is reported; none is silence.
PUSHED = "pushed"
REFUSED_DIRTY = "refused_dirty_tree"
NOT_ENABLED = "not_enabled"
NO_REMOTE = "no_remote"
NOTHING_TO_PUSH = "nothing_to_push"
DETACHED = "detached_head"
FAILED = "push_failed"
UNVERIFIED = "push_reported_success_but_ref_did_not_move"


def _git(root: Path, *args: str, timeout: int = 60) -> tuple[int, str, str]:
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, timeout=timeout, check=False)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def dirty_files(root: Path) -> list[str]:
    """Uncommitted paths, or an empty list. Names them — a count is not actionable."""
    rc, out, _ = _git(root, "status", "--porcelain")
    if rc != 0:
        return []
    return [line[3:].strip() for line in out.splitlines() if line.strip()]


def _current_branch(root: Path) -> str | None:
    rc, out, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or not out or out == "HEAD":
        return None  # detached, or not a repo
    return out


def auto_push(root: Path, sync_config: dict[str, Any], trigger: str) -> dict[str, Any]:
    """Push code for `trigger`, or say precisely why not. Never raises, never silent.

    Returns an outcome dict whose `pushed` is True ONLY when the remote ref was
    re-read and matches local HEAD. Every other path carries a `reason` a human can
    act on, because the alternative is the defect this feature exists to remove.
    """
    enabled = sync_config.get("auto_push_on") or []
    if trigger not in enabled:
        return {"outcome": NOT_ENABLED, "pushed": False, "reason": f"auto_push_on does not include {trigger!r}"}

    # CONSTRAINT 5. No default destination. This read used to end in `or "origin"`,
    # which is the single most consequential guess in the codebase: on at least one
    # seat `origin` is a PUBLIC GitHub repo, so a practitioner who enabled auto-push
    # for a private backup would have published every commit instead. An unset
    # code_remote is an absent answer, not a cautious one — refuse and say so.
    remote = sync_config.get("code_remote")
    if not remote:
        available = sorted(_remotes(root))
        return {
            "outcome": NO_REMOTE,
            "pushed": False,
            "remote": None,
            "available_remotes": available,
            "reason": (
                "code_remote is not set — refusing to guess where to push code. "
                "Set it deliberately: `empirica sync-config code_remote <remote>` "
                f"(configured here: {', '.join(available) or 'none'})"
            ),
        }
    if remote not in _remotes(root):
        return {
            "outcome": NO_REMOTE,
            "pushed": False,
            "remote": remote,
            "reason": f"code_remote {remote!r} is not a configured git remote — nothing was pushed",
        }

    # CONSTRAINT 2. Checked before the push, not after, and the files are named.
    dirty = dirty_files(root)
    if dirty:
        return {
            "outcome": REFUSED_DIRTY,
            "pushed": False,
            "remote": remote,
            "dirty_files": dirty[:20],
            "dirty_count": len(dirty),
            "reason": (
                f"{len(dirty)} uncommitted change(s) — refusing, because a push racing an "
                "uncommitted edit produces a remote state matching nothing you ever had"
            ),
        }

    branch = _current_branch(root)
    if branch is None:
        return {
            "outcome": DETACHED,
            "pushed": False,
            "remote": remote,
            "reason": "detached HEAD — no branch to push",
        }

    local = _rev(root, "HEAD")
    before = _rev(root, f"{remote}/{branch}")
    if local and before and local == before:
        return {
            "outcome": NOTHING_TO_PUSH,
            "pushed": False,
            "remote": remote,
            "branch": branch,
            "reason": f"{remote}/{branch} already at {local[:8]}",
        }

    rc, _, err = _git(root, "push", remote, branch, timeout=180)
    if rc != 0:
        return {
            "outcome": FAILED,
            "pushed": False,
            "remote": remote,
            "branch": branch,
            "reason": (err or "git push failed with no stderr")[:400],
        }

    # CONSTRAINT 4. Re-read the remote ref rather than trusting the exit code.
    # A zero exit is a fact about a subprocess; a moved ref is a fact about the world.
    _git(root, "fetch", remote, branch, timeout=120)
    after = _rev(root, f"{remote}/{branch}")
    if after != local:
        return {
            "outcome": UNVERIFIED,
            "pushed": False,
            "remote": remote,
            "branch": branch,
            "local": local,
            "remote_ref": after,
            "reason": (
                "git push exited 0 but the remote ref does not match local HEAD — "
                "reporting NOT pushed, because the label has to mean verified"
            ),
        }

    return {
        "outcome": PUSHED,
        "pushed": True,
        "remote": remote,
        "branch": branch,
        "commit": local,
        "reason": f"{remote}/{branch} verified at {local[:8]}",
    }


def _remotes(root: Path) -> set[str]:
    rc, out, _ = _git(root, "remote")
    return set(out.split()) if rc == 0 else set()


def _rev(root: Path, ref: str) -> str | None:
    rc, out, _ = _git(root, "rev-parse", ref)
    return out if rc == 0 and out else None


def render(outcome: dict[str, Any]) -> str:
    """One line a practitioner can act on. Never a bare count, never silence."""
    if outcome.get("outcome") == NOT_ENABLED:
        return ""  # the only quiet path: the feature is off, which the user chose
    if outcome.get("pushed"):
        return f"⬆️  auto-push: {outcome['remote']}/{outcome['branch']} verified at {outcome['commit'][:8]}"
    if outcome.get("outcome") == REFUSED_DIRTY:
        files = ", ".join(outcome.get("dirty_files", [])[:5])
        more = "" if outcome["dirty_count"] <= 5 else f" (+{outcome['dirty_count'] - 5} more)"
        return f"⚠️  auto-push refused — {outcome['dirty_count']} uncommitted: {files}{more}"
    return f"⚠️  auto-push did not push: {outcome.get('reason', 'no reason reported')}"
