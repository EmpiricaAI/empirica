"""Command handlers for the ``empirica module`` group (practice-module system).

Currently surfaces ``validate``. The group dispatcher mirrors the ``loop``
group pattern (``handle_loop_group_command``) so ``fetch`` / ``provision`` slot
in as additional ``_MODULE_DISPATCH`` entries in later legs.
"""

from __future__ import annotations

import json
import sys


def handle_module_validate_command(args) -> int:
    """Validate a ``module.yaml`` and report. Exit 0 on valid, 1 on invalid."""
    from empirica.core.modules.manifest import validate_manifest_file

    receipt = validate_manifest_file(args.path)

    if getattr(args, "output", "json") == "json":
        sys.stdout.write(json.dumps(receipt) + "\n")
    elif receipt["ok"]:
        m = receipt["manifest"]
        sys.stdout.write(
            f"ok: {receipt['path']} — module '{m.get('name')}' v{m.get('version')} ({m.get('visibility')}) valid\n"
        )
    else:
        sys.stdout.write(f"INVALID: {receipt['path']}\n")
        for err in receipt["errors"]:
            sys.stdout.write(f"  - {err}\n")

    return 0 if receipt["ok"] else 1


_MODULE_DISPATCH = {
    "validate": handle_module_validate_command,
}


def handle_module_group_command(args) -> int:
    """Dispatch ``empirica module <action>`` to the matching handler."""
    action = getattr(args, "module_action", None)
    if not action:
        sys.stdout.write("usage: empirica module <validate> [args...]\n")
        return 2
    handler = _MODULE_DISPATCH.get(action)
    if handler is None:
        sys.stdout.write(f"error: unknown module action: {action}\n")
        return 2
    return handler(args) or 0
