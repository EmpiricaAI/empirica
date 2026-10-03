"""
Checkpoint Command Handlers - Phase 2

Handles CLI commands for git-enhanced epistemic checkpoints.
Achieves ~85% token reduction through compressed checkpoint storage.
"""

import json
import logging
import sys

# Set up logging for checkpoint commands
logger = logging.getLogger(__name__)


def _get_checkpoint_profile_thresholds():
    """Get checkpoint display and diff thresholds from investigation profiles"""
    try:
        from empirica.config.profile_loader import ProfileLoader

        loader = ProfileLoader()
        universal = loader.universal_constraints

        try:
            profile = loader.get_profile("balanced")
            constraints = profile.investigation

            return {
                "display_high": getattr(constraints, "display_high_threshold", 0.7),
                "display_medium": getattr(constraints, "display_medium_threshold", 0.5),
                "diff_threshold": getattr(constraints, "diff_significance_threshold", 0.15),
                "default_vector_score": getattr(constraints, "default_vector_score", 0.5),
                "engagement_gate": universal.engagement_gate,
                "coherence_min": universal.coherence_min,
            }
        except Exception:
            return {
                "display_high": 0.7,
                "display_medium": 0.5,
                "diff_threshold": 0.15,
                "default_vector_score": 0.5,
                "engagement_gate": 0.6,
                "coherence_min": 0.5,
            }
    except Exception:
        return {
            "display_high": 0.7,
            "display_medium": 0.5,
            "diff_threshold": 0.15,
            "default_vector_score": 0.5,
            "engagement_gate": 0.6,
            "coherence_min": 0.5,
        }


