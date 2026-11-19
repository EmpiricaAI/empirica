"""Which tmux server the cockpit talks to (goal 44203f3d, mesh-support prop_bvovvosl).

By default plain ``tmux`` (the user's private socket under /tmp/tmux-<uid>). A cockpit config with ``socket: <path>`` runs
its server on that path instead, so another account can attach read-only (the shared stage on halo-strix). The path
travels in ``EMPIRICA_TMUX_SOCKET``: the launcher sets it for its own process and in the tmux server's environment, so
hooks, run-shell commands and status scans started from the cockpit reach the same server. Inside a pane ``$TMUX``
already names the socket; this matters for every call made from outside one.
"""

from __future__ import annotations

import os

ENV = "EMPIRICA_TMUX_SOCKET"


def socket_path() -> str | None:
    return os.environ.get(ENV) or None


def tmux_argv(*args: str) -> list[str]:
    """``tmux <args>``, aimed at the configured socket when there is one."""
    sock = socket_path()
    return ["tmux", "-S", sock, *args] if sock else ["tmux", *args]


def use_socket(path: str | None) -> None:
    """Aim this process and its children at ``path`` (None: the default server)."""
    if path:
        os.environ[ENV] = path
    else:
        os.environ.pop(ENV, None)
