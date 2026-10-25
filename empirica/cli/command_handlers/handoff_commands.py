"""
Handoff Commands - Epistemic session handoff reports

Enables session continuity through compressed semantic summaries.
"""

import json
import logging

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def _deltas_summary(deltas: dict) -> str:
    """KNOW / CONTEXT / STATE deltas for the one-line summary; a delta that was not computed (a vector not assessed on both sides
    is left out of the report) prints n/a rather than a measured-looking +0.00."""

    def one(label: str, key: str) -> str:
        value = deltas.get(key)
        return f"{label} n/a" if value is None else f"{label} {value:+.2f}"

    return f"{one('KNOW', 'know')}, {one('CONTEXT', 'context')}, {one('STATE', 'state')}"


def _handoff_read_config(args):
    """Read config data from file/stdin or return None for legacy mode."""
    import os
    import sys

    from ..cli_utils import parse_json_safely

    if not (hasattr(args, "config") and args.config):
        return None

    if args.config == "-":
        return parse_json_safely(sys.stdin.read())

    if not os.path.exists(args.config):
        print(json.dumps({"ok": False, "error": f"Config file not found: {args.config}"}))
        sys.exit(1)
    with open(args.config) as f:
        return parse_json_safely(f.read())


def _handoff_extract_from_legacy(args):
    """Extract handoff parameters from legacy CLI flags."""
    from ..cli_utils import parse_json_safely

    key_findings = (
        parse_json_safely(args.key_findings) if isinstance(args.key_findings, str) else (args.key_findings or [])
    )
    remaining_unknowns = (
        parse_json_safely(args.remaining_unknowns)
        if args.remaining_unknowns and isinstance(args.remaining_unknowns, str)
        else (args.remaining_unknowns or [])
    )

    if isinstance(key_findings, str):
        key_findings = [key_findings]
    if isinstance(remaining_unknowns, str):
        remaining_unknowns = [remaining_unknowns]
    artifacts = (
        parse_json_safely(args.artifacts)
        if args.artifacts and isinstance(args.artifacts, str)
        else (args.artifacts or [])
    )

    return {
        "session_id": args.session_id,
        "task_summary": args.task_summary,
        "key_findings": key_findings,
        "remaining_unknowns": remaining_unknowns,
        "next_session_context": getattr(args, "next_session_context", None),
        "artifacts": artifacts,
        "planning_only": getattr(args, "planning_only", False),
    }


def _handoff_parse_input(args):
    """Parse handoff input from config file or CLI flags.

    Returns dict with keys: session_id, task_summary, key_findings,
    remaining_unknowns, next_session_context, artifacts, planning_only.
    Exits on validation failure.
    """
    import sys

    config_data = _handoff_read_config(args)

    if config_data:
        parsed = {
            "session_id": config_data.get("session_id"),
            "task_summary": config_data.get("task_summary"),
            "key_findings": config_data.get("key_findings", []),
            "remaining_unknowns": config_data.get("remaining_unknowns", []),
            "next_session_context": config_data.get("next_session_context"),
            "artifacts": config_data.get("artifacts", []),
            "planning_only": config_data.get("planning_only", False),
        }
    else:
        parsed = _handoff_extract_from_legacy(args)

    if not parsed["session_id"]:
        from empirica.utils.session_resolver import InstanceResolver as R

        parsed["session_id"] = R.session_id()

    required = [
        ("session_id", "No active transaction and 'session_id' not in config"),
        ("task_summary", "Config must include 'task_summary' field"),
        ("key_findings", "Config must include 'key_findings' array"),
        ("next_session_context", "Config must include 'next_session_context' field"),
    ]
    for field, error_msg in required:
        if not parsed[field]:
            hint = "Either run PREFLIGHT first, or provide 'session_id' in config" if field == "session_id" else None
            err = {"ok": False, "error": error_msg}
            if hint:
                err["hint"] = hint
            print(json.dumps(err))
            sys.exit(1)

    return parsed


def _handoff_determine_type(session_id, planning_only):
    """Determine handoff type based on available assessments.

    Returns (handoff_type, start_assessment, end_assessment) or
    (None, None, None) if no assessments found.
    """
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    preflight = db.get_preflight_assessment(session_id)
    checks = db.get_check_phase_assessments(session_id)
    postflight = db.get_postflight_assessment(session_id)

    if planning_only:
        return "planning", None, None
    if preflight and postflight:
        return "complete", preflight, postflight
    if preflight and checks:
        return "investigation", preflight, checks[-1]
    if preflight:
        return "preflight_only", preflight, None
    return None, None, None


