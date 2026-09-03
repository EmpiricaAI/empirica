"""Which remote to push to — earned from config, never guessed.

Empirica used to carry three remote defaults: ``remote: forgejo``,
``notes_remote: forgejo``, ``code_remote: origin``. Each read as safe. Measured
across three seats in one week they produced *opposite* invisible failures from
the same literal:

- a seat whose ``origin`` was a **public GitHub repo** — the code default pointed
  at publication, and ``sync-status`` reported ``remote_configured: true`` because
  a remote by that name did exist;
- a seat with **no ``origin`` at all** — notes synced nowhere for weeks, and
  nothing said so;
- a seat where ``sync-config remote forgejo`` was set correctly and ``sync-status``
  still printed ``origin``, so the practitioner concluded the write had failed.

**Guessing wrong about a remote is publishing.** A default that is usually right
is the worst possible shape for that, because it works until the seat where it
does not, and never announces which case you are in. So there is no default here
and no literal tail on any lookup: unset means REFUSE, and the refusal names the
git remotes that actually exist plus the exact command to choose one.

David's ruling, 2026-09-02: *no guessing for anything — earned confidence, not
most-likely prediction.*

Two kinds, because they are two different disclosure decisions
--------------------------------------------------------------
``notes`` carries findings, mistakes and mesh messages — private by default.
``code`` carries commits. Resolving both through one key is how a remote chosen
to hold private notes silently became the target for code on a seat where only
one of the two was ever set.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

NOTES = "notes"
CODE = "code"

#: Resolution order per kind. First key holding a truthy value wins. **There is no
#: literal at the end of either tuple** — that absence is the design.
#:
#: ``notes`` falls back to ``remote`` because ``remote`` is the historical notes key
#: and ``sync-push``/``sync-pull`` read it. Without that step, ``sync-config
#: notes_remote X`` would retarget ``profile-sync`` and not ``sync-push`` — one key,
#: two verbs, different destinations, which is the drift this consolidates away.
RESOLUTION_ORDER: dict[str, tuple[str, ...]] = {
    NOTES: ("notes_remote", "remote"),
    CODE: ("code_remote",),
}

#: The key ``sync-config`` should be told to set, per kind.
CONFIG_KEY: dict[str, str] = {NOTES: "notes_remote", CODE: "code_remote"}

_WHAT: dict[str, str] = {
    NOTES: "epistemic notes (findings, mistakes, decisions, mesh messages)",
    CODE: "code — the commits on your current branch",
}


def resolve(kind: str, sync_config: dict[str, Any], explicit: str | None = None) -> str | None:
    """The remote for `kind`, or None. **None means refuse, not fall back.**"""
    if explicit:
        return explicit
    for key in RESOLUTION_ORDER[kind]:
        value = sync_config.get(key)
        if value:
            return value
    return None


def git_remotes(root: Path | str | None = None) -> list[str]:
    """Remote names configured in the repo, or an empty list off-repo."""
    try:
        proc = subprocess.run(
            ["git", "remote"],
            cwd=str(root) if root else None,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return sorted(name for name in proc.stdout.split() if name)


def refusal(kind: str, root: Path | str | None = None) -> dict[str, Any]:
    """The payload every verb returns when :func:`resolve` came back None.

    Names three things, because a refusal naming fewer sends the reader hunting:
    what was not done, which remotes exist *here*, and the command that fixes it.
    """
    available = git_remotes(root)
    key = CONFIG_KEY[kind]
    fix = f"empirica sync-config {key} <remote>"
    remotes_note = ", ".join(available) if available else "none — `git remote add` one first"
    return {
        "ok": False,
        "error": (
            f"No {kind} remote configured — refusing to guess where to send {_WHAT[kind]}. "
            "There is deliberately no default: a wrong guess here publishes."
        ),
        "kind": kind,
        "config_key": key,
        "checked_keys": list(RESOLUTION_ORDER[kind]),
        "available_remotes": available,
        "hint": f"{fix}   (configured git remotes: {remotes_note})",
        "fix": fix,
    }


def render_refusal(payload: dict[str, Any]) -> str:
    """Human form of :func:`refusal`. Same three facts, two lines."""
    return f"❌ REFUSED: {payload['error']}\n   {payload['hint']}"
