#!/usr/bin/env python3
"""
Empirica Sentinel Gate - Noetic Firewall with Epistemic ACLs

Implements least-privilege principle for AI tool access:
- NOETIC tools (read/investigate) → always allowed
- PRAXIC tools (write/execute) → require PREFLIGHT, auto-proceed if confident

This is essentially iptables for cognition - default deny, explicit allow.

Core features (always on):
- Smart project root discovery (env var, known paths, cwd search)
- Noetic tool whitelist (Read, Grep, Glob, etc.)
- Safe Bash command whitelist (ls, cat, git status, etc.)
- PREFLIGHT required for praxic actions (epistemic assessment)
- AUTO-PROCEED: If PREFLIGHT vectors pass dynamic threshold gate, skip CHECK
- LOW-CONFIDENCE: If PREFLIGHT fails gate, explicit CHECK required
- Decision parsing (blocks if CHECK returned "investigate")

Optional features (off by default):
- EMPIRICA_SENTINEL_REQUIRE_BOOTSTRAP=true - Require project-bootstrap before praxic
- EMPIRICA_SENTINEL_COMPACT_INVALIDATION=true - Invalidate CHECK after compact
- EMPIRICA_SENTINEL_CHECK_EXPIRY=true - Enable 30-minute CHECK expiry
- EMPIRICA_SENTINEL_LOOPING=false - Disable sentinel entirely

Related but NOT consumed here:
- EMPIRICA_CALIBRATION_FEEDBACK=false - Suppress calibration feedback in workflow
  output (PREFLIGHT/CHECK enrichment). Does NOT affect gating — the Sentinel always
  uses raw vectors. See workflow_commands.py for where this flag is consumed.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add lib folder to path for shared modules
_lib_path = Path(__file__).parent.parent / "lib"
if str(_lib_path) not in sys.path:
    sys.path.insert(0, str(_lib_path))

from project_resolver import detect_environment, get_active_project_path, get_instance_id  # noqa: E402, I001 — after sys.path setup

# Noetic tools - read/investigate/search - always allowed (whitelist)
NOETIC_TOOLS = {
    "Read",
    "Glob",
    "Grep",
    "LSP",  # File inspection
    "WebFetch",
    "WebSearch",  # Web research
    "ToolSearch",  # Deferred tool discovery
    "Task",
    "TaskOutput",  # Agent delegation
    "TodoWrite",  # Planning
    "AskUserQuestion",  # User interaction
    "Skill",  # Skill invocation
    "KillShell",  # Process management (cleanup)
}

# Chrome MCP tools classified by effect (noetic = read-only, praxic = mutating)
NOETIC_MCP_CHROME = {
    "mcp__claude-in-chrome__tabs_context_mcp",  # List open tabs
    "mcp__claude-in-chrome__tabs_create_mcp",  # Open new tab (viewing, not mutation)
    "mcp__claude-in-chrome__navigate",  # Navigate to URL (viewing)
    "mcp__claude-in-chrome__read_page",  # Read page content
    "mcp__claude-in-chrome__get_page_text",  # Get page text
    "mcp__claude-in-chrome__find",  # Find text on page
    "mcp__claude-in-chrome__read_console_messages",  # Read console output
    "mcp__claude-in-chrome__read_network_requests",  # Read network activity
    "mcp__claude-in-chrome__screenshot",  # Capture page screenshot
    "mcp__claude-in-chrome__gif_creator",  # Record page interaction
}
# Praxic Chrome MCP tools (require CHECK): form_input, javascript_tool, computer

# Cortex MCP tools (all read-only search/investigate)
NOETIC_MCP_CORTEX = {
    "mcp__cortex__investigate",  # Query knowledge base
    "mcp__cortex__search_knowledge",  # Semantic search
    "mcp__cortex__get_entity_context",  # Entity lookup
    "mcp__cortex__cortex_stats",  # Stats (read-only)
    "mcp__cortex__cortex_session_init",  # Session init (read context)
    "mcp__cortex__cortex_finding_log",  # Artifact logging (epistemic workflow)
    "mcp__cortex__cortex_decision_log",  # Artifact logging
    "mcp__cortex__cortex_unknown_log",  # Artifact logging
    "mcp__cortex__cortex_goal_create",  # Goal creation
    "mcp__cortex__cortex_log_artifacts",  # Batch artifact logging
    "mcp__cortex__cortex_collab",  # Phase B: noetic collab (forces collab_brief+REFLEX)
    "mcp__cortex__research",  # Web research
    "mcp__cortex__scrape_url",  # URL scraping
    "mcp__cortex__ingest_file",  # Knowledge ingestion
    "mcp__cortex__ingest_batch",  # Batch ingestion
    "mcp__cortex__cortex_bus_register",  # Bus operations
    "mcp__cortex__cortex_bus_poll",  # Bus polling
    "mcp__cortex__cortex_bus_dispatch",  # Bus dispatch
    "mcp__cortex__cortex_bus_complete",  # Bus completion
}

# Empirica MCP tools — ALL are epistemic workflow, always allowed.
# The empirica-mcp server wraps CLI commands — same trust as Tier 2.
EMPIRICA_MCP_PREFIX = "mcp__empirica__"

# Safe Bash command prefixes - read-only operations (ACL)
SAFE_BASH_PREFIXES = (
    # File inspection
    "cat ",
    "head ",
    "tail ",
    "less ",
    "more ",
    "ls",
    "ls ",
    "dir ",
    "tree ",
    "file ",
    "stat ",
    "wc ",
    "find ",
    "locate ",
    "which ",
    "type ",
    "whereis ",
    # File comparison (read-only)
    "diff ",
    "diff -",
    "cmp ",
    "comm ",
    # Text/data search/processing (read-only)
    "grep ",
    "rg ",
    "ag ",
    "ack ",
    "sed -n",
    "awk ",
    "jq ",
    "jq.",  # JSON processing (read-only)
    # Git read operations
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "git remote",
    "git tag",
    "git stash list",
    "git blame",
    "git ls-files",
    "git ls-tree",
    "git cat-file",
    "git notes show",
    "git notes list",
    # GitHub CLI read operations
    "gh issue list",
    "gh issue view",
    "gh issue status",
    "gh pr list",
    "gh pr view",
    "gh pr diff",
    "gh pr status",
    "gh pr checks",
    "gh repo view",
    "gh release list",
    "gh release view",
    "gh run list",
    "gh run view",
    "gh run watch",  # CI/workflow run inspection (read-only)
    "gh workflow list",
    "gh workflow view",
    "gh search ",  # Search repos, issues, PRs, code (read-only)
    "gh api ",  # API calls (read-only by default)
    # Environment inspection
    "pwd",
    "echo ",
    "printf ",
    "env",
    "printenv",
    "set",
    "whoami",
    "id",
    "hostname",
    "uname",
    "date",
    "cal",
    # Empirica CLI: read-only commands only (tiered whitelist - see is_safe_empirica_command)
    # NOTE: State-changing empirica commands (preflight-submit, goals-create, etc.)
    # are handled separately in is_safe_empirica_command() with loop-state checks.
    # Blanket 'empirica ' whitelist removed to prevent prompt injection bypass.
    # Package inspection (not install)
    "pip show",
    "pip list",
    "pip freeze",
    "pip index",
    "npm list",
    "npm ls",
    "npm view",
    "npm info",
    "cargo tree",
    "cargo metadata",
    # Version/help queries (always safe, any tool)
    "--version",
    "--help",
    "python3 --version",
    "python --version",
    "node --version",
    "npm --version",
    "cargo --version",
    "go version",
    # Process inspection
    "ps ",
    "top -b -n 1",
    "pgrep ",
    "jobs",
    # Terminal/tmux inspection (read-only)
    "tmux capture-pane",
    "tmux list-panes",
    "tmux list-windows",
    "tmux list-sessions",
    "tmux display-message",
    "tmux show-option",
    # Disk inspection
    "df ",
    "du ",
    "mount",
    "lsblk",
    # Network inspection (not modification)
    "curl ",
    "wget -O-",
    "ping -c",
    "dig ",
    "nslookup ",
    "host ",
    # Remote inspection (read-only SSH)
    "ssh ",
    # Documentation
    "man ",
    "info ",
    "help ",
    # Testing (read-only check)
    "test ",
    "[ ",
    # Static analysis (read-only)
    "pyright",
    "ruff check",
    "radon ",
    "mypy ",
    "flake8 ",
    "pylint ",
)

# Dangerous shell operators (command injection prevention)
# Blocks: ls; rm -rf, echo > file, etc.
# NOTE: Pipes handled separately - allowed only to safe targets
DANGEROUS_SHELL_OPERATORS = (
    ";",  # Command chaining
    "&&",  # Conditional AND
    "||",  # Conditional OR
    "`",  # Backtick command substitution
    "$(",  # Modern command substitution
    # NOTE: Redirection (>, >>, <) checked separately to allow safe patterns
)


def _split_outside_quotes(command: str, separator: str) -> list[str]:
    """Split `command` on `separator`, ignoring occurrences inside single
    or double quotes (handles backslash-escaping).

    Solves the false-positive where a quoted regex like grep "A\\|B" gets
    its inner pipe treated as a shell pipe, breaking is_safe_pipe_chain
    classification.

    For multi-char separators (`&&`, `||`), matches the whole sequence.
    """
    if not separator:
        return [command]
    sep_len = len(separator)
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    escape = False
    i = 0
    while i < len(command):
        c = command[i]
        if escape:
            current.append(c)
            escape = False
            i += 1
            continue
        if c == "\\":
            current.append(c)
            escape = True
            i += 1
            continue
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
            i += 1
            continue
        if not in_single and not in_double and command[i : i + sep_len] == separator:
            segments.append("".join(current))
            current = []
            i += sep_len
            continue
        current.append(c)
        i += 1
    segments.append("".join(current))
    return segments


def _contains_outside_quotes(command: str, needle: str) -> bool:
    """True iff `needle` appears in `command` outside any quoted region."""
    return len(_split_outside_quotes(command, needle)) > 1


# Safe redirection patterns (stderr suppression, etc.)
import re  # noqa: E402 — grouped with related patterns below

SAFE_REDIRECT_PATTERN = re.compile(r"2>/dev/null|2>&1|>/dev/null|2>\s*/dev/null")

# Safe pipe targets - read-only commands that can receive piped input
# Allows: grep ... | head, cat ... | wc -l, etc.
SAFE_PIPE_TARGETS = (
    "head",
    "tail",
    "wc",
    "sort",
    "uniq",
    "grep",
    "rg",
    "awk",
    "sed -n",
    "cut",
    "tr",
    "less",
    "more",
    "cat",
    "xargs echo",
    "tee /dev/stderr",
    "python3 -c",
    "python -c",  # For simple JSON parsing
    "jq",
    "jq ",  # JSON processing (read-only)
    "base64",  # Data encoding/decoding (read-only)
)

# Work-type-aware command expansion.
# When PREFLIGHT declares work_type, the Sentinel expands the safe command list.
# The user explicitly chose the work type — this is a scope declaration.
_current_work_type: str | None = None

# Remote-ops auto-detection nudge (fires once per transaction)
_remote_ops_nudge: str = ""
_remote_ops_nudged: bool = False

# Work-type missing nudge (fires once per transaction)
_worktype_nudge: str = ""
_worktype_nudged: bool = False

# Additional safe commands for infra/config/debug work types
INFRA_SAFE_PREFIXES = (
    # System inspection
    "systemctl status",
    "systemctl is-active",
    "systemctl list-units",
    "journalctl --since",
    "journalctl -u",
    "journalctl --no-pager",
    "free",
    "uptime",
    "lscpu",
    "lsmem",
    "lsusb",
    "lspci",
    "htop",
    "vmstat",
    "iostat",
    "dmesg",
    # Docker inspection (not mutation)
    "docker ps",
    "docker images",
    "docker logs",
    "docker inspect",
    "docker network ls",
    "docker volume ls",
    "docker stats",
    "docker compose ps",
    "docker compose logs",
    # Network inspection
    "ss -",
    "ip addr",
    "ip link",
    "ip route",
    "ip -br",
    "netstat -",
    "traceroute ",
    "mtr ",
    "iptables -L",
    "ufw status",
    # Service inspection
    "ollama list",
    "ollama ps",
    "ollama show",
    "nginx -t",
    "nginx -T",
    # Tmux full access
    "tmux ",
    # Cloud/infra read operations
    "kubectl get",
    "kubectl describe",
    "kubectl logs",
    "terraform plan",
    "terraform show",
    "cloudflared tunnel list",
    "cloudflared tunnel info",
)

# Thresholds for CHECK validation.
#
# DESIGN: The Sentinel uses RAW (uncorrected) vectors for all gating decisions.
# Calibration corrections (from grounded verification, Bayesian learning trajectory)
# are FEEDBACK for the AI to internalize and self-correct — they are never applied
# silently by the system. What the AI reports is what the Sentinel evaluates.
#
# This is intentional and NOT controlled by EMPIRICA_CALIBRATION_FEEDBACK.
# The flag gates calibration FEEDBACK in workflow output (PREFLIGHT/CHECK enrichment),
# not gating logic. The Sentinel always uses raw vectors regardless of the flag.
# Static fallbacks — used when dynamic thresholds unavailable
KNOW_THRESHOLD = 0.70
UNCERTAINTY_THRESHOLD = 0.35
MAX_CHECK_AGE_MINUTES = 30


def _get_dynamic_thresholds(db) -> tuple:
    """Read Brier-based dynamic thresholds. Returns (know_threshold, unc_threshold).

    Falls back to static constants if dynamic computation fails or has insufficient data.
    Only the noetic phase thresholds are used for the sentinel gate (investigation → action).
    """
    try:
        from empirica.core.post_test.dynamic_thresholds import compute_dynamic_thresholds
        from empirica.utils.session_resolver import InstanceResolver as R

        # Brier thresholds are per-practice — resolve the canonical ai_id so a
        # multi-practice machine doesn't read 'claude-code' calibration for all.
        dt_result = compute_dynamic_thresholds(ai_id=R.ai_id() or "claude-code", db=db)
        if dt_result.get("source") == "dynamic":
            noetic = dt_result.get("noetic", {})
            if noetic.get("brier_score") is not None:
                return (noetic["ready_know_threshold"], noetic["ready_uncertainty_threshold"])
    except Exception:
        pass
    return (KNOW_THRESHOLD, UNCERTAINTY_THRESHOLD)


def _get_domain_scaled_thresholds(
    base_unc: float,
    domain: str | None,
    criticality: str | None,
    project_path: str | None = None,
) -> float:
    """Scale uncertainty threshold based on domain criticality (B1 Wave 2).

    Higher criticality = stricter threshold (lower uncertainty required).
    Uses coverage_min from the domain checklist as the scaling signal.

    Returns the adjusted uncertainty threshold.
    """
    if not domain and not criticality:
        return base_unc

    try:
        from pathlib import Path

        from empirica.config.domain_registry import DomainKey, DomainRegistry

        reg = DomainRegistry(
            project_path=Path(project_path) if project_path else None,
        )
        key = DomainKey(
            work_type=domain or "code",
            domain=domain or "default",
            criticality=criticality or "medium",
        )
        checklist = reg.resolve(key)

        if not checklist.has_checks:
            # Empty checklist (e.g., remote-ops) — no scaling
            return base_unc

        # Scale: coverage_min maps to uncertainty threshold
        # Higher coverage_min = higher rigor = lower uncertainty threshold
        # coverage_min 0.3 (low) → uncertainty 0.35 (lenient)
        # coverage_min 0.7 (high) → uncertainty 0.20 (strict)
        # coverage_min 0.85 (critical) → uncertainty 0.15 (very strict)
        coverage_min = checklist.thresholds.get("coverage_min", 0.3)
        scaled = max(0.10, base_unc * (1.0 - coverage_min * 0.6))
        return round(scaled, 2)

    except Exception:
        return base_unc


# Transition commands - allowed after POSTFLIGHT to enable new cycle
# These are the commands needed to properly switch projects or start new sessions
TRANSITION_COMMANDS = (
    "cd ",  # Directory change (project switch)
    "empirica session-create",  # New session
    "empirica project-bootstrap",  # Bootstrap new project context
    "empirica project-init",  # Initialize new project
    "empirica project-switch",  # Switch active project context
    "empirica project-list",  # List available projects
    "empirica preflight-submit",  # Start new epistemic cycle (was missing = chicken-and-egg bug)
    "git add",  # Stage work from completed transaction
    "git commit",  # Commit work from completed transaction
)


PAUSE_FILE_BASE = Path.home() / ".empirica"
PAUSE_FILE_GLOBAL = PAUSE_FILE_BASE / "sentinel_paused"


def get_pause_file_path() -> Path:
    """Get instance-specific pause file path.

    Returns ~/.empirica/sentinel_paused_{instance_id} for per-instance control.
    Falls back to ~/.empirica/sentinel_paused global file if no instance_id.
    """
    instance_id = get_instance_id()
    if instance_id:
        # Sanitize instance_id for filename (remove special chars)
        safe_id = instance_id.replace("/", "-").replace("%", "")
        return PAUSE_FILE_BASE / f"sentinel_paused_{safe_id}"
    return PAUSE_FILE_GLOBAL


def is_empirica_paused() -> bool:
    """Check if Empirica tracking is paused (off-the-record mode).

    Checks instance-specific pause file first, then global.
    Instance: ~/.empirica/sentinel_paused_{instance_id}
    Global:   ~/.empirica/sentinel_paused

    This is the cheapest check - no DB needed. Called before any other logic.
    """
    # Check instance-specific pause file first
    instance_pause = get_pause_file_path()
    if instance_pause.exists():
        return True
    # Fallback to global pause (backward compat, also allows pausing ALL instances)
    return PAUSE_FILE_GLOBAL.exists()


# Tiered Empirica CLI whitelist (replaces blanket 'empirica ' whitelist)
# Tier 1: Read-only commands - always safe, no state changes
# Also includes administrative commands (project-switch, project-list) that should always be allowed
EMPIRICA_TIER1_PREFIXES = (
    "empirica epistemics-list",
    "empirica epistemics-show",
    "empirica goals-list",
    "empirica goal-list",
    "empirica gl",  # Goal list + aliases
    "empirica goals-progress",
    "empirica goal-progress",  # Goal progress + alias
    "empirica get-goal-progress",
    "empirica goals-get-tasks",
    "empirica goals-discover",
    "empirica goal-analysis",  # Goal queries
    "empirica project-bootstrap",
    "empirica project-search",
    "empirica project-switch",
    "empirica project-list",  # Administrative - always allowed
    "empirica session-snapshot",
    "empirica get-session-summary",
    "empirica get-epistemic-state",
    "empirica get-calibration-report",
    "empirica monitor",
    "empirica workspace-overview",
    "empirica workspace-map",
    "empirica entity-list",
    "empirica entity-show",
    "empirica entity-walk",
    "empirica entity-search",
    "empirica efficiency-report",
    "empirica skill-suggest",
    "empirica goals-ready",
    "empirica list-goals",
    "empirica query-mistakes",
    "empirica query-handoff",
    "empirica discover-goals",
    "empirica list-identities",
    "empirica issue-list",
    "empirica docs-assess",  # Documentation assessment - read-only investigation tool
    "empirica doctor",  # Read-only diagnostic — MUST stay allowed (recovery escape hatch)
    "empirica diagnose",  # Read-only mesh/listener diagnostic — recovery escape hatch
    "empirica calibration-report",  # Calibration analysis - read-only
    "empirica compact-analysis",  # Compact event analysis - read-only
    "empirica commit-context",  # Per-commit artifact aggregator - read-only
    "empirica practice-context",  # Roster lookup (Ambassador addressbook) - read-only
    "empirica lesson-list",
    "empirica lesson-search",
    "empirica lesson-recommend",
    "empirica lesson-stats",  # Lesson queries - read-only
    "empirica sentinel-status",
    "empirica sentinel-check",  # Sentinel queries - read-only
    "empirica goals-search",
    "empirica goals-get-stale",  # Goal queries - read-only
    "empirica unknown-list",
    "empirica assumption-list",  # Artifact queries - read-only
    "empirica deadend-list",
    "empirica finding-list",  # Artifact queries - read-only
    "empirica decision-list",
    "empirica mistake-list",  # Artifact queries - read-only
    "empirica compliance-report",  # Compliance report - read-only
    "empirica workspace-list",
    "empirica ecosystem-check",  # Workspace queries - read-only
    "empirica --help",
    "empirica -h",
    "empirica help",  # subcommand form (`empirica help` and `empirica help <category>`)
    "empirica version",
    "empirica profile-status",  # Profile status - read-only
    "empirica noetic-batch",  # Batched noetic primitive — IS a noetic operation
    "empirica sentinel ",  # Sentinel subcommand: pause/resume/status
    "empirica loop ",  # Loop registry CRUD — instance-local control plane
    "empirica listener ",  # Event-listener registry CRUD — instance-local control plane
    "empirica instance ",  # Instance lifecycle: kill/forget/label
    "empirica status",  # Multi-instance status overview
    "empirica tui",  # Interactive cockpit (Textual app — destructive ops are modal-confirmed)
    "empirica notify ",  # Notification primitive — loops/hooks call this in any phase
)

# Tier 2: State-changing commands - allowed (these ARE the epistemic workflow)
# These need to pass through to enable PREFLIGHT/CHECK/POSTFLIGHT and breadcrumbs.
# The Sentinel already gates praxic actions via vectors - these commands
# are HOW the AI satisfies those gates.
EMPIRICA_TIER2_PREFIXES = (
    "empirica preflight-submit",
    "empirica check-submit",
    "empirica postflight-submit",
    "empirica finding-log",
    "empirica unknown-log",
    "empirica deadend-log",
    "empirica mistake-log",
    "empirica log-mistake",
    "empirica note",  # Scratchpad note-to-self (metadata-only, ungated like *-log)
    "empirica log-artifacts",
    "empirica resolve-artifacts",
    "empirica delete-artifacts",  # Batch artifact operations
    "empirica goals-create",
    "empirica goal-create",
    "empirica gc",  # Goal create + aliases
    "empirica goals-complete",
    "empirica goal-complete",  # Goal complete + alias
    "empirica goals-add-task",
    "empirica goal-add-task",  # Add task + alias
    "empirica goals-complete-task",
    "empirica goal-complete-task",  # Complete task + alias
    "empirica goals-add-dependency",
    "empirica goals-resume",  # Goal management
    "empirica goals-claim",
    "empirica session-create",
    "empirica session-end",
    "empirica create-goal",
    "empirica add-task",
    "empirica complete-task",
    "empirica create-handoff",
    "empirica resume-goal",
    "empirica unknown-resolve",
    "empirica issue-handoff",
    "empirica project-init",
    "empirica project-embed",
    "empirica create-git-checkpoint",
    "empirica load-git-checkpoint",
    "empirica memory-compact",
    "empirica resume-previous-session",
    "empirica agent-spawn",
    "empirica investigate",
    "empirica source-add",
    "empirica assumption-log",
    "empirica decision-log",  # Noetic artifacts - assumptions/decisions
    "empirica lesson-create",
    "empirica lesson-load",
    "empirica lesson-path",
    "empirica lesson-replay-start",
    "empirica lesson-replay-end",
    "empirica lesson-embed",  # Lesson lifecycle commands
    "empirica sentinel-orchestrate",
    "empirica sentinel-load-profile",  # Sentinel management
    "empirica artifacts-generate",  # Artifact generation
    "empirica goals-mark-stale",
    "empirica goals-refresh",  # Goal staleness management
    "empirica goals-prune",  # Bulk close stale/duplicate/planned goals (dry-run default)
    "empirica profile-sync",
    "empirica profile-prune",  # Profile management - state-changing
    "empirica release",  # Release pipeline — mechanical, no PREFLIGHT needed
    # Self-heal / maintenance verbs — must NEVER be rush-blocked, or a box with a
    # stale hook can't run the very command that fixes it (deploy-staleness deadlock).
    "empirica setup-claude-code",
    "empirica plugin-sync",
    "empirica plugin-version",
)


def is_safe_empirica_command(command: str) -> bool:
    """Tiered whitelist for empirica CLI commands.

    Tier 1: Read-only (always allowed)
    Tier 2: State-changing (allowed - these are the epistemic workflow itself)

    Toggle operations are NOT whitelisted here - they use self-exemption
    in the main gate logic to prevent prompt injection bypass.
    """
    cmd = command.lstrip()
    if not cmd.startswith("empirica "):
        return False

    # Tier 1: Read-only - always safe
    for prefix in EMPIRICA_TIER1_PREFIXES:
        if cmd.startswith(prefix):
            return True

    # Tier 2: State-changing - allowed (these enable the workflow)
    return any(cmd.startswith(prefix) for prefix in EMPIRICA_TIER2_PREFIXES)


def is_toggle_command(command: str) -> str | None:
    """Detect if a command is writing or removing the Sentinel pause file.

    Returns 'pause' if writing, 'unpause' if removing, None otherwise.
    This enables Sentinel self-exemption for the toggle without
    whitelisting it as a general safe command.
    """
    cmd = command.lstrip()

    # Detect pause file write (python3 -c "..." writing sentinel_paused)
    if "sentinel_paused" in cmd and ("write_text" in cmd or "open(" in cmd):
        return "pause"

    # Detect pause file removal
    if cmd.startswith("rm ") and ("sentinel_paused" in cmd):
        return "unpause"

    return None


def is_transition_command(command: str) -> bool:
    """Check if command is a transition command (allowed after POSTFLIGHT).

    Transition commands enable starting a new epistemic cycle:
    - cd to switch projects
    - session-create to start new session
    - project-bootstrap/init for new project context

    These are allowed after POSTFLIGHT to prevent the chicken-and-egg
    problem where you can't switch projects without a new PREFLIGHT,
    but can't create a PREFLIGHT in the new project without switching.

    Also handles piped and chained commands:
    - echo '...' | empirica preflight-submit -
    - cat file | empirica preflight-submit -
    - cd /path && empirica preflight-submit - << 'EOF'
    """
    cmd = command.lstrip()

    # Direct match
    for prefix in TRANSITION_COMMANDS:
        if cmd.startswith(prefix):
            return True

    # Check pipe segments: echo '...' | empirica preflight-submit -
    if "|" in cmd:
        for segment in cmd.split("|"):
            segment = segment.strip()
            for prefix in TRANSITION_COMMANDS:
                if segment.startswith(prefix):
                    return True

    # Check && chain segments: cd /path && empirica preflight-submit -
    if "&&" in cmd:
        for segment in cmd.split("&&"):
            segment = segment.strip()
            # Strip heredoc suffix for matching
            segment_clean = segment.split("<<")[0].strip() if "<<" in segment else segment
            for prefix in TRANSITION_COMMANDS:
                if segment_clean.startswith(prefix):
                    return True

    return False


# --- AUTONOMY CALIBRATION LOOP ---
# Tracks tool call count per transaction and nudges at adaptive thresholds.
# The nudge is informational — Claude decides when to POSTFLIGHT based on
# information completeness, not forced thresholds.

_autonomy_nudge = ""  # Module-level: set during increment, read by respond
_goalless_nudge = ""  # Module-level: set when no goals detected, read by respond
_reread_nudge = ""  # Module-level: set when Read tool targets already-read file
_file_relevance_nudge = ""  # Module-level: set when artifacts reference an Edit/Write target
_last_read_count = 0  # Module-level: how many times current file was read this tx


def _find_transaction_file(
    empirica_dir: Path,
    suffix: str,
    session_id: str | None = None,
    claude_session_id: str | None = None,
) -> Path | None:
    """Find the active transaction file, with suffix-mismatch fallback.

    Primary: exact file matching the current instance suffix.
    Fallback: when exact file doesn't exist (hook context where TMUX_PANE is not
    inherited, OR the ephemeral tmux_N rotated across compaction), scan for any
    active_transaction_*.json matching — preferring the DURABLE
    claude_session_id, then the (rotating) empirica session_id.

    Safe because the scan is scoped by a durable/session key — no cross-instance
    talk. This is the firewall's transaction-resolution path; it must stay in
    sync with empirica/utils/session_resolver.py:_find_transaction_file.
    See: docs/architecture/instance_isolation/KNOWN_ISSUES.md (11.21)
    """
    # Primary: exact suffix match
    exact = empirica_dir / f"active_transaction{suffix}.json"
    if exact.exists():
        return exact

    # Fallback: scan suffix-mismatched files. Rank candidates and return the best
    # rather than the first sorted match — claude_session_id is stable across the
    # whole CC session, so many files (one per past transaction, each
    # POSTFLIGHT-closed) share it; returning the first would resolve a STALE
    # CLOSED transaction and make this firewall block praxic after a valid CHECK.
    # Rank by (cc_match, is_open, updated_at) descending. Kept in sync with
    # empirica/utils/session_resolver.py:_find_transaction_file.
    if not claude_session_id and not session_id:
        return None
    best_rank = None
    best_file = None
    try:
        for tx_file in sorted(empirica_dir.glob("active_transaction*.json")):
            try:
                with open(tx_file) as f:
                    tx_data = json.load(f)
            except Exception:
                continue
            cc_match = bool(claude_session_id and tx_data.get("claude_session_id") == claude_session_id)
            sess_match = bool(session_id and tx_data.get("session_id") == session_id)
            if not (cc_match or sess_match):
                continue
            rank = (cc_match, tx_data.get("status") == "open", tx_data.get("updated_at") or 0.0)
            if best_rank is None or rank > best_rank:
                best_rank, best_file = rank, tx_file
    except Exception:
        return None

    return best_file


def _resolve_empirica_session_id(claude_session_id: str | None) -> str | None:
    """Resolve empirica session_id from claude_session_id via active_work file."""
    if not claude_session_id:
        return None
    try:
        aw_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
        if aw_file.exists():
            with open(aw_file) as f:
                return json.load(f).get("empirica_session_id")
    except Exception:
        pass
    return None


def _locate_transaction_file(
    claude_session_id: str | None, suffix: str, empirica_session_id: str | None
) -> Path | None:
    """Locate the active transaction file using priority chain.

    Try 1: active_work file for project_path
    Try 2: project_resolver canonical path
    Try 3: global fallback
    """
    # Try 1: active_work file
    if claude_session_id:
        aw_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
        if aw_file.exists():
            try:
                with open(aw_file) as f:
                    pp = json.load(f).get("project_path")
                if pp:
                    tx = _find_transaction_file(Path(pp) / ".empirica", suffix, empirica_session_id, claude_session_id)
                    if tx:
                        return tx
            except Exception:
                pass

    # Try 2: project_resolver canonical path
    pp = get_active_project_path(claude_session_id)
    if pp:
        tx = _find_transaction_file(Path(pp) / ".empirica", suffix, empirica_session_id, claude_session_id)
        if tx:
            return tx

    # Try 3: global fallback
    return _find_transaction_file(Path.home() / ".empirica", suffix, empirica_session_id, claude_session_id)


def _is_empirica_mcp_tool(tool_name: str) -> bool:
    """Check if tool is an empirica MCP tool (always allowed — epistemic workflow)."""
    return tool_name.startswith(EMPIRICA_MCP_PREFIX)


def _classify_tool_phase(tool_name: str, tool_input: dict | None) -> bool:
    """Classify whether a tool call is noetic (True) or praxic (False)."""
    return bool(
        tool_name in NOETIC_TOOLS
        or tool_name in NOETIC_MCP_CHROME
        or tool_name in NOETIC_MCP_CORTEX
        or _is_empirica_mcp_tool(tool_name)
        or (tool_name == "Bash" and tool_input and is_safe_bash_command(tool_input))
        or (tool_name in ("Write", "Edit") and tool_input and is_plan_file(tool_input))
    )


def _update_phase_counters(counters: dict, tool_name: str, is_noetic: bool) -> None:
    """Update phase-split counters for calibration."""
    if is_noetic:
        counters["noetic_tool_calls"] = counters.get("noetic_tool_calls", 0) + 1
    else:
        counters["praxic_tool_calls"] = counters.get("praxic_tool_calls", 0) + 1


def _track_edited_files(counters: dict, tool_name: str, tool_input: dict | None) -> None:
    """Track edited file paths for non-git file change detection."""
    if tool_name not in ("Edit", "Write") or not tool_input:
        return
    fp = tool_input.get("file_path", "")
    if fp:
        edited = counters.get("edited_files", [])
        if fp not in edited:
            edited.append(fp)
            counters["edited_files"] = edited


def _track_read_files(counters: dict, tool_name: str, tool_input: dict | None) -> None:
    """Track read file paths for re-read advisory. Sets global _last_read_count."""
    global _last_read_count
    if tool_name != "Read" or not tool_input:
        return
    fp = tool_input.get("file_path", "")
    if fp:
        read_counts = counters.get("read_files", {})
        read_counts[fp] = read_counts.get(fp, 0) + 1
        counters["read_files"] = read_counts
        _last_read_count = read_counts[fp]


def _extract_trace_target(tool_name: str, tool_input: dict | None) -> str:
    """Extract a compact target identifier for workflow trace recording."""
    if tool_name in ("Read", "Edit", "Write") and tool_input:
        target = tool_input.get("file_path", "")
        if target:
            return target.rsplit("/", 1)[-1]  # Just filename
    elif tool_name == "Bash" and tool_input:
        cmd = tool_input.get("command", "")
        return cmd.split()[0] if cmd else ""
    elif tool_name in ("Grep", "Glob") and tool_input:
        return tool_input.get("pattern", "")[:30]
    return ""


def _record_workflow_trace(counters: dict, tool_name: str, tool_input: dict | None, is_noetic: bool) -> None:
    """Record tool sequence entry for pattern mining.

    Compact format: [tool_name, target, phase]. Capped at 200 entries.
    """
    target = _extract_trace_target(tool_name, tool_input)
    phase = "n" if is_noetic else "p"
    trace = counters.get("tool_trace", [])
    trace.append([tool_name, target[:40], phase])
    if len(trace) > 200:
        trace = trace[-200:]
    counters["tool_trace"] = trace


def _atomic_write_counters(counters: dict, counters_path: Path) -> None:
    """Atomic write to counters file (NOT the transaction file)."""
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(counters_path.parent))
    try:
        with os.fdopen(fd, "w") as tf:
            json.dump(counters, tf, indent=2)
        os.replace(tmp, str(counters_path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _try_increment_tool_count(
    claude_session_id: str | None = None, tool_name: str | None = None, tool_input: dict | None = None
) -> tuple:
    """Increment tool_call_count in the hook counters file (separate from transaction).

    Orchestrates: transaction lookup, counter read-modify-write, phase tracking,
    file tracking, workflow trace recording, and atomic persistence.

    Returns (tool_call_count, avg_turns) or (0, 0) if no transaction.
    """
    from empirica.utils.session_resolver import InstanceResolver as R

    suffix = R.instance_suffix()
    empirica_session_id = _resolve_empirica_session_id(claude_session_id)

    tx_path = _locate_transaction_file(claude_session_id, suffix, empirica_session_id)
    if not tx_path:
        return 0, 0

    try:
        with open(tx_path) as f:
            tx = json.load(f)

        if tx.get("status") != "open":
            return 0, 0

        avg = tx.get("avg_turns", 0)

        # Read existing counters
        counters_path = tx_path.parent / f"hook_counters{suffix}.json"
        counters = {}
        if counters_path.exists():
            try:
                with open(counters_path) as f:
                    counters = json.load(f)
            except Exception:
                counters = {}

        counters["tool_call_count"] = counters.get("tool_call_count", 0) + 1
        count = counters["tool_call_count"]

        # Phase-split counting and tracking
        is_noetic = False
        if tool_name:
            is_noetic = _classify_tool_phase(tool_name, tool_input)
            _update_phase_counters(counters, tool_name, is_noetic)

        if tool_name:
            _track_edited_files(counters, tool_name, tool_input)
            _track_read_files(counters, tool_name, tool_input)

        if tool_name == "AskUserQuestion":
            counters["pending_user_response"] = True

        if tool_name:
            _record_workflow_trace(counters, tool_name, tool_input, is_noetic)

        _atomic_write_counters(counters, counters_path)
        return count, avg
    except Exception:
        return 0, 0


def _compute_nudge(count: int, avg: int) -> str:
    """Compute autonomy nudge message based on tool call count vs average.

    Returns empty string if no nudge needed. Nudges are informational —
    Claude decides when to POSTFLIGHT based on coherence, not thresholds.
    """
    if avg <= 0 or count <= 0:
        return ""

    ratio = count / avg

    if ratio >= 2.0:
        return (
            f"AUTONOMY: Transaction extended ({count} tool calls, avg {avg}). "
            f"POSTFLIGHT strongly recommended to capture learning and maintain calibration."
        )
    elif ratio >= 1.5:
        return (
            f"AUTONOMY: Transaction at {count}/{avg} tool calls (1.5x avg). "
            f"Consider POSTFLIGHT soon to preserve measurement fidelity."
        )
    elif ratio >= 1.0:
        return (
            f"AUTONOMY: Transaction at {count}/{avg} tool calls (past avg). "
            f"Natural POSTFLIGHT point when current coherent chunk completes."
        )
    return ""


def respond(decision: str, reason: str = "") -> None:
    """Output in Claude Code's expected format. Appends nudges on allow."""
    global _autonomy_nudge, _goalless_nudge, _reread_nudge, _remote_ops_nudge, _worktype_nudge, _file_relevance_nudge
    full_reason = reason
    show_nudge = False
    if decision == "allow" and (
        _autonomy_nudge
        or _goalless_nudge
        or _reread_nudge
        or _remote_ops_nudge
        or _worktype_nudge
        or _file_relevance_nudge
    ):
        nudges = " | ".join(
            n
            for n in [
                _autonomy_nudge,
                _goalless_nudge,
                _reread_nudge,
                _remote_ops_nudge,
                _worktype_nudge,
                _file_relevance_nudge,
            ]
            if n
        )
        full_reason = f"{reason} | {nudges}"
        show_nudge = True

    output: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": full_reason,
        }
    }
    # Suppress output for "allow" UNLESS there's a nudge to show Claude
    if decision == "allow" and not show_nudge:
        output["suppressOutput"] = True
    print(json.dumps(output))


