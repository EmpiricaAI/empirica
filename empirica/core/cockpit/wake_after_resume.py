"""Wake a resumed claude pane once it is idle, so it takes its first turn (goal 87772ac4, David's ruling (a)).

After a restart a resumed claude sits idle at its prompt and its SessionStart hook has already run. Its mesh Monitor is armed by a turn, and no turn
comes until someone types: mesh-support measured one of about twelve resumed panes armed. A wake turn polls the inbox and re-arms the Monitor
(finding 117de356, the 10-24 trial). `cockpit refresh` calls this for the panes it respawned.

`wake_instance` reads the pane first and refuses one that is busy, mid-draft or on a dialog, so a fresh claude that has not drawn its prompt yet is
simply refused; this polls until it is not, and gives up at a deadline. A pane that never settles is reported, never typed into.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from empirica.core.cockpit.instance_actions import WakeResult, wake_instance
from empirica.core.cockpit.launcher.tmux import _launch_program

DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_INTERVAL_SECONDS = 3.0


def _tmux_instance(pane_id: str) -> str:
    return f"tmux_{pane_id.lstrip('%')}"


def wake_resumed(
    respawned: list[dict[str, Any]],
    *,
    wake: Callable[[str], WakeResult] = wake_instance,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_INTERVAL_SECONDS,
) -> list[dict[str, Any]]:
    """One result per respawned pane: ``{key, pane_id, woken, detail}``. Only a claude pane that respawned without error is woken.

    The idle check and the wake marker are claude's (a codex pane's prompt is drawn differently), and a pane whose respawn failed has nothing
    running to wake. A refusal is retried every ``interval`` seconds until ``timeout``; an unreachable pane (no tmux) is not retried.
    """
    results: list[dict[str, Any]] = []
    pending: dict[
        int, str
    ] = {}  # index into `respawned` -> tmux instance id. Indexed, not keyed: two entries may share a key.
    for i, entry in enumerate(respawned):
        base = {"key": str(entry.get("key")), "pane_id": entry.get("pane_id"), "woken": False}
        if entry.get("error"):
            results.append({**base, "detail": f"not woken: the respawn failed ({entry['error']})"})
        elif not entry.get("command") or _launch_program(str(entry["command"])) != "claude" or not entry.get("pane_id"):
            results.append({**base, "detail": "not woken: not a claude pane"})
        else:
            results.append({**base, "detail": "waiting for the pane to be idle"})
            pending[i] = _tmux_instance(str(entry["pane_id"]))

    start = now()
    expired = False
    while pending and not expired:
        for i, instance in list(pending.items()):
            if now() - start >= timeout:  # checked between panes too: one slow pass must not run far past the bound
                expired = True
                break
            try:
                outcome = wake(instance)
            except Exception as e:
                results[i]["detail"] = f"not woken: {e}"
                del pending[i]
                continue
            results[i]["detail"] = outcome.detail
            if outcome.success:
                results[i]["woken"] = True
                del pending[i]
            elif outcome.method != "refused":  # unreachable: waiting will not change it
                del pending[i]
        if not pending or expired:
            break
        remaining = timeout - (now() - start)
        if remaining <= 0:
            break
        sleep(min(interval, remaining))
    return results