def _handoff_print_no_assessments():
    """Print help message when no CASCADE assessments are found."""
    print("⚠️  No CASCADE workflow assessments found for this session")
    print()
    print("Three handoff options:")
    print()
    print("Option 1: INVESTIGATION HANDOFF (PREFLIGHT + CHECK)")
    print("  → For specialist handoff after investigation phase")
    print("  $ empirica preflight → investigate → check → handoff-create")
    print("  → Epistemic deltas: PREFLIGHT → CHECK (learning from investigation)")
    print()
    print("Option 2: COMPLETE HANDOFF (PREFLIGHT + POSTFLIGHT)")
    print("  → For full workflow completion")
    print("  $ empirica preflight → work → postflight → handoff-create")
    print("  → Epistemic deltas: PREFLIGHT → POSTFLIGHT (full cycle learning)")
    print()
    print("Option 3: PLANNING HANDOFF (no assessments required)")
    print("  → For documentation-only handoff")
    print("  $ empirica handoff-create --session-id ... --planning-only [other args]")
    print("  → No epistemic deltas (documentation only)")
    print()


def _handoff_generate_report(handoff_type, start_assessment, end_assessment, parsed):
    """Generate handoff report based on type. Returns (handoff, display_name)."""
    from empirica.core.handoff.report_generator import EpistemicHandoffReportGenerator

    generator = EpistemicHandoffReportGenerator()
    common_kwargs = {
        "session_id": parsed["session_id"],
        "task_summary": parsed["task_summary"],
        "key_findings": parsed["key_findings"],
        "remaining_unknowns": parsed["remaining_unknowns"],
        "next_session_context": parsed["next_session_context"],
        "artifacts_created": parsed["artifacts"],
    }

    if handoff_type == "planning":
        handoff = generator.generate_planning_handoff(**common_kwargs)
        return handoff, "📋 Planning Handoff"

    if handoff_type == "investigation":
        handoff = generator.generate_handoff_report(
            **common_kwargs,
            start_assessment=start_assessment,
            end_assessment=end_assessment,
            handoff_subtype="investigation",
        )
        handoff["handoff_subtype"] = "investigation"
        handoff["epistemic_note"] = "PREFLIGHT → CHECK deltas (investigation phase)"
        return handoff, "🔬 Investigation Handoff (PREFLIGHT→CHECK)"

    if handoff_type == "complete":
        handoff = generator.generate_handoff_report(
            **common_kwargs,
            start_assessment=start_assessment,
            end_assessment=end_assessment,
            handoff_subtype="complete",
        )
        handoff["handoff_subtype"] = "complete"
        handoff["epistemic_note"] = "PREFLIGHT → POSTFLIGHT deltas (full cycle)"
        return handoff, "📊 Complete Handoff (PREFLIGHT→POSTFLIGHT)"

    # preflight_only
    handoff = generator.generate_planning_handoff(**common_kwargs)
    handoff["handoff_subtype"] = "preflight_only"
    handoff["epistemic_note"] = "Only PREFLIGHT available (aborted session)"
    return handoff, "⚠️  Preflight-Only Handoff (incomplete)"


def _handoff_format_output(args, session_id, handoff_type, handoff, sync_result, handoff_display_name):
    """Format and print handoff output in JSON or human-readable format."""
    if hasattr(args, "output") and args.output == "json":
        result = {
            "ok": True,
            "session_id": session_id,
            "handoff_id": handoff["session_id"],
            "handoff_type": handoff_type,
            "handoff_subtype": handoff.get("handoff_subtype", handoff_type),
            "token_count": len(handoff.get("compressed_json", "")) // 4,
            "storage": f"git:refs/notes/empirica/handoff/{session_id}",
            "has_epistemic_deltas": handoff_type in ["investigation", "complete"],
            "epistemic_deltas": handoff.get("epistemic_deltas", {}),
            "epistemic_note": handoff.get("epistemic_note", ""),
            "calibration_status": handoff.get("calibration_status", "N/A"),
            "storage_sync": sync_result,
        }
        print(json.dumps(result, indent=2))
    else:
        print(f"✅ {handoff_display_name} created successfully")
        print(f"   Session: {session_id[:8]}...")
        print(f"   Type: {handoff_type}")
        if handoff.get("epistemic_note"):
            print(f"   Note: {handoff['epistemic_note']}")
        print(f"   Token count: ~{len(handoff.get('compressed_json', '')) // 4} tokens")
        print("   Storage: git notes (refs/notes/empirica/handoff/)")
        if handoff_type in ["investigation", "complete"]:
            print(f"   Calibration: {handoff.get('calibration_status', 'N/A')}")
            if handoff.get("epistemic_deltas"):
                deltas = handoff["epistemic_deltas"]
                print(f"   Epistemic deltas: {_deltas_summary(deltas)}")
        else:
            print("   Type: Documentation-only (no CASCADE workflow assessments)")

    print(json.dumps(handoff, indent=2))


