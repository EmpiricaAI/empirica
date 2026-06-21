"""
Agent Commands - CLI handlers for epistemic sub-agents

Commands:
- agent-spawn: Create epistemic agent prompt with branch tracking
- agent-report: Report agent postflight results
- agent-aggregate: Aggregate multiple agent results
"""

import json
import sys

from empirica.core.agents import (
    EpistemicAgentConfig,
    spawn_epistemic_agent,
)
from empirica.data.session_database import SessionDatabase


def handle_agent_spawn_command(args) -> dict | int | None:
    """
    Spawn an epistemic agent (returns prompt for external execution).

    Usage:
        empirica agent-spawn --session-id <ID> --task "Review code" --persona security_expert
        empirica agent-spawn --session-id <ID> --task "Review code" --turtle  # auto-select persona

    Returns prompt with branch_id for tracking.
    """

    session_id = getattr(args, 'session_id', None)
    task = getattr(args, 'task', None)
    persona_id = getattr(args, 'persona', 'general')
    use_turtle = getattr(args, 'turtle', False)
    parent_context = getattr(args, 'context', None)
    output_format = getattr(args, 'output', 'text')

    if not session_id:
        return {"ok": False, "error": "session_id required"}

    if not task:
        return {"ok": False, "error": "task required"}

    # Turtle mode: auto-select best emerged persona for task
    # Uses sentinel matching with grounding if available
    turtle_match = None
    if use_turtle:
        try:
            from empirica.core.emerged_personas import sentinel_match_persona
            # Try to get current grounding from session's last checkpoint
            grounding = None
            try:
                db = SessionDatabase()
                cursor = db.conn.cursor()
                # Vectors live in individual reflexes columns, not a JSON blob
                # (reflex_data only holds prompt_summary/uncertainty_notes).
                # Kept as a static query so the schema-reference guard validates it.
                cursor.execute("""
                    SELECT know, do, context, clarity, coherence, signal,
                           density, state, change, completion, impact,
                           uncertainty, engagement
                    FROM reflexes
                    WHERE session_id = ? AND phase IN ('PREFLIGHT', 'POSTFLIGHT')
                    ORDER BY timestamp DESC LIMIT 1
                """, (session_id,))
                row = cursor.fetchone()
                if row:
                    vector_cols = [
                        'know', 'do', 'context', 'clarity', 'coherence',
                        'signal', 'density', 'state', 'change', 'completion',
                        'impact', 'uncertainty', 'engagement',
                    ]
                    grounding = {
                        col: row[i] for i, col in enumerate(vector_cols)
                        if row[i] is not None
                    }
                db.close()
            except Exception:
                pass

            turtle_match = sentinel_match_persona(
                task=task,
                grounding_vectors=grounding,
                min_reputation=0.5
            )
            if turtle_match:
                persona_id = turtle_match.persona_id
        except Exception:
            pass  # Fall back to default persona

    # Read active transaction for epistemic continuity
    transaction_id = None
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        transaction_id = R.transaction_id()
    except Exception:
        pass  # Transaction linkage is optional

    config = EpistemicAgentConfig(
        session_id=session_id,
        task=task,
        persona_id=persona_id,
        parent_context=parent_context,
        transaction_id=transaction_id
    )

    result = spawn_epistemic_agent(config, execute_fn=None)

    response = {
        "ok": True,
        "branch_id": result.branch_id,
        "persona_id": result.persona_id,
        "preflight_vectors": result.preflight_vectors,
        "prompt": result.output,
        "usage": f"Execute prompt with your agent, then: empirica agent-report --branch-id {result.branch_id} -",
        "turtle_mode": use_turtle,
        "turtle_match": turtle_match.name if turtle_match else None,
        "transaction_id": transaction_id,  # Parent's transaction for epistemic continuity
    }

    if output_format == 'json':
        print(json.dumps(response, indent=2))
    else:
        print(f"Branch ID: {result.branch_id}")
        if turtle_match:
            print(f"Persona: {result.persona_id} (turtle-matched: {turtle_match.name})")
        else:
            print(f"Persona: {result.persona_id}")
        print("\n--- AGENT PROMPT ---\n")
        print(result.output)
        print("\n--- END PROMPT ---\n")
        print("After agent completes, report with:")
        print(f"  empirica agent-report --branch-id {result.branch_id} --postflight '<json>'")


