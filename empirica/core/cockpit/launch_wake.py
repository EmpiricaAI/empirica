"""Wake the claude panes `cockpit launch` opened, from a process that outlives the launch (goal f39d3cd0, David's ruling 2026-11-05).

A resumed claude sits idle after its SessionStart hook; its mesh Monitor is armed by a turn, and no turn comes until someone types.
`cockpit refresh` gives each pane it respawns a wake turn. After the 2026-11-02 restart mesh-support measured 0 of 6 resumed panes
armed: the restart was `tmux kill-server` then `cockpit launch`, and launch did not wake.

Launch cannot do it in its own process: two of its three paths hand the terminal to tmux with `os.execvp`, and the third would hold the
shell for up to 90 seconds. So launch starts a detached process that runs the same `wake_resumed` refresh uses and writes what happened
to a file per session. `cockpit status` reads that file. A wake that crashed is `failed`, one that never finished is `stalled`, and a
file that cannot be read is `unreadable`: none of them looks like success.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from empirica.core.cockpit.launcher import state as launcher_state
from empirica.core.cockpit.launcher.tmux import _launch_program
from empirica.core.cockpit.wake_after_resume import DEFAULT_TIMEOUT_SECONDS
from empirica.core.cockpit.wake_after_resume import wake_resumed as _wake_resumed

logger = logging.getLogger(__name__)

#: A wake still marked running this long after it started did not finish: its deadline is DEFAULT_TIMEOUT_SECONDS.
STALLED_AFTER_SECONDS = DEFAULT_TIMEOUT_SECONDS * 2

_CHILD = "import sys; from empirica.core.cockpit.launch_wake import run; run(sys.argv[1])"


def state_path(session_name: str) -> Path:
    """The result file for a session's last launch wake. The name is reduced to safe characters: it comes from a config file."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_name).strip(".") or "cockpit"
    return launcher_state.COCKPIT_DIR / f"launch_wake_{safe}.json"


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".launch_wake_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _is_claude_pane(entry: dict[str, Any]) -> bool:
    return bool(
        entry.get("pane_id")
        and not entry.get("error")
        and entry.get("command")
        and _launch_program(str(entry["command"])) == "claude"
    )


def start(
    opened: list[dict[str, Any]],
    session_name: str,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    """Start the detached wake for the claude panes among ``opened``. Returns ``{started, panes, detail}`` and never raises.

    Nothing is started, and no file is written, when launch opened no claude pane: a practice that was already running was not
    restarted, so it has had its turn."""
    entries = [e for e in opened if _is_claude_pane(e)]
    if not entries:
        return {"started": False, "panes": 0, "detail": "no claude pane was opened, nothing to wake"}
    path = state_path(session_name)
    record = {"session_name": session_name, "status": "running", "started_at": time.time(), "entries": entries}
    try:
        _write(path, record)
        popen(
            [sys.executable, "-c", _CHILD, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # it must outlive a launch that execs tmux, and must not hold the terminal
        )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        try:
            _write(path, {**record, "status": "failed", "finished_at": time.time(), "error": error})
        except Exception as write_error:  # the caller still gets the reason below; only the record for status is lost
            logger.debug("launch wake: could not record the failed start: %s", write_error)
        return {"started": False, "panes": len(entries), "detail": f"the wake could not be started ({error})"}
    return {
        "started": True,
        "panes": len(entries),
        "detail": f"waking {len(entries)} claude pane(s) in the background once each is idle",
    }


def run(path: str, *, wake_resumed: Callable[..., list[dict[str, Any]]] = _wake_resumed, **wake_kwargs: Any) -> None:
    """The detached process: wake the recorded panes and write the outcome. Every exit path writes a status."""
    target = Path(path)
    try:
        record = json.loads(target.read_text())
    except Exception:
        return  # nothing to wake and nowhere to say so: `read` reports the file as unreadable
    try:
        results = wake_resumed(record.get("entries") or [], **wake_kwargs)
        record.update(status="done", finished_at=time.time(), results=results)
    except Exception as e:
        record.update(status="failed", finished_at=time.time(), error=f"{type(e).__name__}: {e}")
    try:
        _write(target, record)
    except Exception as write_error:
        # The file still says running; `read` turns that into stalled once the deadline has passed.
        logger.debug("launch wake: could not write the outcome: %s", write_error)


def read(session_name: str, *, now: Callable[[], float] = time.time) -> dict[str, Any] | None:
    """The last launch wake for a session, or None when launch has never started one."""
    path = state_path(session_name)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            raise ValueError("not a mapping")
    except Exception as e:
        return {"status": "unreadable", "error": f"{type(e).__name__}: {e}", "path": str(path)}
    started = record.get("started_at")
    if (
        record.get("status") == "running"
        and isinstance(started, (int, float))
        and now() - started > STALLED_AFTER_SECONDS
    ):
        record["status"] = "stalled"
    return record


def summary_lines(record: dict[str, Any] | None) -> list[str]:
    """What `cockpit status` prints about the last launch wake: one line, then one per pane that was not woken."""
    if not record:
        return []
    status = record.get("status")
    if status == "unreadable":
        return [f"launch wake: the result file cannot be read ({record.get('error')}): {record.get('path')}"]
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record["started_at"])) if record.get("started_at") else "?"
    total = len(record.get("entries") or [])
    if status == "running":
        return [f"launch wake: running since {when}, {total} claude pane(s) waiting to be idle"]
    if status == "stalled":
        return [
            f"launch wake: started {when} for {total} claude pane(s) and never finished; `empirica cockpit refresh` wakes dead panes, or type in each pane"
        ]
    if status == "failed":
        return [f"launch wake: failed at {when} ({record.get('error')}); no pane was woken by launch"]
    results = record.get("results") or []
    woken = [r for r in results if r.get("woken")]
    lines = [f"launch wake: {len(woken)} of {len(results)} claude pane(s) woken at {when}"]
    lines += [f"  not woken: {r.get('key')} ({r.get('detail')})" for r in results if not r.get("woken")]
    return lines
