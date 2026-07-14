"""Shared version-drift detection for long-running empirica services.

`empirica.__version__` is frozen at import time; `importlib.metadata.version`
re-reads the dist-info every call, and pip overwrites that file on upgrade. A
mismatch means a pip upgrade (or editable-install refresh) landed *under* a
running process — the in-memory code is stale.

Both the mesh listener (`loop_scheduler/listener.py`) and the serve daemon
(`api/serve_app.py`) need this check, so the pure compare lives here as the
single source of truth. Each caller layers its OWN self-heal policy on top:

- The listener assumes a supervisor and self-exits by default (opt-OUT via
  ``EMPIRICA_LISTENER_NO_DRIFT_EXIT``).
- The serve daemon is often standalone, so it always SURFACES drift (on
  ``GET /health``) and only self-exits when supervised (opt-IN — see
  ``serve_app``). This module makes no exit decision; it only reports drift.
"""

from __future__ import annotations

import importlib.metadata


def version_drift() -> tuple[str, str] | None:
    """Return ``(in_process_version, installed_version)`` on drift, else None.

    Best-effort: returns None on any error (missing dist-info, import failure)
    so a drift check can never crash the calling service.
    """
    try:
        from empirica import __version__ as in_process

        installed = importlib.metadata.version("empirica")
        if in_process != installed:
            return (in_process, installed)
    except Exception:
        return None
    return None
