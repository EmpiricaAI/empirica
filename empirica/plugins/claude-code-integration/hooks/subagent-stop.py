#!/usr/bin/env python3
"""
SubagentStop Hook: Roll up epistemic findings from sub-agent to parent session.

Triggered by Claude Code SubagentStop event. Reads the agent's transcript,
extracts findings/unknowns/dead-ends, and logs them to the parent session.

Input (stdin JSON from Claude Code):
  - agent_name: str - The agent identifier
  - agent_type: str - The agent type
  - agent_transcript_path: str - Path to the agent's transcript file
  - session_id: str - Claude Code's internal session ID

Output (stdout JSON):
  - continue: true
  - message: str - Status message with rollup summary

Side effects:
  - Ends child Empirica session
  - Logs findings from transcript to parent session
  - Updates subagent session file status to "completed"
"""

import glob
import json
import sys
from datetime import datetime
from pathlib import Path


def find_subagent_session(agent_name: str) -> dict:
    """Find the most recent active subagent session for this agent."""
    subagent_dir = Path.cwd() / ".empirica" / "subagent_sessions"
    if not subagent_dir.exists():
        return {}

    safe_name = agent_name.replace(":", "_").replace("/", "_")
    # Find most recent active session file for this agent
    pattern = str(subagent_dir / f"{safe_name}_*.json")
    files = sorted(glob.glob(pattern), reverse=True)

    for f in files:
        try:
            data = json.loads(Path(f).read_text())
            if data.get("status") == "active":
                data["_file_path"] = f
                return data
        except (json.JSONDecodeError, OSError):
            continue

    return {}


def mark_session_completed(session_file: str, summary: dict):
    """Mark subagent session as completed with rollup summary."""
    try:
        data = json.loads(Path(session_file).read_text())
        data["status"] = "completed"
        data["completed_at"] = datetime.now().isoformat()
        data["rollup_summary"] = summary
        Path(session_file).write_text(json.dumps(data, indent=2))
    except (json.JSONDecodeError, OSError):
        pass


def count_transcript_tool_calls(transcript_path: str) -> int:
    """Count tool use invocations in a subagent's transcript.

    Counts assistant messages that contain tool_use content blocks.
    This represents the actual work done by the subagent.
    """
    if not transcript_path or not Path(transcript_path).exists():
        return 0

    count = 0
    try:
        content = Path(transcript_path).read_text()
        for line in content.strip().split("\n"):
            try:
                entry = json.loads(line)
                msg = entry.get("message", {})
                if msg.get("role") != "assistant":
                    continue
                # Count tool_use blocks in content
                content_blocks = msg.get("content", [])
                if isinstance(content_blocks, list):
                    for block in content_blocks:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            count += 1
            except json.JSONDecodeError:
                continue
    except (OSError, UnicodeDecodeError):
        pass

    return count


def add_delegated_work_to_parent(tool_call_count: int) -> bool:
    """Add subagent's tool call count to the parent's hook counters file.

    This ensures the autonomy calibration loop accounts for delegated work.
    Without this, spawning subagents would be a blind spot — the parent's
    transaction would appear shorter than the actual work done.

    Writes to hook_counters file (hook-owned), not the transaction file
    (workflow-owned), to avoid race conditions with POSTFLIGHT.

    Returns True if the update succeeded.
    """
    if tool_call_count <= 0:
        return False

    try:
        import os
        import tempfile

        from empirica.utils.session_resolver import InstanceResolver as R

        suffix = R.instance_suffix()
        project_path = R.project_path()

        if project_path:
            tx_path = Path(project_path) / ".empirica" / f"active_transaction{suffix}.json"
        else:
            tx_path = Path.home() / ".empirica" / f"active_transaction{suffix}.json"

        # Check transaction is open (read-only)
        if not tx_path.exists():
            return False

        with open(tx_path) as f:
            tx_data = json.load(f)

        if tx_data.get("status") != "open":
            return False

        # Read-modify-write the hook counters file
        from empirica.utils.session_resolver import hook_counters_path_for_transaction

        counters_path = hook_counters_path_for_transaction(tx_path)
        counters = {}
        if counters_path.exists():
            try:
                with open(counters_path) as f:
                    counters = json.load(f)
            except Exception:
                counters = {}

        counters["tool_call_count"] = counters.get("tool_call_count", 0) + tool_call_count
        counters["delegated_tool_calls"] = counters.get("delegated_tool_calls", 0) + tool_call_count

        # Atomic write to counters file
        fd, tmp = tempfile.mkstemp(dir=str(counters_path.parent))
        try:
            with os.fdopen(fd, "w") as tf:
                json.dump(counters, tf, indent=2)
            os.replace(tmp, str(counters_path))
        except BaseException:
            # Clean up the temp file, then let it propagate: an ordinary error
            # becomes False in the handler below, while an interrupt or exit is
            # not swallowed into a quiet "not added".
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        return True
    except Exception:
        return False


