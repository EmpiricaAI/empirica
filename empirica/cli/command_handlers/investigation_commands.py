"""Investigation commands: ``investigate`` (retrieval) and the epistemic branch verbs.

``empirica investigate <query>`` is the retrieval verb the system prompt and
``noetic-batch`` describe: an in-process alias of ``project-search --task``.
It used to dispatch on the target instead — a file or directory went to two
analyzer modules that were never shipped (every call returned an error dict
under a "✅ Investigation complete" banner and exited 0), ``--type
comprehensive`` raised on a field the parser never set, and a concept
returned a hardcoded mock with ``analysis_depth: 0.7``. Zero working paths for
the verb's whole life; the docs described output nothing produced.

A target that names an existing file or directory is refused with exit 1
rather than searched: that caller wanted analysis, which this verb does not
do — ``Read``/``Grep`` do.
"""

import argparse
import json
import logging
import os
from typing import Any

from ..cli_utils import handle_cli_error, parse_json_safely

logger = logging.getLogger(__name__)


def handle_investigate_command(args):
    """Semantic retrieval over this project's docs + memory (``project-search``)."""
    from .project_search import handle_project_search_command

    target = args.target
    output = getattr(args, "output", "human")
    if os.path.exists(target):
        result = {
            "ok": False,
            "error": f"investigate does not analyze files or directories: {target}",
            "hint": "read it with Read/Grep, or pass a question to retrieve what the practice already knows about it",
        }
        if output == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"❌ {result['error']}")
            print(f"   {result['hint']}")
        return 1

    return handle_project_search_command(
        argparse.Namespace(
            project_id=None,
            task=target,
            type="focused",
            limit=getattr(args, "limit", 5),
            output=output,
            verbose=getattr(args, "verbose", False),
            global_search=getattr(args, "global_search", False),
        )
    )


# ========== Epistemic Branching Commands ==========


def handle_investigate_create_branch_command(args):
    """Handle investigate-create-branch command - Create parallel investigation path"""
    try:
        from empirica.data.session_database import SessionDatabase

        session_id = args.session_id
        investigation_path = args.investigation_path
        description = getattr(args, "description", None)
        preflight_vectors_str = args.preflight_vectors or "{}"

        # Parse epistemic vectors
        preflight_vectors = parse_json_safely(preflight_vectors_str)
        if not isinstance(preflight_vectors, dict):
            raise ValueError("Preflight vectors must be a JSON dict")

        db = SessionDatabase()

        # Generate branch names
        branch_name = f"investigate-{investigation_path}"
        git_branch_name = f"feature/investigate-{investigation_path}"

        # Create branch in database
        branch_id = db.create_branch(
            session_id=session_id,
            branch_name=branch_name,
            investigation_path=investigation_path,
            git_branch_name=git_branch_name,
            preflight_vectors=preflight_vectors,
        )

        db.close()

        result = {
            "ok": True,
            "branch_id": branch_id,
            "branch_name": branch_name,
            "git_branch_name": git_branch_name,
            "investigation_path": investigation_path,
            "message": f"Created investigation branch: {git_branch_name}",
        }

        # Format output
        if hasattr(args, "output") and args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print("✅ Investigation branch created")
            print(f"   Branch: {git_branch_name}")
            print(f"   Path: {investigation_path}")
            print(f"   ID: {branch_id[:8]}...")
            if description:
                print(f"   Description: {description}")

        return result

    except Exception as e:
        handle_cli_error(e, "Create investigation branch", getattr(args, "verbose", False))


def handle_investigate_checkpoint_branch_command(args):
    """Handle investigate-checkpoint-branch command - Checkpoint branch after investigation"""
    try:
        from empirica.data.session_database import SessionDatabase

        branch_id = args.branch_id
        postflight_vectors_str = args.postflight_vectors or "{}"
        tokens_spent = int(args.tokens_spent or 0)
        time_spent = int(args.time_spent or 0)

        # Parse vectors
        postflight_vectors = parse_json_safely(postflight_vectors_str)
        if not isinstance(postflight_vectors, dict):
            raise ValueError("Postflight vectors must be a JSON dict")

        db = SessionDatabase()

        # Checkpoint the branch
        success = db.checkpoint_branch(
            branch_id=branch_id,
            postflight_vectors=postflight_vectors,
            tokens_spent=tokens_spent,
            time_spent_minutes=time_spent,
        )

        # Calculate merge score
        if success:
            score_data = db.calculate_branch_merge_score(branch_id)

        db.close()

        result = {
            "ok": success,
            "branch_id": branch_id,
            "tokens_spent": tokens_spent,
            "time_spent_minutes": time_spent,
            "merge_score": score_data.get("merge_score", 0),
            "quality": score_data.get("quality", 0),
            "confidence": score_data.get("confidence", 0),
            "message": f"Branch checkpointed with merge score: {score_data.get('merge_score', 0):.4f}",
        }

        # Format output
        if hasattr(args, "output") and args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print("✅ Branch checkpointed successfully")
            print(f"   Merge Score: {score_data.get('merge_score', 0):.4f}")
            print(f"   Quality: {score_data.get('quality', 0):.4f}")
            print(f"   Confidence: {score_data.get('confidence', 0):.4f}")
            print(f"   Uncertainty (dampener): {score_data.get('uncertainty_dampener', 0):.4f}")
            print(f"   Tokens spent: {tokens_spent}")
            print(f"   Time spent: {time_spent} minutes")

        return result

    except Exception as e:
        handle_cli_error(e, "Checkpoint investigation branch", getattr(args, "verbose", False))


