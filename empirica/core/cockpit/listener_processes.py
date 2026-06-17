"""Listener subprocess inventory — orphan detection + reaping.

Companion to the listener_active_*.json file GC (`empirica listener gc`):
that pass cleans the durable STATE layer, this module covers the PROCESS
layer. Two listener-shaped process kinds can outlive their parent Claude
Code session and accumulate across sessions:

  - ``empirica loop listen --instance <ai_id>`` (standalone ntfy
    subscriber, plus the ``while true; do …`` supervisor shell that
    wraps it in Monitor-armed standalone mode)
  - ``tail -F …/loop_fires.log`` (the persistent-service-mode session
    bridge)

Orphan criterion: the process was reparented to PID 1 (init/subreaper),
i.e. its parent — the Claude Code session that armed it — is gone.
Children of the systemd user manager are NOT flagged: a user service's
parent is the ``systemd --user`` process, never PID 1, so legitimately
supervised listeners are invisible to this walk.

Container caveat: under a PID-1 entrypoint (e.g. a practice container
that nohups its listener from the entrypoint), PPID 1 is the normal
parent. Callers default to dry-run / report-only so that environment
sees a warning, not a kill.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time

# Substrings that identify a listener-shaped cmdline. Checked against the
# full args string from `ps`; the loop-listen pattern also matches the
# supervisor shell that wraps it (both die together on reap, which is the
# point — killing only the child would just get it relaunched).
_LOOP_LISTEN_MARKER = "empirica loop listen"
_LOG_TAIL_MARKER = "loop_fires.log"


def walk_listener_processes() -> list[dict]:
    """Inventory all listener-shaped processes for the current user.

    Returns one dict per match: ``{pid, ppid, kind, cmdline}`` where
    ``kind`` is ``loop_listen`` | ``log_tail``. Never raises — an
    unavailable ``ps`` returns an empty list.
    """
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    procs: list[dict] = []
    own_pid = os.getpid()
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if pid == own_pid:
            continue
        cmdline = parts[2]
        if _LOOP_LISTEN_MARKER in cmdline:
            kind = "loop_listen"
        elif "tail" in cmdline and _LOG_TAIL_MARKER in cmdline:
            kind = "log_tail"
        else:
            continue
        procs.append({"pid": pid, "ppid": ppid, "kind": kind, "cmdline": cmdline})
    return procs


def _ai_id_from_listener_cmdline(cmdline: str) -> str | None:
    """Extract the ai_id from a listener cmdline.

    loop_listen: ``empirica loop listen --instance <ai_id>``.
    log_tail:    grep filter ``"instance_id": "<ai_id>"``.
    """
    m = re.search(r"--instance\s+(\S+)", cmdline)
    if m:
        return m.group(1).strip("'\"")
    m = re.search(r'"instance_id":\s*"([^"]+)"', cmdline)
    if m:
        return m.group(1)
    return None


def _is_supervised_listener(proc: dict) -> bool:
    """True if a PID-1 ``loop_listen`` proc is a live OS-supervised worker.

    macOS launchd reparents its supervised services to PID 1 (unlike
    systemd-user, whose children's parent is the ``systemd --user`` process,
    never PID 1). So on macOS a live launchd-backed worker looks exactly like an
    orphan to the PPID-1 walk — without this check, ``gc --apply`` reaps live
    workers (→ KeepAlive respawn → churn). Guarded to darwin only: on
    systemd hosts PID 1 genuinely means orphaned, so the check must not run
    there (it would protect real Linux orphans that coexist with a live
    service). Only ``loop_listen`` is OS-supervised — a ``log_tail`` Monitor
    bridge is per-session and is a genuine orphan when reparented. Delegates to
    ``is_listener_running``, whose launchd branch was corrected in the
    duplicate-listener fix.
    """
    if proc.get("kind") != "loop_listen":
        return False
    import sys
    # getattr form avoids the static literal-narrowing that would otherwise
    # mark the macOS branch unreachable on a non-darwin analysis host.
    if getattr(sys, "platform", "") != "darwin":
        return False
    ai = _ai_id_from_listener_cmdline(proc.get("cmdline", ""))
    if not ai:
        return False
    try:
        from empirica.core.loop_scheduler.persistent_listener import (
            is_listener_running,
        )
        return is_listener_running(ai)
    except Exception:
        return False


def walk_orphan_listener_processes(ai_id: str | None = None) -> list[dict]:
    """Listener processes whose parent session is dead (reparented to PID 1).

    Pass ``ai_id`` to scope the walk to one practitioner's listeners —
    matched against the ``--instance <ai_id>`` arg (loop listen) or the
    ``"instance_id": "<ai_id>"`` grep filter (log tail).
    """
    orphans = [p for p in walk_listener_processes() if p["ppid"] == 1]
    if ai_id:
        # Boundary-anchored so `--instance empirica` can't match
        # `--instance empirica-outreach` (slug prefixes are common).
        instance_re = re.compile(
            rf"--instance\s+{re.escape(ai_id)}(?=[\s;'\"]|$)"
        )
        tail_marker = f'"instance_id": "{ai_id}"'
        orphans = [
            p for p in orphans
            if instance_re.search(p["cmdline"]) or tail_marker in p["cmdline"]
        ]
    # Drop live launchd-supervised workers — on macOS they're reparented to
    # PID 1 and would otherwise be mis-flagged as orphans and reaped.
    return [p for p in orphans if not _is_supervised_listener(p)]


def reap_processes(
    procs: list[dict], apply: bool, term_grace_sec: float = 3.0,
) -> list[dict]:
    """TERM each process, escalate to KILL after the grace window.

    Mutates and returns the entries: adds ``removed`` (bool) and, on
    failure, ``error``. Dry-run (``apply=False``) annotates only.
    Best-effort throughout — a process that died between walk and reap
    counts as removed.
    """
    for entry in procs:
        entry["removed"] = False
        if not apply:
            continue
        pid = entry["pid"]
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            entry["removed"] = True
            continue
        except OSError as e:
            entry["error"] = str(e)
            continue
        deadline = time.monotonic() + term_grace_sec
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                entry["removed"] = True
                break
            time.sleep(0.1)
        if not entry["removed"]:
            try:
                os.kill(pid, signal.SIGKILL)
                entry["removed"] = True
            except ProcessLookupError:
                entry["removed"] = True
            except OSError as e:
                entry["error"] = str(e)
    return procs
