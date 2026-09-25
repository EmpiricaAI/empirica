#!/usr/bin/env python3
"""
SubagentStart Hook: Create linked Empirica session when a sub-agent spawns.

Triggered by Claude Code SubagentStart event. Creates a child session
linked via parent_session_id, enabling epistemic lineage tracking.

Input (stdin JSON from Claude Code):
  - agent_name: str - The agent identifier (e.g., "empirica:security")
  - agent_type: str - The agent type
  - session_id: str - Claude Code's internal session ID (not Empirica's)

Output (stdout JSON):
  - continue: true/false
  - message: str - Status message

Side effects:
  - Creates child Empirica session with parent_session_id
  - Writes child session to .empirica/subagent_sessions/<agent_name>.json
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def get_parent_session_id():
    """Get current Empirica session ID from active_session file or DB."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        # Canonical ai_id (project.yaml → basename); 'claude-code' only as last-resort fallback.
        session_id = R.latest_session_id(ai_id=R.ai_id() or "claude-code", active_only=True)
        if session_id:
            return session_id
    except ImportError:
        pass

    # Fallback: read active_session file
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        instance_id = R.instance_id()
        safe_instance = instance_id.replace(":", "_").replace("%", "") if instance_id else ""
        suffix = f"_{safe_instance}" if safe_instance else ""

        for base in [Path.cwd() / ".empirica", Path.home() / ".empirica"]:
            active_file = base / f"active_session{suffix}"
            if active_file.exists():
                sid = active_file.read_text().strip()
                if sid:
                    return sid
    except Exception:
        pass

    return None