def handle_checkpoint_create_command(args):
    """
    Create git checkpoint for session

    Usage:
        empirica checkpoint-create --session-id abc123 --phase PREFLIGHT --round 1
    """
    try:
        from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger
        from empirica.data.session_database import SessionDatabase

        session_id = args.session_id
        phase = args.phase
        round_num = args.round

        # Parse metadata if provided
        metadata = {}
        if hasattr(args, "metadata") and args.metadata:
            try:
                metadata = json.loads(args.metadata)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON metadata provided, ignoring")
                print("⚠️  Invalid JSON metadata, ignoring")

        # Get current vectors from session database
        db = SessionDatabase()
        vectors = {}

        # Try to get latest vectors from session
        try:
            # Load vectors from reflexes table using the new API
            # get_latest_vectors returns the whole record ({session_id, phase, vectors: {...}, ...}). Passing it
            # on as the vectors made every lookup miss and default to 0.5: a checkpoint of a real CHECK
            # was written as an all-0.5 one (ecodex, 2026-10-03).
            latest = db.get_latest_vectors(session_id)
            vectors = (latest or {}).get("vectors") or {}
            if vectors:
                logger.info(f"Loaded vectors from reflexes table for session {session_id}: {len(vectors)} vectors")
            else:
                logger.warning(f"No vectors found in reflexes table for session {session_id}")
        except Exception as e:
            logger.warning(f"Could not load vectors from session: {e}")
            print(f"⚠️  Could not load vectors from session: {e}")
            print("   Creating checkpoint with empty vectors")

        finally:
            db.close()

        # Create git checkpoint
        git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

        checkpoint_id = git_logger.add_checkpoint(
            phase=phase, round_num=round_num, vectors=vectors or {}, metadata=metadata
        )

        logger.info(f"Checkpoint created: {checkpoint_id} (phase={phase}, round={round_num})")
        print("✅ Checkpoint created successfully")
        print(f"   ID: {checkpoint_id}")
        print(f"   Phase: {phase}")
        print(f"   Round: {round_num}")
        print(f"   Storage: {'git notes' if git_logger.git_available else 'SQLite fallback'}")
        print("   Estimated tokens: ~450 (~85% reduction vs typical context)")

    except Exception as e:
        logger.error(f"Failed to create checkpoint: {e}", exc_info=True)
        print(f"❌ Failed to create checkpoint: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


def handle_checkpoint_load_command(args):
    """
    Load latest checkpoint for session

    Usage:
        empirica checkpoint-load --session-id abc123
        empirica checkpoint-load --session-id abc123 --phase PREFLIGHT
    """
    try:
        from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

        session_id = args.session_id
        max_age = args.max_age if hasattr(args, "max_age") else 24
        phase = args.phase if hasattr(args, "phase") else None
        args.format if hasattr(args, "format") else "table"

        git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

        checkpoint = git_logger.get_last_checkpoint(max_age_hours=max_age, phase=phase)

        if not checkpoint:
            logger.info(f"No checkpoint found for session {session_id} (phase={phase}, max_age={max_age}h)")
            print(f"⚠️  No checkpoint found for session: {session_id}")
            if phase:
                print(f"   (filtered by phase: {phase})")
            print(f"   (max age: {max_age} hours)")
            return

        # Display checkpoint
        # Handle both --output and --format for backward compatibility
        output_format = getattr(args, "output", "table")
        if hasattr(args, "format") and args.format and output_format == "table":
            output_format = args.format

        if output_format == "json":
            print(json.dumps(checkpoint, indent=2))
        else:
            # Table format
            print("✅ Checkpoint loaded successfully\n")
            print(f"Session ID:   {session_id}")
            print(f"Checkpoint:   {checkpoint.get('checkpoint_id', 'N/A')}")
            print(f"Phase:        {checkpoint['phase']}")
            print(f"Round:        {checkpoint['round']}")
            print(f"Created:      {checkpoint['timestamp']}")
            print(f"Storage:      {'git notes' if git_logger.git_available else 'SQLite'}")
            print(f"Token count:  {checkpoint.get('token_count', 'N/A')}")

            # Show vectors
            print("\nEpistemic Vectors:")
            vectors = checkpoint.get("vectors", {})
            for key, value in sorted(vectors.items()):
                thresholds = _get_checkpoint_profile_thresholds()
                indicator = (
                    "📈"
                    if value >= thresholds["display_high"]
                    else "📊"
                    if value >= thresholds["display_medium"]
                    else "📉"
                )
                print(f"  {indicator} {key:12s}: {value:.2f}")

            # Show metadata if present
            if checkpoint.get("metadata"):
                print("\nMetadata:")
                for key, value in checkpoint["metadata"].items():
                    print(f"  {key}: {value}")

            # Show token savings
            baseline = 6500  # Typical full history
            saved = baseline - checkpoint.get("token_count", 450)
            reduction = (saved / baseline) * 100
            print("\nToken Efficiency:")
            print(f"  Baseline:   {baseline} tokens")
            print(f"  Actual:     {checkpoint.get('token_count', 450)} tokens")
            print(f"  Reduction:  {reduction:.1f}%")

    except Exception as e:
        logger.error(f"Failed to load checkpoint: {e}", exc_info=True)
        print(f"❌ Failed to load checkpoint: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


def handle_checkpoint_list_command(args):
    """
    List checkpoints for session

    Usage:
        empirica checkpoint-list --session-id abc123
        empirica checkpoint-list --session-id abc123 --limit 5
        empirica checkpoint-list --session-id abc123 --output json
    """
    try:
        from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

        session_id = args.session_id if hasattr(args, "session_id") and args.session_id else None
        limit = args.limit if hasattr(args, "limit") else 10
        phase = args.phase if hasattr(args, "phase") else None
        output_format = getattr(args, "output", "default")

        if not session_id:
            logger.error("Session ID required for listing checkpoints but not provided")
            if output_format == "json":
                print(json.dumps({"ok": False, "error": "Session ID required"}, indent=2))
            else:
                print("⚠️  Session ID required for listing checkpoints")
            sys.exit(1)

        git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

        checkpoints = git_logger.list_checkpoints(limit=limit, phase=phase)

        logger.info(f"Found {len(checkpoints)} checkpoints for session {session_id}")

        if output_format == "json":
            result = {
                "ok": True,
                "session_id": session_id,
                "checkpoints": checkpoints,
                "count": len(checkpoints),
                "limit": limit,
            }
            if phase:
                result["phase_filter"] = phase
            print(json.dumps(result, indent=2))
        else:
            if not checkpoints:
                print(f"No checkpoints found for session: {session_id}")
                if phase:
                    print(f"(filtered by phase: {phase})")
                return

            print(f"Found {len(checkpoints)} checkpoint(s) for session: {session_id}\n")

            for i, cp in enumerate(checkpoints, 1):
                # Checkpoint identifier from phase + round + timestamp
                cp_id = f"{cp['phase']}-R{cp['round']} ({cp['timestamp'][:19]})"
                print(f"{i}. {cp_id}")
                print(f"   Phase: {cp['phase']}, Round: {cp['round']}")
                print(f"   Created: {cp['timestamp']}")
                print(f"   Vectors: {len(cp.get('vectors', {}))} loaded")
                print(f"   Token count: ~{cp.get('token_count', 'unknown')} tokens")
                print()

    except Exception as e:
        logger.error(f"Failed to list checkpoints: {e}", exc_info=True)
        if output_format == "json":
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            print(f"❌ Failed to list checkpoints: {e}")
            import traceback

            traceback.print_exc()
        sys.exit(1)


def handle_checkpoint_diff_command(args):
    """
    Show vector differences from last checkpoint

    Usage:
        empirica checkpoint-diff --session-id abc123
        empirica checkpoint-diff --session-id abc123 --threshold 0.15
    """
    try:
        from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

        session_id = args.session_id
        thresholds = _get_checkpoint_profile_thresholds()
        args.threshold if hasattr(args, "threshold") else thresholds["diff_threshold"]
        output_format = getattr(args, "output", "default")

        git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

        last_checkpoint = git_logger.get_last_checkpoint()

        if not last_checkpoint:
            logger.info("No checkpoint found for comparison")
            print("⚠️  No checkpoint found for comparison")
            print("   Create a checkpoint first with: empirica checkpoint-create")
            return

        # Prepare result for output
        result_data = {
            "checkpoint": last_checkpoint,
            "vectors": last_checkpoint.get("vectors", {}),
            "metadata": last_checkpoint.get("metadata", {}),
        }

        # Output based on format
        if output_format == "json":
            print(json.dumps(result_data, indent=2))
            return

        # Table format (original behavior)
        print(f"Checkpoint: {last_checkpoint['phase']} (round {last_checkpoint['round']})")
        print(f"Created: {last_checkpoint['timestamp']}\n")

        print("Vector State:")
        vectors = last_checkpoint.get("vectors", {})

        # Group vectors by tier
        gate = {"engagement": vectors.get("engagement", 0)}
        foundation = {k: vectors.get(k, 0) for k in ["know", "do", "context"]}
        comprehension = {k: vectors.get(k, 0) for k in ["clarity", "coherence", "signal", "density"]}
        execution = {k: vectors.get(k, 0) for k in ["state", "change", "completion", "impact"]}
        meta = {"uncertainty": vectors.get("uncertainty", 0)}

        def show_tier(name, tier_vectors):
            """Display vectors for a single tier with indicators."""
            print(f"\n{name}:")
            for key, value in tier_vectors.items():
                thresholds = _get_checkpoint_profile_thresholds()
                indicator = (
                    "📈"
                    if value >= thresholds["display_high"]
                    else "📊"
                    if value >= thresholds["display_medium"]
                    else "📉"
                )
                print(f"  {indicator} {key:12s}: {value:.2f}")

        show_tier("GATE", gate)
        show_tier("FOUNDATION", foundation)
        show_tier("COMPREHENSION", comprehension)
        show_tier("EXECUTION", execution)
        show_tier("META", meta)

        # Show metadata
        if last_checkpoint.get("metadata"):
            print("\nMetadata:")
            for key, value in last_checkpoint["metadata"].items():
                print(f"  {key}: {value}")

    except Exception as e:
        print(f"❌ Failed to show diff: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


def handle_efficiency_report_command(args):
    """
    Generate token efficiency report

    Usage:
        empirica efficiency-report --session-id abc123
        empirica efficiency-report --session-id abc123 --format json
        empirica efficiency-report --session-id abc123 --output report.md
    """
    try:
        from empirica.metrics.token_efficiency import TokenEfficiencyMetrics

        session_id = args.session_id
        format_type = args.format if hasattr(args, "format") else "markdown"
        output_path = args.output if hasattr(args, "output") else None

        metrics = TokenEfficiencyMetrics(session_id=session_id)

        # Generate report
        report = metrics.export_report(format=format_type, output_path=output_path)

        # Get comparison summary
        try:
            comparison = metrics.compare_efficiency()

            if output_path:
                print(f"✅ Report saved to: {output_path}")
            else:
                print(report)

            # Show summary
            total = comparison.get("total", {})
            reduction = total.get("reduction_percentage", 0)
            savings = total.get("cost_savings_usd", 0)

            print("\n📊 Efficiency Summary:")
            print(f"   Baseline tokens:  {total.get('baseline_tokens', 'N/A')}")
            print(f"   Actual tokens:    {total.get('actual_tokens', 'N/A')}")
            print(f"   Reduction:        {reduction:.1f}%")
            print(f"   Cost savings:     ${savings:.2f} per 1,000 sessions")

            # Show target achievement
            success = comparison.get("success_criteria", {})
            target_met = success.get("target_met", False)
            achieved = success.get("achieved_reduction_pct", 0)

            if target_met:
                print(f"\n✅ Target met: {achieved:.1f}% ≥ 80% (target)")
            else:
                print(f"\n⚠️  Below target: {achieved:.1f}% < 80% (target)")

        except Exception as e:
            # If comparison fails, just show the report
            if not output_path:
                print(report)
            print(f"\n⚠️  Could not generate comparison summary: {e}")

    except Exception as e:
        print(f"❌ Failed to generate efficiency report: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
