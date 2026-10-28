"""Which core these hooks were released with, and what to say when the installed core is older.

Hooks are vendored: a harness repo copies them at a tag, and the plugin directory is written by `setup-claude-code`, while the core that answers
them is whatever `empirica` is installed. A hook shipped with core X may call functions added in X (`session-end-postflight.py` imports
`restore_if_swapped`, absent before 1.14.15), so with an older core every SessionEnd prints an import failure and nothing says why (ecodex, 1.14.15).
`HOOK_VINTAGE` is the version these hooks were released with, which is the lowest core they are known to run against. `release.py` sweeps it with
`empirica/__init__.py`, and a test fails if the two differ.

Never raises: a hook must not fail because of its own version check, and a version it cannot read is not a reason to warn.
"""

from __future__ import annotations

import re

HOOK_VINTAGE = "1.14.18"


def _parse(version: object) -> tuple[int, int, int] | None:
    """The leading X.Y.Z of a version string, or None. A pre-release suffix is ignored: 1.15.0.dev2 compares as 1.15.0."""
    if not isinstance(version, str):
        return None
    match = re.match(r"\s*v?(\d+)\.(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def core_older_than_hooks(installed: object, vintage: object = HOOK_VINTAGE) -> bool:
    """True only when both versions parse and the installed core is strictly older than the hooks' vintage."""
    have, need = _parse(installed), _parse(vintage)
    return have is not None and need is not None and have < need


def _installed_core_version() -> str | None:
    try:
        from empirica import __version__

        return __version__
    except Exception:
        return None


def startup_notice() -> str:
    """One paragraph for SessionStart when the installed core is older than these hooks, else an empty string."""
    installed = _installed_core_version()
    if not core_older_than_hooks(installed):
        return ""
    return (
        f"Hooks version mismatch: these hooks were released with empirica {HOOK_VINTAGE} but the installed core is {installed}. "
        "A hook that calls code added since then fails (the session-end hook prints an import error on every end). "
        f"Upgrade the installed empirica to {HOOK_VINTAGE} or later, or use the hooks that shipped with {installed}."
    )