def resolve_project_root(claude_session_id: str | None = None) -> Path | None:
    """Resolve the correct project root using the shared project_resolver.

    Uses canonical get_active_project_path() from lib/project_resolver.py.
    NO CWD FALLBACK - fails explicitly if instance-aware mechanisms don't work.

    Args:
        claude_session_id: Claude Code conversation UUID from hook input

    Returns:
        Path to project root (parent of .empirica), or None if not found.
    """
    project_path = get_active_project_path(claude_session_id)
    if project_path:
        project_root = Path(project_path)
        if (project_root / ".empirica").exists():
            return project_root
    return None


def find_empirica_package() -> Path | None:
    """Find where empirica package can be imported from.

    This is ONLY for setting up sys.path to enable imports.
    Actual path resolution (DB location, etc.) is delegated to
    empirica.config.path_resolver after import.

    Returns:
        Path to add to sys.path, or None if empirica is already importable.
    """
    # Check if already importable (pip installed)
    try:
        import empirica.config.path_resolver  # noqa: F401  # pyright: ignore[reportUnusedImport,reportMissingImports]

        return None  # Already available, no path needed
    except ImportError:
        pass

    # Search for empirica package in known development locations
    def has_empirica_package(path: Path) -> bool:
        return (path / "empirica" / "__init__.py").exists()

    # Check cwd and parents first (respect project context)
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        if has_empirica_package(parent):
            return parent
        if parent == parent.parent:
            break

    # Fallback to known dev paths
    known_paths = [
        Path.home() / "empirical-ai" / "empirica",
        Path.home() / "empirica",
    ]
    for path in known_paths:
        if has_empirica_package(path):
            return path

    return None


