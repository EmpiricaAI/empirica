"""Onboarding command parsers."""


def add_onboarding_parsers(subparsers):
    """Add onboarding command parsers"""
    # Onboard command - interactive introduction to Empirica
    onboard_parser = subparsers.add_parser(
        'onboard',
        help='Interactive introduction to Empirica (recommended for first-time users)'
    )
    onboard_parser.add_argument(
        '--ai-id',
        default='claude-code',
        help='AI identifier (optional, default: claude-code)'
    )

    # Setup Claude Code command - configure Claude Code integration
    setup_cc_parser = subparsers.add_parser(
        'setup-claude-code',
        help='Configure Claude Code integration (hooks, CLAUDE.md, MCP server)',
        description="""
Configure Claude Code integration for Empirica. This command:

1. Installs the empirica plugin to ~/.claude/plugins/local/
2. Configures CLAUDE.md system prompt in ~/.claude/
3. Sets up hooks in settings.json:
   - Sentinel gate (blocks praxic tools until CHECK passes)
   - Pre/post compact (epistemic state persistence)
   - Session lifecycle (init, end, subagent tracking)
4. Configures MCP server in mcp.json (installs empirica-mcp if needed)

Run this after 'brew install empirica' or 'pip install empirica'.
        """
    )
    setup_cc_parser.add_argument(
        '--force',
        action='store_true',
        help='Reinstall plugin even if it already exists'
    )
    setup_cc_parser.add_argument(
        '--skip-mcp',
        action='store_true',
        help='Skip MCP server installation and configuration'
    )
    setup_cc_parser.add_argument(
        '--skip-credentials',
        action='store_true',
        help='Skip the credentials validation + wizard (use env vars or pre-populated credentials.yaml)'
    )
    # Tenant metadata escape hatches — override the cortex /v1/tenant/me fetch
    # field-by-field. Useful when running setup-claude-code without cortex
    # creds, or pre-baking tenant identity into a fleet image.
    setup_cc_parser.add_argument(
        '--org-id',
        default=None,
        help='Override tenant org_id (skip cortex /v1/tenant/me fetch for this field)'
    )
    setup_cc_parser.add_argument(
        '--tenant-slug',
        default=None,
        help='Override tenant_slug (skip cortex /v1/tenant/me fetch for this field)'
    )
    setup_cc_parser.add_argument(
        '--mesh-id-prefix',
        default=None,
        help='Override mesh_id_prefix (skip cortex /v1/tenant/me fetch for this field)'
    )
    setup_cc_parser.add_argument(
        '--skip-claude-md',
        action='store_true',
        help='Skip CLAUDE.md installation (keep existing system prompt)'
    )
    setup_cc_parser.add_argument(
        '--full-prompt',
        action='store_true',
        help='Use full system prompt instead of lean core default (lean loads skills on demand)'
    )
    setup_cc_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format (default: human)'
    )
    setup_cc_parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )

    # ENP Setup command - initialize ENP watcher infrastructure
    enp_parser = subparsers.add_parser(
        'enp-setup',
        help='Initialize the Epistemic Network Protocol (ENP) watcher',
        description="""
Initialize the ENP watcher — monitors shared git repos for changes
and notifies AI sessions when relevant files update.

This command:
1. Creates ~/.empirica/enp/ directory
2. Copies config template (edit to add your repos and ntfy topics)
3. Initializes watcher state from current repo HEADs
4. Shows cron setup instructions
5. Shows hook registration instructions

The watcher runs via cron (every 5 min) and writes pending notifications.
SessionStart and PostToolUse hooks surface these to Claude at natural points.
        """
    )
    enp_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format (default: human)'
    )

    # Release command - thin wrapper around scripts/release.py
    release_parser = subparsers.add_parser(
        'release',
        help='Run the release pipeline (wraps scripts/release.py)',
        description="""
Thin wrapper around scripts/release.py for the Empirica release pipeline.
Does NOT require PREFLIGHT/POSTFLIGHT (mechanical pipeline, work_type=release).

Recommended flow:
  empirica release --prepare          # merge, build, test
  (review artifacts, smoke test)
  empirica release --publish          # push to all channels

One-shot:
  empirica release                    # prepare + publish
  empirica release --dry-run          # preview without executing
        """
    )
    release_parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without executing'
    )
    release_parser.add_argument(
        '--prepare',
        action='store_true',
        help='Merge to main, build, and test — but do NOT publish'
    )
    release_parser.add_argument(
        '--publish',
        action='store_true',
        help='Publish a prepared release (requires --prepare to have been run first)'
    )
    release_parser.add_argument(
        '--version-only',
        action='store_true',
        help='Update version strings only (no build/publish). Requires --old-version.'
    )
    release_parser.add_argument(
        '--old-version',
        help='Previous version for broad sweep replacement (e.g. 1.5.6)'
    )

    # Diagnose command - check Empirica + Claude Code integration health
    diagnose_parser = subparsers.add_parser(
        'diagnose',
        help='Check Empirica + Claude Code integration health (run this when statusline isn\'t showing)',
        description="""
Walks through the Empirica + Claude Code integration step-by-step and
reports PASS / FAIL / WARN with an actionable hint per check. Designed
for the recurring "I installed it but the statusline isn't showing"
class of question.

Checks:
  - Python version
  - empirica CLI on PATH
  - Claude Code config dir (~/.claude/ or $CLAUDE_CONFIG_DIR)
  - Plugin files installed in ~/.claude/plugins/local/empirica/
  - settings.json present and valid JSON
  - statusLine block configured
  - Hooks registered (PreToolUse, PreCompact, PostCompact, SessionStart)
  - Local marketplace registered
  - Statusline script runnable + produces output
  - Active session in current project

Exit codes:
  0  - all checks passed
  1  - one or more FAIL checks
  2  - one or more WARN checks (no FAIL)
        """
    )
    diagnose_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format (default: human)'
    )
    diagnose_parser.add_argument(
        '--frontend',
        choices=['claude-code', 'ecodex'],
        default='claude-code',
        help=(
            'Which frontend to diagnose (default: claude-code). '
            "'ecodex' runs the ecodex-specific check set: codex-empirica-plugin "
            'install, statusline runtime stdin wiring, codex-empirica-translator '
            'on 127.0.0.1:18080, curated provider env_keys, Rust cargo fmt+check.'
        ),
    )
    diagnose_parser.add_argument(
        '--fast',
        action='store_true',
        help=(
            'Skip slow checks (cargo check). Useful for the /diagnose skill\'s '
            'interactive walk-through; CI can leave this off.'
        ),
    )

    # Doctor command - Desktop + general install health (sibling of diagnose)
    doctor_parser = subparsers.add_parser(
        'doctor',
        help='Check Empirica install health (Desktop + general — empirica-mcp, .empirica/, git remote, Cortex reachability)',
        description="""
Frontend-agnostic health check for Empirica installs. Sibling of `diagnose`
(which is Claude Code-centric). Designed to be callable from Claude Desktop
via the empirica-mcp `doctor` tool.

Checks:
  - Python version
  - empirica CLI on PATH
  - empirica-mcp on PATH (Desktop MCP install)
  - .empirica/ folder presence + structure
  - git repo + remote configured (sync_push prereq)
  - sync state (uncommitted changes)
  - Cortex reachability (CORTEX_URL env or default)

Exit codes:
  0  - all checks passed
  1  - one or more FAIL checks
  2  - one or more WARN checks (no FAIL)
        """
    )
    doctor_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='json',
        help='Output format (default: json — Desktop calls expect machine-readable)'
    )