def _merge_tag_losing_branches(db, session_id, merge_result):
    """Tag losing branches as dead ends in DB and Qdrant.

    Returns (dead_ends_logged, dead_ends_embedded).
    """
    dead_ends_logged = 0
    dead_ends_embedded = 0

    winning_name = merge_result["winning_branch_name"]
    winning_score = merge_result["winning_score"]
    winning_branch_id = merge_result["winning_branch_id"]

    project_id = None
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            project_id = row[0]
    except Exception as e:
        # Without the project id the dead-ends below are logged unscoped and
        # never embedded; say so rather than tag losers into the void.
        logger.warning(
            "could not resolve project_id for session %s: %s — dead-ends logged unscoped, not embedded", session_id, e
        )

    for loser in merge_result["other_branches"]:
        loser_name = loser.get("branch_name", "unknown")
        loser_score = loser.get("score", 0)
        loser_branch_id = loser.get("branch_id")
        score_diff = winning_score - loser_score
        approach = f"Investigation branch: {loser_name}"
        why_failed = (
            f"Lost epistemic merge to {winning_name} (score diff: {score_diff:.4f}). "
            f"Branch score: {loser_score:.4f} vs winner: {winning_score:.4f}"
        )

        db.log_project_dead_end(
            project_id=project_id,
            session_id=session_id,
            approach=approach,
            why_failed=why_failed,
            goal_id=None,
            subtask_id=None,
        )
        dead_ends_logged += 1

        if project_id:
            try:
                from empirica.core.qdrant.vector_store import embed_dead_end_with_branch_context

                embedded = embed_dead_end_with_branch_context(
                    project_id=project_id,
                    dead_end_id=f"{session_id}_{loser_branch_id}",
                    approach=approach,
                    why_failed=why_failed,
                    session_id=session_id,
                    branch_id=loser_branch_id,
                    winning_branch_id=winning_branch_id,
                    score_diff=score_diff,
                    preflight_vectors=loser.get("preflight_vectors"),
                    postflight_vectors=loser.get("postflight_vectors"),
                )
                if embedded:
                    dead_ends_embedded += 1
            except ImportError as e:
                logger.warning("dead-end for branch %s not embedded: %s", loser_branch_id, e)

    return dead_ends_logged, dead_ends_embedded


def handle_investigate_merge_branches_command(args):
    """Handle investigate-merge-branches command - Auto-merge best branch based on epistemic scores"""
    try:
        from empirica.data.session_database import SessionDatabase

        session_id = args.session_id
        investigation_round = int(getattr(args, "round", 1) or 1)
        tag_losers = getattr(args, "tag_losers", False)

        db = SessionDatabase()

        merge_result = db.merge_branches(session_id=session_id, investigation_round=investigation_round)

        if "error" in merge_result:
            db.close()
            result = {"ok": False, "error": merge_result["error"]}
        else:
            dead_ends_logged = 0
            dead_ends_embedded = 0
            if tag_losers and merge_result.get("other_branches"):
                dead_ends_logged, dead_ends_embedded = _merge_tag_losing_branches(db, session_id, merge_result)

            db.close()

            result = {
                "ok": True,
                "winning_branch_id": merge_result["winning_branch_id"],
                "winning_branch_name": merge_result["winning_branch_name"],
                "winning_score": merge_result["winning_score"],
                "merge_decision_id": merge_result["merge_decision_id"],
                "other_branches": merge_result["other_branches"],
                "rationale": merge_result["rationale"],
                "message": f"Auto-merged {merge_result['winning_branch_name']} (score: {merge_result['winning_score']:.4f})",
                "dead_ends_logged": dead_ends_logged if tag_losers else None,
                "dead_ends_embedded": dead_ends_embedded if tag_losers else None,
            }

        if hasattr(args, "output") and args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            if result.get("ok"):
                print("Epistemic Auto-Merge Complete")
                print(f"   Winner: {merge_result['winning_branch_name']}")
                print(f"   Merge Score: {merge_result['winning_score']:.4f}")
                print(f"   Decision ID: {merge_result['merge_decision_id'][:8]}...")
                print(f"   Evaluated {len(merge_result['other_branches']) + 1} paths")
                print(f"   Rationale: {merge_result['rationale']}")
                if tag_losers and dead_ends_logged > 0:
                    embedded_info = f" ({dead_ends_embedded} embedded to Qdrant)" if dead_ends_embedded > 0 else ""
                    print(f"   Dead ends logged: {dead_ends_logged}{embedded_info}")
            else:
                print(f"Merge failed: {result.get('error')}")

        return result

    except Exception as e:
        handle_cli_error(e, "Merge investigation branches", getattr(args, "verbose", False))