def _get_current_project_id(db_conn, session_id: str) -> str | None:
    """Get project_id from session table (authoritative source).

    The session table stores the project_id that was resolved at session
    creation time. This is the SAME project_id that gets stored in reflexes
    table via store_vectors().

    Args:
        db_conn: Database connection
        session_id: Session UUID to look up

    Returns:
        project_id (UUID) from the session, or None
    """
    try:
        cursor = db_conn.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def get_last_compact_timestamp(project_root: Path) -> datetime | None:
    """Get timestamp of most recent compact from pre_summary snapshot."""
    try:
        ref_docs_dir = project_root / ".empirica" / "ref-docs"
        if not ref_docs_dir.exists():
            return None
        snapshots = sorted(ref_docs_dir.glob("pre_summary_*.json"), reverse=True)
        if not snapshots:
            return None
        # Parse: pre_summary_2026-01-21T12-30-45.json
        filename = snapshots[0].name
        ts = filename.replace("pre_summary_", "").replace(".json", "")
        # Convert 2026-01-21T12-30-45 to ISO
        date_part, time_part = ts.split("T")
        time_part = time_part.replace("-", ":")
        return datetime.fromisoformat(f"{date_part}T{time_part}")
    except Exception:
        return None


def is_plan_file(tool_input: dict) -> bool:
    """Check if a Write/Edit targets a plan file (.claude/plans/).

    Plan files are noetic artifacts — planning is investigation, not execution.
    Allow writes to plan files without requiring CHECK authorization.
    """
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return False
    # Normalize path for reliable matching
    try:
        normalized = str(Path(file_path).resolve())
    except Exception:
        normalized = file_path
    return "/.claude/plans/" in normalized


def _matches_safe_prefix(cmd: str) -> bool:
    """Check if a command matches any SAFE_BASH_PREFIXES entry."""
    for prefix in SAFE_BASH_PREFIXES:
        if cmd.startswith(prefix):
            return True
        if prefix.endswith(" ") and cmd == prefix.rstrip():
            return True
    return False


# Shell control-flow keywords that are inert on their own (no commands run).
# A segment that's just one of these (or starts with one followed by an
# already-validated body) is safe — the substitutions/commands embedded
# inside the larger construct get validated separately via the chain split.
_SHELL_KEYWORDS_INERT = frozenset(
    {
        "then",
        "else",
        "fi",
        "elif",
        "do",
        "done",
        "esac",
        "true",
        "false",  # bash builtins, no exec
    }
)

# Compound keywords: `<keyword> <body>` — strip the keyword and recurse on body.
# Covers `if cond`, `then cmd`, `else cmd`, `elif cond`, `while cond`, etc.
_SHELL_COMPOUND_PREFIXES = (
    "if ",
    "elif ",
    "then ",
    "else ",
    "while ",
    "until ",
    "for ",
    "case ",
    "do ",
    "! ",  # negation: `! cmd` — strip and check rest
)


def _extract_command_substitutions(segment: str) -> list[str]:
    """Pull inner commands out of $(...) and `...` substitutions.

    Returns the list of inner commands (one per substitution found).
    Caller validates each inner command independently via the normal
    pipe/chain rules. Substitutions can nest; the top-level extractor
    walks parens with a depth counter to handle that.
    """
    extracted: list[str] = []
    i = 0
    while i < len(segment):
        if segment[i : i + 2] == "$(":
            depth = 1
            j = i + 2
            while j < len(segment) and depth > 0:
                if segment[j : j + 2] == "$(":
                    depth += 1
                    j += 2
                elif segment[j] == ")":
                    depth -= 1
                    j += 1
                else:
                    j += 1
            if depth == 0:
                extracted.append(segment[i + 2 : j - 1])
            i = j
        else:
            i += 1
    # Backticks (non-nested — bash doesn't allow nested backticks anyway)
    for match in re.finditer(r"`([^`]*)`", segment):
        extracted.append(match.group(1))
    return extracted


def _strip_command_substitutions(segment: str) -> str:
    """Replace $(...) and `...` substitutions with a placeholder string.

    After substitutions are extracted-and-validated separately, the
    residue is what classifies the SHAPE of the segment (control flow,
    test, assignment, etc.). The placeholder is a literal "X" so test
    forms like `[ "$VAR" = "X" ]` stay parseable.
    """
    out: list[str] = []
    i = 0
    while i < len(segment):
        if segment[i : i + 2] == "$(":
            depth = 1
            j = i + 2
            while j < len(segment) and depth > 0:
                if segment[j : j + 2] == "$(":
                    depth += 1
                    j += 2
                elif segment[j] == ")":
                    depth -= 1
                    j += 1
                else:
                    j += 1
            out.append("X")
            i = j
        else:
            out.append(segment[i])
            i += 1
    residue = "".join(out)
    return re.sub(r"`[^`]*`", "X", residue)


def _is_inert_shape(stripped: str) -> bool:
    """Is the residue (substitutions stripped) a known-safe shell shape?

    Recognizes:
      • Bare control-flow keywords: then, else, fi, elif, do, done, esac
      • exit / return (with optional integer arg)
      • Test commands: [ ... ] and [[ ... ]] (no exec, just comparison)
      • Plain VAR=value assignment (no command sub — that was already stripped)

    Compound forms like `if X; then Y; fi` get split by `;` upstream;
    each segment then matches one of the above shapes individually.
    """
    if stripped in _SHELL_KEYWORDS_INERT:
        return True
    # exit / return with optional integer
    if re.match(r"^(exit|return)(\s+\d+)?$", stripped):
        return True
    # [ ... ] and [[ ... ]] — pure tests, no exec
    if (stripped.startswith("[ ") and stripped.endswith(" ]")) or (
        stripped.startswith("[[ ") and stripped.endswith(" ]]")
    ):
        return True
    # VAR=value assignment — the value here is the *placeholder* if it
    # had a substitution (which was already validated), or a literal.
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$", stripped))