def create_child_session(parent_session_id: str, agent_name: str) -> dict:
    """Create a linked child session in Empirica.

    Writes to the dedicated `subagent_sessions` table (migration 034) so
    subagent rows don't pollute the main `sessions` table or its
    "recent sessions" diagnostics. Lineage to the parent is preserved
    via parent_session_id; rollup at SubagentStop logs findings to the
    parent session in the main `sessions` table.
    """
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        child_session_id = db.create_subagent_session(
            agent_name=agent_name,
            parent_session_id=parent_session_id,
        )
        db.close()

        return {
            "ok": True,
            "child_session_id": child_session_id,
            "parent_session_id": parent_session_id,
            "agent_name": agent_name,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_budget_allocation(parent_session_id: str, agent_name: str) -> dict:
    """Get attention budget allocation for this agent, if one exists."""
    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.conn.cursor()

        # Find active budget for parent session
        cursor.execute(
            """
            SELECT id, remaining, domain_allocations, strategy
            FROM attention_budgets
            WHERE session_id = ? ORDER BY created_at DESC LIMIT 1
        """,
            (parent_session_id,),
        )
        row = cursor.fetchone()
        db.close()

        if not row:
            return {}

        budget_id, remaining, alloc_json, strategy = row
        allocations = json.loads(alloc_json) if alloc_json else []

        # Find domain allocation for this agent
        # Agent name format: "empirica:security" -> domain "security"
        agent_domain = agent_name.split(":")[-1] if ":" in agent_name else "general"

        domain_alloc = None
        for alloc in allocations:
            if alloc.get("domain") == agent_domain:
                domain_alloc = alloc
                break

        return {
            "budget_id": budget_id,
            "budget_remaining": remaining,
            "strategy": strategy,
            "domain": agent_domain,
            "domain_budget": domain_alloc.get("budget", 5) if domain_alloc else 5,
            "expected_gain": domain_alloc.get("expected_gain", 0.5) if domain_alloc else 0.5,
            "priority": domain_alloc.get("priority", 0.5) if domain_alloc else 0.5,
        }
    except Exception:
        return {}


def _create_default_budget(parent_session_id: str) -> dict:
    """Auto-create a default attention budget for spontaneous agent spawning.

    When agents are spawned without `agent-parallel` planning, create a
    default budget so the rollup gate has something to work with.
    Only creates if no budget exists for this session (idempotent).
    """
    try:
        import uuid

        from empirica.core.attention_budget import AttentionBudget, DomainAllocation, persist_budget
        from empirica.data.session_database import SessionDatabase

        # Check if budget already exists (idempotent)
        db = SessionDatabase()
        cursor = db.conn.cursor()
        cursor.execute(
            """
            SELECT id, remaining FROM attention_budgets
            WHERE session_id = ? ORDER BY created_at DESC LIMIT 1
        """,
            (parent_session_id,),
        )
        existing = cursor.fetchone()
        db.close()

        if existing:
            return {
                "budget_id": existing[0],
                "budget_remaining": existing[1],
                "strategy": "existing",
                "domain": "general",
                "domain_budget": existing[1],
            }

        # Create default budget
        budget = AttentionBudget(
            id=str(uuid.uuid4()),
            session_id=parent_session_id,
            total_budget=20,
            allocated=0,
            remaining=20,
            strategy="spontaneous",
            allocations=[
                DomainAllocation(
                    domain="general",
                    budget=20,
                    priority=0.5,
                    expected_gain=0.5,
                )
            ],
        )
        persist_budget(budget)

        return {
            "budget_id": budget.id,
            "budget_remaining": 20,
            "strategy": "spontaneous",
            "domain": "general",
            "domain_budget": 20,
        }
    except Exception:
        return {}


def store_subagent_session(agent_name: str, child_session_id: str, parent_session_id: str):
    """Store subagent session mapping for later rollup by SubagentStop."""
    subagent_dir = Path.cwd() / ".empirica" / "subagent_sessions"
    subagent_dir.mkdir(parents=True, exist_ok=True)

    # Use timestamp to allow multiple invocations of same agent
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = agent_name.replace(":", "_").replace("/", "_")
    session_file = subagent_dir / f"{safe_name}_{timestamp}.json"

    # Get budget allocation for this agent (or auto-create default)
    budget_info = get_budget_allocation(parent_session_id, agent_name)
    if not budget_info:
        budget_info = _create_default_budget(parent_session_id)

    session_data = {
        "agent_name": agent_name,
        "child_session_id": child_session_id,
        "parent_session_id": parent_session_id,
        "started_at": datetime.now().isoformat(),
        "status": "active",
        "budget": budget_info,
    }

    session_file.write_text(json.dumps(session_data, indent=2))
    return str(session_file)


def _write_subagent_active_work(
    subagent_claude_session_id: str,
    child_session_id: str,
    parent_claude_session_id: str | None,
    parent_session_id: str,
    agent_name: str,
) -> bool:
    """Write active_work_<subagent_claude_session_id>.json so the subagent's
    empirica CLI calls resolve to their own child_session_id.

    Without this file, subagent CLI invocations fall through the resolver
    chain to TTY-based session resolution, which returns the *parent's*
    empirica_session_id (since TTY is shared). Reflexes from the subagent
    then get tagged with the parent's session_id, contaminating the
    parent's loop and tripping the gate at sentinel-gate.py:1788-1843
    on subsequent edits.

    Carries `is_subagent: true` so sentinel-gate's _detect_subagent can
    flag-detect rather than absence-detect (the existing fallback path
    still works for in-flight subagents that pre-date this fix).

    Returns True if the file was written, False on error (non-fatal).
    """
    if not subagent_claude_session_id:
        return False
    try:
        active_work_file = Path.home() / ".empirica" / f"active_work_{subagent_claude_session_id}.json"
        Path.home().joinpath(".empirica").mkdir(parents=True, exist_ok=True)
        data = {
            "claude_session_id": subagent_claude_session_id,
            "empirica_session_id": child_session_id,
            "is_subagent": True,
            "parent_claude_session_id": parent_claude_session_id,
            "parent_empirica_session_id": parent_session_id,
            "agent_name": agent_name,
            "source": "subagent-start",
            "timestamp": datetime.now().isoformat(),
            "timestamp_epoch": datetime.now().timestamp(),
        }
        with open(active_work_file, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(active_work_file, 0o600)
        return True
    except Exception:
        return False


def main():
    try:
        # Read hook input from stdin
        input_data = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, EOFError):
        input_data = {}

    agent_name = input_data.get("agent_name", input_data.get("agent_type", "unknown-agent"))
    # SUBAGENT's claude_session_id (not parent's) — used to write the
    # active_work file the subagent's CLI calls will resolve from.
    subagent_claude_session_id = input_data.get("session_id")

    # Get parent session
    parent_session_id = get_parent_session_id()

    if not parent_session_id:
        # No active session — allow agent to proceed without tracking
        result = {
            "continue": True,
            "message": f"SubagentStart: No active Empirica session. Agent '{agent_name}' proceeding without lineage tracking.",
        }
        print(json.dumps(result))
        return

    # PRE-SPAWN BUDGET CHECK: Warn strongly if budget is exhausted
    # This is advisory (fail-open) — the rollup gate will reject findings anyway,
    # but warning at spawn time saves compute by letting Claude decide not to spawn.
    budget_warning = ""
    try:
        budget_info = get_budget_allocation(parent_session_id, agent_name)
        if budget_info and budget_info.get("budget_remaining", 20) <= 0:
            budget_warning = (
                f" WARNING: Attention budget EXHAUSTED (0/{budget_info.get('budget_remaining', '?')} remaining). "
                f"Findings from this agent will be rejected by the rollup gate. "
                f"Consider whether this spawn is necessary."
            )
    except Exception:
        pass

    # Create linked child session
    child_result = create_child_session(parent_session_id, agent_name)

    if child_result.get("ok"):
        child_session_id = child_result["child_session_id"]

        # Store mapping for SubagentStop rollup
        store_subagent_session(agent_name, child_session_id, parent_session_id)

        # Write active_work_<subagent_uuid>.json so the subagent's empirica
        # CLI calls resolve to child_session_id, not the parent's session
        # (the parent-bleed pattern from #95 Issue 1).
        aw_written = _write_subagent_active_work(
            subagent_claude_session_id=subagent_claude_session_id,
            child_session_id=child_session_id,
            parent_claude_session_id=None,  # not in input; lineage via parent_session_id
            parent_session_id=parent_session_id,
            agent_name=agent_name,
        )
        aw_msg = " active_work written." if aw_written else ""

        result = {
            "continue": True,
            "message": f"SubagentStart: Created child session {child_session_id[:8]} for '{agent_name}' (parent: {parent_session_id[:8]}){aw_msg}{budget_warning}",
        }
    else:
        # Creation failed — allow agent to proceed anyway (fail-open)
        result = {
            "continue": True,
            "message": f"SubagentStart: Failed to create child session for '{agent_name}': {child_result.get('error', 'unknown')}. Proceeding without tracking.",
        }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
