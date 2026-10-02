"""Does another LIVE claude, in another project, already own this instance id?

`~/.empirica/instance_projects/<id>.json` says which project an instance id means and which claude
session holds it. Two claudes that share an id share that file, and whichever starts last used to take
it over: on 2026-09-29, 10-01 and 10-02 a claude in empirica-nle that carried `EMPIRICA_INSTANCE_ID=empirica`
rewrote core's pointer at its own SessionStart, and core's commands then landed beside the wrong
transaction file.

The guard that existed looked for an open transaction in the NEWCOMER's project directory, where the
owner's transaction cannot be, and only when it was open. What identifies an owner worth protecting is
that it is alive. Claude Code keeps `~/.claude/sessions/<pid>.json` (sessionId, pid) for every running
session, so liveness is knowable without guessing.

Never raises: a hook must not fail because of this check.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    # A recycled pid number would pass the kill probe. The session file's owner must still be a claude.
    comm = Path(f"/proc/{pid}/comm")
    if comm.exists():
        try:
            return comm.read_text().strip() == "claude"
        except OSError:
            return True
    return True


def live_claude_pid(session_id: str, claude_dir: Path | None = None) -> int | None:
    """The pid of the running claude that holds ``session_id``, or None when there is none we can see."""
    sessions = (claude_dir or Path.home() / ".claude") / "sessions"
    try:
        files = list(sessions.glob("*.json"))
    except OSError:
        return None
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("sessionId") != session_id:
            continue
        try:
            pid = int(doc.get("pid") or f.stem)
        except (TypeError, ValueError):
            continue
        if _alive(pid):
            return pid
    return None


def foreign_live_owner(
    existing: object, my_session_id: str | None, my_project: str, claude_dir: Path | None = None
) -> dict | None:
    """The owner of an existing instance pointer when it must not be overwritten, else None.

    It must not be overwritten when the pointer names a DIFFERENT claude session, in a DIFFERENT
    project, and that session is alive. A dead owner, the same project (a resume or a second window
    in one practice), a pointer with no session id, or no session of ours to compare all fall through
    to the old behaviour.
    """
    try:
        if not isinstance(existing, dict) or not my_session_id:
            return None
        owner_session = existing.get("claude_session_id")
        owner_project = existing.get("project_path")
        if not owner_session or owner_session == my_session_id or not owner_project:
            return None
        if os.path.realpath(str(owner_project)) == os.path.realpath(my_project):
            return None
        pid = live_claude_pid(str(owner_session), claude_dir)
        if pid is None:
            return None
        return {"claude_session_id": str(owner_session), "project_path": str(owner_project), "pid": pid}
    except Exception:
        return None


def clash_notice(instance_id: str, owner: dict, my_project: str) -> str:
    """The text a newcomer is shown when it was refused the pointer."""
    mine = Path(my_project).name
    theirs = Path(owner["project_path"]).name
    return (
        f"## INSTANCE ID CLASH: not taking over `{instance_id}`\n"
        f"This claude carries EMPIRICA_INSTANCE_ID={instance_id}, which belongs to the practice `{theirs}` "
        f"(live claude session {owner['claude_session_id'][:8]}, pid {owner['pid']}). Its instance pointer was NOT "
        f"overwritten. You are in `{mine}`; empirica commands that resolve through the instance id would "
        f"write into `{theirs}`'s store, and PREFLIGHT will refuse them.\n"
        f"Fix: restart this claude from a shell that does not carry the id: "
        f"`unset EMPIRICA_INSTANCE_ID; EMPIRICA_INSTANCE_ID={mine} claude --continue` "
        f"(or, in a cockpit, exit it and run `empirica cockpit refresh`)."
    )