def _is_segment_safe(segment: str) -> bool:
    """Check if a single command segment (from && / || / ; chain) is safe.

    Covers BOTH Tier 1 and Tier 2 empirica commands via
    is_safe_empirica_command — chain handling is not narrower than
    single-command handling for empirica verbs.

    Also recognizes common shell constructs that cron bodies use:
      • $(...) and backtick command substitutions — inner command is
        validated against the full safe-command rules (pipes, chains,
        empirica tier classification).
      • Control-flow keywords (`if`, `then`, `else`, `fi`, etc.) and
        compound forms (`if X`, `then Y`) — keyword is stripped and
        the body is recursively classified.
      • Test commands (`[ ... ]`, `[[ ... ]]`) — pure comparison, no exec.
      • Variable assignment (`VAR=value`, `VAR=$(safe)`) — assignment is
        inert; any embedded substitution must independently classify safe.
      • `exit N` and `return N` — terminators with no exec.

    The shape classifier never gates on shell text alone — every
    embedded command (whether in $(...), in a pipe, or as the body of
    a control-flow construct) is independently validated. Shape
    recognition only excuses inert *structure*, never grants safety to
    the commands inside it.
    """
    clean = segment.split("<<")[0].strip() if "<<" in segment else segment
    clean = SAFE_REDIRECT_PATTERN.sub("", clean).strip()
    if not clean:
        return True

    # 1. Validate every embedded $() and backtick substitution. The inner
    # command must independently be safe — we treat substitutions as
    # opaque-but-validated; their text doesn't hide unsafe commands.
    for inner in _extract_command_substitutions(clean):
        inner_clean = inner.strip()
        if not inner_clean:
            continue
        # Inner may have pipes/chains itself — validate via the same
        # safe-command machinery the top level uses.
        if not _is_command_text_safe(inner_clean):
            return False

    # 2. Strip substitutions for shape classification.
    stripped = _strip_command_substitutions(clean).strip()
    if not stripped:
        return True

    # 3. Compound keyword: `<keyword> <body>` — strip and recurse on body.
    for prefix in _SHELL_COMPOUND_PREFIXES:
        if stripped.startswith(prefix):
            rest = stripped[len(prefix) :].strip()
            if not rest:
                return True
            return _is_segment_safe(rest)

    # 4. Original safe forms.
    if stripped.startswith("cd "):
        return True
    # A piped segment (`empirica goals-list | tail`) must be validated
    # stage-by-stage — the trailing pipe can otherwise smuggle an executor
    # (`empirica goals-list | sh`) past the bare empirica-prefix match.
    if _contains_outside_quotes(stripped, "|"):
        return is_safe_pipe_chain(stripped)
    if is_safe_empirica_command(stripped):
        return True
    if stripped.startswith(("ssh ", "rsync ", "scp ", "ssh-")):
        return is_safe_remote_command(stripped)
    if _matches_safe_prefix(stripped):
        return True

    # 5. Inert shell shapes (control-flow keywords, tests, assignments,
    # exit/return). All embedded commands have already been validated
    # in step 1; this only excuses the structural shell text.
    return _is_inert_shape(stripped)


def _is_command_text_safe(cmd: str) -> bool:
    """Validate a command text (used for substitution inner commands).

    Independently checks pipes (via is_safe_pipe_chain) and chains
    (via _is_segment_safe per segment). Mirrors the classification
    is_safe_bash_command applies at the top level.
    """
    if _contains_outside_quotes(cmd, "|"):
        return is_safe_pipe_chain(cmd)
    for chain_op in ("&&", "||", ";"):
        if _contains_outside_quotes(cmd, chain_op):
            segments = [s.strip() for s in _split_outside_quotes(cmd, chain_op)]
            return all(_is_segment_safe(s) for s in segments)
    return _is_segment_safe(cmd)


def _has_dangerous_operators(command: str) -> bool:
    """Check for dangerous shell operators (excluding &&, ||, ; handled in chain check).

    Quoted occurrences are ignored — a backtick or `$(` inside a string
    literal is just text, not command substitution.
    """
    for operator in DANGEROUS_SHELL_OPERATORS:
        if operator in ("&&", "||", ";"):
            continue
        if _contains_outside_quotes(command, operator):
            return True
    return False


def _has_dangerous_redirects(command: str) -> bool:
    """Check for file redirection (dangerous) vs stderr suppression (safe).

    Quote-aware: a `>` or `<` inside a quoted argument (e.g. python3 -c
    "if len(body) > 3000:" or jq '.x > 5') is data, not a redirect. Only
    redirects appearing OUTSIDE quoted regions are flagged.
    """
    cmd_clean = SAFE_REDIRECT_PATTERN.sub("", command)
    if _contains_outside_quotes(cmd_clean, ">>") or _contains_outside_quotes(cmd_clean, ">"):
        return True
    return _contains_outside_quotes(cmd_clean, "<") and "<<" not in command


def _maybe_nudge_remote_ops(cmd: str) -> None:
    """Set remote-ops nudge if work_type isn't already remote-ops or infra."""
    global _remote_ops_nudge, _remote_ops_nudged
    if _remote_ops_nudged or _current_work_type in ("remote-ops", "infra", "config"):
        return
    _remote_ops_nudged = True
    _remote_ops_nudge = (
        "REMOTE-OPS: SSH/rsync/scp detected but work_type is "
        f"'{_current_work_type or 'not set'}'. Consider setting "
        "work_type=remote-ops in PREFLIGHT if this is remote work — "
        "local sensors can't observe it, so calibration will use "
        "ungrounded_remote_ops status and self-assessment stands."
    )


def _classify_chain(command: str) -> bool | None:
    """Classify a multi-segment shell chain.

    Returns True/False if `command` is a chain joined by &&/||/;/newline
    (outside quotes) — True iff EVERY segment is safe. Returns None when it
    isn't a chain (caller continues with single-command classification).

    Newline counts as a separator (a multi-line payload of planning verbs is a
    chain), EXCEPT when a heredoc (`<<`) is present — its body legitimately
    spans lines, and splitting on those newlines would shred it.
    """
    chain_ops: tuple[str, ...] = ("&&", "||", ";")
    if "<<" not in command:
        chain_ops = (*chain_ops, "\n")
    for chain_op in chain_ops:
        if _contains_outside_quotes(command, chain_op):
            segments = [s.strip() for s in _split_outside_quotes(command, chain_op)]
            return all(_is_segment_safe(s) for s in segments)
    return None


def is_safe_bash_command(tool_input: dict) -> bool:
    """Check if a Bash command is in the safe (noetic) whitelist.

    When work_type is infra/config/debug, expands the whitelist with
    system inspection commands (docker, systemctl, ss, tmux, etc.).
    """
    global _current_work_type
    command = tool_input.get("command", "")
    if not command:
        return False

    # Chain commands (&&, ||, ;, newline) — safe ONLY if ALL segments are
    # safe. MUST run before the single-command shortcuts, otherwise
    # `empirica goals-list && rm -rf /` slips through on the leading safe
    # prefix. See _classify_chain (handles the heredoc + newline nuances).
    chain_result = _classify_chain(command)
    if chain_result is not None:
        return chain_result

    # Single command. A trailing pipe can smuggle an executor
    # (`empirica goals-list | sh`), so a piped command is NOT safe on the bare
    # empirica-prefix match — it goes through the pipe-chain check below.
    if not _contains_outside_quotes(command, "|") and is_safe_empirica_command(command):
        return True

    # Work-type expansion: infra/config/debug/remote-ops get broader safe
    # commands. remote-ops added here so system inspection (docker, systemctl,
    # ss, tmux) flows for a remote-ops AI that's inspecting locally before/
    # after SSH-recon. The SSH branch below is the load-bearing relaxation.
    if _current_work_type in ("infra", "config", "debug", "remote-ops"):
        cmd = command.lstrip()
        if any(cmd.startswith(prefix) for prefix in INFRA_SAFE_PREFIXES):
            return True

    # Under work_type=remote-ops, SSH/rsync/scp pass wholesale — the AI's
    # PREFLIGHT declaration IS the gate, since local sensors can't observe
    # the remote box (calibration is already ungrounded_remote_ops). This
    # MUST run before the dangerous_operators/redirects checks below: real
    # recon often uses stdin redirects (ssh host 'cmd' < script.sh) which
    # the per-command classifier rejects. Local writes (cat > /tmp/foo)
    # stay subject to normal gating — those ARE observable.
    if _current_work_type == "remote-ops":
        rcmd = command.lstrip()
        if rcmd.startswith(("ssh ", "rsync ", "scp ", "ssh-")):
            _maybe_nudge_remote_ops(rcmd)
            return True

    if _has_dangerous_operators(command):
        return False

    if _has_dangerous_redirects(command):
        return False

    if _contains_outside_quotes(command, "|"):
        return is_safe_pipe_chain(command)

    cmd = command.lstrip()

    # Special cases: remote, sqlite, python
    if cmd.startswith(("ssh ", "rsync ", "scp ", "ssh-")):
        _maybe_nudge_remote_ops(cmd)
        return is_safe_remote_command(cmd)
    if cmd.startswith("sqlite3 ") and is_safe_sqlite_command(cmd):
        return True
    if cmd.startswith(("python3 -c ", "python -c ")) and is_safe_python_command(cmd):
        return True

    return _matches_safe_prefix(cmd)


def is_safe_sqlite_command(command: str) -> bool:
    """
    Check if a sqlite3 command is read-only (noetic).

    Allows:
    - sqlite3 db ".schema", ".tables", ".dump" (meta commands)
    - sqlite3 db "SELECT ..." (read queries)
    - sqlite3 db "PRAGMA ..." (read pragmas)

    Blocks:
    - sqlite3 db "INSERT/UPDATE/DELETE/DROP/CREATE/ALTER ..."
    """
    import re

    # Extract the SQL/command part (everything after db path in quotes)
    # Pattern: sqlite3 <db_path> "<query>" or sqlite3 <db_path> '<query>'
    # Also handles: sqlite3 <db_path> ".tables" (dot commands)
    match = re.search(r'sqlite3\s+\S+\s+["\'](.+?)["\']', command)
    if not match:
        # No quoted query found - could be interactive mode, block it
        return False

    query = match.group(1).strip().upper()

    # Safe meta commands (dot commands)
    safe_meta = (
        ".SCHEMA",
        ".TABLES",
        ".DUMP",
        ".INDICES",
        ".INDEXES",
        ".MODE",
        ".HEADERS",
        ".WIDTH",
        ".HELP",
        ".DATABASES",
    )
    for meta in safe_meta:
        if query.startswith(meta):
            return True

    # Safe SQL operations (read-only)
    safe_sql = ("SELECT", "PRAGMA", "EXPLAIN", "ANALYZE")
    return any(query.startswith(sql) for sql in safe_sql)


def is_safe_python_command(command: str) -> bool:
    """
    Check if a python3 -c command is read-only (noetic).

    Allows:
    - Read-only DB queries (import, SELECT, fetchall, print)
    - Data analysis, JSON parsing, aggregation
    - Imports from empirica for read-only operations

    Blocks:
    - File writes (open(..., 'w'), Path.write_text, shutil)
    - Subprocess calls (subprocess.run, os.system, os.popen)
    - File deletion (os.remove, os.unlink, shutil.rmtree)
    - Network writes (requests.post, requests.put, requests.delete)
    """
    # Extract the Python code from the command
    # Handles: python3 -c "code" and python3 -c 'code'
    code = command
    for prefix in ("python3 -c ", "python -c "):
        if command.startswith(prefix):
            code = command[len(prefix) :]
            break

    # Strip outer quotes
    code_stripped = code.strip()
    if (code_stripped.startswith('"') and code_stripped.endswith('"')) or (
        code_stripped.startswith("'") and code_stripped.endswith("'")
    ):
        code_stripped = code_stripped[1:-1]

    code_upper = code_stripped.upper()

    # Block patterns: file writes, subprocess, deletion, network mutation
    write_patterns = (
        # File write operations
        "OPEN(",
        ".WRITE(",
        ".WRITE_TEXT(",
        ".WRITE_BYTES(",
        "SHUTIL.",
        "OS.REMOVE(",
        "OS.UNLINK(",
        "OS.RMDIR(",
        "OS.MAKEDIRS(",
        "OS.MKDIR(",
        # Subprocess / shell execution
        "SUBPROCESS.RUN(",
        "SUBPROCESS.CALL(",
        "SUBPROCESS.POPEN(",
        "OS.SYSTEM(",
        "OS.POPEN(",
        "OS.EXEC",
        # Network mutation
        "REQUESTS.POST(",
        "REQUESTS.PUT(",
        "REQUESTS.DELETE(",
        "REQUESTS.PATCH(",
        ".POST(",
        ".PUT(",
        ".DELETE(",
        ".PATCH(",
        # Database writes
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "DROP ",
        "CREATE ",
        "ALTER ",
        # Dangerous builtins
        "EXEC(",
        "EVAL(",
        "__IMPORT__(",
    )

    # Allow: anything that's not writing is investigation
    return all(pattern not in code_upper for pattern in write_patterns)


def is_safe_remote_command(command: str) -> bool:
    """
    Classify remote commands (ssh, rsync, scp) as noetic or praxic.

    Remote commands need their own classification logic because:
    - ssh wraps an arbitrary remote command that may be read-only or destructive
    - rsync/scp direction determines whether it's reading or writing
    - A blanket allow/deny for SSH is too coarse

    Returns True if the remote command is noetic (safe/read-only).

    Classification:
    - ssh user@host "ls /path"        → noetic (reading remotely)
    - ssh user@host "docker ps"       → noetic (inspecting)
    - ssh user@host "git push ..."    → praxic (writing remotely)
    - ssh user@host (no command)      → noetic (interactive session / investigation)
    - rsync --dry-run ...             → noetic
    - rsync src/ server:/path         → praxic (uploading)
    - rsync server:/path local/       → noetic (downloading)
    - scp file server:/path           → praxic (uploading)
    - scp server:/path file           → noetic (downloading)
    - ssh-copy-id                     → praxic (modifying remote authorized_keys)
    - ssh-add, ssh-keygen             → local operations, allowed
    """
    command_stripped = command.lstrip()

    # --- ssh-add, ssh-keygen, ssh-agent: local key management, always safe ---
    if command_stripped.startswith(("ssh-add", "ssh-keygen", "ssh-agent", "ssh -T")):
        return True

    # --- ssh-copy-id: modifies remote, always praxic ---
    if command_stripped.startswith("ssh-copy-id"):
        return False

    # --- scp: check transfer direction ---
    if command_stripped.startswith("scp "):
        return _classify_scp(command_stripped)

    # --- rsync: check direction and flags ---
    if command_stripped.startswith("rsync "):
        return _classify_rsync(command_stripped)

    # --- ssh: extract and classify the inner command ---
    if command_stripped.startswith("ssh "):
        return _classify_ssh(command_stripped)

    return False  # Unknown remote command type