def handle_agent_report_command(args) -> dict | int | None:
    """
    Report agent postflight results.

    Usage:
        empirica agent-report --branch-id <ID> --postflight '{"vectors": {...}, "findings": [...]}'

    Or pipe agent output:
        echo "<agent output>" | empirica agent-report --branch-id <ID> -
    """

    branch_id = getattr(args, 'branch_id', None)
    postflight_json = getattr(args, 'postflight', None)
    output_format = getattr(args, 'output', 'text')

    if not branch_id:
        return {"ok": False, "error": "branch_id required"}

    # Read from stdin if '-' provided
    if postflight_json == '-':
        postflight_json = sys.stdin.read()

    if not postflight_json:
        return {"ok": False, "error": "postflight data required (JSON or agent output with postflight block)"}

    # Try to parse as JSON directly
    try:
        data = json.loads(postflight_json)
        if 'vectors' in data:
            postflight_data = data
        else:
            postflight_data = None
    except json.JSONDecodeError:
        # Try to extract postflight block from agent output
        from empirica.core.agents.epistemic_agent import parse_postflight
        postflight_data = parse_postflight(postflight_json, branch_id)

    if not postflight_data:
        return {"ok": False, "error": "Could not parse postflight data. Expected JSON with 'vectors' key or agent output with ```postflight block."}

    # Update branch
    db = SessionDatabase()
    try:
        db.branches.checkpoint_branch(
            branch_id=branch_id,
            postflight_vectors=postflight_data['vectors'],
            tokens_spent=postflight_data.get('tokens_spent', 0),
            time_spent_minutes=postflight_data.get('time_spent_minutes', 0)
        )
        # Calculate merge score after checkpoint
        merge_result = db.branches.calculate_branch_merge_score(branch_id)
        merge_score = merge_result.get('merge_score') if merge_result else None

        # Log findings if any
        findings = postflight_data.get('findings', [])
        unknowns = postflight_data.get('unknowns', [])

        # Embed to Qdrant for semantic search (graceful degradation if unavailable)
        embedded_count = 0
        try:
            import time
            import uuid

            from empirica.core.qdrant.vector_store import embed_single_memory_item, upsert_epistemics

            # Get project_id from branch's session
            branch_data = db.branches.get_branch(branch_id)
            session_id = branch_data.get('session_id') if branch_data else None
            session = db.get_session(session_id) if session_id else None
            project_id = session.get('project_id') if session else None

            if project_id:
                # Embed each finding
                for finding in findings:
                    item_id = str(uuid.uuid4())
                    if embed_single_memory_item(
                        project_id=project_id,
                        item_id=item_id,
                        text=finding,
                        item_type="agent_finding",
                        session_id=session.get('session_id'),
                        impact=merge_score or 0.5
                    ):
                        embedded_count += 1

                # Embed epistemic trajectory (preflight → postflight)
                trajectory_text = f"Agent {branch_id[:8]}: {postflight_data.get('summary', 'epistemic investigation')}"
                upsert_epistemics(project_id, [{
                    "id": int(uuid.uuid4().int % (2**31 - 1)),
                    "text": trajectory_text,
                    "metadata": {
                        "branch_id": branch_id,
                        "postflight_vectors": postflight_data['vectors'],
                        "merge_score": merge_score,
                        "findings_count": len(findings),
                        "timestamp": time.time()
                    }
                }])
        except Exception:
            # Qdrant embedding is optional - don't fail the command
            pass

        response = {
            "ok": True,
            "branch_id": branch_id,
            "postflight_vectors": postflight_data['vectors'],
            "merge_score": merge_score,
            "findings_count": len(findings),
            "unknowns_count": len(unknowns),
            "findings": findings,
            "unknowns": unknowns,
            "embedded_to_qdrant": embedded_count
        }

        if output_format == 'json':
            print(json.dumps(response, indent=2))
        else:
            print(f"Branch {branch_id[:8]}... updated")
            print(f"Merge Score: {merge_score:.4f}" if isinstance(merge_score, (int, float)) else "Merge Score: pending")
            print(f"Findings: {len(findings)}, Unknowns: {len(unknowns)}")
            if findings:
                print("\nFindings:")
                for f in findings[:5]:
                    print(f"  - {f}")

        return 0  # Success exit code

    finally:
        db.close()