def _extract_text_from_message(msg: dict) -> str:
    """Extract text content from a transcript message entry."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def _extract_matching_sentence(text_content: str, pattern_start: str) -> str | None:
    """Find the first sentence containing the pattern, return it truncated or None."""
    if pattern_start not in text_content:
        return None
    keyword = pattern_start.rstrip(":")
    for sentence in text_content.split("."):
        if keyword in sentence:
            stripped = sentence.strip()
            if len(stripped) > 10:
                return stripped[:200]
            break
    return None


def extract_findings_from_transcript(transcript_path: str) -> dict:
    """Extract epistemic artifacts from agent transcript.

    Looks for patterns indicating discoveries, uncertainties, and failures.
    Returns structured findings for rollup to parent session.
    """
    findings = []
    unknowns = []
    dead_ends = []

    if not transcript_path or not Path(transcript_path).exists():
        return {"findings": findings, "unknowns": unknowns, "dead_ends": dead_ends}

    finding_patterns = ["Found:", "Discovered:", "Key insight:", "Result:"]
    unknown_patterns = ["Unknown:", "Unclear:", "Need to investigate:", "TODO:"]

    try:
        content = Path(transcript_path).read_text()
        for line in content.strip().split("\n"):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = entry.get("message", {})
            if msg.get("role") != "assistant":
                continue

            text_content = _extract_text_from_message(msg)
            if not text_content:
                continue

            for pattern in finding_patterns:
                match = _extract_matching_sentence(text_content, pattern)
                if match:
                    findings.append(match)

            for pattern in unknown_patterns:
                match = _extract_matching_sentence(text_content, pattern)
                if match:
                    unknowns.append(match)

    except (OSError, UnicodeDecodeError):
        pass

    return {
        "findings": findings[:5],  # Cap at 5 per type
        "unknowns": unknowns[:5],
        "dead_ends": dead_ends[:3],
    }


def rollup_to_parent(parent_session_id: str, agent_name: str, extracted: dict, subagent_data: dict | None = None):
    """Log extracted findings/unknowns to parent session via epistemic rollup gate.

    Uses EpistemicRollupGate to score and filter findings before logging.
    Falls back to naive rollup if the gate module is unavailable.
    """
    logged = {"findings": 0, "unknowns": 0, "dead_ends": 0, "rejected": 0}

    try:
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        # Resolve project_id from parent session
        parent = db.sessions.get_session(parent_session_id)
        project_id = parent.get("project_id", "") if parent else ""

        # Try to use the epistemic rollup gate for scored rollup
        raw_findings = extracted.get("findings", [])
        gated = _gated_rollup(parent_session_id, project_id, agent_name, raw_findings, db, subagent_data=subagent_data)

        if gated is not None:
            # Gated rollup succeeded — log only accepted findings
            for scored in gated.get("accepted", []):
                try:
                    db.log_finding(
                        project_id=project_id,
                        session_id=parent_session_id,
                        finding=f"[{agent_name}] {scored['finding']}",
                        impact=min(1.0, scored.get("score", 0.5)),
                    )
                    logged["findings"] += 1
                except Exception:
                    pass
            logged["rejected"] = len(gated.get("rejected", []))
        else:
            # Fallback: naive rollup (no gate available)
            for finding in raw_findings:
                try:
                    db.log_finding(
                        project_id=project_id,
                        session_id=parent_session_id,
                        finding=f"[{agent_name}] {finding}",
                        impact=0.5,
                    )
                    logged["findings"] += 1
                except Exception:
                    pass

        # Unknowns and dead ends pass through without gating
        for unknown in extracted.get("unknowns", []):
            try:
                db.log_unknown(project_id=project_id, session_id=parent_session_id, unknown=f"[{agent_name}] {unknown}")
                logged["unknowns"] += 1
            except Exception:
                pass

        for dead_end in extracted.get("dead_ends", []):
            try:
                db.log_dead_end(
                    project_id=project_id,
                    session_id=parent_session_id,
                    approach=f"[{agent_name}] {dead_end.get('approach', 'unknown')}",
                    why_failed=dead_end.get("why_failed", "unknown"),
                )
                logged["dead_ends"] += 1
            except Exception:
                pass

        db.close()
    except ImportError:
        pass

    return logged


def _gated_rollup(parent_session_id, project_id, agent_name, raw_findings, db, subagent_data=None):
    """Run findings through EpistemicRollupGate. Returns None if gate unavailable."""
    try:
        from empirica.core.epistemic_rollup import EpistemicRollupGate, log_rollup_decision

        gate = EpistemicRollupGate(
            min_score=0.3,
            jaccard_threshold=0.7,
        )

        # Extract domain from subagent session's budget allocation
        domain = "general"
        confidence = 0.7
        if subagent_data and subagent_data.get("budget"):
            budget_data = subagent_data["budget"]
            domain = budget_data.get("domain", "general")
            # Use priority as confidence proxy (higher priority = more trusted)
            confidence = budget_data.get("priority", 0.7)

        # Get existing findings for dedup
        existing = []
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                """
                SELECT finding FROM project_findings
                WHERE session_id = ?
                ORDER BY created_timestamp DESC LIMIT 50
            """,
                (parent_session_id,),
            )
            existing = [row[0] for row in cursor.fetchall()]
        except Exception:
            pass

        # Load budget if one exists for this session
        budget_id = None
        budget_remaining = 20  # Default max
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                """
                SELECT id, remaining FROM attention_budgets
                WHERE session_id = ? ORDER BY created_at DESC LIMIT 1
            """,
                (parent_session_id,),
            )
            row = cursor.fetchone()
            if row:
                budget_id = row[0]
                budget_remaining = row[1]
        except Exception:
            pass

        # Run rollup pipeline with actual domain and confidence
        result = gate.process(
            raw_findings=raw_findings,
            agent_name=agent_name,
            domain=domain,
            confidence=confidence,
            existing_findings=existing,
            budget_remaining=budget_remaining,
            project_id=project_id,
        )

        # Log decisions
        log_rollup_decision(parent_session_id, budget_id, result)

        # Update budget remaining
        if budget_id and result.budget_consumed > 0:
            try:
                cursor = db.conn.cursor()
                cursor.execute(
                    """
                    UPDATE attention_budgets
                    SET allocated = allocated + ?,
                        remaining = remaining - ?,
                        updated_at = ?
                    WHERE id = ?
                """,
                    (result.budget_consumed, result.budget_consumed, datetime.now().timestamp(), budget_id),
                )
                db.conn.commit()
            except Exception:
                pass

        return {
            "accepted": [f.to_dict() for f in result.accepted],
            "rejected": [f.to_dict() for f in result.rejected],
        }

    except ImportError:
        return None
    except Exception:
        return None


def _check_regulation(parent_session_id: str, logged: dict) -> dict:
    """Check if more agents should be spawned based on budget and information gain.

    Returns regulation recommendation for inclusion in hook output.
    """
    regulation = {
        "budget_total": 20,
        "budget_remaining": 20,
        "should_spawn_more": True,
        "reason": "No budget tracking available",
    }

    try:
        from empirica.core.information_gain import should_spawn_more
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.conn.cursor()

        # Get current budget state
        cursor.execute(
            """
            SELECT total_budget, remaining FROM attention_budgets
            WHERE session_id = ? ORDER BY created_at DESC LIMIT 1
        """,
            (parent_session_id,),
        )
        row = cursor.fetchone()

        if row:
            regulation["budget_total"] = row[0]
            regulation["budget_remaining"] = row[1]

            rounds_without_novel = 0 if logged.get("findings", 0) > 0 else 1

            spawn = should_spawn_more(
                budget_remaining=row[1],
                gain_estimate=0.5,  # Moderate default
                rounds_without_novel=rounds_without_novel,
            )
            regulation["should_spawn_more"] = spawn

            if not spawn:
                if row[1] <= 0:
                    regulation["reason"] = "Budget exhausted"
                else:
                    regulation["reason"] = "Low information gain"
            elif logged.get("findings", 0) > 2:
                regulation["reason"] = "High novelty — consider spawning more"
            else:
                regulation["reason"] = "Moderate gain — continue"

        db.close()
    except (ImportError, Exception):
        pass

    return regulation


def _cleanup_subagent_active_work(subagent_claude_session_id: str | None) -> None:
    """Delete the active_work file written for this subagent at SubagentStart.

    Counterpart to _write_subagent_active_work in subagent-start.py. Leaves
    stale active_work files behind would otherwise pollute the resolver
    chain for new subagents that happen to get the same claude_session_id
    (rare but cheap to prevent).

    Non-fatal — errors are silenced; the file is small and overwritten on
    next SubagentStart in the worst case.
    """
    if not subagent_claude_session_id:
        return
    try:
        from pathlib import Path as _P

        aw_file = _P.home() / ".empirica" / f"active_work_{subagent_claude_session_id}.json"
        if aw_file.exists():
            aw_file.unlink()
    except Exception:
        pass


def main():
    try:
        input_data = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, EOFError):
        input_data = {}

    agent_name = input_data.get("agent_name", input_data.get("agent_type", "unknown-agent"))
    transcript_path = input_data.get("agent_transcript_path", "")
    subagent_claude_session_id = input_data.get("session_id")

    # Find the subagent session
    subagent_data = find_subagent_session(agent_name)

    if not subagent_data:
        result = {
            "continue": True,
            "message": f"SubagentStop: No active session found for '{agent_name}'. Skipping rollup.",
        }
        print(json.dumps(result))
        return

    parent_session_id = subagent_data.get("parent_session_id")
    child_session_id = subagent_data.get("child_session_id")

    # Count subagent's tool calls from transcript (delegated work tracking)
    subagent_tool_calls = count_transcript_tool_calls(transcript_path)

    # Add delegated work to parent's transaction counter
    # This ensures the autonomy calibration loop sees ALL work, not just direct tool calls
    delegated_ok = False
    if subagent_tool_calls > 0:
        try:
            sys.path.insert(0, str(Path.home() / "empirical-ai" / "empirica"))
            delegated_ok = add_delegated_work_to_parent(subagent_tool_calls)
        except Exception:
            pass

    # Extract findings from transcript
    extracted = extract_findings_from_transcript(transcript_path)
    total_extracted = sum(len(v) for v in extracted.values())

    # Roll up to parent session
    logged = {"findings": 0, "unknowns": 0, "dead_ends": 0}
    if parent_session_id and total_extracted > 0:
        logged = rollup_to_parent(parent_session_id, agent_name, extracted, subagent_data=subagent_data)

    # Always close the child session in DB, regardless of extracted findings.
    # Subagent rows live in the dedicated subagent_sessions table (migration 034)
    # so we use end_subagent_session, not end_session.
    if child_session_id:
        try:
            from empirica.data.session_database import SessionDatabase

            db = SessionDatabase()
            db.end_subagent_session(child_session_id)
            db.close()
        except Exception:
            pass

    # Clean up the active_work file written for this subagent at SubagentStart.
    # Without this, stale files accumulate; future subagents could resolve
    # to old child_session_ids if claude_session_ids ever recur.
    _cleanup_subagent_active_work(subagent_claude_session_id)

    # Mark session completed
    if subagent_data.get("_file_path"):
        mark_session_completed(
            subagent_data["_file_path"],
            {
                "extracted": total_extracted,
                "logged": logged,
                "transcript_path": transcript_path,
                "subagent_tool_calls": subagent_tool_calls,
                "delegated_to_parent": delegated_ok,
            },
        )

    rejected_count = logged.get("rejected", 0)
    accepted_count = logged.get("findings", 0) + logged.get("unknowns", 0) + logged.get("dead_ends", 0)

    # Check regulation: should more agents be spawned?
    regulation = {}
    if parent_session_id:
        regulation = _check_regulation(parent_session_id, logged)

    budget_msg = ""
    if regulation.get("budget_total"):
        remaining = regulation.get("budget_remaining", "?")
        total = regulation.get("budget_total", "?")
        reason = regulation.get("reason", "")
        spawn_more = regulation.get("should_spawn_more", True)
        action = "continue" if spawn_more else "STOP"
        budget_msg = f" Budget: {remaining}/{total} remaining. Regulation: {action} ({reason})."

    delegation_msg = ""
    if subagent_tool_calls > 0:
        delegation_msg = (
            f" Delegated work: {subagent_tool_calls} tool calls"
            f"{' added to parent transaction' if delegated_ok else ' NOT added to the parent transaction (none found, or the counters write failed)'}."
        )

    # Regulation enforcement: make STOP unmissable
    regulation_directive = ""
    if regulation and not regulation.get("should_spawn_more", True):
        regulation_directive = (
            f" REGULATION: DO NOT spawn more agents — {regulation.get('reason', 'budget/gain threshold reached')}."
        )

    result = {
        "continue": True,
        "message": f"SubagentStop: Agent '{agent_name}' completed. "
        f"Extracted {total_extracted} artifacts, accepted {accepted_count}, "
        f"rejected {rejected_count} via rollup gate. "
        f"Parent: {parent_session_id[:8] if parent_session_id else 'none'}."
        f"{budget_msg}{delegation_msg}{regulation_directive}",
        "regulation": regulation,
        "delegated_work": {"subagent_tool_calls": subagent_tool_calls, "added_to_parent_transaction": delegated_ok},
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