def _classify_ssh(command: str) -> bool:
    """
    Extract the remote command from an SSH invocation and classify it.

    SSH format: ssh [options] [user@]host [command...]
    Options that take arguments: -B -b -c -D -E -e -F -I -i -J -L -l -m -O -o -p -R -S -W -w
    """
    # Handle heredoc-style SSH: ssh user@host << 'EOF' ... EOF
    # These are complex multi-command blocks — treat as praxic
    if "<<" in command:
        # Extract the heredoc content and classify each line
        return _classify_ssh_heredoc(command)

    parts = command.split()
    if len(parts) < 2:
        return True  # Just 'ssh' alone, harmless

    # SSH options that consume the NEXT argument
    ssh_opts_with_arg = set("BbcDEeFIiJLlmOopRSWw")

    i = 1  # Skip 'ssh'
    skip_next = False
    host_found = False
    remote_cmd_parts = []

    for i in range(1, len(parts)):
        part = parts[i]

        if skip_next:
            skip_next = False
            continue

        # ConnectTimeout and similar -o options
        if part.startswith("-o"):
            if part == "-o":
                skip_next = True  # -o Option=Value
            # else: -oOption=Value (combined)
            continue

        # Options with arguments: -p 22, -i ~/.ssh/key, etc.
        if part.startswith("-") and len(part) >= 2:
            opt_char = part[1]
            if opt_char in ssh_opts_with_arg:
                if len(part) == 2:
                    skip_next = True  # Arg is next word
                # else: -p22 (combined), no skip
            # Flags without args: -A, -v, -N, -T, etc.
            continue

        if not host_found:
            host_found = True
            continue  # This is the hostname

        # Everything after hostname is the remote command
        remote_cmd_parts = parts[i:]
        break

    if not remote_cmd_parts:
        return True  # No remote command = interactive session (noetic investigation)

    # Reconstruct the remote command
    # Handle quoted strings: ssh host "ls -la && echo done"
    # The shell already split on spaces, so we rejoin
    remote_cmd = " ".join(remote_cmd_parts)

    # Strip surrounding quotes if present
    if (remote_cmd.startswith('"') and remote_cmd.endswith('"')) or (
        remote_cmd.startswith("'") and remote_cmd.endswith("'")
    ):
        remote_cmd = remote_cmd[1:-1]

    # Now classify the remote command using the same logic as local commands
    return _is_remote_cmd_safe(remote_cmd)


def _classify_ssh_heredoc(command: str) -> bool:
    """
    Classify an SSH command that uses a heredoc for its remote commands.

    Format: ssh user@host 'cmd1 && cmd2 && ...'
    Or:     ssh user@host << 'EOF'
            cmd1
            cmd2
            EOF

    Strategy: Extract each command line and check all are safe.
    If we can't parse it reliably, default to praxic (conservative).
    """
    # For heredoc-in-command (the heredoc content is in the command string),
    # try to extract the content between the delimiters
    heredoc_match = re.search(r"<<\s*'?(\w+)'?\s*\n(.*?)\n\1", command, re.DOTALL)
    if heredoc_match:
        heredoc_content = heredoc_match.group(2)
        lines = [l.strip() for l in heredoc_content.strip().split("\n") if l.strip()]
        return all(_is_remote_cmd_safe(line) for line in lines)

    # Can't parse heredoc content (probably not in the command string yet)
    # Conservative: treat as praxic
    return False


def _is_remote_cmd_safe(remote_cmd: str) -> bool:
    """
    Classify a remote command string as noetic or praxic.
    Uses the same SAFE_BASH_PREFIXES logic as local commands,
    plus handles chains (&&, ||) within the remote command.
    """
    remote_cmd = remote_cmd.strip()
    if not remote_cmd:
        return True

    # Handle chains within the remote command: cmd1 && cmd2 && cmd3
    # (split outside quotes — `;` etc. inside a quoted string is not a chain op)
    for chain_op in ("&&", "||", ";"):
        if _contains_outside_quotes(remote_cmd, chain_op):
            segments = [s.strip() for s in _split_outside_quotes(remote_cmd, chain_op)]
            return all(_is_single_remote_cmd_safe(seg) for seg in segments if seg)

    # Handle pipes within the remote command
    if _contains_outside_quotes(remote_cmd, "|"):
        segments = [s.strip() for s in _split_outside_quotes(remote_cmd, "|")]
        if not segments:
            return False
        # First segment must be safe, rest must be safe pipe targets
        if not _is_single_remote_cmd_safe(segments[0]):
            return False
        for seg in segments[1:]:
            seg = seg.strip()
            if not any(seg.startswith(t) for t in SAFE_PIPE_TARGETS):
                if not _is_single_remote_cmd_safe(seg):
                    return False
        return True

    return _is_single_remote_cmd_safe(remote_cmd)


def _is_single_remote_cmd_safe(cmd: str) -> bool:
    """Check a single remote command against SAFE_BASH_PREFIXES."""
    cmd = cmd.strip()
    if not cmd:
        return True

    # Strip safe redirects
    cmd_clean = SAFE_REDIRECT_PATTERN.sub("", cmd).strip()

    # cd is always safe
    if cmd_clean.startswith("cd "):
        return True

    # Docker inspection commands (common in remote infra work)
    docker_safe = (
        "docker ps",
        "docker images",
        "docker logs",
        "docker inspect",
        "docker stats",
        "docker top",
        "docker port",
        "docker diff",
        "docker info",
        "docker version",
        "docker network ls",
        "docker network inspect",
        "docker volume ls",
        "docker volume inspect",
        "docker compose ps",
        "docker compose logs",
        "docker-compose ps",
        "docker-compose logs",
    )
    for prefix in docker_safe:
        if cmd_clean.startswith(prefix):
            return True

    # systemctl status/is-active (read-only)
    if cmd_clean.startswith(("systemctl status", "systemctl is-active", "systemctl list-")):
        return True

    # journalctl (log reading)
    if cmd_clean.startswith("journalctl"):
        return True

    # Check standard SAFE_BASH_PREFIXES
    for prefix in SAFE_BASH_PREFIXES:
        if cmd_clean.startswith(prefix) or (prefix.endswith(" ") and cmd_clean == prefix.rstrip()):
            return True

    return False


def _classify_rsync(command: str) -> bool:
    """
    Classify rsync as noetic or praxic based on direction and flags.

    Noetic: --dry-run/-n, downloading (remote→local)
    Praxic: uploading (local→remote), --delete
    """
    parts = command.split()

    # --dry-run or -n → always noetic (just showing what would happen)
    if "--dry-run" in parts or "-n" in parts:
        return True

    # --delete is always destructive → praxic
    if "--delete" in parts or "--delete-before" in parts or "--delete-after" in parts:
        return False

    # Determine direction by finding src and dest arguments
    # rsync [options] source... dest
    # Remote paths contain ':' (user@host:/path or host:/path)
    # Skip option arguments
    rsync_opts_with_arg = set("efi")  # Common opts that take next arg
    non_option_args = []
    skip_next = False

    for _i, part in enumerate(parts[1:], 1):
        if skip_next:
            skip_next = False
            continue
        if part.startswith("--"):
            if "=" not in part and part in (
                "--rsh",
                "--filter",
                "--exclude",
                "--include",
                "--exclude-from",
                "--include-from",
                "--files-from",
                "--log-file",
                "--out-format",
                "--backup-dir",
                "--suffix",
                "--compare-dest",
                "--copy-dest",
                "--link-dest",
                "--compress-level",
                "--skip-compress",
                "--max-size",
                "--min-size",
                "--timeout",
                "--contimeout",
                "--address",
                "--port",
                "--sockopts",
                "--outbuf",
                "--remote-option",
                "--info",
                "--debug",
                "--chmod",
                "--chown",
                "--groupmap",
                "--usermap",
            ):
                skip_next = True
            continue
        if part.startswith("-") and not part.startswith("--"):
            # Short options, check if any consume next arg
            opt_chars = part[1:]
            if opt_chars and opt_chars[-1] in rsync_opts_with_arg:
                skip_next = True
            continue
        non_option_args.append(part)

    if len(non_option_args) < 2:
        return False  # Can't determine direction, conservative

    # Last non-option arg is destination
    dest = non_option_args[-1]
    sources = non_option_args[:-1]

    # If destination has ':' → uploading → praxic
    if ":" in dest and not dest.startswith("/"):
        return False

    # If any source has ':' and dest is local → downloading → noetic
    # Both local (or can't tell) → praxic (conservative)
    return any(":" in src and not src.startswith("/") for src in sources)


def _classify_scp(command: str) -> bool:
    """
    Classify scp as noetic or praxic based on transfer direction.

    Noetic: downloading (remote→local)
    Praxic: uploading (local→remote)
    """
    parts = command.split()

    # SCP options that consume next argument
    scp_opts_with_arg = set("cFiloPSs")
    non_option_args = []
    skip_next = False

    for part in parts[1:]:
        if skip_next:
            skip_next = False
            continue
        if part.startswith("-") and len(part) >= 2:
            opt_char = part[1]
            if opt_char in scp_opts_with_arg and len(part) == 2:
                skip_next = True
            continue
        non_option_args.append(part)

    if len(non_option_args) < 2:
        return False  # Can't determine direction

    # Last arg is destination
    dest = non_option_args[-1]

    # If destination contains ':' (and isn't an absolute path) → uploading → praxic
    # Otherwise → downloading or local copy → noetic
    return not (":" in dest and not dest.startswith("/"))


def is_safe_pipe_chain(command: str) -> bool:
    """
    Check if a piped command chain is safe (all segments are read-only).

    Allows: grep pattern file | head -20 | wc -l
    Allows: echo '...' | empirica preflight-submit -  (empirica CLI)
    Allows: grep "A\\|B\\|C" file | head      (quoted | inside regex)
    Blocks: grep pattern | xargs rm, cat file | bash

    Splits on `|` *outside* quoted regions — a `|` inside a quoted regex
    alternation is data, not a shell pipe.
    """
    segments = [s.strip() for s in _split_outside_quotes(command, "|")]

    if not segments:
        return False

    # First segment must be a safe command
    first_cmd = segments[0]
    first_is_safe = False

    # Check sqlite3 commands first
    if first_cmd.startswith("sqlite3 ") and is_safe_sqlite_command(first_cmd):
        first_is_safe = True

    # Check empirica CLI whitelist — Tier 1 commands (loop status, goals-list,
    # etc.) routinely produce JSON that gets piped to jq. The trailing-segment
    # rule below already accepts is_safe_empirica_command; matching it for the
    # first segment removes the asymmetry that blocked cron-body shell idioms.
    if not first_is_safe and is_safe_empirica_command(first_cmd):
        first_is_safe = True

    # Check standard safe prefixes
    if not first_is_safe:
        for prefix in SAFE_BASH_PREFIXES:
            if first_cmd.startswith(prefix) or (prefix.endswith(" ") and first_cmd == prefix.rstrip()):
                first_is_safe = True
                break

    if not first_is_safe:
        return False

    # All subsequent segments must start with safe pipe targets OR be safe empirica commands
    for segment in segments[1:]:
        segment = segment.strip()
        # Strip heredoc suffix for matching (e.g., "empirica preflight-submit - << 'EOF'")
        segment_clean = segment.split("<<")[0].strip() if "<<" in segment else segment
        segment_safe = False

        # Check empirica CLI whitelist (tiered)
        if is_safe_empirica_command(segment_clean):
            segment_safe = True

        # Check standard safe pipe targets
        if not segment_safe:
            for target in SAFE_PIPE_TARGETS:
                if segment.startswith(target):
                    segment_safe = True
                    break

        if not segment_safe:
            return False

    return True


# --- Confidence Gate for Remote Commands ---
# Lightweight threshold check for praxic remote work (SSH writes, scp uploads).
# Replaces full PREFLIGHT/POSTFLIGHT for remote infra where grounded verification
# can't see the evidence. Thresholds match confidence_gate.py in empirica-autonomy.

_CONFIDENCE_GATE_THRESHOLDS = {
    "remote_infra": {"know_min": 0.70, "uncertainty_max": 0.25},
}


def _is_praxic_remote_command(command: str) -> bool:
    """Check if a command is a praxic (write) remote command.

    Returns True for SSH commands that modify remote state.
    Read-only remote commands are already handled by is_safe_remote_command().
    """
    cmd = command.lstrip()
    if not cmd.startswith(("ssh ", "scp ", "rsync ")):
        return False
    # If is_safe_remote_command says it's noetic, it's not praxic
    # It's a remote command that's NOT read-only → praxic remote
    return not is_safe_remote_command(cmd)


def _confidence_gate_remote(claude_session_id: str | None = None) -> str:
    """Apply ConfidenceGate threshold check using latest vectors.

    Reads the most recent PREFLIGHT or CHECK vectors from the session DB.
    Returns a description string if gate passes, or empty string if fails.
    """
    thresholds = _CONFIDENCE_GATE_THRESHOLDS["remote_infra"]

    # Find vectors from the most recent assessment in this session
    try:
        empirica_session_id = _resolve_empirica_session_id(claude_session_id)
        if not empirica_session_id:
            return ""

        pp = get_active_project_path(claude_session_id)
        if not pp:
            return ""

        db_path = Path(pp) / ".empirica" / "sessions.db"
        if not db_path.exists():
            # Try home fallback
            db_path = Path.home() / ".empirica" / "sessions.db"
        if not db_path.exists():
            return ""

        import sqlite3

        db = sqlite3.connect(str(db_path))
        cursor = db.cursor()

        # Get latest vectors from PREFLIGHT or CHECK
        cursor.execute(
            """
            SELECT phase,
                   json_extract(reflex_data, '$.vectors.know') as know,
                   json_extract(reflex_data, '$.vectors.uncertainty') as uncertainty
            FROM reflexes
            WHERE session_id = ? AND phase IN ('PREFLIGHT', 'CHECK')
            ORDER BY timestamp DESC LIMIT 1
        """,
            (empirica_session_id,),
        )
        row = cursor.fetchone()
        db.close()

        if not row:
            return ""

        phase, know, uncertainty = row
        know = float(know) if know else 0.0
        uncertainty = float(uncertainty) if uncertainty else 1.0

        # Gate uses META UNCERTAINTY ONLY (2026-04-07).
        # Uncertainty is the unified confidence summary across all 12 other
        # vectors. The 'know' threshold is no longer evaluated as a gating
        # condition — it remains in the thresholds dict for back-compat with
        # consumers that read it for display.
        if uncertainty <= thresholds["uncertainty_max"]:
            return f"unc={uncertainty:.2f}<={thresholds['uncertainty_max']}, from {phase}"
        return ""

    except Exception:
        return ""  # Fail-closed: if we can't read vectors, require normal gating


def _noetic_firewall_check(tool_name: str, tool_input: dict, hook_input: dict) -> tuple | None:
    """Check if a tool invocation is noetic (read/investigate) and should be allowed.

    Returns (True, message) if the tool is noetic and should be allowed,
    or None if the tool is not noetic (caller continues with praxic gating).
    """
    # Rule 1: Noetic tools always allowed (read/investigate)
    if (
        tool_name in NOETIC_TOOLS
        or tool_name in NOETIC_MCP_CHROME
        or tool_name in NOETIC_MCP_CORTEX
        or _is_empirica_mcp_tool(tool_name)
    ):
        return (True, f"Noetic tool: {tool_name}")

    # Rule 2: Safe Bash commands always allowed (read-only shell)
    if tool_name == "Bash" and is_safe_bash_command(tool_input):
        return (True, "Safe Bash (read-only)")

    # Rule 2b: Plan file writes are noetic (planning is investigation, not execution)
    # Claude Code writes plan files to ~/.claude/plans/ during plan mode.
    # These should be allowed without CHECK since planning is inherently noetic work.
    if tool_name in ("Write", "Edit") and is_plan_file(tool_input):
        return (True, f"Plan file write (noetic): {tool_name}")

    # Rule 2c: CONFIDENCE GATE for praxic remote commands (SSH writes, scp uploads, etc.)
    # Remote infra work doesn't produce local evidence for grounded verification,
    # so full PREFLIGHT/POSTFLIGHT is meaningless. Instead, apply lightweight
    # threshold check against latest vectors. No transaction overhead.
    if tool_name == "Bash" and tool_input:
        command = tool_input.get("command", "")
        if command and _is_praxic_remote_command(command):
            gate_result = _confidence_gate_remote(hook_input.get("session_id"))
            if gate_result:
                return (True, f"ConfidenceGate: remote infra ({gate_result})")
            # If gate fails, fall through to normal praxic gating
            # (user needs PREFLIGHT or higher confidence)

    return None