def handle_agent_aggregate_command(args) -> dict | int | None:
    """
    Aggregate results from multiple epistemic agents.

    Usage:
        empirica agent-aggregate --session-id <ID> --round 1

    Runs auto-merge scoring on all active branches in session.
    """

    session_id = getattr(args, 'session_id', None)
    investigation_round = getattr(args, 'round', 1)
    output_format = getattr(args, 'output', 'text')

    if not session_id:
        return {"ok": False, "error": "session_id required"}

    db = SessionDatabase()
    try:
        # Get all active branches for session
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT id, branch_name, investigation_path,
                   preflight_vectors, postflight_vectors,
                   merge_score, status
            FROM investigation_branches
            WHERE session_id = ? AND status = 'active'
            ORDER BY created_timestamp DESC
        """, (session_id,))

        branches = cursor.fetchall()

        if not branches:
            return {"ok": False, "error": "No active branches found for session"}

        # Run merge
        merge_result = db.branches.merge_branches(
            session_id=session_id,
            investigation_round=investigation_round
        )

        response = {
            "ok": True,
            "session_id": session_id,
            "investigation_round": investigation_round,
            "branches_evaluated": len(branches),
            "winner": merge_result.get('winning_branch_name'),
            "winning_score": merge_result.get('winning_score'),
            "decision_rationale": merge_result.get('decision_rationale'),
            "merge_decision_id": merge_result.get('decision_id')
        }

        if output_format == 'json':
            print(json.dumps(response, indent=2))
        else:
            print(f"Session: {session_id[:8]}...")
            print(f"Round: {investigation_round}")
            print(f"Branches Evaluated: {len(branches)}")
            print(f"\nWinner: {merge_result.get('winning_branch_name')}")
            print(f"Score: {merge_result.get('winning_score', 0):.4f}")
            print(f"\nRationale: {merge_result.get('decision_rationale')}")

        return None  # Success - output already printed

    finally:
        db.close()


def handle_agent_export_command(args) -> dict | int | None:
    """
    Export an epistemic agent as a shareable JSON package.

    The export contains:
    - Persona profile (vectors, thresholds, focus domains)
    - Accumulated findings from the branch
    - Calibration data (learning deltas, merge scores)
    - Provenance (project, session, branch info)

    Usage:
        empirica agent-export --branch-id <ID> --output-file agent.json
        empirica agent-export --branch-id <ID> --register  # Register to sharing network
    """
    import time

    branch_id = getattr(args, 'branch_id', None)
    output_file = getattr(args, 'output_file', None)
    register = getattr(args, 'register', False)
    getattr(args, 'output', 'json')

    if not branch_id:
        return {"ok": False, "error": "branch_id required"}

    db = SessionDatabase()
    try:
        # Get branch data
        branch = db.branches.get_branch(branch_id)
        if not branch:
            return {"ok": False, "error": f"Branch {branch_id} not found"}

        # Get session and project info
        session = db.get_session(branch['session_id'])
        project_id = session.get('project_id') if session else None

        # Calculate learning delta
        preflight = branch.get('preflight_vectors', {})
        postflight = branch.get('postflight_vectors', {})
        learning_delta = {}
        for key in ['know', 'uncertainty', 'context', 'clarity', 'completion', 'impact']:
            learning_delta[key] = postflight.get(key, 0.5) - preflight.get(key, 0.5)

        # Build exportable agent package
        agent_package = {
            "format_version": "1.0",
            "export_timestamp": time.time(),
            "agent_type": "epistemic",

            # Identity
            "agent_id": f"agent-{branch_id[:8]}",
            "branch_id": branch_id,
            "persona_id": branch.get('branch_name', 'general').replace('agent-', ''),

            # Epistemic profile
            "epistemic_profile": {
                "preflight_vectors": preflight,
                "postflight_vectors": postflight,
                "learning_delta": learning_delta,
                "merge_score": branch.get('merge_score')
            },

            # Provenance
            "provenance": {
                "project_id": project_id,
                "session_id": branch['session_id'],
                "investigation_path": branch.get('investigation_path'),
                "status": branch.get('status')
            },

            # For the sharing network
            "shareable": True,
            "reputation_seed": branch.get('merge_score') or 0.5
        }

        # Register to PersonaRegistry if requested
        registered_id = None
        if register:
            try:
                from empirica.core.qdrant.persona_registry import PersonaRegistry
                registry = PersonaRegistry()
                registered_id = registry.register_agent(agent_package)
                print(f"📡 Registered to sharing network (ID: {registered_id})")
            except Exception as e:
                print(f"⚠️  Registration failed (Qdrant unavailable?): {e}")

        # Output
        if output_file:
            with open(output_file, 'w') as f:
                json.dump(agent_package, f, indent=2)
            print(f"✅ Agent exported to {output_file}")
            print(f"   Agent ID: {agent_package['agent_id']}")
            print(f"   Merge Score: {branch.get('merge_score', 'N/A')}")
        else:
            print(json.dumps(agent_package, indent=2))

        return 0

    finally:
        db.close()


def handle_agent_import_command(args) -> dict | int | None:
    """
    Import an epistemic agent from a JSON package.

    Creates a new investigation branch with the imported agent's
    epistemic profile as preflight vectors.

    Usage:
        empirica agent-import --session-id <ID> --input-file agent.json
    """

    session_id = getattr(args, 'session_id', None)
    input_file = getattr(args, 'input_file', None)
    output_format = getattr(args, 'output', 'json')

    if not session_id:
        return {"ok": False, "error": "session_id required"}

    if not input_file:
        return {"ok": False, "error": "input_file required"}

    # Load agent package
    try:
        with open(input_file) as f:
            agent_package = json.load(f)
    except Exception as e:
        return {"ok": False, "error": f"Failed to load agent file: {e}"}

    # Validate format
    if agent_package.get('format_version') != '1.0':
        return {"ok": False, "error": "Unsupported agent format version"}

    db = SessionDatabase()
    try:
        # Use the agent's postflight as our preflight (inheriting learned state)
        inherited_vectors = agent_package.get('epistemic_profile', {}).get('postflight_vectors', {})
        if not inherited_vectors:
            inherited_vectors = agent_package.get('epistemic_profile', {}).get('preflight_vectors', {})

        # Create new branch with inherited vectors
        branch_id = db.branches.create_branch(
            session_id=session_id,
            branch_name=f"imported-{agent_package.get('agent_id', 'agent')}",
            investigation_path=f"imported-from-{agent_package.get('provenance', {}).get('project_id', 'unknown')[:8]}",
            git_branch_name="",
            preflight_vectors=inherited_vectors
        )

        response = {
            "ok": True,
            "imported_agent_id": agent_package.get('agent_id'),
            "new_branch_id": branch_id,
            "inherited_vectors": inherited_vectors,
            "source_merge_score": agent_package.get('epistemic_profile', {}).get('merge_score'),
            "provenance": agent_package.get('provenance')
        }

        if output_format == 'json':
            print(json.dumps(response, indent=2))
        else:
            print("✅ Agent imported successfully")
            print(f"   Imported: {agent_package.get('agent_id')}")
            print(f"   New Branch: {branch_id[:8]}...")
            print(f"   Source Score: {agent_package.get('epistemic_profile', {}).get('merge_score', 'N/A')}")

        return 0

    finally:
        db.close()


def handle_agent_discover_command(args) -> dict | int | None:
    """
    Discover epistemic agents in the sharing network.

    Search by domain expertise or reputation score.

    Usage:
        empirica agent-discover --domain security
        empirica agent-discover --min-reputation 0.7
    """

    domain = getattr(args, 'domain', None)
    min_reputation = getattr(args, 'min_reputation', None)
    limit = getattr(args, 'limit', 10)
    output_format = getattr(args, 'output', 'text')

    try:
        from empirica.core.qdrant.persona_registry import PersonaRegistry
        registry = PersonaRegistry()

        if domain:
            agents = registry.find_agents_by_domain(domain, limit=limit)
            search_type = f"domain: {domain}"
        elif min_reputation is not None:
            agents = registry.find_agents_by_reputation(min_reputation, limit=limit)
            search_type = f"reputation >= {min_reputation}"
        else:
            # List all agents
            agents = registry.find_agents_by_reputation(0.0, limit=limit)
            search_type = "all agents"

        response = {
            "ok": True,
            "search_type": search_type,
            "agents_found": len(agents),
            "agents": agents
        }

        if output_format == 'json':
            print(json.dumps(response, indent=2))
        else:
            print(f"🔍 Searching: {search_type}")
            print(f"   Found: {len(agents)} agents\n")

            for agent in agents:
                print(f"   [{agent.get('agent_id', 'unknown')}]")
                print(f"      Type: {agent.get('persona_type', 'general')}")
                print(f"      Domains: {', '.join(agent.get('focus_domains', []))}")
                print(f"      Reputation: {agent.get('reputation_score', 0):.2f}")
                delta = agent.get('learning_delta', {})
                if delta:
                    print(f"      Learning: know {delta.get('know', 0):+.2f}, uncertainty {delta.get('uncertainty', 0):+.2f}")
                print()

        return 0

    except Exception as e:
        error_msg = f"Discovery failed: {e}"
        if output_format == 'json':
            print(json.dumps({"ok": False, "error": error_msg}))
        else:
            print(f"❌ {error_msg}")
        return 1


def handle_agent_parallel_command(args) -> dict | int | None:
    """
    Plan and orchestrate parallel epistemic agents with attention budget.

    Usage:
        empirica agent-parallel --session-id <ID> --task "Investigate X" --budget 20 --max-agents 3
        empirica agent-parallel --session-id <ID> --task "Review Y" --domains security architecture

    Returns orchestration plan with agent allocations and budget.
    """

    session_id = getattr(args, 'session_id', None)
    task = getattr(args, 'task', None)
    budget = getattr(args, 'budget', 20)
    max_agents = getattr(args, 'max_agents', 5)
    strategy = getattr(args, 'strategy', 'information_gain')
    domains = getattr(args, 'domains', None)
    output_format = getattr(args, 'output', 'text')

    if not session_id:
        return {"ok": False, "error": "session_id required"}

    if not task:
        return {"ok": False, "error": "task required"}

    try:
        from empirica.core.parallel_orchestrator import ParallelOrchestrator

        orchestrator = ParallelOrchestrator(
            session_id=session_id,
            max_agents=max_agents,
            total_budget=budget,
            strategy=strategy,
        )

        plan = orchestrator.plan(
            task=task,
            domains=domains,
        )

        response = {
            "ok": True,
            "session_id": session_id,
            "task": task,
            "budget": {
                "id": plan.budget.id,
                "total": plan.budget.total_budget,
                "remaining": plan.budget.remaining,
                "strategy": plan.budget.strategy,
            },
            "agents": [a.to_dict() for a in plan.agents],
            "agent_count": len(plan.agents),
            "usage": (
                "Launch agents in parallel via Claude Code Task tool. "
                "Each agent's SubagentStart/Stop hooks will handle session "
                "creation, budget tracking, and scored rollup automatically."
            ),
        }

        if output_format == 'json':
            print(json.dumps(response, indent=2))
        else:
            print("Parallel Orchestration Plan")
            print(f"{'='*50}")
            print(f"Task: {task}")
            print(f"Budget: {plan.budget.total_budget} findings")
            print(f"Strategy: {plan.budget.strategy}")
            print(f"Agents: {len(plan.agents)}")
            print()

            for i, agent in enumerate(plan.agents, 1):
                print(f"  Agent {i}: {agent.agent_name}")
                print(f"    Domain: {agent.domain}")
                print(f"    Persona: {agent.persona_id}")
                print(f"    Budget: {agent.budget} findings")
                print(f"    Priority: {agent.priority:.2f}")
                print(f"    Expected Gain: {agent.expected_gain:.3f}")
                print()

            print(f"Budget ID: {plan.budget.id}")
            print("\nLaunch agents with:")
            for agent in plan.agents:
                print(f"  empirica agent-spawn --session-id {session_id} "
                      f"--task \"{agent.task_focus}\" --turtle")

        return 0

    except Exception as e:
        error_msg = f"Parallel orchestration failed: {e}"
        if output_format == 'json':
            print(json.dumps({"ok": False, "error": error_msg}))
        else:
            print(f"Error: {error_msg}")
        return 1


