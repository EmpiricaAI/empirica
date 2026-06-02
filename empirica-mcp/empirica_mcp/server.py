#!/usr/bin/env python3
"""
Empirica MCP Server — Thin CLI wrapper for AI agent environments.

Provides MCP tools that route to `empirica` CLI commands via subprocess.
No epistemic middleware — gating is handled by the Sentinel (hooks) in
Claude Code, or self-enforced on other platforms.

Architecture:
- Table-driven: TOOL_REGISTRY maps tool names → CLI commands + params
- All commands run with stdin=DEVNULL and timeout (no hanging)
- Graceful: if CLI not found, returns clear error
- Stateless: no session state in the server itself

Version tracked via the empirica-mcp package metadata (see pyproject.toml).
Last TOOL_REGISTRY re-verification against `empirica --help`: 2026-04-05
(stale — see TOOL_REGISTRY refresh goal for the additions backlog).
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

logger = logging.getLogger(__name__)

# CLI resolution
EMPIRICA_CLI = shutil.which("empirica")
if not EMPIRICA_CLI:
    for path in [
        Path.home() / ".local" / "bin" / "empirica",
        Path("/usr/local/bin/empirica"),
    ]:
        if path.exists():
            EMPIRICA_CLI = str(path)
            break

CLI_TIMEOUT = int(os.environ.get("EMPIRICA_MCP_TIMEOUT", "30"))
# CASCADE commands (POSTFLIGHT especially) run grounded verification,
# Qdrant embedding, memory management — need more time than standard commands
CASCADE_TIMEOUT = int(os.environ.get("EMPIRICA_MCP_CASCADE_TIMEOUT", "120"))
MAX_OUTPUT = 30000

# =============================================================================
# Tool Registry — single source of truth for tool→CLI mapping
# =============================================================================
# Each entry: {cli, params, required, desc, stdin_json?}
# Params verified against CLI --help on 2026-04-05

TOOL_REGISTRY: dict[str, dict] = {
    # --- Session lifecycle ---
    "session_create": {
        "cli": "session-create",
        "params": {"ai_id": "--ai-id", "project_id": "--project-id", "subject": "--subject"},
        "required": [],
        "desc": "Create new Empirica session",
    },
    "project_bootstrap": {
        "cli": "project-bootstrap",
        "params": {"ai_id": "--ai-id", "session_id": "--session-id", "depth": "--depth"},
        "required": [],
        "desc": "Load project context (findings, goals, unknowns, calibration)",
    },
    "bootstrap_context": {
        "cli": "bootstrap-context",
        "params": {
            "project_path": "--project-path",
            "session_id": "--session-id",
            "similarity_threshold": "--similarity-threshold",
        },
        "required": [],
        "desc": (
            "Three-circle artifact graph bootstrap (v2 wire shape). "
            "Returns active_state (recency-decayed) + persistent_reference "
            "(no decay) + topic_relevant_backlog (Qdrant similarity-pulled). "
            "Designed for Claude Desktop / Cursor / Cline at chat-start to "
            "match what CLI hooks inject. See PROPOSAL_BOOTSTRAP_AGGREGATOR.md."
        ),
    },
    "session_snapshot": {
        "cli": "session-snapshot",
        "params": {},
        "required": [],
        "desc": "Create snapshot of current session state",
    },
    "resume_previous_session": {
        "cli": "sessions-resume",
        "params": {"ai_id": "--ai-id", "count": "--count", "detail_level": "--detail-level"},
        "required": [],
        "desc": "Resume a previous session",
    },

    # --- CASCADE workflow ---
    "submit_preflight_assessment": {
        "cli": "preflight-submit",
        "params": {},
        "required": ["session_id", "vectors"],
        "desc": "Submit PREFLIGHT self-assessment (13 vectors 0.0-1.0)",
        "stdin_json": True,
    },
    "submit_check_assessment": {
        "cli": "check-submit",
        "params": {},
        "required": ["session_id", "vectors"],
        "desc": "Submit CHECK gate assessment",
        "stdin_json": True,
    },
    "submit_postflight_assessment": {
        "cli": "postflight-submit",
        "params": {},
        "required": ["session_id", "vectors"],
        "desc": "Submit POSTFLIGHT assessment — closes transaction, triggers grounded verification",
        "stdin_json": True,
    },

    # --- Noetic artifacts ---
    # `visibility` (public/shared/local) and `epistemic_source` (intuition/
    # search/mixed) are part of the cross-Claude intelligence-sharing
    # discipline — exposed on every log verb so the MCP path stays at
    # parity with the CLI and the provenance flags are enforceable
    # through either interface (v1.9.6+).
    "finding_log": {
        "cli": "finding-log",
        "params": {"finding": "--finding", "impact": "--impact", "session_id": "--session-id",
                   "goal_id": "--goal-id", "task_id": "--task-id", "project_id": "--project-id",
                   "subject": "--subject", "scope": "--scope",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via",
                   "source_ids": "--source",
                   "visibility": "--visibility", "epistemic_source": "--epistemic-source"},
        "required": ["finding"],
        "desc": "Log a finding (what was learned). Use source_ids to link to epistemic sources, visibility to opt into cross-project sharing, epistemic_source to tag provenance.",
        "list_params": ["source_ids"],
    },
    "unknown_log": {
        "cli": "unknown-log",
        "params": {"unknown": "--unknown", "session_id": "--session-id",
                   "goal_id": "--goal-id", "task_id": "--task-id", "project_id": "--project-id",
                   "subject": "--subject", "scope": "--scope",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via",
                   "visibility": "--visibility", "epistemic_source": "--epistemic-source"},
        "required": ["unknown"],
        "desc": "Log an unknown (what needs investigation)",
    },
    "deadend_log": {
        "cli": "deadend-log",
        "params": {"approach": "--approach", "why_failed": "--why-failed", "session_id": "--session-id",
                   "goal_id": "--goal-id", "task_id": "--task-id", "project_id": "--project-id",
                   "subject": "--subject", "scope": "--scope",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via",
                   "visibility": "--visibility", "epistemic_source": "--epistemic-source"},
        "required": ["approach", "why_failed"],
        "desc": "Log a dead-end (approach that didn't work)",
    },
    "mistake_log": {
        "cli": "mistake-log",
        "params": {"mistake": "--mistake", "why_wrong": "--why-wrong", "prevention": "--prevention",
                   "session_id": "--session-id", "goal_id": "--goal-id", "project_id": "--project-id",
                   "scope": "--scope", "entity_type": "--entity-type", "entity_id": "--entity-id",
                   "visibility": "--visibility", "epistemic_source": "--epistemic-source"},
        "required": ["mistake", "why_wrong", "prevention"],
        "desc": "Log a mistake (error to avoid in future)",
    },
    "assumption_log": {
        "cli": "assumption-log",
        "params": {"assumption": "--assumption", "confidence": "--confidence", "domain": "--domain",
                   "session_id": "--session-id", "goal_id": "--goal-id", "project_id": "--project-id",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via",
                   "visibility": "--visibility", "epistemic_source": "--epistemic-source"},
        "required": ["assumption"],
        "desc": "Log an unverified assumption with confidence level",
    },
    "decision_log": {
        "cli": "decision-log",
        "params": {"choice": "--choice", "rationale": "--rationale", "alternatives": "--alternatives",
                   "reversibility": "--reversibility", "confidence": "--confidence", "domain": "--domain",
                   "session_id": "--session-id", "goal_id": "--goal-id", "project_id": "--project-id",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via",
                   "evidence_refs": "--evidence",
                   "visibility": "--visibility", "epistemic_source": "--epistemic-source"},
        "required": ["choice", "rationale"],
        "desc": "Log a decision with rationale. Use evidence_refs to link to supporting findings.",
        "list_params": ["evidence_refs"],
    },
    "source_add": {
        "cli": "source-add",
        "params": {"title": "--title", "url": "--url", "source_type": "--source-type",
                   "description": "--description", "session_id": "--session-id"},
        "required": ["title", "source_type"],
        "desc": "Add an epistemic source reference",
    },

    # --- Batch artifact graph (parity with Cortex MCP cortex_log_artifacts/resolve/delete) ---
    "log_artifacts": {
        "cli": "log-artifacts",
        "params": {"session_id": "--session-id", "project_id": "--project-id"},
        "required": [],
        "desc": (
            "Log a connected set of epistemic artifacts in one call. JSON body on stdin: "
            "{nodes:[{ref, type, data}], edges:[{from, to, relation}]}. Node types: "
            "finding, unknown, dead_end, mistake, assumption, decision, source. Edge relations: "
            "evidence, raised_by, grounded_by, resolves, invalidates, sourced_from, "
            "caused_by, prevents, attached_to. Creates in dependency order, resolves $-refs to UUIDs."
        ),
        "stdin_json": True,
    },
    "resolve_artifacts": {
        "cli": "resolve-artifacts",
        "params": {},
        "required": [],
        "desc": (
            "Batch resolve open artifacts in one call. JSON body on stdin: "
            "{resolutions:[{type, id, resolution}]}. Closes unknowns, marks assumptions "
            "verified/falsified, completes goals. One call replaces N individual resolutions."
        ),
        "stdin_json": True,
    },
    "delete_artifacts": {
        "cli": "delete-artifacts",
        "params": {"dry_run": "--dry-run"},
        "required": [],
        "desc": (
            "Batch delete stale/non-pertinent artifacts. JSON body on stdin: "
            "{deletions:[{type, id}], reason: '...'}. Deletes from SQLite + Qdrant, "
            "logs deletion as a decision for audit. Use dry_run=true to preview."
        ),
        "stdin_json": True,
    },

    # --- Sync (local → cloud propagation via git remote → Cortex git_watcher) ---
    "sync_push": {
        "cli": "sync-push",
        "params": {"remote": "--remote", "dry_run": "--dry-run", "force": "--force"},
        "required": [],
        "desc": (
            "Push local .empirica/ state to git remote. Cortex's git_watcher consumes the "
            "push and ingests artifacts into the project's Qdrant collection. Use after a "
            "coherent unit of work (or on a cadence) to propagate local writes to cloud. "
            "Lag budget: 10-15s end-to-end. Idempotent — Cortex dedups on artifact hash."
        ),
    },
    "sync_status": {
        "cli": "sync-status",
        "params": {"remote": "--remote"},
        "required": [],
        "desc": "Show sync state — remote configured, last push/pull timestamp, pending local changes.",
    },

    # --- Health ---
    "doctor": {
        "cli": "doctor",
        "params": {},
        "required": [],
        "desc": (
            "Frontend-agnostic Empirica health check. Returns structured JSON: "
            "Python version, empirica CLI path/version, empirica-mcp path, .empirica/ "
            "folder state, git repo + remote, sync state (uncommitted changes), "
            "Cortex reachability. Designed for Claude Desktop where shell exec isn't "
            "available — narrow scoped replacement for general exec MCPs."
        ),
    },

    # --- Goals ---
    "goals_create": {
        "cli": "goals-create",
        "params": {"objective": "--objective", "session_id": "--session-id", "project_id": "--project-id"},
        "required": ["objective"],
        "desc": "Create a new goal",
    },
    "goals_list": {
        "cli": "goals-list",
        "params": {"session_id": "--session-id", "project_id": "--project-id",
                   "completed": "--completed"},
        "required": [],
        "desc": "List goals (use --completed to show completed goals)",
    },
    "goals_complete": {
        "cli": "goals-complete",
        "params": {"goal_id": "--goal-id", "reason": "--reason"},
        "required": ["goal_id"],
        "desc": "Mark a goal as complete",
    },
    "goals_add_task": {
        "cli": "goals-add-task",
        "params": {"goal_id": "--goal-id", "description": "--description",
                   "importance": "--importance"},
        "required": ["goal_id", "description"],
        "desc": "Add a task to a goal",
    },
    "goals_complete_task": {
        "cli": "goals-complete-task",
        "params": {"task_id": "--task-id", "evidence": "--evidence"},
        "required": ["task_id"],
        "desc": "Mark a task as complete",
    },
    "goals_progress": {
        "cli": "goals-progress",
        "params": {"goal_id": "--goal-id"},
        "required": ["goal_id"],
        "desc": "Get goal progress details",
    },
    "goals_search": {
        "cli": "goals-search",
        "params": {"project_id": "--project-id", "status": "--status",
                   "type": "--type", "limit": "--limit"},
        "required": [],
        "desc": "Search goals (positional query text required)",
        "positional": "query",  # First positional arg
    },
    "goals_ready": {
        "cli": "goals-ready",
        "params": {"session_id": "--session-id"},
        "required": [],
        "desc": "List goals ready for work (no blockers)",
    },

    # --- Unknowns ---
    "unknown_list": {
        "cli": "unknown-list",
        "params": {"session_id": "--session-id", "project_id": "--project-id",
                   "subject": "--subject", "limit": "--limit",
                   "resolved": "--resolved", "all": "--all"},
        "required": [],
        "desc": "List unknowns (open by default, use --resolved or --all)",
    },
    "unknown_resolve": {
        "cli": "unknown-resolve",
        "params": {"unknown_id": "--unknown-id", "resolved_by": "--resolved-by",
                   "resolution_finding_id": "--finding"},
        "required": ["unknown_id"],
        "desc": "Resolve an unknown. Use resolution_finding_id to link to the finding that answered it.",
    },

    # --- Search and memory ---
    "project_search": {
        "cli": "project-search",
        "params": {"task": "--task", "project_id": "--project-id", "type": "--type",
                   "limit": "--limit"},
        "required": ["task"],
        "desc": "Semantic search over project knowledge (Qdrant)",
    },
    "project_embed": {
        "cli": "project-embed",
        "params": {"project_id": "--project-id"},
        "required": [],
        "desc": "Embed project artifacts to Qdrant for semantic search",
    },

    # --- Calibration and state ---
    "calibration_report": {
        "cli": "calibration-report",
        "params": {"ai_id": "--ai-id", "weeks": "--weeks",
                   "trajectory": "--trajectory"},
        "required": [],
        "desc": "Get calibration report with optional trajectory trend",
    },
    "assess_state": {
        "cli": "assess-state",
        "params": {"session_id": "--session-id"},
        "required": [],
        "desc": "Get current epistemic state assessment",
    },
    "profile_status": {
        "cli": "profile-status",
        "params": {},
        "required": [],
        "desc": "Show artifact counts, drift, calibration summary",
    },

    # --- Lessons ---
    "lesson_create": {
        "cli": "lesson-create",
        "params": {"name": "--name"},
        "required": ["name"],
        "desc": "Create a reusable lesson (use --input or --json for full data)",
        "stdin_json": True,  # lesson-create accepts JSON via stdin with -
    },

    # --- Noetic batch (multi-op investigation in one tool call) ---
    "noetic_batch": {
        "cli": "noetic-batch",
        "params": {},
        "required": ["intent"],
        "desc": (
            "Batched investigation: reads + greps + globs + investigate in one call. "
            "Replaces N round-trip noetic operations with a single tool call. "
            "Sentinel sees one noetic intent, no per-call gating overhead. "
            "JSON schema: {intent, reads:[{path,lines?}], greps:[{pattern,glob?,context?,case_sensitive?,max_matches?}], "
            "globs:[pattern OR {pattern,root?}], investigate:[{query,scope?,limit?}]}. "
            "See docs/architecture/NOETIC_BATCH_SPEC.md."
        ),
        "stdin_json": True,
    },
    "lesson_list": {
        "cli": "lesson-list",
        "params": {"domain": "--domain"},
        "required": [],
        "desc": "List available lessons",
    },
    "lesson_search": {
        "cli": "lesson-search",
        "params": {"query": "--query"},
        "required": ["query"],
        "desc": "Search lessons by text",
    },

    # --- Issues ---
    "issue_list": {
        "cli": "issue-list",
        "params": {"status": "--status", "severity": "--severity"},
        "required": [],
        "desc": "List auto-captured issues",
    },
    "issue_resolve": {
        "cli": "issue-resolve",
        "params": {"session_id": "--session-id", "issue_id": "--issue-id",
                   "resolution": "--resolution"},
        "required": ["session_id", "issue_id", "resolution"],
        "desc": "Resolve an auto-captured issue",
    },

    # --- Workflow Patterns ---
    "workflow_patterns": {
        "cli": "workflow-patterns",
        "params": {"limit": "--limit", "min_frequency": "--min-frequency"},
        "required": [],
        "desc": "Detect repeated workflow patterns across transactions (tool sequence mining)",
    },

    # --- Investigation ---
    "investigate": {
        "cli": "investigate",
        "params": {"session_id": "--session-id", "type": "--type", "context": "--context"},
        "required": [],
        "desc": "Run structured investigation (positional query text)",
        "positional": "query",
    },

    # --- Handoff ---
    "handoff_create": {
        "cli": "handoff-create",
        "params": {"session_id": "--session-id", "task_summary": "--task-summary",
                   "key_findings": "--key-findings", "remaining_unknowns": "--remaining-unknowns",
                   "next_session_context": "--next-session-context", "planning_only": "--planning-only"},
        "required": [],
        "desc": "Create handoff report for session continuation",
    },

    # --- Workspace ---
    "workspace_overview": {
        "cli": "workspace-overview",
        "params": {},
        "required": [],
        "desc": "Show workspace overview (all tracked projects)",
    },
    "workspace_map": {
        "cli": "workspace-map",
        "params": {},
        "required": [],
        "desc": "Show knowledge map across projects",
    },

    # --- Monitor ---
    "monitor": {
        "cli": "monitor",
        "params": {"turtle": "--turtle", "cost": "--cost", "health": "--health"},
        "required": [],
        "desc": "Show session monitoring (epistemic health, cost, adapter health)",
    },

    # --- Checkpoint ---
    "checkpoint_create": {
        "cli": "checkpoint-create",
        "params": {"session_id": "--session-id", "phase": "--phase", "metadata": "--metadata"},
        "required": [],
        "desc": "Create a git checkpoint with epistemic metadata",
    },
    "checkpoint_load": {
        "cli": "checkpoint-load",
        "params": {"session_id": "--session-id", "max_age": "--max-age", "phase": "--phase"},
        "required": [],
        "desc": "Load a checkpoint",
    },

    # --- Docs ---
    # refdoc_add removed in goal 3d6aeb08 Phase 2 — use source_add instead.
    # MCP callers should pass source_type='pointer' to source_add for the
    # same semantics (refdocs migrated to epistemic_sources WHERE
    # source_type='pointer' via migration 046).

    # --- Dispatch Bus (cross-instance typed messaging) ---
    "bus_register": {
        "cli": "bus-register",
        "params": {"instance_id": "--instance-id", "type": "--type",
                   "capabilities": "--capabilities", "subscribes": "--subscribes"},
        "required": ["instance_id", "type"],
        "desc": "Register this Claude instance in the shared dispatch bus registry. capabilities and subscribes are comma-separated strings.",
    },
    "bus_dispatch": {
        "cli": "bus-dispatch",
        "params": {"from_instance": "--from", "to_instance": "--to", "action": "--action",
                   "payload": "--payload", "priority": "--priority", "deadline": "--deadline",
                   "required_capabilities": "--required-capabilities",
                   "callback_channel": "--callback-channel", "ttl": "--ttl",
                   "wait": "--wait", "wait_timeout": "--wait-timeout"},
        "required": ["to_instance", "action"],
        "desc": "Send a typed dispatch action to another instance. Use to_instance='*' with required_capabilities for capability routing. Payload is JSON string.",
    },
    "bus_instances": {
        "cli": "bus-instances",
        "params": {"capability": "--capability"},
        "required": [],
        "desc": "List all registered bus instances (optionally filter by capability)",
    },
    "bus_status": {
        "cli": "bus-status",
        "params": {"instance_id": "--instance-id"},
        "required": ["instance_id"],
        "desc": "Show an instance's registry state and inbox summary",
    },
    "bus_poll": {
        "cli": "message-inbox",
        "params": {"ai_id": "--ai-id", "channel": "--channel", "status": "--status",
                   "limit": "--limit"},
        "required": ["ai_id"],
        "desc": "Poll for incoming messages on a channel (default channel: dispatch). Use bus_subscribe equivalent: pass channel='dispatch' for typed dispatches.",
    },

    # --- Misc ---
    "memory_compact": {
        "cli": "memory-compact",
        "params": {},
        "required": [],
        "desc": "Compact session memory (deduplicate, prune stale)",
    },
    "efficiency_report": {
        "cli": "efficiency-report",
        "params": {"session_id": "--session-id"},
        "required": [],
        "desc": "Generate efficiency report for session",
    },
}

# =============================================================================
# Tool schemas — auto-generated from registry
# =============================================================================

_NUMERIC_PARAMS = {"impact", "confidence", "estimated_complexity", "limit", "weeks", "count",
                   "max_age", "deadline", "wait_timeout", "ttl"}
_BOOLEAN_PARAMS = {"grounded", "trajectory", "completed", "resolved", "all", "planning_only",
                   "turtle", "cost", "health", "wait", "dry_run", "force"}
_ENUM_PARAMS = {
    "reversibility": ["exploratory", "committal", "forced"],
    "scope": ["session", "project", "both"],
    "entity_type": ["project", "organization", "contact", "engagement"],
    "source_type": ["doc", "spec", "paper", "blog", "video", "code", "api", "other"],
    "status": ["in_progress", "completed", "stale", "abandoned", "new", "resolved", "unread", "read", "all"],
    "severity": ["low", "medium", "high", "critical"],
    "type": ["auto", "file", "directory", "concept", "comprehensive", "goal", "task"],
    "detail_level": ["summary", "detailed", "full"],
    "phase": ["PREFLIGHT", "CHECK", "ACT", "POSTFLIGHT"],
    "priority": ["low", "normal", "high", "urgent"],
    "visibility": ["public", "shared", "local"],
    "epistemic_source": ["intuition", "search", "mixed"],
}


def _build_tool_schema(name: str, entry: dict) -> types.Tool:
    """Build MCP Tool schema from registry entry."""
    properties = {}

    # Add positional arg as a string property
    if entry.get("positional"):
        pos_name = entry["positional"]
        properties[pos_name] = {"type": "string", "description": f"{pos_name} (positional argument)"}

    list_params = set(entry.get("list_params", []))
    for param in entry["params"]:
        if param in list_params:
            properties[param] = {"type": "array", "items": {"type": "string"},
                                 "description": f"List of {param.replace('_', ' ')}"}
        elif param in _NUMERIC_PARAMS:
            properties[param] = {"type": "number", "description": param.replace("_", " ").title()}
        elif param in _BOOLEAN_PARAMS:
            properties[param] = {"type": "boolean", "description": param.replace("_", " ").title()}
        elif param in _ENUM_PARAMS:
            properties[param] = {"type": "string", "enum": _ENUM_PARAMS[param]}
        else:
            properties[param] = {"type": "string"}

    # For stdin_json tools, add vectors and reasoning
    if entry.get("stdin_json") and name.startswith("submit_"):
        properties["session_id"] = {"type": "string", "description": "Session UUID"}
        properties["vectors"] = {"type": "object", "description": "13 epistemic vectors (0.0-1.0)"}
        properties["reasoning"] = {"type": "string", "description": "Assessment reasoning"}
        if name == "submit_preflight_assessment":
            properties["task_context"] = {"type": "string", "description": "What you're working on"}
            properties["work_type"] = {"type": "string", "description": "code|infra|research|debug|docs|comms|design|audit"}
            properties["work_context"] = {"type": "string", "description": "greenfield|iteration|investigation|refactor"}
        if name == "submit_check_assessment":
            properties["decision"] = {"type": "string", "enum": ["proceed", "investigate"]}

    return types.Tool(
        name=name,
        description=entry["desc"],
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": entry["required"],
        },
    )


# =============================================================================
# MCP Server
# =============================================================================

app = Server("empirica")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """List all available tools."""
    tools = [_build_tool_schema(name, entry) for name, entry in TOOL_REGISTRY.items()]

    tools.append(types.Tool(
        name="get_empirica_introduction",
        description="Get introduction to the Empirica epistemic framework",
        inputSchema={"type": "object", "properties": {}},
    ))
    return tools


def _build_cli_command(
    entry: dict, arguments: dict
) -> tuple[list[str], bytes | None]:
    """Build the argv + optional stdin payload for a TOOL_REGISTRY entry.

    Two shapes:
    - `stdin_json` entries (CASCADE commands, lesson-create) get the full
      `arguments` dict piped as JSON via `-`.
    - Standard entries map `arguments` keys to CLI flags per `entry["params"]`,
      with `positional` and `list_params` for the special-case forms.
    """
    cmd: list[str] = [EMPIRICA_CLI, entry["cli"], "--output", "json"]
    if entry.get("stdin_json"):
        cmd.append("-")
        return cmd, json.dumps(arguments).encode("utf-8")

    positional = entry.get("positional")
    if positional:
        pos_val = arguments.pop(positional, None)
        if pos_val:
            cmd.append(str(pos_val))

    list_params = set(entry.get("list_params", []))
    for param, flag in entry["params"].items():
        value = arguments.get(param)
        if value is None:
            continue
        if param in list_params and isinstance(value, list):
            for item in value:
                cmd.extend([flag, str(item)])
        elif isinstance(value, bool):
            if value:
                cmd.append(flag)
        else:
            cmd.extend([flag, str(value)])
    return cmd, None


def _resolve_cwd(arguments: dict) -> str | None:
    """Pick the working directory for the CLI invocation.

    Precedence: explicit `project_path` arg → `$EMPIRICA_WORKSPACE_ROOT` →
    `session_resolver.get_active_project_path()` → None.
    """
    cwd = arguments.get("project_path")
    if cwd:
        return cwd
    cwd = os.environ.get("EMPIRICA_WORKSPACE_ROOT")
    if cwd:
        return cwd
    try:
        from empirica.utils.session_resolver import get_active_project_path
        return get_active_project_path()
    except Exception as e:
        logger.debug(f"get_active_project_path lookup failed: {e}")
        return None


def _err_text(payload: dict) -> list[types.TextContent]:
    """Wrap an error payload as the single-element TextContent list the SDK expects."""
    return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]


@app.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Route tool calls to CLI."""

    if name == "get_empirica_introduction":
        return [types.TextContent(type="text", text=json.dumps({
            "framework": "Empirica",
            "purpose": "Measurement and calibration layer for AI — track what it knows, gate what it does",
            "workflow": "PREFLIGHT → CHECK → work → POSTFLIGHT",
            "vectors": 13,
            "docs": "https://github.com/Nubaeon/empirica",
            "commands": sorted(TOOL_REGISTRY.keys()),
        }, indent=2))]

    entry = TOOL_REGISTRY.get(name)
    if not entry:
        return _err_text({
            "ok": False,
            "error": f"Unknown tool: {name}",
            "available": sorted(TOOL_REGISTRY.keys()),
        })

    if not EMPIRICA_CLI:
        return _err_text({
            "ok": False,
            "error": "empirica CLI not found. Install: pip install empirica",
        })

    cmd, stdin_data = _build_cli_command(entry, arguments)
    cwd = _resolve_cwd(arguments)

    timeout = CASCADE_TIMEOUT if entry.get("stdin_json") else CLI_TIMEOUT
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                input=stdin_data.decode("utf-8") if stdin_data else None,
                stdin=None if stdin_data else subprocess.DEVNULL,
                cwd=cwd,
                timeout=timeout,
            ),
        )
    except subprocess.TimeoutExpired:
        return _err_text({
            "ok": False,
            "error": f"Command timed out ({timeout}s): {entry['cli']}",
        })

    if result.returncode == 0:
        output = result.stdout or result.stderr or '{"ok": true}'
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + f"\n\n⚠️ Truncated ({len(output)} chars)"
        return [types.TextContent(type="text", text=output)]
    return _err_text({
        "ok": False,
        "error": result.stderr or result.stdout or "Command failed",
        "command": entry["cli"],
    })


# =============================================================================
# Entry point
# =============================================================================

async def main():
    """Run MCP server via stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def run():
    """CLI entry point for empirica-mcp."""
    parser = argparse.ArgumentParser(description="Empirica MCP Server")
    parser.add_argument("--workspace", "-w", help="Project workspace root")
    args = parser.parse_args()

    if args.workspace:
        workspace = Path(args.workspace).expanduser().resolve()
        if workspace.exists():
            os.environ["EMPIRICA_WORKSPACE_ROOT"] = str(workspace)
            logger.info(f"Workspace: {workspace}")
    elif not os.environ.get("EMPIRICA_WORKSPACE_ROOT"):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if result.returncode == 0:
                git_root = Path(result.stdout.strip())
                if (git_root / ".empirica").exists():
                    os.environ["EMPIRICA_WORKSPACE_ROOT"] = str(git_root)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    asyncio.run(main())


if __name__ == "__main__":
    run()