def _in_linked_git_worktree() -> bool:
    """True if CWD is inside a LINKED git worktree (vs the main checkout).

    A linked worktree (`git worktree add`) marks its root with a `.git` *file*
    (`gitdir: …/worktrees/…`), whereas the main checkout has a `.git` *directory*.
    Pure filesystem stat — no subprocess — so it's cheap enough for the per-tool
    PreToolUse path. Used as a worktree-aware subagent signal: isolation:worktree
    subagents run here; the real practitioner runs in the main checkout.
    """
    try:
        cwd = Path.cwd()
        for d in [cwd, *cwd.parents]:
            g = d / ".git"
            if g.exists():
                return g.is_file()  # file → linked worktree; dir → main checkout
    except Exception:
        pass
    return False


def _detect_subagent(claude_session_id: str) -> bool:
    """Detect if the current invocation is from a subagent.

    Subagents don't need their own CASCADE — the parent's CHECK already
    authorized the spawn. Subagents have a different Claude session_id
    than the parent (who owns the active_work file).

    Detection priority (post-fix for #95 Issue 1):
      1. active_work_{claude_session_id}.json exists with `is_subagent: true`
         → confirmed subagent (flag-based, written by SubagentStart hook).
      2. active_work missing → fallback to absence-detection via active_session
         match (back-compat for in-flight subagents pre-dating the fix, and
         for the broken-session-init failure mode).

    Why the flag exists: subagent-start.py now writes active_work for the
    subagent so its empirica CLI calls resolve to child_session_id (not
    parent's via TTY fallback). That means active_work is no longer
    reliably absent for subagents. The flag carries the signal explicitly.

    Returns True if this is a confirmed subagent invocation.
    """
    try:
        _aw_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
        if _aw_file.exists():
            # Path 1: flag-based detection (post-fix subagents)
            try:
                with open(_aw_file) as _awf:
                    _aw_data = json.load(_awf)
                if _aw_data.get("is_subagent") is True:
                    return True
            except Exception:
                pass  # corrupt file → fall through to absence path
            # File exists but no is_subagent flag → parent session
            return False

        # Path 2: absence-based fallback (pre-fix subagents, broken session-init).
        # No active_work file for this claude_session_id — likely a subagent
        # (or session-init failed / project initialized mid-session).
        #
        # Worktree-aware signal (ecodex prop_3dih #3, David architectural flag):
        # isolation:worktree subagents run in a LINKED git worktree where the
        # active_work lookup above fails — without this they fall through and risk
        # mis-detection as the PARENT (then get measured, polluting parent state).
        # Safe: the real practitioner runs in the main checkout and has its
        # active_work (Path 1), so only genuine subagents reach this Path-2 branch.
        if _in_linked_git_worktree():
            return True

        # TIGHTENED CHECK (fixes #68): Don't just check if active_session exists —
        # verify its session matches the current transaction. Stale active_session
        # files from other projects/sessions cause false positive subagent detection.
        from empirica.utils.session_resolver import InstanceResolver as R

        _as_suffix = R.instance_suffix()
        _as_file = Path.home() / ".empirica" / f"active_session{_as_suffix}"
        if _as_file.exists():
            # Read the active_session to get its empirica_session_id
            try:
                with open(_as_file) as _asf:
                    _as_data = json.load(_asf)
                _as_session_id = _as_data.get("empirica_session_id")

                # Find the current transaction to compare session IDs
                _tx_session_match = False
                if _as_session_id:
                    # Check if any active_work file has this session
                    for _aw_candidate in Path.home().glob(".empirica/active_work_*.json"):
                        try:
                            with open(_aw_candidate) as _awf:
                                _aw_data = json.load(_awf)
                            if _aw_data.get("empirica_session_id") == _as_session_id:
                                _tx_session_match = True
                                break
                        except Exception:
                            continue

                if _tx_session_match:
                    # Parent session is active AND has a matching active_work file
                    # This session doesn't → confirmed subagent
                    return True
            except Exception:
                pass  # Can't read active_session → not confident it's a subagent
        # Not a confirmed subagent → fall through to normal gating
        # (covers: broken session-init, mid-session project init, stale files)
    except Exception:
        pass  # Detection failure → continue with normal sentinel logic
    return False


def _check_postflight_loop_closed(
    cursor, session_id: str, current_transaction_id: str | None, preflight_timestamp, tool_name: str, tool_input: dict
) -> tuple | None:
    """Check if the epistemic loop is closed (POSTFLIGHT exists after PREFLIGHT).

    Returns (status, message) if the loop is closed and a decision was made,
    or None if no POSTFLIGHT found or timestamps can't be compared (caller continues).
    """
    # Scope by transaction_id to prevent cross-instance bleed (multiple Claudes sharing session)
    if current_transaction_id:
        cursor.execute(
            """
            SELECT timestamp FROM reflexes
            WHERE session_id = ? AND phase = 'POSTFLIGHT' AND transaction_id = ?
            ORDER BY timestamp DESC LIMIT 1
        """,
            (session_id, current_transaction_id),
        )
    else:
        cursor.execute(
            """
            SELECT timestamp FROM reflexes
            WHERE session_id = ? AND phase = 'POSTFLIGHT'
            ORDER BY timestamp DESC LIMIT 1
        """,
            (session_id,),
        )
    postflight_row = cursor.fetchone()

    if not postflight_row:
        return None

    postflight_timestamp = postflight_row[0]
    try:
        preflight_ts = float(preflight_timestamp)
        postflight_ts = float(postflight_timestamp)

        if postflight_ts > preflight_ts:
            # Loop closed. Only block truly praxic operations (file modification).
            # Allow read-only, empirica workflow, toggles, and transitions.
            # This enables artifact lifecycle between transactions:
            # goals-list, goals-complete, unknown-resolve, finding-log, etc.
            if tool_name == "Bash":
                command = tool_input.get("command", "")

                # Safe Bash (read-only + empirica workflow) — always allowed
                # This is a safety net: Rule 2 should catch most of these,
                # but edge cases (|| chains, complex pipes) may reach here.
                if is_safe_bash_command(tool_input):
                    return ("allow", "Safe Bash between transactions (artifact lifecycle)")

                # Toggle commands (pause/unpause)
                toggle_action = is_toggle_command(command)
                if toggle_action == "pause":
                    return ("allow", "Sentinel self-exemption: pause toggle (loop closed)")
                elif toggle_action == "unpause":
                    return ("allow", "Sentinel self-exemption: unpause toggle")

                # Transition commands (cd, session-create, project-bootstrap)
                if is_transition_command(command):
                    return ("allow", "Transition command (starting new cycle)")

                # Empirica CLI commands (Tier 1 read-only + Tier 2 artifact lifecycle).
                # The doc comment above says this should be allowed —
                # "goals-list, goals-complete, unknown-resolve, finding-log, etc." —
                # but the prior code only checked is_safe_bash_command, which doesn't
                # cover `empirica help`, `empirica goals-list`, etc. Honor the intent.
                if is_safe_empirica_command(command):
                    return ("allow", "Empirica command between transactions (artifact lifecycle / read-only)")

            return (
                "deny",
                "Epistemic loop closed (POSTFLIGHT completed). Run new PREFLIGHT to start next goal. Command: empirica preflight-submit - (JSON with vectors on stdin)",
            )
    except (ValueError, TypeError):
        pass  # If timestamps can't be compared, continue with other checks

    return None


