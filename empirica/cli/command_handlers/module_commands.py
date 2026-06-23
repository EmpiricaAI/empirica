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


def handle_module_fetch_command(args) -> int:
    """Stage a module's distribution artifacts. Exit 0 on success, 1 on any error."""
    from pathlib import Path

    from empirica.core.modules.executors import fetch_module
    from empirica.core.modules.manifest import load_manifest

    try:
        manifest = load_manifest(args.path)
    except Exception as e:  # ManifestError — invalid manifest can't be fetched
        receipt = {"ok": False, "action": "fetch", "errors": [str(e)]}
        sys.stdout.write(json.dumps(receipt) + "\n")
        return 1

    receipt = fetch_module(
        manifest,
        dry_run=getattr(args, "dry_run", False),
        staging_root=Path(args.staging_root) if getattr(args, "staging_root", None) else None,
        registry_base=getattr(args, "registry", None),
        index_url=getattr(args, "index_url", None),
    )

    if getattr(args, "output", "json") == "json":
        sys.stdout.write(json.dumps(receipt) + "\n")
    else:
        head = "fetch (dry-run)" if receipt["dry_run"] else "fetch"
        sys.stdout.write(f"{head}: {receipt['module']} → {receipt['staged_path']}\n")
        for step in receipt["steps"]:
            sys.stdout.write(f"  [{step['status']}] {step['kind']}: {step['target']} ({step['detail']})\n")
        for err in receipt["errors"]:
            sys.stdout.write(f"  ERROR: {err}\n")

    return 0 if receipt["ok"] else 1


def handle_module_provision_command(args) -> int:
    """Run the plugin layer for a module. Exit 0 on success, 1 on any error."""
    from pathlib import Path

    from empirica.core.modules.executors import provision_module
    from empirica.core.modules.manifest import load_manifest

    try:
        manifest = load_manifest(args.path)
    except Exception as e:
        receipt = {"ok": False, "action": "provision", "errors": [str(e)]}
        sys.stdout.write(json.dumps(receipt) + "\n")
        return 1

    receipt = provision_module(
        manifest,
        dry_run=getattr(args, "dry_run", False),
        staging_root=Path(args.staging_root) if getattr(args, "staging_root", None) else None,
        plugin_root=Path(args.plugin_root) if getattr(args, "plugin_root", None) else None,
        cortex_url=getattr(args, "cortex_url", None),
        org=getattr(args, "org", None),
        tenant=getattr(args, "tenant", None),
    )

    if getattr(args, "output", "json") == "json":
        sys.stdout.write(json.dumps(receipt) + "\n")
    else:
        head = "provision (dry-run)" if receipt["dry_run"] else "provision"
        sys.stdout.write(f"{head}: {receipt['module']}\n")
        for step in receipt["steps"]:
            sys.stdout.write(f"  [{step['status']}] {step['kind']}: {step['target']} ({step['detail']})\n")
        for err in receipt["errors"]:
            sys.stdout.write(f"  ERROR: {err}\n")

    return 0 if receipt["ok"] else 1


_MODULE_DISPATCH = {
    "validate": handle_module_validate_command,
    "fetch": handle_module_fetch_command,
    "provision": handle_module_provision_command,
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
