"""
Sentinel CLI Commands - Orchestration and Domain Profile Management

Commands:
- sentinel-orchestrate: Run autonomous orchestration with persona selection
- sentinel-load-profile: Load domain profile for compliance gates
- sentinel-status: Show current Sentinel status and loop tracking
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def handle_sentinel_orchestrate_command(args):
    """Handle sentinel-orchestrate command - autonomous multi-agent orchestration"""
    try:
        from empirica.core.sentinel import MergeStrategy, Sentinel

        session_id = args.session_id
        task = args.task
        max_agents = getattr(args, "max_agents", 3)
        profile = getattr(args, "profile", None)
        scope_breadth = getattr(args, "scope_breadth", 0.5)
        scope_duration = getattr(args, "scope_duration", 0.5)
        merge = getattr(args, "merge", "union")
        dry_run = getattr(args, "dry_run", False)
        output_format = getattr(args, "output", "human")

        # Create Sentinel
        sentinel = Sentinel(session_id=session_id)

        # Load domain profile if specified
        if profile:
            sentinel.load_domain_profile(profile)

        # Map merge strategy
        strategy_map = {
            "union": MergeStrategy.UNION,
            "consensus": MergeStrategy.CONSENSUS,
            "best_score": MergeStrategy.BEST_SCORE,
            "weighted": MergeStrategy.WEIGHTED,
        }
        merge_strategy = strategy_map.get(merge, MergeStrategy.UNION)

        if dry_run:
            # Just select personas, don't spawn
            personas = sentinel.select_personas(task, max_personas=max_agents)
            result: dict[str, Any] = {
                "ok": True,
                "dry_run": True,
                "task": task,
                "personas_selected": [p.to_dict() for p in personas],
                "profile": profile,
                "scope": {"breadth": scope_breadth, "duration": scope_duration},
                "estimated_loops": int((scope_breadth + scope_duration) / 2 * 8) + 1,
            }
        else:
            # Full orchestration
            orch_result = sentinel.auto_orchestrate(
                task=task,
                max_agents=max_agents,
                merge_strategy=merge_strategy,
                scope_breadth=scope_breadth,
                scope_duration=scope_duration,
            )
            result = orch_result.to_dict()

        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Sentinel Orchestration {'(dry run)' if dry_run else ''}")
            print(f"  Task: {task[:60]}...")
            print(f"  Personas: {[p.get('persona_id') for p in result.get('personas_selected', [])]}")
            if not dry_run:
                print(f"  Agents spawned: {len(result.get('agents_spawned', []))}")
                print(f"  Findings: {len(result.get('aggregated_findings', []))}")
            if profile:
                print(f"  Domain profile: {profile}")
            if not result.get("ok", True):
                print(f"  ❌ {result.get('error') or 'orchestration failed'}")

        return 0 if result.get("ok", True) else 1

    except Exception as e:
        logger.error(f"Sentinel orchestrate failed: {e}")
        print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        return 1


def handle_sentinel_load_profile_command(args):
    """Handle sentinel-load-profile command - load domain compliance profile"""
    try:
        from empirica.core.sentinel import Sentinel

        session_id = args.session_id
        profile_name = args.profile
        profile_file = getattr(args, "file", None)
        output_format = getattr(args, "output", "human")

        # Create Sentinel
        sentinel = Sentinel(session_id=session_id)

        # Load custom file if provided
        custom_profile = None
        if profile_file:
            import yaml

            with open(profile_file) as f:
                custom_profile = yaml.safe_load(f)

        # Load profile
        profile = sentinel.load_domain_profile(profile_name, custom_profile)

        result = {
            "ok": True,
            "profile": profile.name,
            "compliance_framework": profile.compliance_framework,
            "uncertainty_trigger": profile.uncertainty_trigger,
            "confidence_to_proceed": profile.confidence_to_proceed,
            "gates_count": len(profile.gates),
            "gates": [{"id": g.gate_id, "condition": g.condition, "action": g.action.value} for g in profile.gates],
            "audit_configured": profile.audit_all_actions,
            "enforced": False,  # configuration only: nothing writes an audit trail or restricts tools (4938f99e)
        }

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"Domain Profile Loaded: {profile.name}")
            if profile.compliance_framework:
                print(f"  Compliance: {profile.compliance_framework}")
            print(f"  Uncertainty trigger: {profile.uncertainty_trigger}")
            print(f"  Confidence to proceed: {profile.confidence_to_proceed}")
            print(f"  Gates: {len(profile.gates)}")
            for g in profile.gates:
                print(f"    - {g.gate_id}: {g.condition} → {g.action.value}")

        return None

    except Exception as e:
        logger.error(f"Load profile failed: {e}")
        print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        return 1


def handle_sentinel_status_command(args):
    """Handle sentinel-status command - show Sentinel status and loop tracking"""
    try:
        from empirica.core.sentinel import Sentinel

        session_id = args.session_id
        output_format = getattr(args, "output", "human")

        # Note: In real usage, Sentinel state would be persisted
        # For now, create fresh and show what would be tracked
        sentinel = Sentinel(session_id=session_id)

        result: dict[str, Any] = {
            "ok": True,
            "session_id": session_id,
            "domain_profile": sentinel.get_domain_stats(),
            "loop_tracking": sentinel.get_loop_summary(),
            "available_profiles": list(Sentinel.DEFAULT_PROFILES.keys()),
        }

        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Sentinel Status for session {session_id[:8]}...")
            print(f"  Domain profile: {result['domain_profile'].get('profile', 'None')}")
            print(f"  Loop tracking: {'Active' if result['loop_tracking'] else 'Not initialized'}")
            print(f"  Available profiles: {', '.join(result['available_profiles'])}")

        return None

    except Exception as e:
        logger.error(f"Sentinel status failed: {e}")
        print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        return 1


def handle_sentinel_check_command(args):
    """Handle sentinel-check command - run compliance check with domain gates"""
    try:
        import sys

        from empirica.core.sentinel import Sentinel

        session_id = args.session_id
        profile = getattr(args, "profile", None)
        output_format = getattr(args, "output", "human")

        # Read vectors from stdin or args
        vectors_json = getattr(args, "vectors", None)
        if vectors_json == "-":
            vectors_json = sys.stdin.read()

        if vectors_json:
            vectors = json.loads(vectors_json)
        else:
            vectors = {"know": getattr(args, "know", 0.5), "uncertainty": getattr(args, "uncertainty", 0.5)}

        findings = getattr(args, "findings", []) or []
        unknowns = getattr(args, "unknowns", []) or []

        # Create Sentinel and load profile
        sentinel = Sentinel(session_id=session_id)
        if profile:
            sentinel.load_domain_profile(profile)

        # Run compliance check
        result = sentinel.check_compliance(vectors=vectors, findings=findings, unknowns=unknowns)
        result["ok"] = True

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            decision = result.get("decision", "unknown")
            icon = {"proceed": "✅", "investigate": "🔍", "halt": "🛑", "require_human": "👤"}.get(decision, "❓")
            print(f"{icon} Decision: {decision.upper()}")
            print(f"  Rationale: {result.get('rationale', 'N/A')}")
            if result.get("triggered_gates"):
                print(f"  Triggered gates: {', '.join(result['triggered_gates'])}")
            if result.get("not_evaluated"):
                # Shown in human mode too: a gate never evaluated must not read as one that passed (1.17.10 sweep)
                print(f"  Not evaluated (input not supplied): {', '.join(result['not_evaluated'])}")
            if result.get("profile"):
                print(f"  Profile: {result['profile']} ({result.get('framework', '')})")

        return None

    except Exception as e:
        logger.error(f"Sentinel check failed: {e}")
        print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        return 1
