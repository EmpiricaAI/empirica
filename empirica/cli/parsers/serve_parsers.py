"""
Serve Parsers - Local daemon for Chrome extension integration

Commands:
- serve: Start FastAPI daemon on localhost for extension communication
"""

import os


def add_serve_parsers(subparsers):
    """Add serve command parser."""

    serve_parser = subparsers.add_parser(
        "serve",
        help="Start local daemon for Chrome extension integration",
        description="Launch a FastAPI server on localhost that the Empirica Chrome "
        "extension uses to import artifacts, sync profiles, and query status. "
        "Runs on http://localhost:8000 by default.",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("EMPIRICA_SERVE_PORT", "8000")),
        help="Port to listen on (default: 8000, or EMPIRICA_SERVE_PORT env; the explicit flag wins over the env var)",
    )
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1, use 0.0.0.0 for network access)"
    )
    serve_parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload on code changes (development only)"
    )

    # Opt-in supervision of the serve daemon as an OS service (systemd-user /
    # launchd). NEVER auto-installed — this verb is the only path. Gives the
    # daemon reboot-restart AND drift-triggered relaunch (picks up new code on
    # update), which a manually-launched `empirica serve` does not have.
    svc_parser = subparsers.add_parser(
        "serve-service",
        help="Install/uninstall/inspect the serve daemon as a persistent OS service (opt-in)",
        description="Supervise `empirica serve` with systemd-user (Linux/WSL2) or launchd "
        "(macOS) so it restarts on reboot and self-relaunches on version drift to pick up "
        "new code. Opt-in only — never installed by setup.",
    )
    svc_parser.add_argument(
        "action",
        choices=["install", "uninstall", "status"],
        help="install (+start), uninstall (+stop), or status",
    )
    svc_parser.add_argument(
        "--path",
        default=None,
        help="Project root the daemon binds to (WorkingDirectory). Default: current directory.",
    )
    svc_parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("EMPIRICA_SERVE_PORT", "8000")),
        help="Port the supervised daemon serves on (default: 8000 or EMPIRICA_SERVE_PORT)",
    )
    svc_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host for the supervised daemon (default: 127.0.0.1)"
    )
    svc_parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