def handle_investigate_multi_command(args):
    """
    Multi-persona parallel investigation with epistemic auto-merge.

    Spawns parallel epistemic agents with different persona priors,
    then aggregates results using merge scoring.

    Usage:
        empirica investigate-multi --task "Review auth code" --personas security,ux --session-id <ID>
    """
    try:
        from empirica.core.agents import EpistemicAgentConfig, spawn_epistemic_agent
        from empirica.core.persona import PersonaManager
        from empirica.data.session_database import SessionDatabase

        session_id = args.session_id
        task = args.task
        personas_str = args.personas
        context = getattr(args, "context", None)
        strategy = getattr(args, "aggregate_strategy", "epistemic-score")
        output_format = getattr(args, "output", "human")

        # Parse personas
        persona_ids = [p.strip() for p in personas_str.split(",")]

        # Load personas
        manager = PersonaManager()
        loaded_personas = {}
        for pid in persona_ids:
            try:
                loaded_personas[pid] = manager.load_persona(pid)
            except FileNotFoundError:
                # Fall back to general persona with modified name
                loaded_personas[pid] = None  # Will use default

        # Spawn agents for each persona
        db = SessionDatabase()
        branches = {}

        for pid in persona_ids:
            config = EpistemicAgentConfig(
                session_id=session_id,
                task=task,
                persona_id=pid,
                persona=loaded_personas.get(pid),
                investigation_path=f"multi-{pid}",
                parent_context=context,
            )
            result = spawn_epistemic_agent(config, execute_fn=None)
            branches[pid] = {
                "branch_id": result.branch_id,
                "persona_id": pid,
                "preflight_vectors": result.preflight_vectors,
                "prompt": result.output,
            }

        # Build response
        response: dict[str, Any] = {
            "ok": True,
            "session_id": session_id,
            "task": task,
            "personas": persona_ids,
            "branches": branches,
            "aggregate_strategy": strategy,
            "next_steps": [
                "Execute each agent's prompt (see branches[persona_id].prompt)",
                "Report results: empirica agent-report --branch-id <ID> --postflight '<json>'",
                f"Aggregate: empirica agent-aggregate --session-id {session_id}",
            ],
        }

        db.close()

        # Output
        if output_format == "json":
            # Don't include full prompts in JSON output (too verbose)
            json_response = {**response}
            for pid in json_response["branches"]:
                json_response["branches"][pid]["prompt"] = (
                    f"[{len(branches[pid]['prompt'])} chars - use --output human to see]"
                )
            print(json.dumps(json_response, indent=2))
        else:
            print("✅ Multi-Persona Investigation Started")
            print(f"   Task: {task}")
            print(f"   Personas: {', '.join(persona_ids)}")
            print(f"   Strategy: {strategy}")
            print("\n📋 Branches Created:")
            for pid, branch in branches.items():
                print(f"\n   [{pid}] Branch: {branch['branch_id'][:8]}...")
                print(
                    f"   Priors: know={branch['preflight_vectors'].get('know', 0.5):.2f}, uncertainty={branch['preflight_vectors'].get('uncertainty', 0.5):.2f}"
                )

            print("\n📝 Next Steps:")
            print("   1. Execute each agent prompt (shown below)")
            print("   2. Report: empirica agent-report --branch-id <ID> --postflight '<json>'")
            print(f"   3. Aggregate: empirica agent-aggregate --session-id {session_id}")

            # Show prompts
            for pid, branch in branches.items():
                print(f"\n{'=' * 60}")
                print(f"PROMPT FOR [{pid}] (branch: {branch['branch_id'][:8]}...)")
                print(f"{'=' * 60}")
                print(branch["prompt"][:1500] + "..." if len(branch["prompt"]) > 1500 else branch["prompt"])

        return 0

    except Exception as e:
        handle_cli_error(e, "Multi-persona investigation", getattr(args, "verbose", False))
