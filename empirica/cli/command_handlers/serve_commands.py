"""
Serve command handler — starts FastAPI daemon for Chrome extension.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def handle_serve_service_command(args):
    """Install / uninstall / inspect the serve daemon as a persistent OS service.

    Opt-in supervision (systemd-user / launchd). Never auto-installed — this verb
    is the only path. Gives the daemon reboot-restart AND drift-triggered relaunch
    (a manually-launched `empirica serve` has neither).
    """
    from empirica.core.loop_scheduler.persistent_serve import (
        PersistentServeService,
        ServeServiceUnavailable,
    )

    action = getattr(args, "action", None)
    output = getattr(args, "output", "human")
    as_json = output == "json"
    svc = PersistentServeService()

    def _emit(payload: dict, code: int) -> int:
        if as_json:
            print(json.dumps(payload, default=str))
        else:
            if payload.get("ok"):
                st = payload.get("status") or {}
                if action == "status":
                    print(f"serve service [{st.get('backend')}]")
                    print(f"  installed: {st.get('installed')}")
                    print(f"  active:    {st.get('active')}")
                    if st.get("unit_path"):
                        print(f"  unit:      {st.get('unit_path')}")
                    if st.get("log_path"):
                        print(f"  log:       {st.get('log_path')}")
                elif action == "install":
                    print(f"✅ serve service installed + started: {payload.get('unit_path')}")
                    print("   Restarts on reboot; self-relaunches on version drift.")
                elif action == "uninstall":
                    print(
                        "✅ serve service removed"
                        if payload.get("removed")
                        else "serve service was not installed (nothing to remove)"
                    )
            else:
                print(f"❌ serve-service {action}: {payload.get('error')}")
        return code

    try:
        if action == "install":
            root = Path(getattr(args, "path", None) or ".").resolve()
            unit = svc.install(
                root,
                port=getattr(args, "port", 8000),
                host=getattr(args, "host", "127.0.0.1"),
            )
            return _emit({"ok": True, "unit_path": str(unit), "status": svc.status().__dict__}, 0)
        if action == "uninstall":
            removed = svc.uninstall()
            return _emit({"ok": True, "removed": removed}, 0)
        if action == "status":
            return _emit({"ok": True, "status": svc.status().__dict__}, 0)
        return _emit({"ok": False, "error": f"unknown action: {action}"}, 1)
    except ServeServiceUnavailable as e:
        return _emit({"ok": False, "error": str(e)}, 1)


def handle_serve_command(args):
    """Start the Empirica serve daemon."""
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)
    reload = getattr(args, "reload", False)

    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn not installed. Run: pip install 'empirica[api]'")
        return 1

    # Fail-closed: never expose the entity-mint endpoint unauthenticated. A
    # non-loopback bind requires a configured service-token set.
    from empirica.api.entity_mint_auth import assert_bind_safe

    try:
        assert_bind_safe(host)
    except RuntimeError as e:
        print(f"Error: {e}")
        return 1

    print(f"Starting Empirica serve daemon on http://{host}:{port}")
    print(f"  Health:  http://{host}:{port}/api/v1/health")
    print(f"  Import:  POST http://{host}:{port}/api/v1/artifacts/import")
    print(f"  Status:  GET  http://{host}:{port}/api/v1/profile/status")
    print(f"  Sync:    POST http://{host}:{port}/api/v1/profile/sync")
    print()

    uvicorn.run(
        "empirica.api.serve_app:create_serve_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
        log_level="info",
    )

    return 0
