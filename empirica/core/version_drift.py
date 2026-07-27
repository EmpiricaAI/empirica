"""Shared version-drift detection for long-running empirica services.

`empirica.__version__` is frozen at import time; `importlib.metadata.version`
re-reads the dist-info every call, and pip overwrites that file on upgrade. For a
NON-editable install a mismatch means a pip upgrade landed *under* a running
process — the in-memory code is stale and a restart genuinely fixes it.

**For an editable install the same comparison means the opposite thing, and the
guard built on it is unsound.** An editable install's entire purpose is that
source changes take effect WITHOUT reinstallation, so the code is current by
construction while the dist-info stays frozen at whatever it was when the install
was last recorded. A release bumps the source tree; every editable seat then
reports drift forever, and a restart reloads the identical stale metadata.

Measured 2026-07-27 on David's box: **1849 restarts over ~4 days**, roughly 182 in
the hour extension spent measuring, because `serve` self-exited on this false
positive and `Restart=always` relaunched it into the same state. Nothing about it
was specific to that release — it is what EVERY version bump does to an editable
seat, and all three seats checked (local venv, pipx, the hetzner host) report
``dir_info.editable``. So the guard was not protecting the fleet, it was flapping
it.

The comparison is therefore skipped entirely under editable installs: there is no
version of it that carries signal there.

Both the mesh listener (`loop_scheduler/listener.py`) and the serve daemon
(`api/serve_app.py`) need this check, so the pure compare lives here as the single
source of truth. Each caller layers its OWN self-heal policy on top:

- The listener assumes a supervisor and self-exits by default (opt-OUT via
  ``EMPIRICA_LISTENER_NO_DRIFT_EXIT``).
- The serve daemon is often standalone, so it always SURFACES drift (on
  ``GET /health``) and only self-exits when supervised (opt-IN — see
  ``serve_app``). This module makes no exit decision; it only reports drift.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import json


def is_editable_install(distribution: str = "empirica") -> bool:
    """Is this distribution installed in editable/development mode?

    Primary signal is PEP 610's ``direct_url.json`` (``dir_info.editable``), which
    pip writes for any ``pip install -e``. Falls back to the ``__editable__*``
    finder/``.pth`` artefacts that setuptools' PEP 660 backend leaves in
    site-packages, so an install whose ``direct_url.json`` is missing or malformed
    is still recognised.

    The fallback is deliberate rather than belt-and-braces. The two failure
    directions are not symmetric: wrongly concluding NOT-editable restores the
    restart storm this module exists to prevent, while wrongly concluding editable
    merely disables a self-heal whose drift is still surfaced on ``/health``.
    So the cheap extra check is worth it.

    Never raises — a detection failure must not break the caller's drift check.
    """
    try:
        dist = importlib.metadata.distribution(distribution)
    except Exception:
        return False

    # Each signal falls through to the next on failure — suppression IS the control
    # flow here, not a swallowed error: a malformed direct_url.json is exactly when
    # the later markers need to run.
    with contextlib.suppress(Exception):
        raw = dist.read_text("direct_url.json")
        if raw and json.loads(raw).get("dir_info", {}).get("editable") is True:
            return True

    # A resolved `.egg-info` directory means the metadata came from a SOURCE TREE,
    # not from an installed `.dist-info` — setuptools' development-mode signature.
    #
    # This is not a theoretical case, it is the load-bearing one: a process started
    # with the repo on sys.path (which is how the daemon runs from a dev checkout)
    # resolves `empirica.egg-info` in the working directory, and that directory has
    # neither `direct_url.json` nor `__editable__` entries. Without this check the
    # detection returned False in precisely the situation the whole fix exists for.
    # Caught by the real-environment test skipping instead of passing.
    with contextlib.suppress(Exception):
        meta_dir = getattr(dist, "_path", None)
        if meta_dir is not None and str(meta_dir).rstrip("/").endswith(".egg-info"):
            return True

    try:
        files = dist.files or []
        return any(f.name.startswith("__editable__") for f in files)
    except Exception:
        return False


def version_drift() -> tuple[str, str] | None:
    """Return ``(in_process_version, installed_version)`` on drift, else None.

    Returns None for editable installs regardless of the versions: there the code
    is current by construction and only the metadata is stale, so a mismatch is a
    false positive and acting on it produces an unbounded restart loop (see module
    docstring).

    Best-effort otherwise: returns None on any error (missing dist-info, import
    failure) so a drift check can never crash the calling service.
    """
    try:
        from empirica import __version__ as in_process

        # Checked BEFORE the compare — under editable the comparison has no
        # meaning, so there is nothing to report rather than something to suppress.
        if is_editable_install():
            return None

        installed = importlib.metadata.version("empirica")
        if in_process != installed:
            return (in_process, installed)
    except Exception:
        return None
    return None