def _validate_check_record(
    cursor,
    session_id: str,
    current_transaction_id,
    preflight_timestamp,
    tool_input: dict | None = None,
    tool_name: str = "",
):
    """Lookup CHECK record, verify sequence, detect rushed assessments.

    Returns (know, uncertainty, decision, check_timestamp) on success,
    or ("deny", message) tuple on failure.
    """
    # ── RECOVERY ESCAPE HATCH (ecodex prop_3dih #1, David-flagged) ──────────
    # A firewall must NEVER gate its own escape hatch. Empirica's discipline /
    # recovery verbs (check/postflight-submit, *-log, note, doctor) + noetic
    # tools must ALWAYS pass — regardless of CHECK / rush / stuck state. The
    # safe-command escapes below live only in the no-CHECK-row branch; without
    # this hoist, a rushed assessment (short noetic + 0 artifacts) makes EVERY
    # praxic call — INCLUDING the postflight that would clear it — deny "Rushed
    # assessment", an unrecoverable deadlock. Covers both the no-CHECK-row and
    # the has-CHECK-row (rush) paths.
    if (
        tool_name in NOETIC_TOOLS
        or tool_name in NOETIC_MCP_CHROME
        or tool_name in NOETIC_MCP_CORTEX
        or _is_empirica_mcp_tool(tool_name)
        or (
            tool_name == "Bash"
            and tool_input
            and (is_safe_bash_command(tool_input) or is_safe_empirica_command(tool_input.get("command", "")))
        )
    ):
        return None

    if current_transaction_id:
        cursor.execute(
            """
            SELECT know, uncertainty, reflex_data, timestamp
            FROM reflexes WHERE session_id = ? AND phase = 'CHECK' AND transaction_id = ?
            ORDER BY timestamp DESC LIMIT 1
        """,
            (session_id, current_transaction_id),
        )
    else:
        cursor.execute(
            """
            SELECT know, uncertainty, reflex_data, timestamp
            FROM reflexes WHERE session_id = ? AND phase = 'CHECK'
            ORDER BY timestamp DESC LIMIT 1
        """,
            (session_id,),
        )
    check_row = cursor.fetchone()

    if not check_row:
        # PRE-CHECK PHASE: PREFLIGHT submitted, no CHECK yet.
        # This IS the noetic investigation phase. All noetic tools pass
        # silently — no ask, no prompts, no friction. Only genuinely
        # praxic tools (Edit, Write, destructive Bash) get denied.

        # Always allow check-submit (creates the CHECK record)
        if tool_input and "check-submit" in tool_input.get("command", ""):
            return None

        # Noetic tools: silent pass (no message, no logging)
        if (
            tool_name in NOETIC_TOOLS
            or tool_name in NOETIC_MCP_CHROME
            or tool_name in NOETIC_MCP_CORTEX
            or _is_empirica_mcp_tool(tool_name)
        ):
            return None
        if tool_name == "Bash" and is_safe_bash_command(tool_input):
            return None
        if tool_name == "Bash" and is_safe_empirica_command(tool_input.get("command", "")):
            return None

        # Praxic tools: deny (need CHECK first)
        return ("deny", "No valid CHECK found. Run CHECK after investigation to gate the noetic→praxic transition.")

    know, uncertainty, reflex_data, check_timestamp = check_row

    try:
        preflight_ts = float(preflight_timestamp)
        check_ts = float(check_timestamp)

        if check_ts < preflight_ts:
            return (
                "deny",
                "CHECK is from previous transaction (before current PREFLIGHT). Run CHECK to validate readiness.",
            )

        noetic_duration = check_ts - preflight_ts
        min_duration = float(os.getenv("EMPIRICA_MIN_NOETIC_DURATION", "30"))

        if noetic_duration < min_duration:
            cursor.execute(
                """
                SELECT COUNT(*) FROM project_findings
                WHERE session_id = ? AND created_timestamp > ? AND created_timestamp < ?
            """,
                (session_id, preflight_ts, check_ts),
            )
            findings = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT COUNT(*) FROM project_unknowns
                WHERE session_id = ? AND created_timestamp > ? AND created_timestamp < ?
            """,
                (session_id, preflight_ts, check_ts),
            )
            unknowns = cursor.fetchone()[0]
            if findings == 0 and unknowns == 0:
                return ("deny", f"Rushed assessment ({noetic_duration:.0f}s). Investigate and log learnings first.")
    except (TypeError, ValueError):
        pass

    decision = None
    if reflex_data:
        try:
            decision = json.loads(reflex_data).get("decision")
        except Exception:
            pass

    return (know, uncertainty, decision, check_timestamp)


def _check_prior_investigate(
    cursor, session_id: str, current_transaction_id, preflight_timestamp, tool_name: str, tool_input: dict
) -> "tuple | None":
    """Advisory nudge if previous transaction ended with INVESTIGATE and no evidence gathered.

    INVESTIGATE is a suggestion to gather more evidence, not a hard gate.
    The user starting a new PREFLIGHT overrides it. All noetic tools always
    pass through. Praxic tools get a one-time ask (per transaction).
    """
    cursor.execute(
        """
        SELECT json_extract(reflex_data, '$.decision') as decision, transaction_id
        FROM reflexes WHERE session_id = ? AND phase = 'CHECK'
        ORDER BY timestamp DESC LIMIT 1
    """,
        (session_id,),
    )
    prev_check = cursor.fetchone()
    if not prev_check:
        return None

    prev_decision, prev_tx_id = prev_check
    if prev_decision != "investigate" or prev_tx_id == current_transaction_id:
        return None

    # If findings have been logged, INVESTIGATE is satisfied — allow everything
    cursor.execute(
        """
        SELECT COUNT(*) FROM project_findings
        WHERE session_id = ? AND created_timestamp > ?
    """,
        (session_id, preflight_timestamp),
    )
    if (cursor.fetchone()[0] or 0) > 0:
        return None

    # All noetic tools always allowed — INVESTIGATE means "investigate more",
    # not "stop using tools". Read, Grep, Glob, Bash grep/ls/cat, etc.
    if (
        tool_name in NOETIC_TOOLS
        or tool_name in NOETIC_MCP_CHROME
        or tool_name in NOETIC_MCP_CORTEX
        or _is_empirica_mcp_tool(tool_name)
    ):
        return None  # Silent allow — don't even log it as a decision
    if tool_name == "Bash" and is_safe_bash_command(tool_input):
        return None  # Safe Bash is noetic

    # Only genuinely praxic tools (Edit, Write, destructive Bash) get ask
    return ("ask", "Previous CHECK returned INVESTIGATE. Consider running CHECK with proceed before praxic actions.")


def _check_goalless_work(
    cursor, session_id: str, preflight_project_id, claude_session_id, empirica_root, suffix
) -> str:
    """Check if transaction has tool calls but no goals. Returns nudge string or empty."""
    try:
        _gl_count = 0
        if empirica_root:
            _gl_tx_file = _find_transaction_file(empirica_root, suffix, _resolve_empirica_session_id(claude_session_id))
            if _gl_tx_file:
                with open(_gl_tx_file) as _gl_f:
                    _gl_count = json.load(_gl_f).get("tool_call_count", 0)

        if _gl_count < 5:
            return ""

        _gl_project_id = preflight_project_id
        if not _gl_project_id:
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
            _gl_row = cursor.fetchone()
            _gl_project_id = _gl_row[0] if _gl_row else None

        if _gl_project_id:
            cursor.execute(
                """
                SELECT COUNT(*) FROM goals
                WHERE project_id = ? AND status = 'in_progress'
            """,
                (_gl_project_id,),
            )
            if cursor.fetchone()[0] == 0:
                if _gl_count >= 10:
                    return (
                        f"DISCIPLINE: {_gl_count} tool calls with NO GOALS. "
                        f"Create goals now: empirica goals-create --objective '...'. "
                        f"Tell the user: 'We should create goals before continuing — "
                        f"work without goals produces unmeasurable transactions.'"
                    )
                return (
                    f"DISCIPLINE: {_gl_count} tool calls with no goals for this project. "
                    f"Consider creating goals: empirica goals-create --objective '...'"
                )
    except Exception:
        pass
    return ""


def _check_project_context(cursor, db, session_id: str, preflight_project_id) -> "tuple | None":
    """Check if project context changed since PREFLIGHT. Returns (status, msg) or None."""
    current_project_id = _get_current_project_id(db, session_id)
    if not (current_project_id and preflight_project_id and current_project_id != preflight_project_id):
        return None
    cursor.execute(
        """
        SELECT timestamp FROM reflexes
        WHERE session_id = ? AND phase = 'POSTFLIGHT' AND project_id = ?
        ORDER BY timestamp DESC LIMIT 1
    """,
        (session_id, preflight_project_id),
    )
    prev_postflight = cursor.fetchone()
    if prev_postflight:
        return ("deny", "Project context changed. Run PREFLIGHT for new project.")
    return (
        "deny",
        "Project context changed (previous loop unclosed - consider POSTFLIGHT). Run PREFLIGHT for new project.",
    )


def _handle_no_preflight(tool_name: str, tool_input: dict, session_id: str, env_annotation: str) -> tuple:
    """Handle tool calls when no PREFLIGHT exists yet.

    Allows read-only commands and transitions. Tracks pre-transaction tool call count
    and nudges AI to open a transaction. Returns (status, message) tuple.
    """
    pre_tx_nudge = ""
    counter_file = None
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        suffix = R.instance_suffix()
        counter_file = Path.home() / ".empirica" / f"pre_tx_calls{suffix}.json"
        count = 0
        if counter_file.exists():
            with open(counter_file) as f:
                count = json.load(f).get("count", 0)
        count += 1
        with open(counter_file, "w") as f:
            json.dump({"count": count, "session_id": session_id}, f)
        if count >= 10:
            pre_tx_nudge = f" STRONGLY RECOMMENDED: {count} tool calls without a transaction. Submit PREFLIGHT now — this work is unmeasured."
        elif count >= 5:
            pre_tx_nudge = f" NOTE: {count} tool calls without a transaction. Consider submitting PREFLIGHT to begin measured work."
    except Exception:
        pass

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if is_safe_bash_command(tool_input):
            return ("allow", f"Safe Bash before PREFLIGHT (artifact review).{pre_tx_nudge}")
        if is_transition_command(command):
            if "preflight" in command.lower() and counter_file is not None:
                try:
                    counter_file.unlink(missing_ok=True)
                except Exception:
                    pass
            return ("allow", f"Transition command (no PREFLIGHT yet - starting new cycle).{pre_tx_nudge}")

    return (
        "deny",
        f"No open transaction. Submit PREFLIGHT with your self-assessed vectors to begin measured work.{pre_tx_nudge}{env_annotation}",
    )


def _handle_investigate_continuation(
    decision: str, tool_name: str, tool_input: dict, suffix: str, tx_file: Path | None, db
) -> tuple | None:
    """Handle the case where CHECK returned 'investigate'.

    Noetic tools and safe Bash (read-only) are still allowed —
    investigation work needs to investigate (read DBs, run queries, analyze).

    Tracks noetic tool calls since investigate. When AI resubmits CHECK after
    investigate, requires evidence of actual investigation (N noetic tool calls)
    before allowing it. Prevents gaming by resubmitting CHECK with inflated vectors
    without doing real investigation work.

    Returns (status, message) if a decision was made, or None if not in investigate state.
    """
    if decision != "investigate":
        return None

    # INVESTIGATE COOL-DOWN: Track noetic tool calls since investigate.
    # NOTE: noetic_since_investigate is tracked in hook_counters file
    # (hook-owned), not the transaction file (workflow-owned).
    MIN_NOETIC_AFTER_INVESTIGATE = 3

    # Resolve counters file path (co-located with transaction file)
    _inv_counters_path = None
    if tx_file:
        _inv_counters_path = tx_file.parent / f"hook_counters{suffix}.json"

    def _read_inv_counters():
        if not _inv_counters_path or not _inv_counters_path.exists():
            return {}
        try:
            with open(_inv_counters_path) as _f:
                return json.load(_f)
        except Exception:
            return {}

    def _write_inv_counters(data):
        if not _inv_counters_path:
            return
        try:
            import tempfile

            _fd, _tmp = tempfile.mkstemp(dir=str(_inv_counters_path.parent))
            with os.fdopen(_fd, "w") as _tf:
                json.dump(data, _tf, indent=2)
            os.rename(_tmp, str(_inv_counters_path))
        except Exception:
            pass

    if (
        tool_name in NOETIC_TOOLS
        or tool_name in NOETIC_MCP_CHROME
        or tool_name in NOETIC_MCP_CORTEX
        or _is_empirica_mcp_tool(tool_name)
    ):
        # Increment noetic counter in hook counters file
        _inv_c = _read_inv_counters()
        _inv_c["noetic_since_investigate"] = _inv_c.get("noetic_since_investigate", 0) + 1
        _write_inv_counters(_inv_c)
        return ("allow", f"Noetic tool during investigation phase: {tool_name}")
    if tool_name == "Bash" and is_safe_bash_command(tool_input):
        command = tool_input.get("command", "")
        # Block check-submit if insufficient noetic work since investigate
        if "check-submit" in command or "check " in command:
            _inv_c = _read_inv_counters()
            _inv_noetic = _inv_c.get("noetic_since_investigate", 0)
            if _inv_noetic < MIN_NOETIC_AFTER_INVESTIGATE:
                return (
                    "deny",
                    "Previous transaction ended with INVESTIGATE. "
                    "Show evidence of investigation (findings) or submit CHECK with proceed decision.",
                )
        # Increment noetic counter for safe bash (read-only investigation)
        _inv_c = _read_inv_counters()
        _inv_c["noetic_since_investigate"] = _inv_c.get("noetic_since_investigate", 0) + 1
        _write_inv_counters(_inv_c)
        return ("allow", "Safe Bash during investigation phase (read-only)")
    # ADVISORY MODE: Sentinel surfaces the investigate recommendation but lets the AI decide.
    # The AI sees the message and can choose to investigate more or proceed with awareness.
    # This is a measurement system, not a rules-based gate — the holistic judgment is the AI's.
    return (
        "allow",
        "ADVISORY: CHECK returned 'investigate'. Predictions in this domain may be ungrounded. Sentinel recommends noetic (read-only) work to gather grounding evidence before acting.",
    )


def _track_tool_usage(hook_input: dict, tool_name: str, tool_input: dict) -> None:
    """Track tool call counts and re-read advisory nudges.

    Counts PARENT tool calls only (subagent work counted via SubagentStop delegation).
    Nudge thresholds are informational — Claude decides when to POSTFLIGHT.
    Also sets re-read advisory when Read tool targets already-read file.
    """
    global _autonomy_nudge, _reread_nudge
    try:
        _claude_sid = hook_input.get("session_id")
        # Only increment for sessions with active_work (parent sessions).
        # Subagent tool calls are counted from transcript by SubagentStop and
        # added to parent's delegated_tool_calls — no double-counting.
        _aw_check = Path.home() / ".empirica" / f"active_work_{_claude_sid}.json"
        if _claude_sid and _aw_check.exists():
            _count, _avg = _try_increment_tool_count(_claude_sid, tool_name, tool_input)
            _autonomy_nudge = _compute_nudge(_count, _avg)
    except Exception:
        pass  # Counter failure is non-fatal

    # _try_increment_tool_count sets _last_read_count when tracking Read tool calls.
    # Advisory only — never blocks. Helps AI conserve context window.
    if tool_name == "Read" and _last_read_count > 1:
        _rd_fp = (tool_input or {}).get("file_path", "")
        _short = Path(_rd_fp).name if _rd_fp else "file"
        _reread_nudge = f"Re-reading {_short} ({_last_read_count}x this tx). Consider using cached knowledge."


def _set_file_relevance_nudge(tool_name: str, tool_input: dict | None, claude_session_id: str | None) -> None:
    """For Edit/Write/MultiEdit: surface artifacts that already mention the
    target file so the AI sees prior knowledge before overwriting.

    Advisory only. Never raises. Caps at ~50ms via per-table query limits.
    """
    global _file_relevance_nudge
    if tool_name not in ("Edit", "Write", "MultiEdit") or not tool_input:
        return
    fp = tool_input.get("file_path") or ""
    if not fp:
        return

    project_root = resolve_project_root(claude_session_id=claude_session_id)
    if not project_root:
        return

    # Ensure empirica package is importable (the main pipeline does this
    # later, but the file-relevance setter runs early in main()).
    try:
        package_path = find_empirica_package()
        if package_path and str(package_path) not in sys.path:
            sys.path.insert(0, str(package_path))
    except Exception:
        return

    try:
        from empirica.core.file_relevance import (  # type: ignore[import-not-found]
            format_relevance_nudge,
            get_file_relevant_artifacts,
        )
    except ImportError:
        return

    try:
        artifacts = get_file_relevant_artifacts(project_root, fp, limit=5)
        _file_relevance_nudge = format_relevance_nudge(artifacts)
    except Exception:
        # Never let an advisory nudge break the hook
        _file_relevance_nudge = ""


def _check_exemptions(hook_input: dict, tool_name: str) -> tuple | None:
    """Check for praxic tool exemptions: subagent, paused, sentinel disabled.

    Returns (decision, reason) if an exemption applies, or None to continue gating.
    """
    # Rule 3a: SUBAGENT EXEMPTION - subagents bypass gating (parent CHECK authorized spawn)
    claude_session_id = hook_input.get("session_id")
    if claude_session_id and _detect_subagent(claude_session_id):
        return ("allow", f"Subagent exemption: {tool_name} (no active_work for {claude_session_id[:8]})")

    # OFF-RECORD CHECK: If Empirica is paused, allow everything (cheapest check first)
    if is_empirica_paused():
        return ("allow", "Empirica paused (off-record)")

    # Check if sentinel looping is disabled (escape hatch)
    # Priority: file flag > env var (file is dynamically settable, env var requires restart)
    sentinel_flag = Path.home() / ".empirica" / "sentinel_enabled"
    if sentinel_flag.exists():
        flag_val = sentinel_flag.read_text().strip().lower()
        if flag_val == "false":
            return ("allow", "Sentinel disabled (file flag)")
    elif os.getenv("EMPIRICA_SENTINEL_LOOPING", "true").lower() == "false":
        return ("allow", "Sentinel disabled (env var)")

    return None


def _build_env_annotation() -> str:
    """Detect remote/container/CI environments and build annotation string."""
    env_context = detect_environment()
    if not (env_context["is_remote"] or env_context["is_container"] or env_context["is_ci"]):
        return ""
    env_type = "SSH" if env_context["is_remote"] else "container" if env_context["is_container"] else "CI"
    if env_context["is_trusted"]:
        return f" [REMOTE:{env_type}:trusted ({env_context['trust_source']})]"
    return (
        f" [REMOTE:{env_type}:UNTRUSTED — {env_context['trust_source']}. "
        f"Add '{env_context['hostname']}' to ~/.empirica/trusted_hosts to trust this host]"
    )


def _resolve_empirica_root(claude_session_id: str | None) -> Path | None:
    """Resolve .empirica root directory, setting up imports and CWD.

    Returns the empirica_root Path, or None (after responding allow + exit)
    if imports cannot be resolved.
    """
    # Setup imports - find empirica package if not already installed
    package_path = find_empirica_package()
    if package_path:
        sys.path.insert(0, str(package_path))

    # Resolve project root using priority chain (claude_session → transaction → instance → TTY → CWD)
    # This is critical for multi-project scenarios where CWD may be reset
    #
    # NOTE: Do NOT use CWD cross-check here. CWD is unreliable in hooks — Claude Code
    # may reset it after compaction or context shifts (see instance_isolation/KNOWN_ISSUES.md
    # Issue 11.10). The path_resolver's get_session_db_path() has its own CWD cross-check
    # gated behind EMPIRICA_CWD_RELIABLE for CLI commands where CWD IS reliable.
    project_root = resolve_project_root(claude_session_id=claude_session_id)
    if project_root:
        empirica_root = project_root / ".empirica"
        os.chdir(project_root)  # Set CWD to the correct project
        return empirica_root

    # Fallback to path_resolver if priority chain fails
    try:
        from empirica.config.path_resolver import get_empirica_root  # type: ignore[import-not-found]

        empirica_root = get_empirica_root()
        if empirica_root.exists():
            os.chdir(empirica_root.parent)
        return empirica_root
    except ImportError as e:
        respond("allow", f"Cannot import path_resolver: {e}")
        sys.exit(0)


def _read_transaction_state(
    empirica_root: Path | None, claude_session_id: str | None, tool_name: str, tool_input: dict
) -> dict:
    """Read active transaction file and handle closed transactions.

    Returns dict with keys: current_transaction_id, tx_session_id, tx_file,
    suffix, _current_work_type, _current_domain, _current_criticality.

    If the transaction is closed, responds and exits directly (closed-tx short-circuit).
    """
    result = {
        "current_transaction_id": None,
        "tx_session_id": None,
        "tx_file": None,
        "suffix": "",
        "_current_work_type": None,
        "_current_domain": None,
        "_current_criticality": None,
    }

    if not empirica_root:
        return result

    from empirica.utils.session_resolver import InstanceResolver as R

    suffix = R.instance_suffix()
    result["suffix"] = suffix
    empirica_session_id = _resolve_empirica_session_id(claude_session_id)
    tx_file = _find_transaction_file(empirica_root, suffix, empirica_session_id)
    result["tx_file"] = str(tx_file) if tx_file else None

    if not tx_file:
        return result

    try:
        with open(tx_file) as f:
            tx_data = json.load(f)

        # CLOSED TRANSACTION CHECK: Closed transactions persist as project anchors.
        # POSTFLIGHT sets status="closed" but does NOT delete the file.
        # This allows post-compact to resolve the correct project even after
        # the loop closes. The file is overwritten by the next PREFLIGHT.
        # See: docs/architecture/instance_isolation/KNOWN_ISSUES.md
        tx_candidate_session = tx_data.get("session_id")
        _tx_closed = tx_data.get("status") != "open"

        # Only use open transactions for gating; closed ones are just project anchors
        if not _tx_closed:
            result["current_transaction_id"] = tx_data.get("transaction_id")
            result["tx_session_id"] = tx_candidate_session
            # Extract work_type, domain, criticality for domain-aware gating
            result["_current_work_type"] = tx_data.get("work_type")
            result["_current_domain"] = tx_data.get("domain")
            result["_current_criticality"] = tx_data.get("criticality")
            # Set module-level work_type for is_safe_bash_command() expansion
            global _current_work_type, _worktype_nudge, _worktype_nudged
            _current_work_type = result["_current_work_type"]
            # Nudge once if PREFLIGHT omitted work_type
            if not result["_current_work_type"] and not _worktype_nudged:
                _worktype_nudged = True
                _worktype_nudge = (
                    "WORK-TYPE: No work_type set in PREFLIGHT. Consider setting "
                    "work_type (code|infra|research|docs|debug|config|release|remote-ops) "
                    "for better calibration — evidence weights scale by work type."
                )
        else:
            # CLOSED TRANSACTION SHORT-CIRCUIT: Don't fall through to
            # stale session fallback which produces confusing errors
            # like "No valid CHECK found" when the real issue is
            # "loop closed, run new PREFLIGHT".
            _handle_closed_transaction(tool_name, tool_input)
    except Exception:
        pass

    return result


def _handle_closed_transaction(tool_name: str, tool_input: dict) -> None:
    """Handle tool calls against a closed transaction. Responds and exits.

    Allow noetic tools (Read, Grep, Glob, etc.) and safe Bash
    to pass — only block praxic actions.
    """
    if tool_name == "Bash":
        if is_safe_bash_command(tool_input):
            respond("allow", "Safe Bash (transaction closed, artifact lifecycle)")
            sys.exit(0)
        command = tool_input.get("command", "")
        if is_transition_command(command):
            respond("allow", "Transition command (starting new cycle)")
            sys.exit(0)
    elif (
        tool_name in NOETIC_TOOLS
        or tool_name in NOETIC_MCP_CHROME
        or tool_name in NOETIC_MCP_CORTEX
        or _is_empirica_mcp_tool(tool_name)
    ):
        respond("allow", "Noetic tool (transaction closed)")
        sys.exit(0)
    # Praxic tool with closed transaction → correct error message
    respond(
        "deny",
        "Epistemic loop closed (POSTFLIGHT completed). Run new PREFLIGHT to start next goal. Command: empirica preflight-submit - (JSON with vectors on stdin)",
    )
    sys.exit(0)


def _resolve_session(tx_session_id: str | None, claude_session_id: str | None, env_annotation: str) -> str | None:
    """Resolve empirica session_id from transaction, active_work, or TTY fallback.

    Returns session_id, or None after responding with a warning and exiting.
    """
    # Priority 0: transaction file (authoritative during transaction)
    session_id = tx_session_id

    if not session_id and claude_session_id:
        # Priority 1: active_work file (updated by PREFLIGHT, project-switch)
        try:
            active_work_file = Path.home() / ".empirica" / f"active_work_{claude_session_id}.json"
            if active_work_file.exists():
                with open(active_work_file) as f:
                    work_data = json.load(f)
                session_id = work_data.get("empirica_session_id")
        except Exception:
            pass

    if not session_id:
        # Priority 2+: TTY session, generic active_work.json, project fallback
        # Uses canonical resolver which has the full fallback chain
        try:
            from empirica.utils.session_resolver import InstanceResolver as R

            session_id = R.session_id(claude_session_id)
        except Exception:
            pass

    if not session_id:
        # Name the canonical practice in the suggested command, not a generic
        # 'claude-code' the user would then have to correct by hand.
        try:
            from empirica.utils.session_resolver import InstanceResolver as R

            hint_ai_id = R.ai_id() or "claude-code"
        except Exception:
            hint_ai_id = "claude-code"
        respond(
            "allow",
            f"WARNING: No session found. Run: empirica session-create --ai-id {hint_ai_id} && empirica preflight-submit -{env_annotation}",
        )
        sys.exit(0)

    return session_id


def _lookup_preflight(cursor, session_id: str, current_transaction_id: str | None):
    """Query DB for the latest PREFLIGHT record.

    Returns the preflight row (know, uncertainty, timestamp, project_id)
    or None if no PREFLIGHT found.
    """
    if current_transaction_id:
        cursor.execute(
            """
            SELECT know, uncertainty, timestamp, project_id FROM reflexes
            WHERE session_id = ? AND phase = 'PREFLIGHT' AND transaction_id = ?
            ORDER BY timestamp DESC LIMIT 1
        """,
            (session_id, current_transaction_id),
        )
    else:
        cursor.execute(
            """
            SELECT know, uncertainty, timestamp, project_id FROM reflexes
            WHERE session_id = ? AND phase = 'PREFLIGHT'
            ORDER BY timestamp DESC LIMIT 1
        """,
            (session_id,),
        )
    return cursor.fetchone()


def _check_auto_proceed(
    raw_know: float,
    raw_unc: float,
    db,
    tx_file,
    _current_domain: str | None,
    _current_criticality: str | None,
    env_annotation: str,
) -> tuple | None:
    """Check if PREFLIGHT vectors pass the auto-proceed threshold.

    Returns (decision, reason) if auto-proceed applies, or None to continue.
    Also returns the computed thresholds for use by CHECK evaluation.
    """
    _dyn_know, _dyn_unc = _get_dynamic_thresholds(db)
    _domain_unc = _get_domain_scaled_thresholds(
        _dyn_unc,
        _current_domain,
        _current_criticality,
        project_path=str(Path(tx_file).parent.parent) if tx_file else None,
    )
    if raw_know >= _dyn_know and raw_unc <= _domain_unc:
        _domain_info = ""
        if _current_domain or _current_criticality:
            _domain_info = f" [{_current_domain or 'default'}/{_current_criticality or 'medium'}]"
        return (
            "allow",
            f"PREFLIGHT confidence sufficient - proceeding (threshold: U<={_domain_unc:.0%}{_domain_info}){env_annotation}",
        )
    return None


def _check_expiry_and_compact(check_timestamp, empirica_root: Path | None) -> tuple | None:
    """Check optional CHECK expiry and compact invalidation.

    Returns (decision, reason) if CHECK is expired/invalidated, or None to continue.
    """
    check_time = None

    if os.getenv("EMPIRICA_SENTINEL_CHECK_EXPIRY", "false").lower() == "true":
        try:
            if isinstance(check_timestamp, (int, float)) or (
                isinstance(check_timestamp, str) and check_timestamp.replace(".", "").isdigit()
            ):
                check_time = datetime.fromtimestamp(float(check_timestamp))
            else:
                check_time = datetime.fromisoformat(check_timestamp.replace("Z", "+00:00").replace("+00:00", ""))
            age_minutes = (datetime.now() - check_time).total_seconds() / 60

            if age_minutes > MAX_CHECK_AGE_MINUTES:
                return ("deny", f"CHECK expired ({age_minutes:.0f}min). Refresh epistemic state.")
        except Exception:
            pass

    if os.getenv("EMPIRICA_SENTINEL_COMPACT_INVALIDATION", "false").lower() == "true":
        if empirica_root:
            last_compact = get_last_compact_timestamp(empirica_root.parent)
            if last_compact and check_time and last_compact > check_time:
                return ("deny", "Context compacted. Recalibrate with fresh CHECK.")

    return None


def _evaluate_check_threshold(know, uncertainty, db, env_annotation: str) -> tuple:
    """Evaluate CHECK vectors against dynamic thresholds.

    Returns (decision, reason) — always produces a final decision.
    """
    raw_check_know = know or 0
    raw_check_unc = uncertainty or 1
    _dyn_know, _dyn_unc = _get_dynamic_thresholds(db)

    if raw_check_know >= _dyn_know and raw_check_unc <= _dyn_unc:
        return ("allow", f"CHECK passed - proceeding (threshold: K>={_dyn_know:.0%} U<={_dyn_unc:.0%}){env_annotation}")
    # ADVISORY MODE: Surface the gap but let the AI proceed with awareness.
    return (
        "allow",
        f"ADVISORY: Prediction groundedness below threshold (K={raw_check_know:.0%} vs {_dyn_know:.0%}, U={raw_check_unc:.0%} vs {_dyn_unc:.0%}). Consider gathering more grounding evidence.{env_annotation}",
    )


def _run_authorization_pipeline(hook_input: dict, tool_name: str, tool_input: dict) -> tuple[str, str]:
    """Run the full authorization pipeline: session resolution, DB lookup,
    PREFLIGHT/CHECK validation, auto-proceed, investigate handling, expiry.

    Returns (decision, message) tuple for respond(). db is closed internally.
    """
    env_annotation = _build_env_annotation()
    claude_session_id = hook_input.get("session_id")

    empirica_root = _resolve_empirica_root(claude_session_id)
    tx_state = _read_transaction_state(empirica_root, claude_session_id, tool_name, tool_input)
    current_transaction_id = tx_state["current_transaction_id"]
    suffix = tx_state["suffix"]
    tx_file = tx_state["tx_file"]

    session_id = _resolve_session(tx_state["tx_session_id"], claude_session_id, env_annotation)
    if not session_id:
        return ("allow", "No session resolved — sentinel inactive")

    from empirica.data.session_database import SessionDatabase  # type: ignore[import-not-found]

    db = SessionDatabase()
    if db.conn is None:
        return ("allow", "No database connection — sentinel inactive")
    cursor = db.conn.cursor()

    try:
        # Optional: Bootstrap requirement
        if os.getenv("EMPIRICA_SENTINEL_REQUIRE_BOOTSTRAP", "false").lower() == "true":
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if not row or not row[0]:
                return ("deny", f"No bootstrap for {session_id[:8]}. Run: empirica project-bootstrap")

        # PREFLIGHT check (authentication gate)
        preflight_row = _lookup_preflight(cursor, session_id, current_transaction_id)
        if not preflight_row:
            return _handle_no_preflight(tool_name, tool_input, session_id, env_annotation)

        preflight_know, preflight_uncertainty, preflight_timestamp, preflight_project_id = preflight_row

        # Goalless-work advisory nudge
        global _goalless_nudge
        _goalless_nudge = _check_goalless_work(
            cursor, session_id, preflight_project_id, claude_session_id, empirica_root, suffix
        )

        # Sequential pre-CHECK validations
        for check in (
            _check_project_context(cursor, db, session_id, preflight_project_id),
            _check_postflight_loop_closed(
                cursor, session_id, current_transaction_id, preflight_timestamp, tool_name, tool_input
            ),
            _check_prior_investigate(
                cursor, session_id, current_transaction_id, preflight_timestamp, tool_name, tool_input
            ),
            _check_auto_proceed(
                preflight_know or 0,
                preflight_uncertainty or 1,
                db,
                tx_file,
                tx_state["_current_domain"],
                tx_state["_current_criticality"],
                env_annotation,
            ),
        ):
            if check:
                return check

        # CHECK validation: returns None (silent pass), len-2 tuple (deny), or len-4 tuple (success)
        check_result = _validate_check_record(
            cursor, session_id, current_transaction_id, preflight_timestamp, tool_input, tool_name
        )
        if check_result is None:
            return ("allow", "")
        if len(check_result) == 2:
            return (check_result[0], check_result[1])
        know, uncertainty, decision, check_timestamp = check_result

        investigate_result = _handle_investigate_continuation(decision, tool_name, tool_input, suffix, tx_file, db)
        if investigate_result:
            return investigate_result

        expiry_result = _check_expiry_and_compact(check_timestamp, empirica_root)
        if expiry_result:
            return expiry_result

        return _evaluate_check_threshold(know, uncertainty, db, env_annotation)
    finally:
        db.close()


def _proportionality_state_path(session_id: str) -> Path:
    """Mirror tool-router.py's path computation. The pair must agree on
    the same key per session for the budget to function."""
    safe_sid = "".join(c if c.isalnum() or c in "-_" else "_" for c in (session_id or "no-sid"))
    return Path.home() / ".empirica" / "state" / f"proportionality_{safe_sid}.json"


def _check_proportionality_budget(hook_input: dict, tool_name: str) -> str | None:
    """Tx-AG: enforce investigation-proportionality budget.

    tool-router.py arms the budget when the proportionality block fires
    on a hypothesis-bearing prompt. This function increments a counter
    on each Read/Grep/Glob and returns a deny reason once the limit is
    exceeded — turning a soft context-injection block into a runtime
    constraint the model can't ignore.

    Returns None to allow (default), a string reason to deny.

    Fail-quiet: any IO/JSON/path error returns None — we never block on
    our own state plumbing.
    """
    if tool_name not in ("Read", "Grep", "Glob"):
        return None
    session_id = hook_input.get("session_id", "")
    if not session_id:
        return None
    path = _proportionality_state_path(session_id)
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    # Stale armings shouldn't block forever. 1h timeout matches the
    # implicit "this turn" framing — if the model hasn't acted on the
    # block within an hour, the conversational context is gone.
    armed_at = data.get("armed_at", 0)
    if time.time() - armed_at > 3600:
        try:
            path.unlink()
        except OSError:
            pass
        return None

    count = int(data.get("tool_count", 0)) + 1
    limit = int(data.get("limit", 5))
    data["tool_count"] = count
    try:
        path.write_text(json.dumps(data))
    except OSError:
        pass

    if count > limit:
        return (
            f"Investigation-proportionality budget exceeded "
            f"({count} read/grep/glob calls since the hypothesis-bearing prompt; "
            f"limit={limit}). The user gave you a hypothesis to test. "
            f"Your next action MUST be: (1) state the hypothesis explicitly "
            f'in your reply ("Hypothesis: ..."), (2) run a single '
            f"bash/grep/read that confirms or disconfirms it. "
            f"Survey-mode is blocked until the next user prompt resets "
            f"the budget. If you genuinely need to map this subsystem first, "
            f"answer the user with the hypothesis-test result and ask them "
            f"whether to expand."
        )
    return None


def main():
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except Exception:
        hook_input = {}

    tool_name = hook_input.get("tool_name", "unknown")
    tool_input = hook_input.get("tool_input", {})

    _track_tool_usage(hook_input, tool_name, tool_input)
    _set_file_relevance_nudge(tool_name, tool_input, hook_input.get("session_id"))

    # Tx-AG: investigation-proportionality budget enforcement. When
    # tool-router.py armed the budget on a hypothesis-bearing prompt,
    # deny Read/Grep/Glob once the limit is exceeded. Soft text blocks
    # were empirically insufficient (model ignored them); this is the
    # runtime constraint that makes the discipline real.
    proportionality_deny = _check_proportionality_budget(hook_input, tool_name)
    if proportionality_deny:
        respond("deny", proportionality_deny)
        sys.exit(0)

    # Noetic firewall: whitelist-based access control
    noetic_result = _noetic_firewall_check(tool_name, tool_input, hook_input)
    if noetic_result:
        respond("allow", noetic_result[1])
        sys.exit(0)

    # Exemptions: subagent, paused, sentinel disabled
    exemption = _check_exemptions(hook_input, tool_name)
    if exemption:
        respond(exemption[0], exemption[1])
        sys.exit(0)

    # Everything else requires CHECK authorization via the pipeline
    decision, message = _run_authorization_pipeline(hook_input, tool_name, tool_input)
    respond(decision, message)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Crash recovery: the outermost catch handles transient errors (DB
        # lock, import race, path resolution bug) so a sentinel crash doesn't
        # strand every tool invocation. Default behavior is fail-OPEN — allow
        # the action but emit SENTINEL_CRASH on stderr so failures are
        # visible without blocking work.
        #
        # Tx-AJ: opt-in fail-CLOSED mode for hardened deployments.
        # Set EMPIRICA_SENTINEL_FAIL_CLOSED=1 (or "true"/"yes") to flip the
        # default — sentinel crashes will then DENY the action with a reason,
        # producing a hard error the user can investigate. Suitable for
        # production agentic frameworks where a silent fail-open is a worse
        # failure mode than a noisy block. Default unchanged for dev.
        import os as _os
        import sys as _sys

        _sys.stderr.write(f"SENTINEL_CRASH: {type(e).__name__}: {e}\n")
        fail_closed = _os.environ.get("EMPIRICA_SENTINEL_FAIL_CLOSED", "").strip().lower() in {"1", "true", "yes"}
        if fail_closed:
            respond(
                "deny",
                f"Sentinel internal error (fail-closed mode): {type(e).__name__}: {e}. "
                "Investigate via SENTINEL_CRASH stderr; unset "
                "EMPIRICA_SENTINEL_FAIL_CLOSED to revert to fail-open default.",
            )
            _sys.exit(2)
        respond("allow", f"Sentinel error (fail-open): {type(e).__name__}: {e}")
        _sys.exit(0)
