"""
CLI Core - Main entry point and argument parsing for Empirica CLI

This module provides the main() function and argument parser setup.
Parser definitions are modularized in the parsers/ subdirectory.
"""

# Apply asyncio fixes early (before any MCP connections)
try:
    from empirica.cli.asyncio_fix import patch_asyncio_for_mcp

    patch_asyncio_for_mcp()
except Exception:
    pass  # Don't fail if fix can't be applied

import argparse
import json
import sys
import time

from .cli_utils import handle_cli_error
from .command_handlers import *  # noqa: F403 — re-exports all command handlers by design
from .command_handlers.domain_commands import (
    handle_domain_validate_command,
)
from .command_handlers.edit_verification_command import handle_edit_with_confidence_command
from .command_handlers.issue_capture_commands import (
    handle_issue_export_command,
    handle_issue_handoff_command,
    handle_issue_list_command,
    handle_issue_resolve_command,
    handle_issue_show_command,
    handle_issue_stats_command,
)
from .command_handlers.resolve_command import handle_resolve_command
from .command_handlers.utility_commands import (
    handle_efficiency_report,
    handle_log_token_saving,
    handle_qdrant_cleanup_command,
    handle_qdrant_status_command,
)


class GroupedHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Argparse formatter that groups Empirica's subcommands by category.

    Empirica has 180+ CLI commands across 26 categories. The default
    argparse subcommand listing renders all of them in a flat,
    impossible-to-scan list. This formatter overrides _format_action
    to render only a curated "Core Commands" view (~25 high-traffic
    commands grouped under Workflow / Epistemic Artifacts / Goals /
    Context / Monitoring), with a footer pointing users to
    `empirica help <category>` for the full enumeration.

    Inherits RawDescriptionHelpFormatter so the multi-line program
    description survives without word-wrap mangling. Used as the
    `formatter_class` of the top-level argparse parser in
    `cli_core.py:create_parser`.
    """

    def _format_action(self, action):
        """Format action with grouped subcommands by category."""
        try:
            if isinstance(action, argparse._SubParsersAction):
                # Compact default help — show core commands only
                # Full list available via 'empirica help <category>'
                parts = [
                    "\nCore Commands:\n",
                    "=" * 60 + "\n",
                    "\n  Workflow:\n",
                    "    preflight-submit (pre)    Start measurement transaction\n",
                    "    check-submit (check)      Gate: ready to act?\n",
                    "    postflight-submit (post)   Close transaction, measure learning\n",
                    "\n  Epistemic Artifacts (log as you discover):\n",
                    "    finding-log (fl)           What was learned\n",
                    "    unknown-log (ul)           What needs investigation\n",
                    "    deadend-log (de)           Approach that didn't work\n",
                    "    mistake-log                Error to avoid in future\n",
                    "    assumption-log             Unverified belief\n",
                    "    decision-log               Choice with rationale\n",
                    "\n  Goals:\n",
                    "    goals-create (gc)          Create a goal\n",
                    "    goals-list (gl)            List goals\n",
                    "    goals-complete             Mark goal done\n",
                    "\n  Context:\n",
                    "    session-create (sc)        Start session\n",
                    "    project-bootstrap (pb)     Load project context\n",
                    "    project-switch             Switch active project\n",
                    "    project-search             Search knowledge (Qdrant)\n",
                    "\n  Monitoring:\n",
                    "    calibration-report         Calibration accuracy\n",
                    "    compliance-report          Release-readiness checks\n",
                    "    profile-status              Artifact counts + drift\n",
                    "\n" + "=" * 60 + "\n",
                    "\n180+ commands in 26 categories. To explore:\n",
                    "  empirica help                  Show all commands by category\n",
                    "  empirica <command> --help      Detailed help for one command\n",
                ]
                return "".join(parts)
        except Exception:
            pass

        return super()._format_action(action)


# Import all parser modules
from empirica.cli.command_handlers.bus_commands import (
    handle_bus_dispatch_command,
    handle_bus_instances_command,
    handle_bus_register_command,
    handle_bus_status_command,
    handle_bus_subscribe_command,
)

from .command_handlers.architecture_commands import (
    handle_assess_compare_command,
    handle_assess_component_command,
    handle_assess_directory_command,
)
from .command_handlers.bootstrap_context_commands import handle_bootstrap_context_command
from .command_handlers.chat_commands import handle_chat_command
from .command_handlers.cockpit_commands import (
    handle_instance_group_command,
    handle_listener_group_command,
    handle_loop_group_command,
    handle_sentinel_group_command,
    handle_sentinel_pause_command,
    handle_sentinel_resume_command,
    handle_tui_command,
)
from .command_handlers.cockpit_commands import (
    handle_status_command as handle_cockpit_status_command,
)
from .command_handlers.concept_graph_commands import (
    handle_concept_build,
    handle_concept_related,
    handle_concept_stats,
    handle_concept_top,
)
from .command_handlers.docs_commands import handle_docs_assess, handle_docs_explain
from .command_handlers.docs_link_check_commands import handle_docs_link_check_command
from .command_handlers.mailbox_commands import handle_mailbox_group_command
from .command_handlers.mcp_commands import handle_mcp_list_tools_command
from .command_handlers.memory_commands import (
    handle_memory_prime_command,
    handle_memory_report_command,
    handle_memory_scope_command,
    handle_memory_value_command,
    handle_pattern_check_command,
    handle_session_rollup_command,
)
from .command_handlers.mesh_agreements_commands import handle_mesh_agreements_group_command
from .command_handlers.mesh_commands import handle_mesh_group_command
from .command_handlers.message_commands import (
    handle_message_channels_command,
    handle_message_cleanup_command,
    handle_message_inbox_command,
    handle_message_read_command,
    handle_message_reply_command,
    handle_message_send_command,
    handle_message_thread_command,
)
from .command_handlers.module_commands import handle_module_group_command
from .command_handlers.practice_context_commands import handle_practice_context_command
from .command_handlers.query_commands import handle_query_command
from .command_handlers.release_commands import handle_release_command, handle_release_ready_command
from .command_handlers.rust_docs_commands import handle_rust_docs_assess
from .command_handlers.sentinel_commands import (
    handle_sentinel_check_command,
    handle_sentinel_load_profile_command,
    handle_sentinel_orchestrate_command,
    handle_sentinel_status_command,
)
from .command_handlers.trajectory_commands import (
    handle_trajectory_backfill as handle_trajectory_backfill_command,
)
from .command_handlers.trajectory_commands import (
    handle_trajectory_show as handle_trajectory_show_command,
)
from .command_handlers.trajectory_commands import (
    handle_trajectory_stats as handle_trajectory_stats_command,
)
from .parsers import (
    add_architecture_parsers,
    add_bus_parsers,
    add_cascade_parsers,
    add_chat_parsers,
    add_checkpoint_parsers,
    add_cockpit_launcher_parsers,
    add_cockpit_parsers,
    add_concept_graph_parsers,
    add_config_parsers,
    add_domain_parsers,
    add_edit_verification_parsers,
    add_epistemics_parsers,
    add_investigation_parsers,
    add_issue_capture_parsers,
    add_lesson_parsers,
    add_mailbox_parsers,
    add_mcp_parsers,
    add_memory_parsers,
    add_mesh_parsers,
    add_message_parsers,
    add_module_parsers,
    add_monitor_parsers,
    add_notify_parsers,
    add_onboarding_parsers,
    add_performance_parsers,
    add_profile_parsers,
    add_projects_parsers,
    add_query_parsers,
    add_release_parsers,
    add_resolve_parser,
    add_scan_parsers,
    add_sentinel_parsers,
    add_serve_parsers,
    add_session_parsers,
    add_skill_parsers,
    add_trajectory_parsers,
    add_user_interface_parsers,
    add_utility_parsers,
    add_visibility_parsers,
    add_vision_parsers,
    add_voice_parsers,
)


def _get_version():
    """Get Empirica version with additional info"""
    try:
        import empirica

        version = empirica.__version__

        # Add Python version and install location
        import sys

        python_version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        install_path = empirica.__file__.rsplit("/", 2)[0] if "/" in empirica.__file__ else empirica.__file__

        return f"{version}\n{python_version}\nInstall: {install_path}"
    except Exception:
        return "1.0.5 (version info unavailable)"


def create_argument_parser():
    """Create and configure the main argument parser"""
    parser = argparse.ArgumentParser(
        prog="empirica",
        usage="empirica [--version] [--verbose] <command> [args]",
        description="Empirica - Measurement and calibration layer for AI",
        formatter_class=GroupedHelpFormatter,
        epilog='Examples:\n  empirica session-create --ai-id empirica\n  empirica preflight-submit -     # JSON on stdin\n  empirica finding-log --finding "Discovered X" --impact 0.7\n  empirica goals-create --objective "Implement Y"\n  empirica project-bootstrap     # Load project context',
    )

    # Global options (must come before subcommand)
    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output (shows DB path, execution time, etc.). Must come before command name.",
    )
    parser.add_argument("--config", help="Path to configuration file")

    # Create subcommands
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # Add all parser groups
    add_session_parsers(subparsers)
    add_cascade_parsers(subparsers)
    add_investigation_parsers(subparsers)
    add_performance_parsers(subparsers)
    add_skill_parsers(subparsers)
    add_utility_parsers(subparsers)
    add_config_parsers(subparsers)
    add_domain_parsers(subparsers)
    add_resolve_parser(subparsers)
    add_monitor_parsers(subparsers)
    add_checkpoint_parsers(subparsers)
    add_user_interface_parsers(subparsers)
    add_vision_parsers(subparsers)
    add_epistemics_parsers(subparsers)
    add_edit_verification_parsers(subparsers)
    add_issue_capture_parsers(subparsers)
    add_architecture_parsers(subparsers)
    add_query_parsers(subparsers)
    add_sentinel_parsers(subparsers)
    add_release_parsers(subparsers)
    add_lesson_parsers(subparsers)
    add_onboarding_parsers(subparsers)
    add_module_parsers(subparsers)
    add_trajectory_parsers(subparsers)
    add_concept_graph_parsers(subparsers)
    add_mcp_parsers(subparsers)
    add_message_parsers(subparsers)
    add_mailbox_parsers(subparsers)
    add_bus_parsers(subparsers)
    add_mesh_parsers(subparsers)
    add_cockpit_parsers(subparsers)
    add_cockpit_launcher_parsers(subparsers)
    add_chat_parsers(subparsers)
    add_notify_parsers(subparsers)
    add_voice_parsers(subparsers)
    add_visibility_parsers(subparsers)
    add_scan_parsers(subparsers)

    # Built-in help command (handled in main(), not via handler)
    subparsers.add_parser("help", help="Show all commands by category")
    add_memory_parsers(subparsers)
    add_profile_parsers(subparsers)
    add_projects_parsers(subparsers)
    add_serve_parsers(subparsers)

    return parser


# Categorized command map used by `empirica help` AND by
# `scripts/generate_cli_docs.py` (which emits the unified-reference doc).
# Adding a new command? Wire its parser in `add_*_parsers()` first; then
# add it here so it appears under a category. Commands not listed here
# still work but surface in the generator's "uncategorized" section as
# drift signal.
_HELP_CATEGORIES = {
    "session": [
        "session-create",
        "sessions-list",
        "sessions-show",
        "sessions-export",
        "sessions-resume",
        "session-snapshot",
        "memory-compact",
        "transaction-adopt",
    ],
    "workflow": ["preflight-submit", "check", "check-submit", "postflight-submit"],
    "goals": [
        "goals-create",
        "goals-list",
        "goals-search",
        "goals-complete",
        "goals-claim",
        "goals-add-task",
        "goals-add-dependency",
        "goals-complete-task",
        "goals-get-tasks",
        "goals-progress",
        "goals-discover",
        "goals-ready",
        "goals-resume",
        "goals-mark-stale",
        "goals-get-stale",
        "goals-refresh",
    ],
    "logging": [
        "finding-log",
        "finding-resolve",
        "unknown-log",
        "unknown-list",
        "unknown-resolve",
        "deadend-log",
        "assumption-log",
        "decision-log",
        "mistake-log",
        "note",
        "source-add",
        "source-get",
        "source-list",
        "sources-map",
        "sources-reconcile",
        "sources-check",
        "source-archive",
        "source-update",
        "blindspot-scan",
        "log-artifacts",
        "resolve-artifacts",
        "delete-artifacts",
        "epistemics-list",
        "epistemics-show",
        "noetic-batch",
    ],
    "project": [
        "project-init",
        "project-update",
        "project-create",
        "project-list",
        "project-switch",
        "project-bootstrap",
        "project-handoff",
        "project-search",
        "project-embed",
        "code-embed",
        "doc-check",
        "bootstrap-context",
        "practice-context",
        "projects-sync",
        "projects-discover",
        "projects-list",
        "projects-unregister",
    ],
    "workspace": [
        "workspace-init",
        "workspace-map",
        "workspace-list",
        "workspace-overview",
        "workspace-search",
        "engagement-focus",
        "ecosystem-check",
        "entity-create",
        "entity-list",
        "entity-show",
        "entity-walk",
        "entity-search",
        "entity-link",
        "entity-delete",
        "entity-reindex",
        "engagement-create",
        "engagement-list",
        "engagement-show",
        "engagement-walk",
        "engagement-update",
    ],
    "checkpoint": [
        "checkpoint-create",
        "checkpoint-load",
        "checkpoint-list",
        "checkpoint-diff",
        "checkpoint-sign",
        "checkpoint-verify",
        "checkpoint-signatures",
    ],
    "sync": ["sync-config", "sync-push", "sync-pull", "sync-status", "rebuild", "artifacts-generate"],
    "profile": ["profile-sync", "profile-prune", "profile-status", "profile-import"],
    "identity": ["identity-create", "identity-export", "identity-list", "identity-verify"],
    "handoff": ["handoff-create", "handoff-query"],
    "issue": ["issue-list", "issue-show", "issue-handoff", "issue-resolve", "issue-export", "issue-stats"],
    "investigation": [
        "investigate",
        "investigate-create-branch",
        "investigate-checkpoint-branch",
        "investigate-merge-branches",
        "investigate-multi",
    ],
    "monitoring": [
        "monitor",
        "assess-state",
        "trajectory-project",
        "efficiency-report",
        "calibration-report",
        "grounding-export",
        "commit-context",
        "compact-analysis",
        "compliance-report",
    ],
    "cockpit": [
        "status",
        "tui",
        "off",
        "on",
        "sentinel",
        "loop",
        "listener",
        "instance",
        "practitioner",
        "mailbox",
        "cockpit",
        "daemon-list",
        "daemon-grant",
        "daemon-deny",
        "daemon-grants-list",
        "notify",
    ],
    "skills": ["skill-suggest", "skill-fetch", "skill-extract"],
    "architecture": ["assess-component", "assess-compare", "assess-directory"],
    "sentinel": ["sentinel-orchestrate", "sentinel-load-profile", "sentinel-status", "sentinel-check"],
    "lessons": [
        "lesson-create",
        "lesson-load",
        "lesson-list",
        "lesson-search",
        "lesson-recommend",
        "lesson-stats",
    ],
    "mcp": ["mcp-list-tools"],
    "memory": ["memory-prime", "memory-scope", "memory-value", "pattern-check", "session-rollup", "memory-report"],
    "vision": ["vision"],
    "domains": ["domain-validate"],
    "setup": ["onboard", "setup-claude-code", "plugin-sync", "enp-setup", "diagnose", "doctor", "release", "serve"],
}


def _handle_help_command(parsed_args) -> None:
    """Handle the built-in help command, printing categorized command lists."""
    cat_arg = getattr(parsed_args, "_help_category", None)
    if cat_arg and cat_arg in _HELP_CATEGORIES:
        cat = cat_arg
        print(f"\n{cat.title()} ({len(_HELP_CATEGORIES[cat])} commands):\n")
        for cmd in _HELP_CATEGORIES[cat]:
            print(f"  {cmd}")
        print("\nUse 'empirica <command> --help' for details.")
    else:
        total = sum(len(cmds) for cmds in _HELP_CATEGORIES.values())
        print(f"\nAll Empirica Commands ({total} total):\n")
        for cat, cmds in _HELP_CATEGORIES.items():
            print(f"  {cat:16s} ({len(cmds):2d})  {', '.join(cmds[:4])}{'...' if len(cmds) > 4 else ''}")
        print("\nUse 'empirica help <category>' to see all commands in a category.")


def _handle_command_result(result, parsed_args) -> int:
    """Handle command result: print output and return exit code."""
    if isinstance(result, dict):
        output_format = getattr(parsed_args, "output", "json")
        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            if result.get("ok", True):
                for key, value in result.items():
                    if key != "ok":
                        print(f"{key}: {value}")
            else:
                print(f"❌ {result.get('error', 'Unknown error')}")
        return 0 if result.get("ok", True) else 1
    elif result is not None and result != 0:
        return result
    return 0


# Self-heal verbs that must NOT trigger the plugin auto-sync (they'd re-invoke
# it → infinite loop), plus cheap/help paths.
_PLUGIN_AUTOSYNC_EXEMPT = frozenset(
    {"plugin-sync", "setup-claude-code", "plugin-version", "doctor", "diagnose", "help"}
)


def _maybe_autosync_plugin(command: str) -> None:
    """GAP 1: self-heal a stale *deployed* Claude Code plugin from the CLI.

    The deployed plugin (``~/.claude/plugins/local/empirica``) is a copy; ``pip
    install -U`` upgrades the package but not the copy, so a box silently runs
    old hooks. The session-init auto-heal lives *inside* that plugin, so a box
    whose plugin predates it never self-heals (chicken-and-egg). The CLI is
    always current after the upgrade, so we check skew here and run
    ``plugin-sync`` once when it drifts — bootstrapping even ancient boxes.

    Fail-safe (never breaks the user's command), debounced (once / 10 min via a
    marker), re-entrancy-guarded (exempt verbs above). Opt out with
    ``EMPIRICA_NO_AUTOSYNC=1``. The rush-guard already whitelists plugin-sync, so
    the sync won't self-deadlock even with a stale sentinel-gate live mid-heal.
    """
    import os
    import subprocess
    import time as _time
    from pathlib import Path

    if command in _PLUGIN_AUTOSYNC_EXEMPT or os.environ.get("EMPIRICA_NO_AUTOSYNC"):
        return
    try:
        marker = Path.home() / ".empirica" / ".plugin_autosync_checked"
        now = _time.time()
        if marker.exists() and now - marker.stat().st_mtime < 600:
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(now))  # stamp first so concurrent CLI calls debounce

        plugin_dir = Path.home() / ".claude" / "plugins" / "local" / "empirica"
        if not plugin_dir.exists():
            return  # not a Claude Code box — nothing to heal
        stamp_file = plugin_dir / ".plugin-version"
        stamp = stamp_file.read_text().strip() if stamp_file.exists() else None
        try:
            from empirica import __version__ as cli_ver
        except Exception:
            cli_ver = None
        if cli_ver and stamp != cli_ver:  # None (ancient plugin) also drifts → sync
            failed_marker = marker.parent / ".plugin_autosync_failed"
            proc = subprocess.run(
                ["empirica", "plugin-sync"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if proc.returncode != 0:
                # Make a failing self-heal legible. Without this the box silently
                # keeps running stale hooks while the debounce marker reads
                # "checked" — doctor/diagnose (and a human) surface this breadcrumb.
                err = (proc.stderr or b"").decode("utf-8", "replace").strip()[-500:]
                failed_marker.write_text(f"{now}\trc={proc.returncode}\t{err}\n")
                sys.stderr.write(
                    f"[empirica] plugin auto-sync failed (rc={proc.returncode}); "
                    "run 'empirica plugin-sync' or 'empirica doctor' to retry.\n"
                )
            else:
                failed_marker.unlink(missing_ok=True)  # recovered → clear breadcrumb
    except Exception:
        return  # self-heal must never break the user's command


def _life_support_handlers() -> dict:
    """The minimal dispatch that must survive a feature-verb registration failure.

    If the full command table (built in ``main``) raises ``NameError`` because a
    feature verb's handler failed to import, the loop-opening verbs must still
    resolve — otherwise the praxic firewall fail-closes on a broken
    ``preflight-submit`` into an *unrecoverable* deadlock: you can't preflight to
    fix the thing that broke preflight (incident prop_m2orjtozsvco5ansfqdxi2g5fu).

    Resolved defensively from module globals so a genuinely-missing handler is
    simply absent here, never a second crash. These verbs open transactions,
    switch context, and provide the recovery escape (doctor/diagnose/sentinel).
    """
    g = globals()
    wanted = {
        "preflight-submit": "handle_preflight_submit_command",
        "check-submit": "handle_check_submit_command",
        "postflight-submit": "handle_postflight_submit_command",
        "session-create": "handle_session_create_command",
        "project-bootstrap": "handle_project_bootstrap_command",
        "doctor": "handle_doctor_command",
        "diagnose": "handle_diagnose_command",
        "sentinel": "handle_sentinel_group_command",
    }
    return {verb: g[name] for verb, name in wanted.items() if callable(g.get(name))}


def main(args=None):
    """Main CLI entry point"""
    start_time = time.time()

    parser = create_argument_parser()

    # Intercept 'help <category>' before argparse rejects the category as unknown
    raw_args = args if args is not None else sys.argv[1:]
    if raw_args and raw_args[0] == "help":
        # Handled below after parse — but argparse needs to accept it
        # Strip category arg so argparse only sees 'help'
        help_category = raw_args[1] if len(raw_args) > 1 else None
        parsed_args = parser.parse_args(["help"])
        # Stash the category for the handler below
        parsed_args._help_category = help_category
    else:
        parsed_args = parser.parse_args(args)

    if not parsed_args.command:
        parser.print_help()
        sys.exit(1)

    # Built-in 'help' command — show full categorised command list
    if parsed_args.command == "help":
        _handle_help_command(parsed_args)
        sys.exit(0)

    # GAP 1: self-heal a stale deployed plugin from the CLI (always current after
    # `pip -U`), bootstrapping boxes whose plugin predates the session-init
    # auto-heal. Fail-safe + debounced + re-entrancy-guarded (see helper).
    _maybe_autosync_plugin(parsed_args.command)

    # Normalize --project-id: resolve names to UUIDs via workspace.db
    if hasattr(parsed_args, "project_id") and parsed_args.project_id:
        try:
            from empirica.cli.utils.project_resolver import resolve_project_id

            parsed_args.project_id = resolve_project_id(parsed_args.project_id)
        except (SystemExit, Exception):
            pass  # Let downstream handlers deal with invalid project IDs

    # Enable verbose output if requested
    verbose = getattr(parsed_args, "verbose", False)
    if verbose:
        print(f"[VERBOSE] Empirica v{_get_version().split()[0]}", file=sys.stderr)
        print(f"[VERBOSE] Command: {parsed_args.command}", file=sys.stderr)
        try:
            from empirica.data.session_database import SessionDatabase

            db = SessionDatabase()
            print(f"[VERBOSE] Database: {db.db_path}", file=sys.stderr)
            db.close()
        except Exception as e:
            print(f"[VERBOSE] Database: (unavailable: {e})", file=sys.stderr)

    # Command handler mapping. Pre-seed with the life-support set FIRST: if the
    # full table below raises NameError (a feature verb's handler failed to
    # import), the failed dict-literal assignment leaves THIS value in place, so
    # the loop-opening verbs never share fate with a feature verb (prop_m2orjtoz).
    command_handlers = _life_support_handlers()
    _dispatch_reached = False  # distinguishes a table-build NameError from one raised inside a handler
    try:
        command_handlers = {
            # Session commands
            "session-create": handle_session_create_command,
            "sessions-list": handle_sessions_list_command,
            "sessions-show": handle_sessions_show_command,
            "sessions-export": handle_sessions_export_command,
            "sessions-resume": handle_sessions_resume_command,
            "session-snapshot": handle_session_snapshot_command,
            "memory-compact": handle_memory_compact_command,
            "transaction-adopt": handle_transaction_adopt_command,
            # CASCADE commands (working -submit variants only)
            "preflight-submit": handle_preflight_submit_command,
            "check": handle_check_command,
            "check-submit": handle_check_submit_command,
            "postflight-submit": handle_postflight_submit_command,
            # Investigation commands
            "investigate": handle_investigate_command,
            "investigate-create-branch": handle_investigate_create_branch_command,
            "investigate-checkpoint-branch": handle_investigate_checkpoint_branch_command,
            "investigate-merge-branches": handle_investigate_merge_branches_command,
            "investigate-multi": handle_investigate_multi_command,
            # Performance commands
            "performance": handle_performance_command,
            # Skill commands
            "skill-suggest": handle_skill_suggest_command,
            "skill-fetch": handle_skill_fetch_command,
            "skill-extract": handle_skill_extract_command,
            # Utility commands
            "log-token-saving": handle_log_token_saving,
            "efficiency-report": handle_efficiency_report,
            # Qdrant maintenance commands
            "qdrant-cleanup": handle_qdrant_cleanup_command,
            "qdrant-status": handle_qdrant_status_command,
            # Config commands
            "config": handle_config_command,
            # Domain registry commands (A1 — Sentinel reframe). Inspection verbs
            # (list/show/resolve) pruned in the CLI-surface cut; the registry
            # itself stays load-bearing (calibration tuple resolution). validate
            # kept as the YAML guard-rail for that config.
            "domain-validate": handle_domain_validate_command,
            # Unified resolve command
            "resolve": handle_resolve_command,
            # Monitor commands
            "monitor": handle_monitor_command,
            "system-status": handle_system_status_command,
            "assess-state": handle_assess_state_command,
            "mco-load": handle_mco_load_command,
            "trajectory-project": handle_trajectory_project_command,
            "compact-analysis": handle_compact_analysis,
            "commit-context": handle_commit_context_command,
            "calibration-report": handle_calibration_report_command,
            "grounding-export": handle_grounding_export_command,
            "calibration-dispute": handle_calibration_dispute_command,
            "compliance-report": handle_compliance_report_command,
            "security-audit": handle_security_audit_command,
            "noetic-batch": handle_noetic_batch_command,
            # Cockpit (proposal: PROPOSAL_SENTINEL_LOOP_TUI.md)
            "sentinel": handle_sentinel_group_command,
            "loop": handle_loop_group_command,
            "module": handle_module_group_command,
            "listener": handle_listener_group_command,
            "mailbox": handle_mailbox_group_command,
            "instance": handle_instance_group_command,
            "practitioner": handle_practitioner_group_command,
            "status": handle_cockpit_status_command,
            "tui": handle_tui_command,
            # User-facing Sentinel toggle (friendly aliases for sentinel pause/resume).
            # Per-instance by default; --global widens to all instances.
            "off": handle_sentinel_pause_command,
            "on": handle_sentinel_resume_command,
            # Chat (single-instance collaborative epistemic workspace)
            # See empirica/docs/architecture/CHAT.md
            "chat": handle_chat_command,
            # Notify dispatcher (proposal: PROPOSAL_NOTIFY_DISPATCHER.md)
            "notify": handle_notify_group_command,
            # Voice profiles (prosodic patterns for outreach drafting)
            "voice": handle_voice_group_command,
            # Visibility tiers (proposal: PROPOSAL_VISIBILITY_TIERS.md, Phase 0)
            "visibility": handle_visibility_group_command,
            # Cockpit launcher (proposal: PROPOSAL_COCKPIT_LAUNCHER.md, v1)
            "cockpit": handle_cockpit_group_command,
            # Unified mesh diagnostic + control surface
            "mesh": handle_mesh_group_command,
            # AI service scanner (proposal: PROPOSAL_AI_SERVICE_SCANNER.md)
            # Phase 1: scan (one-shot). Phase 3: scan-history/show/diff verbs.
            "scan": handle_scan_command,
            "scan-history": handle_scan_history_command,
            "scan-show": handle_scan_show_command,
            "scan-diff": handle_scan_diff_command,
            "services-audit": handle_services_audit_command,
            # Checkpoint commands
            "checkpoint-create": handle_checkpoint_create_command,
            "checkpoint-load": handle_checkpoint_load_command,
            "checkpoint-list": handle_checkpoint_list_command,
            "checkpoint-diff": handle_checkpoint_diff_command,
            "checkpoint-sign": handle_checkpoint_sign_command,
            "checkpoint-verify": handle_checkpoint_verify_command,
            "checkpoint-signatures": handle_checkpoint_signatures_command,
            # Identity commands
            "identity-create": handle_identity_create_command,
            "identity-export": handle_identity_export_command,
            "identity-list": handle_identity_list_command,
            "identity-verify": handle_identity_verify_command,
            # Handoff commands
            "handoff-create": handle_handoff_create_command,
            "handoff-query": handle_handoff_query_command,
            # Mistake logging
            "mistake-log": handle_mistake_log_command,
            # Project commands
            "project-init": handle_project_init_command,
            "project-update": handle_project_update_command,
            "project-create": handle_project_create_command,
            "project-handoff": handle_project_handoff_command,
            "project-list": handle_project_list_command,
            "project-switch": handle_project_switch_command,
            "project-bootstrap": handle_project_bootstrap_command,
            "workspace-overview": handle_workspace_overview_command,
            "workspace-map": handle_workspace_map_command,
            "workspace-list": handle_workspace_list_command,
            "workspace-backfill-entities": handle_workspace_backfill_entities_command,
            "workspace-init": handle_workspace_init_command,
            "ecosystem-check": handle_ecosystem_check_command,
            "workspace-search": handle_workspace_search_command,
            "entity-create": handle_entity_create_command,
            "entity-list": handle_entity_list_command,
            "entity-show": handle_entity_show_command,
            "entity-walk": handle_entity_walk_command,
            "entity-search": handle_entity_search_command,
            "entity-link": handle_entity_link_command,
            "entity-delete": handle_entity_delete_command,
            "entity-reindex": handle_entity_reindex_command,
            "engagement-create": handle_engagement_create_command,
            "engagement-list": handle_engagement_list_command,
            "engagement-show": handle_engagement_show_command,
            "engagement-walk": handle_engagement_walk_command,
            "engagement-update": handle_engagement_update_command,
            "engagement-focus": handle_engagement_focus_command,
            "project-search": handle_project_search_command,
            "project-embed": handle_project_embed_command,
            "code-embed": handle_code_embed_command,
            "doc-check": handle_doc_check_command,
            "projects-discover": handle_projects_discover_command,
            "projects-list": handle_projects_list_command,
            "projects-sync": handle_projects_sync_command,
            # Alias: argparse `aliases=` only covers parsing/--help; args.command
            # carries the invoked alias string, so dispatch needs its own entry.
            "project-sync": handle_projects_sync_command,
            "projects-unregister": handle_projects_unregister_command,
            "project-register": handle_project_register_command,
            "forgejo-publish": handle_forgejo_publish_command,
            "daemon-list": handle_daemon_list_command,
            "daemon-grant": handle_daemon_grant_command,
            "daemon-deny": handle_daemon_deny_command,
            "daemon-grants-list": handle_daemon_grants_list_command,
            # Finding/unknown/deadend/assumption/decision logging
            "note": handle_note_command,
            "finding-log": handle_finding_log_command,
            "finding-resolve": handle_finding_resolve_command,
            "unknown-log": handle_unknown_log_command,
            "unknown-resolve": handle_unknown_resolve_command,
            "unknown-list": handle_unknown_list_command,
            "deadend-log": handle_deadend_log_command,
            "assumption-log": handle_assumption_log_command,
            "decision-log": handle_decision_log_command,
            "log-artifacts": handle_log_artifacts_command,
            "resolve-artifacts": handle_resolve_artifacts_command,
            "delete-artifacts": handle_delete_artifacts_command,
            "source-add": handle_source_add_command,
            "source-get": handle_source_get_command,
            "source-list": handle_source_list_command,
            "sources-map": handle_sources_map_command,
            "sources-reconcile": handle_sources_reconcile_command,
            "sources-check": handle_sources_check_command,
            "source-archive": handle_source_archive_command,
            "source-update": handle_source_update_command,
            "blindspot-scan": handle_blindspot_scan_command,
            "epp-activate": handle_epp_activate_command,
            # Training data export
            "training-export": handle_training_export_command,
            # Sync commands (git notes synchronization)
            "sync-config": handle_sync_config_command,
            "sync-push": handle_sync_push_command,
            "sync-pull": handle_sync_pull_command,
            "sync-status": handle_sync_status_command,
            "rebuild": handle_rebuild_command,
            "artifacts-generate": handle_artifacts_generate_command,
            # Goals commands
            "goals-create": handle_goals_create_command,
            "goals-list": handle_goals_list_command,
            "goals-search": handle_goals_search_command,
            "goals-complete": handle_goals_complete_command,
            "goals-prune": handle_goals_prune_command,
            "goals-claim": handle_goals_claim_command,
            "goals-add-task": handle_goals_add_task_command,
            "goals-add-dependency": handle_goals_add_dependency_command,
            "goals-complete-task": handle_goals_complete_task_command,
            "goals-get-tasks": handle_goals_get_tasks_command,
            "goals-progress": handle_goals_progress_command,
            "goals-discover": handle_goals_discover_command,
            "goals-ready": handle_goals_ready_command,
            "goals-resume": handle_goals_resume_command,
            "goals-mark-stale": handle_goals_mark_stale_command,
            "goals-get-stale": handle_goals_get_stale_command,
            "goals-activate": handle_goals_activate_command,
            "goal-activate": handle_goals_activate_command,
            "goals-reopen": handle_goals_reopen_command,
            "goal-reopen": handle_goals_reopen_command,
            "goals-archive": handle_goals_archive_command,
            "goal-archive": handle_goals_archive_command,
            "goals-refresh": handle_goals_refresh_command,
            # Vision commands
            "vision": handle_vision_analyze,
            # Epistemics commands
            "epistemics-list": handle_epistemics_list_command,
            "epistemics-show": handle_epistemics_stats_command,
            # Edit verification commands
            "edit-with-confidence": handle_edit_with_confidence_command,
            # Issue capture commands
            "issue-list": handle_issue_list_command,
            "issue-show": handle_issue_show_command,
            "issue-handoff": handle_issue_handoff_command,
            "issue-resolve": handle_issue_resolve_command,
            "issue-export": handle_issue_export_command,
            "issue-stats": handle_issue_stats_command,
            # Architecture assessment commands
            "assess-component": handle_assess_component_command,
            "assess-compare": handle_assess_compare_command,
            "assess-directory": handle_assess_directory_command,
            # Unified query command
            "query": handle_query_command,
            # Sentinel orchestration commands
            "sentinel-orchestrate": handle_sentinel_orchestrate_command,
            "sentinel-load-profile": handle_sentinel_load_profile_command,
            "sentinel-status": handle_sentinel_status_command,
            "sentinel-check": handle_sentinel_check_command,
            # Release commands
            "release-ready": handle_release_ready_command,
            "docs-assess": handle_docs_assess,
            "docs-explain": handle_docs_explain,
            "rust-docs-assess": handle_rust_docs_assess,
            "docs-link-check": handle_docs_link_check_command,
            "bootstrap-context": handle_bootstrap_context_command,
            "practice-context": handle_practice_context_command,
            "mesh-agreements": handle_mesh_agreements_group_command,
            # Lesson commands (Epistemic Procedural Knowledge)
            "lesson-create": handle_lesson_create_command,
            "lesson-load": handle_lesson_load_command,
            "lesson-list": handle_lesson_list_command,
            "lesson-search": handle_lesson_search_command,
            "lesson-recommend": handle_lesson_recommend_command,
            "lesson-stats": handle_lesson_stats_command,
            "lesson-embed": handle_lesson_embed_command,
            # Onboarding commands
            "onboard": handle_onboard_command,
            "setup-claude-code": handle_setup_claude_code_command,
            "plugin-sync": handle_plugin_sync_command,
            "enp-setup": handle_enp_setup_command,
            "diagnose": handle_diagnose_command,
            "doctor": handle_doctor_command,
            "release": handle_release_command,
            # Trajectory commands (experimental epistemic prediction)
            "trajectory-show": handle_trajectory_show_command,
            "trajectory-stats": handle_trajectory_stats_command,
            "trajectory-backfill": handle_trajectory_backfill_command,
            # Concept graph commands (experimental epistemic prediction)
            "concept-build": handle_concept_build,
            "concept-stats": handle_concept_stats,
            "concept-top": handle_concept_top,
            "concept-related": handle_concept_related,
            # MCP inspection (lifecycle now owned by harness mcp.json configs)
            "mcp-list-tools": handle_mcp_list_tools_command,
            # Inter-agent messaging commands
            "message-send": handle_message_send_command,
            "message-inbox": handle_message_inbox_command,
            "message-read": handle_message_read_command,
            "message-reply": handle_message_reply_command,
            "message-thread": handle_message_thread_command,
            "message-channels": handle_message_channels_command,
            "message-cleanup": handle_message_cleanup_command,
            # Dispatch bus commands (typed cross-instance dispatch)
            "bus-register": handle_bus_register_command,
            "bus-dispatch": handle_bus_dispatch_command,
            "bus-subscribe": handle_bus_subscribe_command,
            "bus-instances": handle_bus_instances_command,
            "bus-status": handle_bus_status_command,
            # Profile management commands
            "profile-sync": handle_profile_sync_command,
            "profile-prune": handle_profile_prune_command,
            "profile-status": handle_profile_status_command,
            "profile-import": handle_profile_import_command,
            # Server commands
            "serve": handle_serve_command,
            # Memory management commands
            "memory-prime": handle_memory_prime_command,
            "memory-scope": handle_memory_scope_command,
            "memory-value": handle_memory_value_command,
            "pattern-check": handle_pattern_check_command,
            "session-rollup": handle_session_rollup_command,
            "memory-report": handle_memory_report_command,
            # === ALIASES ===
            # Argparse registers aliases for --help, but handler lookup needs them too
            # CASCADE aliases
            "pre": handle_preflight_submit_command,
            "preflight": handle_preflight_submit_command,
            "post": handle_postflight_submit_command,
            "postflight": handle_postflight_submit_command,
            # Session aliases
            "sc": handle_session_create_command,
            "sl": handle_sessions_list_command,
            "sr": handle_sessions_resume_command,
            "session-list": handle_sessions_list_command,
            "session-show": handle_sessions_show_command,
            "session-export": handle_sessions_export_command,
            "session-resume": handle_sessions_resume_command,
            # Goal aliases
            "gc": handle_goals_create_command,
            "gl": handle_goals_list_command,
            "goal-create": handle_goals_create_command,
            "goal-list": handle_goals_list_command,
            "goal-complete": handle_goals_complete_command,
            "goal-progress": handle_goals_progress_command,
            "goal-add-task": handle_goals_add_task_command,
            "goal-complete-task": handle_goals_complete_task_command,
            # Logging aliases
            "fl": handle_finding_log_command,
            "ul": handle_unknown_log_command,
            "de": handle_deadend_log_command,
            # Project aliases
            "pb": handle_project_bootstrap_command,
            "bootstrap": handle_project_bootstrap_command,
            # Message aliases
            "msg-send": handle_message_send_command,
            "ms": handle_message_send_command,
            "msg-inbox": handle_message_inbox_command,
            "mi": handle_message_inbox_command,
            "msg-read": handle_message_read_command,
            "mr": handle_message_read_command,
            "msg-reply": handle_message_reply_command,
        }

        if parsed_args.command in command_handlers:
            handler = command_handlers[parsed_args.command]
            _dispatch_reached = True  # past table build — a NameError now is the handler's, not registration
            result = handler(parsed_args)

            # Handle result output and exit code
            exit_code = _handle_command_result(result, parsed_args)

            # Log execution time
            elapsed_ms = int((time.time() - start_time) * 1000)
            if verbose:
                print(f"[VERBOSE] Execution time: {elapsed_ms}ms", file=sys.stderr)

            sys.exit(exit_code)
        else:
            print(f"❌ Unknown command: {parsed_args.command}")
            sys.exit(1)

    except NameError as _reg_err:
        # A feature verb's handler failed to import while BUILDING the dispatch
        # table (not while running a handler — that's gated by _dispatch_reached).
        # Do NOT let it brick the loop: dispatch from the pre-seeded life-support
        # set so preflight/check/postflight + recovery verbs still work and the
        # firewall never fail-closes into a deadlock (incident prop_m2orjtoz).
        if _dispatch_reached:
            handle_cli_error(_reg_err, getattr(parsed_args, "command", None))
            sys.exit(1)
        import logging as _logging

        _logging.getLogger(__name__).error(
            "empirica CLI: a command handler failed to register (%s); feature verbs "
            "degraded, life-support verbs remain available. Reinstall/repair the package.",
            _reg_err,
        )
        cmd = getattr(parsed_args, "command", None)
        if cmd in command_handlers:  # life-support set (pre-seeded above)
            sys.exit(_handle_command_result(command_handlers[cmd](parsed_args), parsed_args))
        print(
            f"⚠ empirica: '{cmd}' is unavailable due to a command-registration error "
            f"({_reg_err}); core verbs (preflight/check/postflight/session-create/"
            f"doctor/diagnose/sentinel) still work. Reinstall/repair the package.",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        handle_cli_error(e, parsed_args.command)
        sys.exit(1)


if __name__ == "__main__":
    main()