def handle_handoff_create_command(args):
    """Handle handoff-create command

    Supports two modes:
    1. Epistemic handoff (requires PREFLIGHT/POSTFLIGHT assessments)
    2. Planning handoff (documentation-only, no CASCADE workflow needed)

    Input modes:
    - AI-first: JSON via stdin (empirica handoff-create -)
    - Legacy: CLI flags (backward compatible)
    """
    try:
        from empirica.core.handoff.storage import HybridHandoffStorage

        # Stage 1: Parse and validate input
        parsed = _handoff_parse_input(args)
        session_id = parsed["session_id"]

        # Stage 2: Determine handoff type
        handoff_type, start_assessment, end_assessment = _handoff_determine_type(session_id, parsed["planning_only"])

        if handoff_type is None:
            _handoff_print_no_assessments()
            return None

        # Stage 3: Generate handoff report
        handoff, handoff_display_name = _handoff_generate_report(handoff_type, start_assessment, end_assessment, parsed)

        # Stage 4: Store in BOTH git notes AND database
        storage = HybridHandoffStorage()
        sync_result = storage.store_handoff(session_id, handoff)

        if not sync_result["fully_synced"]:
            logger.warning(f"⚠️ Partial storage: git={sync_result['git_stored']}, db={sync_result['db_stored']}")

        # Stage 5: Format output
        _handoff_format_output(args, session_id, handoff_type, handoff, sync_result, handoff_display_name)
        return 0

    except Exception as e:
        handle_cli_error(e, "Handoff create", getattr(args, "verbose", False))
        return 1


def handle_handoff_query_command(args):
    """Handle handoff-query command"""
    try:
        from empirica.core.handoff.storage import HybridHandoffStorage

        # Parse arguments
        ai_id = getattr(args, "ai_id", None)
        session_id = getattr(args, "session_id", None)
        limit = getattr(args, "limit", 5)

        # Query handoffs
        storage = HybridHandoffStorage()

        if session_id:
            # Query by session ID (works from either storage)
            handoff = storage.load_handoff(session_id)
            if handoff:
                handoffs = [handoff]
            else:
                handoffs = []
        elif ai_id:
            # Query by AI ID (uses database index - FAST!)
            handoffs = storage.query_handoffs(ai_id=ai_id, limit=limit)
        else:
            # Get recent handoffs (uses database - FAST!)
            handoffs = storage.query_handoffs(limit=limit)

        # Format output
        if hasattr(args, "output") and args.output == "json":
            result = {
                "ok": True,
                "handoffs_count": len(handoffs),
                "handoffs": [
                    {
                        "session_id": h["session_id"],
                        "ai_id": h["ai_id"],
                        "timestamp": h["timestamp"],
                        "task_summary": h["task_summary"],
                        "epistemic_deltas": h["epistemic_deltas"],
                        "key_findings": h["key_findings"],
                        "remaining_unknowns": h["remaining_unknowns"],
                        "next_session_context": h["next_session_context"],
                        "calibration_status": h["calibration_status"],
                    }
                    for h in handoffs
                ],
            }
            print(json.dumps(result, indent=2))
        else:
            print(f"📋 Found {len(handoffs)} handoff report(s):")
            for i, h in enumerate(handoffs, 1):
                print(f"\n{i}. Session: {h['session_id'][:8]}...")
                print(f"   AI: {h['ai_id']}")
                print(f"   Task: {h['task_summary'][:60]}...")
                print(f"   Calibration: {h['calibration_status']}")
                print(f"   Token count: ~{len(h.get('compressed_json', '')) // 4}")

            print(json.dumps({"handoffs": handoffs}, indent=2))

        return 0

    except Exception as e:
        handle_cli_error(e, "Handoff query", getattr(args, "verbose", False))
        return 1


# DELETE THIS - No longer needed!
# Database returns expanded format already
