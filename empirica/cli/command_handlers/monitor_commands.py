"""
Monitoring Commands - CLI commands for usage monitoring and cost tracking

NOTE: Modality switcher (adapter monitoring) is deprecated.
This module provides basic session monitoring via Empirica core.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

# Modality switcher is DEPRECATED and no longer available
MODALITY_AVAILABLE = False

from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error

# Set up logging for monitor commands
logger = logging.getLogger(__name__)


class UsageMonitor:
    """Track and display adapter usage statistics for CLI monitoring commands.

    Persists usage stats to disk (default `~/.empirica/usage_stats.json`)
    and exposes a small API for incrementing counters and querying
    aggregated metrics. Used by the `empirica usage-stats` and related
    monitoring CLI commands.

    Tracks per adapter:
        * Request counts (total and by call type)
        * Total cost in USD
        * Average latency in milliseconds
        * Success / failure rates and recent error log

    Attributes:
        stats_file (Path): Filesystem path where the JSON stats blob is
            persisted. Created on demand if it doesn't exist.
        stats (dict): In-memory copy of the persisted stats, loaded on
            __init__ and rewritten by record_request / save_stats.

    Example:
        >>> mon = UsageMonitor()
        >>> mon.record_request("anthropic", success=True, latency_ms=420)
        >>> summary = mon.get_summary()
    """

    def __init__(self, stats_file: Path | None = None):
        """
        Initialize UsageMonitor.

        Args:
            stats_file: Path to stats file (default from config)
        """
        if stats_file is None:
            default_path = '~/.empirica/usage_stats.json'
            self.stats_file = Path(default_path).expanduser()
        else:
            self.stats_file = stats_file

        self.stats_file.parent.mkdir(parents=True, exist_ok=True)

        self.stats: dict[str, Any] = self._load_stats()

    def _load_stats(self) -> dict[str, Any]:
        """Load existing stats or create new."""
        if self.stats_file.exists():
            try:
                with open(self.stats_file) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load stats from {self.stats_file}: {e}")
                pass

        # Initialize new stats
        return {
            "session_start": datetime.now().isoformat(),
            "adapters": {
                "minimax": {"requests": 0, "tokens": 0, "cost": 0.0, "errors": 0},
                "qwen": {"requests": 0, "tokens": 0, "cost": 0.0, "errors": 0},
                "local": {"requests": 0, "tokens": 0, "cost": 0.0, "errors": 0}
            },
            "total_requests": 0,
            "total_cost": 0.0,
            "fallbacks": 0,
            "history": []
        }

    def _save_stats(self):
        """Save stats to file."""
        with open(self.stats_file, 'w') as f:
            json.dump(self.stats, f, indent=2)

    def record_request(
        self,
        adapter: str,
        success: bool,
        tokens: int = 0,
        cost: float = 0.0,
        latency: float = 0.0
    ):
        """Record a request."""
        if adapter not in self.stats["adapters"]:
            logger.debug(f"Creating new stats entry for adapter: {adapter}")
            self.stats["adapters"][adapter] = {"requests": 0, "tokens": 0, "cost": 0.0, "errors": 0}

        self.stats["adapters"][adapter]["requests"] += 1
        self.stats["adapters"][adapter]["tokens"] += tokens
        self.stats["adapters"][adapter]["cost"] += cost

        if not success:
            self.stats["adapters"][adapter]["errors"] += 1
            logger.warning(f"Request error recorded for adapter: {adapter}")

        self.stats["total_requests"] += 1
        self.stats["total_cost"] += cost

        logger.debug(f"Recorded request: adapter={adapter}, success={success}, tokens={tokens}, cost=${cost:.4f}")

        # Add to history
        self.stats["history"].append({
            "timestamp": datetime.now().isoformat(),
            "adapter": adapter,
            "success": success,
            "tokens": tokens,
            "cost": cost,
            "latency": latency
        })

        # Keep only last 1000 records
        if len(self.stats["history"]) > 1000:
            logger.debug("Trimming history to last 1000 records")
            self.stats["history"] = self.stats["history"][-1000:]

        self._save_stats()

    def get_stats(self) -> dict[str, Any]:
        """Get current statistics."""
        return self.stats

    def reset_stats(self):
        """Reset all statistics."""
        logger.info("Resetting all monitoring statistics")
        self.stats = {
            "session_start": datetime.now().isoformat(),
            "adapters": {
                "minimax": {"requests": 0, "tokens": 0, "cost": 0.0, "errors": 0},
                "qwen": {"requests": 0, "tokens": 0, "cost": 0.0, "errors": 0},
                "local": {"requests": 0, "tokens": 0, "cost": 0.0, "errors": 0}
            },
            "total_requests": 0,
            "total_cost": 0.0,
            "fallbacks": 0,
            "history": []
        }
        self._save_stats()


def handle_monitor_command(args):
    """
    Unified monitor handler (consolidates all 4 monitor commands).

    NOTE: Adapter usage monitoring (modality switcher) is deprecated.
    Use session and project commands for Empirica monitoring.
    """
    try:
        print("\n📊 Empirica Usage Monitor")
        print("=" * 70)

        print("\n⚠️  Adapter Monitoring Deprecated")
        print("-" * 70)
        print("The modality switcher (adapter routing) feature has been deprecated.")
        print("Adapter usage statistics are no longer tracked.")

        print("\n💡 Alternative Monitoring Commands:")
        print("-" * 70)
        print("   empirica sessions-list          - View session history")
        print("   empirica project-bootstrap      - View project state")
        print("   empirica efficiency-report      - View token efficiency")
        print("   empirica query findings         - View learnings")
        print("   empirica query issues           - View auto-captured issues")

        print("\n📈 Session Statistics:")
        print("-" * 70)

        # Try to show basic session stats from Empirica core
        try:
            from empirica.data.session_database import SessionDatabase
            db = SessionDatabase()
            sessions = db.get_all_sessions(limit=5)
            db.close()

            if sessions:
                print(f"   Recent sessions: {len(sessions)}")
                for s in sessions[:3]:
                    print(f"     • {s.get('session_id', 'N/A')[:8]}... ({s.get('ai_id', 'unknown')})")
            else:
                print("   No sessions recorded yet")
        except Exception:
            print("   Session data unavailable")

        print("=" * 70)

    except Exception as e:
        handle_cli_error(e, "Monitor", getattr(args, 'verbose', False))


def handle_monitor_export_command(args):
    """
    Export monitoring data to file.

    Supports JSON and CSV formats.
    """
    try:
        print("\n📤 Exporting Monitoring Data")
        print("=" * 70)

        monitor = UsageMonitor()
        stats = monitor.get_stats()

        output_format = getattr(args, 'format', 'json')
        output_file = getattr(args, 'output', None) or getattr(args, 'export', None)

        if output_format == 'json':
            # Export as JSON
            with open(output_file, 'w') as f:
                json.dump(stats, f, indent=2)

            print(f"\n✅ Exported to JSON: {output_file}")

        elif output_format == 'csv':
            # Export history as CSV
            import csv

            history = stats.get("history", [])

            if not history:
                print("⚠️  No history to export")
                return

            with open(output_file, 'w', newline='') as f:
                fieldnames = ['timestamp', 'adapter', 'success', 'tokens', 'cost', 'latency']
                writer = csv.DictWriter(f, fieldnames=fieldnames)

                writer.writeheader()
                for record in history:
                    writer.writerow({k: record.get(k, '') for k in fieldnames})

            print(f"\n✅ Exported to CSV: {output_file}")
            print(f"   Records: {len(history)}")

        print("=" * 70)

    except Exception as e:
        handle_cli_error(e, "Monitor Export", getattr(args, 'verbose', False))


def handle_monitor_reset_command(args):
    """
    Reset monitoring statistics.

    Clears all recorded data.
    """
    try:
        print("\n🔄 Resetting Monitoring Statistics")
        print("=" * 70)

        # Confirm unless --yes flag
        if not getattr(args, 'yes', False):
            confirm = input("\n⚠️  This will clear all monitoring data. Continue? [y/N]: ").strip().lower()
            if confirm not in ['y', 'yes']:
                print("❌ Reset cancelled")
                return

        monitor = UsageMonitor()
        monitor.reset_stats()

        print("\n✅ Statistics reset")
        print(f"   Stats file: {monitor.stats_file}")
        print("=" * 70)

    except Exception as e:
        handle_cli_error(e, "Monitor Reset", getattr(args, 'verbose', False))


def handle_monitor_cost_command(args):
    """
    Display cost analysis.

    Shows detailed cost breakdown by adapter and time period.
    """
    try:
        print("\n💰 Cost Analysis")
        print("=" * 70)

        monitor = UsageMonitor()
        stats = monitor.get_stats()

        total_cost = stats.get("total_cost", 0.0)
        adapters_stats = stats.get("adapters", {})

        print(f"\n📊 Total Cost: ${total_cost:.4f}")

        print("\n" + "=" * 70)
        print("Cost by Adapter:")
        print("=" * 70)

        for adapter, data in sorted(adapters_stats.items(), key=lambda x: x[1].get('cost', 0.0), reverse=True):
            cost = data.get("cost", 0.0)
            requests = data.get("requests", 0)

            if cost > 0:
                percentage = (cost / total_cost * 100) if total_cost > 0 else 0
                avg_cost = cost / requests if requests > 0 else 0

                print(f"\n🔹 {adapter.upper()}")
                print(f"   Total:       ${cost:.4f} ({percentage:.1f}%)")
                print(f"   Avg/Request: ${avg_cost:.6f}")
                print(f"   Requests:    {requests:,}")

        # Project costs
        if getattr(args, 'project', False):
            print("\n" + "=" * 70)
            print("📈 Cost Projections")
            print("=" * 70)

            total_requests = stats.get("total_requests", 0)

            if total_requests > 0:
                avg_cost_per_request = total_cost / total_requests

                print(f"\n   Average cost per request: ${avg_cost_per_request:.6f}")
                print("\n   Projected costs:")
                print(f"      100 requests:   ${avg_cost_per_request * 100:.2f}")
                print(f"      1,000 requests: ${avg_cost_per_request * 1000:.2f}")
                print(f"      10,000 requests: ${avg_cost_per_request * 10000:.2f}")

        print("\n" + "=" * 70)

    except Exception as e:
        handle_cli_error(e, "Cost Analysis", getattr(args, 'verbose', False))


# NOTE: handle_pre_summary_snapshot, handle_post_summary_drift_check, and
# handle_check_drift_command were removed in v1.6.6. MirrorDriftMonitor was
# superseded by the grounded calibration pipeline (postflight → post-test →
# bayesian updates) which detects drift through objective evidence rather
# than vector-to-vector temporal comparison.




def handle_mco_load_command(args):
    """
    Load and present MCO (Meta-Agent Configuration Object) configuration.

    Used for:
    1. Session start - Load fresh MCO config for AI
    2. Post-compact - Reload MCO config from pre-summary snapshot
    3. Manual query - Check active MCO configuration

    Args from argparse:
        session_id: Session identifier (optional)
        ai_id: AI identifier (optional, for model/persona inference)
        snapshot: Path to pre_summary snapshot (optional, for post-compact reload)
        model: Explicit model override (optional)
        persona: Explicit persona override (optional)
        output: Output format ('json' or 'human', default 'human')
    """
    import json

    from empirica.config.mco_loader import get_mco_config
    from empirica.data.session_database import SessionDatabase

    try:
        session_id = getattr(args, 'session_id', None)
        ai_id = getattr(args, 'ai_id', None)
        snapshot_path = getattr(args, 'snapshot', None)
        model = getattr(args, 'model', None)
        persona = getattr(args, 'persona', None)
        output_format = getattr(args, 'output', 'human')

        mco = get_mco_config()

        # Load from snapshot if post-compact
        if snapshot_path:
            try:
                with open(snapshot_path) as f:
                    snapshot_data = json.load(f)
                    mco_snapshot = snapshot_data.get('mco_config', {})

                if not mco_snapshot:
                    if output_format == 'json':
                        print(json.dumps({
                            "ok": False,
                            "error": "No MCO config found in snapshot",
                            "message": "Snapshot may be from older version before MCO integration"
                        }))
                    else:
                        print("\n⚠️  No MCO Configuration in Snapshot")
                        print("=" * 70)
                        print("   This snapshot was created before MCO integration.")
                        print("   Falling back to fresh MCO load from files...")
                        print("=" * 70)
                        # Fall through to fresh load
                    snapshot_path = None

                else:
                    formatted = mco.format_for_prompt(mco_snapshot)

                    if output_format == 'json':
                        print(json.dumps({
                            "ok": True,
                            "source": "pre_summary_snapshot",
                            "snapshot_path": snapshot_path,
                            "mco_config": mco_snapshot,
                            "formatted": formatted
                        }))
                    else:
                        print("\n🔧 MCO Configuration (Post-Compact Reload)")
                        print("=" * 70)
                        print(f"   Source: {snapshot_path}")
                        print("=" * 70)
                        print(formatted)
                        print("\n💡 Your configuration has been restored from pre-compact snapshot.")
                        print("   Apply these bias corrections when doing PREFLIGHT/CHECK/POSTFLIGHT.")

                    return

            except Exception as e:
                logger.error(f"Failed to load snapshot: {e}")
                if output_format == 'json':
                    print(json.dumps({"ok": False, "error": str(e)}))
                else:
                    print(f"\n❌ Error loading snapshot: {e}")
                return

        # Fresh load from MCO files
        if session_id:
            db = SessionDatabase()
            try:
                session_data = db.get_session(session_id)
                if session_data:
                    ai_id = ai_id or session_data.get('ai_id')
            except Exception:
                pass

        # Export snapshot
        mco_snapshot = mco.export_snapshot(
            session_id=session_id or 'unknown',
            ai_id=ai_id,
            model=model,
            persona=persona,
            cascade_style='default'
        )

        formatted = mco.format_for_prompt(mco_snapshot)

        if output_format == 'json':
            print(json.dumps({
                "ok": True,
                "source": "mco_files",
                "session_id": session_id,
                "ai_id": ai_id,
                "mco_config": mco_snapshot,
                "formatted": formatted
            }))
        else:
            print("\n🔧 MCO Configuration (Fresh Load)")
            print("=" * 70)
            if session_id:
                print(f"   Session ID: {session_id}")
            if ai_id:
                print(f"   AI ID: {ai_id}")
            print("=" * 70)
            print(formatted)
            print("\n💡 Internalize these values. Apply bias corrections during transaction assessments.")

    except Exception as e:
        handle_cli_error(e, "MCO Load", getattr(args, 'verbose', False))


def _load_checkpoint_vectors(session_id: str | None, verbose: bool) -> tuple[dict, dict]:
    """Load last checkpoint vectors for a session, trying git notes then reflexes table.

    Returns (vectors, checkpoint_data) tuple. Both empty dicts if no data found.
    """
    vectors: dict = {}
    checkpoint_data: dict = {}

    if not session_id:
        return vectors, checkpoint_data

    # Try git notes first (canonical source)
    try:
        from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger
        git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)
        checkpoints = git_logger.list_checkpoints(limit=1)

        if checkpoints and checkpoints[0] is not None:
            checkpoint_data = checkpoints[0]
            vectors = checkpoint_data.get('vectors', {}) or {}
    except Exception as e:
        if verbose:
            logger.warning(f"Could not load checkpoint from git notes: {e}")

    # Fallback to reflexes table if git notes empty
    if not vectors:
        try:
            from empirica.data.session_database import SessionDatabase
            db = SessionDatabase()
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT engagement, know, do, context, clarity, coherence,
                       signal, density, state, change, completion, impact, uncertainty
                FROM reflexes
                WHERE session_id = ?
                ORDER BY timestamp DESC LIMIT 1
            """, (session_id,))
            row = cursor.fetchone()
            db.close()

            if row:
                vectors = {
                    'engagement': row[0], 'know': row[1], 'do': row[2],
                    'context': row[3], 'clarity': row[4], 'coherence': row[5],
                    'signal': row[6], 'density': row[7], 'state': row[8],
                    'change': row[9], 'completion': row[10], 'impact': row[11],
                    'uncertainty': row[12]
                }
                vectors = {k: v for k, v in vectors.items() if v is not None}
                checkpoint_data = {'vectors': vectors, 'source': 'reflexes_table'}
        except Exception as e:
            if verbose:
                logger.warning(f"Could not load checkpoint from reflexes: {e}")

    return vectors, checkpoint_data


def handle_assess_state_command(args):
    """
    Capture sessionless epistemic state (fresh measurement without session context).

    Used for:
    - Statusline displays (current epistemic state)
    - Pre-compact snapshots (fresh vectors before memory compacting)
    - Post-compact snapshots (fresh vectors after memory compacting)
    - Monitoring dashboards (current epistemic health)

    Captures a fresh measurement (not stored in reflexes table, sessionless).
    Not stored in reflexes table (sessionless), can be included in snapshots.

    Output:
    - JSON: Just vectors and metadata
    - Human: Formatted display with context
    """
    try:
        import json
        from datetime import datetime, timezone

        session_id = getattr(args, 'session_id', None)
        prompt = getattr(args, 'prompt', None)
        output_format = getattr(args, 'output', 'human')
        verbose = getattr(args, 'verbose', False)

        # Print header only for human output
        if output_format != 'json':
            print("\n🔍 Epistemic State Assessment (Sessionless)")
            print("=" * 70)
            if session_id:
                print(f"   Session ID: {session_id}")
            if prompt:
                print(f"   Context: {prompt[:60]}...")
            print("=" * 70)

        # If session_id provided, load last checkpoint as reference
        vectors, checkpoint_data = _load_checkpoint_vectors(session_id, verbose)

        # Capture fresh state
        # In production, this would call into an LLM or use cached epistemic state
        # For now, return the last known checkpoint vectors with metadata
        state = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'vectors': vectors,
            'has_session': session_id is not None,
            'has_checkpoint': bool(checkpoint_data),
            'prompt_context': prompt is not None
        }

        # Output results
        if output_format == 'json':
            print(json.dumps({
                'ok': True,
                'state': state,
                'session_id': session_id,
                'timestamp': state['timestamp']
            }))
        else:
            print("\n📊 Current Epistemic Vectors:")
            print("-" * 70)
            if vectors:
                for key, value in sorted(vectors.items()):
                    if isinstance(value, (int, float)):
                        bar_length = int(value * 20)
                        bar = "█" * bar_length + "░" * (20 - bar_length)
                        print(f"   {key:20s} {value:5.2f}  {bar}")
                    else:
                        print(f"   {key:20s} {value!s}")
            else:
                print("   ⚠️  No vectors available")
                print("   Run PREFLIGHT or CHECK to establish baseline")
            print("-" * 70)
            print(f"\n   Timestamp: {state['timestamp']}")
            if session_id:
                print(f"   Session:   {session_id}")
            print()

        # TURTLE MODE: Recursive grounding check (Noetic Handshake)
        if getattr(args, 'turtle', False):
            _display_turtle_stack(vectors, session_id, prompt)

    except Exception as e:
        handle_cli_error(e, "Assess State", getattr(args, 'verbose', False))


def _display_turtle_stack(vectors: dict, session_id: str | None = None, prompt: str | None = None):
    """
    Display recursive grounding stack trace (the Noetic Handshake).

    Verifies observer stability before observing by checking grounding layers:
    - Layer 0: User Intent (can we parse the request?)
    - Layer 1: Noetic Grasp (do we understand the concept?)
    - Layer 2: Praxic Path (can we execute?)
    - Layer 3: Epistemic Safety (is uncertainty below threshold?)
    """
    print("\n" + "=" * 70)
    print("🐢 TURTLE STACK REPORT (Recursive Grounding Check)")
    print("=" * 70)

    # Moon phase indicators based on confidence levels
    def get_moon_phase(score: float) -> tuple:
        """Return (emoji, status) tuple based on confidence score."""
        if score >= 0.85:
            return "🌕", "CRYSTALLINE"
        elif score >= 0.70:
            return "🌔", "SOLID"
        elif score >= 0.50:
            return "🌓", "EMERGENT"
        elif score >= 0.30:
            return "🌒", "FORMING"
        else:
            return "🌑", "DARK"

    # Calculate layer scores from vectors
    layers = []
    safe_to_proceed = True

    # Layer 0: User Intent (based on context + signal)
    context = vectors.get('context', 0.5)
    signal = vectors.get('signal', 0.5)
    layer0_score = (context + signal) / 2
    moon0, status0 = get_moon_phase(layer0_score)
    layers.append({
        'layer': 0,
        'name': 'USER INTENT',
        'score': layer0_score,
        'moon': moon0,
        'status': status0,
        'detail': f"Context={context:.2f}, Signal={signal:.2f}"
    })

    # Layer 1: Noetic Grasp (based on know + clarity + coherence)
    know = vectors.get('know', 0.5)
    clarity = vectors.get('clarity', 0.5)
    coherence = vectors.get('coherence', 0.5)
    layer1_score = (know + clarity + coherence) / 3
    moon1, status1 = get_moon_phase(layer1_score)
    layers.append({
        'layer': 1,
        'name': 'NOETIC GRASP',
        'score': layer1_score,
        'moon': moon1,
        'status': status1,
        'detail': f"Know={know:.2f}, Clarity={clarity:.2f}, Coherence={coherence:.2f}"
    })

    # Layer 2: Praxic Path (based on do + state + change)
    do = vectors.get('do', 0.5)
    state = vectors.get('state', 0.5)
    change = vectors.get('change', 0.5)
    layer2_score = (do + state + change) / 3
    moon2, status2 = get_moon_phase(layer2_score)
    layers.append({
        'layer': 2,
        'name': 'PRAXIC PATH',
        'score': layer2_score,
        'moon': moon2,
        'status': status2,
        'detail': f"Do={do:.2f}, State={state:.2f}, Change={change:.2f}"
    })

    # Layer 3: Epistemic Safety (based on uncertainty + engagement + impact)
    uncertainty = vectors.get('uncertainty', 0.5)
    engagement = vectors.get('engagement', 0.5)
    impact = vectors.get('impact', 0.5)
    # For safety, LOW uncertainty is GOOD, so we invert it
    safety_score = ((1 - uncertainty) + engagement + impact) / 3
    moon3, status3 = get_moon_phase(safety_score)
    layers.append({
        'layer': 3,
        'name': 'EPISTEMIC SAFETY',
        'score': safety_score,
        'moon': moon3,
        'status': status3,
        'detail': f"Uncertainty={uncertainty:.2f} (inverted), Engagement={engagement:.2f}"
    })

    # Display each layer
    for layer in layers:
        print(f"\n  🐢 [LAYER {layer['layer']}: {layer['name']}] -> {layer['moon']} {layer['status']}")
        print(f"     Score: {layer['score']:.2f} | {layer['detail']}")

        # Check for warnings
        if layer['score'] < 0.50:
            print(f"     ⚠️  Warning: {layer['name']} is below grounding threshold")
            safe_to_proceed = False
        elif layer['score'] < 0.70:
            print(f"     ⚡ Caution: {layer['name']} may need investigation")

    # Overall status
    print("\n" + "-" * 70)
    overall_score = sum(l['score'] for l in layers) / len(layers)
    overall_moon, overall_status = get_moon_phase(overall_score)

    if safe_to_proceed and overall_score >= 0.70:
        print(f"STATUS: {overall_moon} [{overall_status}] - SAFE TO PROCEED")
        print("        Observer is stable. Grounding verified.")
    elif safe_to_proceed and overall_score >= 0.50:
        print(f"STATUS: {overall_moon} [{overall_status}] - PROCEED WITH CAUTION")
        print("        Observer is forming. Consider CHECK before praxic action.")
    else:
        print(f"STATUS: {overall_moon} [{overall_status}] - HALT RECOMMENDED")
        print("        Observer is unstable. Run PREFLIGHT or investigate unknowns.")

    print("=" * 70)
    print()


def _print_trajectory_human(overall_moon, overall_status, overall_grounding, layers,
                            unknowns_count, findings_count, sentinel_status, sentinel_moon,
                            paths, recommendation, recommendation_action,
                            show_turtle, depth, verbose):
    """Print trajectory projection in human-readable format."""
    print("\n" + "=" * 70)
    print("🔭 TRAJECTORY PROJECTION (Turtle Telescope)")
    print("=" * 70)
    print(f"\nCurrent Grounding: {overall_moon} {overall_status} ({overall_grounding:.2f})")

    if show_turtle:
        print("\n┌─ TURTLE STACK ─────────────────────────────────────────────────────┐")
        for i, layer in enumerate(layers):
            moon, status = layer['moon']
            print(f"│  Layer {i}: {layer['name']:20} {moon} {status:12} ({layer['score']:.2f}) │")
        print("└────────────────────────────────────────────────────────────────────┘")

    print(f"\nContext: {unknowns_count} unknowns | {findings_count} findings")
    if sentinel_status:
        print(f"Sentinel: {sentinel_moon} {sentinel_status.upper()}")

    print("\n┌─ VIABLE PATHS ─────────────────────────────────────────────────────┐")
    for path in paths[:depth + 2]:
        viable_marker = "✓" if path['viable'] else "○"
        print("│                                                                    │")
        print(f"│  {path['icon']} {path['name']:15} (confidence: {path['confidence']:.2f}) [{viable_marker}]")
        print(f"│     {path['description'][:60]}")
        if verbose and path['blockers']:
            for blocker in path['blockers'][:2]:
                print(f"│     ⚠ {blocker[:55]}")
    print("│                                                                    │")
    print("└────────────────────────────────────────────────────────────────────┘")
    print(f"\n📍 RECOMMENDATION: {recommendation}")
    print(f"   {recommendation_action}")
    print("=" * 70 + "\n")


def _resolve_current_vectors(db, session_id=None) -> tuple:
    """Resolve current epistemic vectors and project_id. Returns (vectors, project_id)."""
    import sqlite3
    vectors = {}
    project_id = None

    if session_id:
        try:
            from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger
            checkpoints = GitEnhancedReflexLogger(
                session_id=session_id, enable_git_notes=True
            ).list_checkpoints(session_id=session_id, limit=1)
            if checkpoints:
                vectors = checkpoints[0].get('vectors', {})
        except Exception:
            pass
        session = db.get_session(session_id)
        if session:
            project_id = session.get('project_id')

    if not vectors:
        try:
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT know, do, context, clarity, coherence, signal, density,
                           engagement, state, change, completion, impact, uncertainty
                    FROM reflexes ORDER BY id DESC LIMIT 1
                """)
                row = cursor.fetchone()
                if row:
                    names = ['know', 'do', 'context', 'clarity', 'coherence', 'signal', 'density',
                             'engagement', 'state', 'change', 'completion', 'impact', 'uncertainty']
                    vectors = {n: row[i] for i, n in enumerate(names) if row[i] is not None}
        except Exception:
            pass

    if not vectors:
        vectors = dict.fromkeys(['know', 'do', 'context', 'clarity', 'coherence', 'signal', 'density', 'engagement', 'state', 'change', 'completion', 'impact', 'uncertainty'], 0.5)
    return vectors, project_id


def _calculate_trajectory_paths(
    layer0_score: float, layer1_score: float, layer2_score: float, layer3_score: float,
    overall_grounding: float, unknowns_count: int, sentinel_status: str | None,
) -> list[dict]:
    """Calculate viability of each epistemic path based on grounding scores.

    Returns sorted list of path dicts (most viable first).
    """
    paths = []

    # PRAXIC path - can we execute?
    praxic_confidence = min(layer1_score, layer2_score, layer3_score)
    praxic_viable = praxic_confidence >= 0.70 and unknowns_count <= 3
    praxic_blockers = []
    if layer1_score < 0.70:
        praxic_blockers.append(f"NOETIC GRASP too low ({layer1_score:.2f})")
    if layer2_score < 0.70:
        praxic_blockers.append(f"PRAXIC PATH unclear ({layer2_score:.2f})")
    if unknowns_count > 3:
        praxic_blockers.append(f"{unknowns_count} unknowns blocking")
    paths.append({
        'name': 'PRAXIC',
        'icon': '🟢' if praxic_viable else '🟡' if praxic_confidence >= 0.50 else '🔴',
        'confidence': praxic_confidence,
        'viable': praxic_viable,
        'description': 'Execute with confidence. Grounding supports action.',
        'blockers': praxic_blockers,
        'action': 'Enter praxic phase, implement the planned changes'
    })

    # NOETIC-SHALLOW path - quick investigation
    noetic_shallow_confidence = (layer0_score + layer1_score) / 2
    noetic_shallow_viable = 0.50 <= overall_grounding < 0.70 or (unknowns_count > 0 and unknowns_count <= 5)
    paths.append({
        'name': 'NOETIC-SHALLOW',
        'icon': '🟢' if noetic_shallow_viable else '🟡',
        'confidence': noetic_shallow_confidence,
        'viable': noetic_shallow_viable,
        'description': 'Quick targeted investigation. Address specific unknowns.',
        'blockers': [] if noetic_shallow_viable else ['Grounding too low for shallow investigation'],
        'action': f'Investigate {min(unknowns_count, 3)} unknowns, then re-CHECK'
    })

    # NOETIC-DEEP path - thorough investigation
    noetic_deep_confidence = layer0_score
    noetic_deep_viable = overall_grounding < 0.50 or unknowns_count > 5
    paths.append({
        'name': 'NOETIC-DEEP',
        'icon': '🟢' if noetic_deep_viable else '🟡',
        'confidence': noetic_deep_confidence,
        'viable': noetic_deep_viable,
        'description': 'Thorough investigation required. Many unknowns or low grounding.',
        'blockers': [] if noetic_deep_viable else ['Grounding sufficient for shallower path'],
        'action': 'Deep exploration, log findings, resolve unknowns before proceeding'
    })

    # SCOPE-EXPAND path - broaden task scope
    scope_expand_confidence = overall_grounding * (1 - (unknowns_count / 10)) if unknowns_count <= 10 else 0
    scope_expand_viable = overall_grounding >= 0.75 and unknowns_count <= 2 and scope_expand_confidence is not None
    scope_blockers = []
    if overall_grounding < 0.75:
        scope_blockers.append(f"Grounding ({overall_grounding:.2f}) < 0.75 threshold")
    if unknowns_count > 2:
        scope_blockers.append(f"{unknowns_count} unknowns would expand further")
    paths.append({
        'name': 'SCOPE-EXPAND',
        'icon': '🟢' if scope_expand_viable else '🔴',
        'confidence': max(0, scope_expand_confidence),
        'viable': scope_expand_viable,
        'description': 'Broaden task scope. Current grounding supports expansion.',
        'blockers': scope_blockers,
        'action': 'Add tasks or related goals, then re-baseline with PREFLIGHT'
    })

    # HANDOFF path - transfer to different AI
    handoff_confidence = 1 - overall_grounding
    handoff_viable = overall_grounding < 0.40 or (sentinel_status and sentinel_status in ['forming', 'dark'])
    paths.append({
        'name': 'HANDOFF',
        'icon': '🟡' if handoff_viable else '⚪',
        'confidence': handoff_confidence,
        'viable': handoff_viable,
        'description': 'Transfer to different AI/session. Observer stability questionable.',
        'blockers': [] if handoff_viable else ['Observer stable enough to continue'],
        'action': 'Create handoff artifact, transfer context to fresh session/AI'
    })

    # HALT path - stop and seek guidance
    halt_confidence = 1 - min(layer3_score, overall_grounding)
    halt_viable = layer3_score < 0.30 or overall_grounding < 0.25
    paths.append({
        'name': 'HALT',
        'icon': '🔴' if halt_viable else '⚪',
        'confidence': halt_confidence,
        'viable': halt_viable,
        'description': 'Stop and seek human guidance. Critical grounding issues.',
        'blockers': [] if halt_viable else ['No critical issues detected'],
        'action': 'Escalate to human, do not proceed without guidance'
    })

    # Ensure all paths have valid viable values (defensive)
    for p in paths:
        if p['viable'] is None:
            p['viable'] = False
        if p['confidence'] is None:
            p['confidence'] = 0.0

    # Sort paths by viability first, then confidence (descending)
    paths.sort(key=lambda p: (-int(bool(p['viable'])), -p['confidence']))
    return paths


def handle_trajectory_project_command(args):
    """
    Project viable epistemic paths forward based on current grounding.

    The Turtle Telescope: Uses current turtle stack + context to project
    which epistemic paths are viable given the observer's grounding state.

    Paths:
    - PRAXIC: Execute with confidence (grounding >= 0.70)
    - NOETIC-SHALLOW: Quick investigation (grounding 0.50-0.70)
    - NOETIC-DEEP: Thorough investigation (grounding < 0.50 or high unknowns)
    - SCOPE-EXPAND: Broaden task scope (requires high grounding + low unknowns)
    - HANDOFF: Transfer to different AI/session (unstable observer)
    - HALT: Stop and seek human guidance (critical issues)
    """
    import sqlite3

    from empirica.core.canonical.empirica_git import SentinelHooks
    from empirica.core.canonical.empirica_git.sentinel_hooks import auto_enable_sentinel
    from empirica.data.session_database import SessionDatabase
    auto_enable_sentinel()

    try:
        session_id = getattr(args, 'session_id', None)
        output_format = getattr(args, 'output', 'human')
        show_turtle = getattr(args, 'turtle', False)
        depth = getattr(args, 'depth', 3)
        verbose = getattr(args, 'verbose', False)

        db = SessionDatabase()
        vectors, project_id = _resolve_current_vectors(db, session_id)

        # Calculate turtle stack layers
        def get_moon_phase(score: float) -> tuple:
            """Return moon emoji and status label for a confidence score."""
            if score >= 0.85:
                return "🌕", "CRYSTALLINE"
            elif score >= 0.70:
                return "🌔", "SOLID"
            elif score >= 0.50:
                return "🌓", "EMERGENT"
            elif score >= 0.30:
                return "🌒", "FORMING"
            else:
                return "🌑", "DARK"

        # Layer calculations
        layer0_score = (vectors.get('context', 0.5) + vectors.get('signal', 0.5)) / 2  # USER INTENT
        layer1_score = (vectors.get('know', 0.5) + vectors.get('clarity', 0.5) + vectors.get('coherence', 0.5)) / 3  # NOETIC GRASP
        layer2_score = (vectors.get('do', 0.5) + vectors.get('state', 0.5) + vectors.get('change', 0.5)) / 3  # PRAXIC PATH
        uncertainty = vectors.get('uncertainty', 0.5)
        layer3_score = ((1 - uncertainty) + vectors.get('engagement', 0.5) + vectors.get('impact', 0.5)) / 3  # EPISTEMIC SAFETY

        overall_grounding = (layer0_score + layer1_score + layer2_score + layer3_score) / 4
        overall_moon, overall_status = get_moon_phase(overall_grounding)

        layers = [
            {'name': 'USER INTENT', 'score': layer0_score, 'moon': get_moon_phase(layer0_score)},
            {'name': 'NOETIC GRASP', 'score': layer1_score, 'moon': get_moon_phase(layer1_score)},
            {'name': 'PRAXIC PATH', 'score': layer2_score, 'moon': get_moon_phase(layer2_score)},
            {'name': 'EPISTEMIC SAFETY', 'score': layer3_score, 'moon': get_moon_phase(layer3_score)},
        ]

        # Get unknowns and findings count
        unknowns_count = 0
        findings_count = 0

        if project_id:
            try:
                with sqlite3.connect(db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM project_unknowns WHERE project_id = ? AND is_resolved = 0", (project_id,))
                    unknowns_count = cursor.fetchone()[0]
                    cursor.execute("SELECT COUNT(*) FROM project_findings WHERE project_id = ?", (project_id,))
                    findings_count = cursor.fetchone()[0]
            except Exception:
                pass

        # Get Sentinel status if available
        sentinel_status = None
        sentinel_moon = None
        if SentinelHooks.is_enabled():
            turtle_result = SentinelHooks.turtle_check()
            sentinel_status = turtle_result.get('status')
            sentinel_moon = turtle_result.get('moon')

        # Calculate path viabilities
        paths = _calculate_trajectory_paths(
            layer0_score, layer1_score, layer2_score, layer3_score,
            overall_grounding, unknowns_count, sentinel_status
        )

        # Determine recommendation
        recommendation = paths[0]['name']
        recommendation_action = paths[0]['action']

        # Build result
        result = {
            'ok': True,
            'grounding': {
                'overall': overall_grounding,
                'moon': overall_moon,
                'status': overall_status,
                'layers': [{'name': l['name'], 'score': l['score'], 'moon': l['moon'][0], 'status': l['moon'][1]} for l in layers]
            },
            'context': {
                'session_id': session_id,
                'project_id': project_id,
                'unknowns_count': unknowns_count,
                'findings_count': findings_count
            },
            'sentinel': {
                'status': sentinel_status,
                'moon': sentinel_moon
            } if sentinel_status else None,
            'paths': [{
                'name': p['name'],
                'icon': p['icon'],
                'confidence': round(p['confidence'], 2),
                'viable': p['viable'],
                'description': p['description'],
                'blockers': p['blockers'],
                'action': p['action']
            } for p in paths[:depth + 2]],  # Show depth+2 paths
            'recommendation': {
                'path': recommendation,
                'action': recommendation_action
            }
        }

        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            _print_trajectory_human(
                overall_moon, overall_status, overall_grounding, layers,
                unknowns_count, findings_count, sentinel_status, sentinel_moon,
                paths, recommendation, recommendation_action,
                show_turtle, depth, verbose)

    except Exception as e:
        handle_cli_error(e, "Trajectory Project", getattr(args, 'verbose', False))


def _get_open_disputes(db) -> dict:
    """Get open disputes keyed by vector name."""
    try:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT vector, reported_value, expected_value, reason, created_at
            FROM calibration_disputes
            WHERE status = 'open'
            ORDER BY created_at DESC
        """)
        disputes = {}
        for row in cursor.fetchall():
            vector = row[0]
            if vector not in disputes:
                disputes[vector] = {
                    'reported': row[1],
                    'expected': row[2],
                    'reason': row[3],
                    'created_at': row[4],
                }
        return disputes
    except Exception:
        return {}


def _show_disputes(output_format: str):
    """Show all calibration disputes (open and resolved)."""
    import json
    from datetime import datetime

    from empirica.data.session_database import SessionDatabase

    try:
        db = SessionDatabase()
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT dispute_id, vector, reported_value, expected_value,
                   reason, evidence, work_context, status, resolution, created_at
            FROM calibration_disputes
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        db.close()

        if not rows:
            if output_format == 'json':
                print(json.dumps({"ok": True, "disputes": [], "count": 0}))
            else:
                print("No calibration disputes filed.")
            return

        disputes = []
        for row in rows:
            disputes.append({
                "dispute_id": row[0],
                "vector": row[1],
                "reported": row[2],
                "expected": row[3],
                "reason": row[4],
                "evidence": row[5],
                "work_context": row[6],
                "status": row[7],
                "resolution": row[8],
                "created_at": row[9],
            })

        if output_format == 'json':
            print(json.dumps({"ok": True, "disputes": disputes, "count": len(disputes)}, indent=2))
        else:
            print("=" * 70)
            print(f"⚖️  CALIBRATION DISPUTES ({len(disputes)} total)")
            print("=" * 70)
            for d in disputes:
                status_icon = "🟢" if d["status"] == "open" else "⚪"
                ts = datetime.fromtimestamp(d["created_at"]).strftime("%Y-%m-%d %H:%M") if d["created_at"] else "?"
                print(f"\n{status_icon} [{d['status'].upper()}] {d['vector']}  ({ts})")
                print(f"   Reported: {d['reported']:.2f}  Expected: {d['expected']:.2f}  Gap: {abs(d['reported'] - d['expected']):.2f}")
                print(f"   Reason: {d['reason']}")
                if d["evidence"]:
                    print(f"   Evidence: {d['evidence']}")
                if d["work_context"]:
                    print(f"   Context: {d['work_context']}")
                if d["resolution"]:
                    print(f"   Resolution: {d['resolution']}")
            print()

    except Exception as e:
        if output_format == 'json':
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"Error loading disputes: {e}")


def _show_brier_profile(args, ai_id: str, output_format: str):
    """Show Brier score decomposition per phase.

    Brier decomposition (Murphy 1973): BS = Reliability - Resolution + Uncertainty
    - Reliability: calibration error (lower = better, 0 = perfect)
    - Resolution: discrimination power (higher = better)
    - Uncertainty: inherent domain difficulty (not controllable)
    """
    import json

    from empirica.data.session_database import SessionDatabase

    MIN_SAMPLES = 3  # get_brier_profile uses 3 internally

    try:
        db = SessionDatabase()
        from empirica.core.post_test.dynamic_thresholds import get_brier_profile

        profile = get_brier_profile(ai_id, db)
        db.close()

        if not profile:
            if output_format == 'json':
                print(json.dumps({
                    "ok": False,
                    "error": "No Brier profile data available",
                    "hint": "Run POSTFLIGHT sessions to build calibration_trajectory data",
                }))
            else:
                print("No Brier profile data available.")
                print("Hint: Run POSTFLIGHT sessions to build calibration_trajectory data.")
            return

        if output_format == 'json':
            result = {
                "ok": True,
                "calibration_type": "brier",
                "ai_id": ai_id,
                "note": "Murphy (1973) Brier decomposition: BS = Reliability - Resolution + Uncertainty",
                "phases": profile,
            }
            print(json.dumps(result, indent=2))
        else:
            print("=" * 70)
            print("BRIER SCORE DECOMPOSITION (Murphy 1973)")
            print("=" * 70)
            print()
            print("BS = Reliability - Resolution + Uncertainty")
            print("  Reliability: calibration error (lower = better, 0 = perfect)")
            print("  Resolution:  discrimination power (higher = better)")
            print("  Uncertainty: domain difficulty (not controllable)")
            print()

            for phase in ["noetic", "praxic", "combined"]:
                data = profile.get(phase, {})
                phase_label = phase.upper()

                if data.get("status") == "insufficient_data":
                    n = data.get("n", 0)
                    print(f"  {phase_label}: insufficient data ({n}/{MIN_SAMPLES} samples)")
                    print()
                    continue

                if not data or "brier_score" not in data:
                    print(f"  {phase_label}: no data")
                    print()
                    continue

                bs = data["brier_score"]
                rel = data["reliability"]
                res = data["resolution"]
                unc = data["uncertainty"]
                n = data["n_predictions"]
                trend = data.get("trend", "stable")

                # Quality assessment based on Brier score
                if bs < 0.05:
                    quality = "excellent"
                elif bs < 0.10:
                    quality = "good"
                elif bs < 0.20:
                    quality = "fair"
                else:
                    quality = "poor"

                trend_arrow = {"improving": "improving", "degrading": "degrading", "stable": "stable"}.get(trend, "stable")

                print(f"  {phase_label} (n={n}, {quality}, {trend_arrow}):")
                print(f"    Brier Score:  {bs:.4f}")
                print(f"    Reliability:  {rel:.4f}  {'(well calibrated)' if rel < 0.03 else '(calibration error)' if rel > 0.10 else ''}")
                print(f"    Resolution:   {res:.4f}  {'(good discrimination)' if res > 0.05 else ''}")
                print(f"    Uncertainty:  {unc:.4f}")
                print()

            print("-" * 70)
            print("Reliability drives CHECK threshold inflation. Lower = better.")
            print("Resolution measures ability to distinguish easy/hard tasks.")
            print("=" * 70)
            print()

    except Exception as e:
        if output_format == 'json':
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            print(f"Brier profile unavailable: {e}")
            print("Hint: Run POSTFLIGHT sessions to collect calibration data")


def _print_grounded_calibration_human(
    total_grounded_evidence: int, open_disputes: dict, divergence: dict, grounded_adjustments: dict
) -> None:
    """Print human-readable grounded calibration report."""
    print("=" * 70)
    print("🔬 CALIBRATION REPORT (grounded evidence)")
    print("=" * 70)
    print()
    print("Compares POSTFLIGHT self-assessment against objective evidence")
    print("(test results, git metrics, artifact counts, goal completion)")
    print()
    print(f"Total evidence observations: {total_grounded_evidence}")
    if open_disputes:
        print(f"Open disputes: {len(open_disputes)} vector(s)")
    print()

    if divergence:
        print("📊 CALIBRATION (self-assessment vs evidence):")
        print("-" * 70)
        print(f"{'Vector':<15} {'Self-Assessed':>12} {'Grounded':>10} {'Gap':>8} {'Evidence':>10}")
        print("-" * 70)

        sorted_div = sorted(
            divergence.items(),
            key=lambda x: abs(x[1]['gap']),
            reverse=True,
        )
        for vector, data in sorted_div:
            gap = data['gap']
            sign = "+" if gap >= 0 else ""
            disputed = vector in open_disputes
            prefix = "⚖️ " if disputed else ("⚠️ " if abs(gap) >= 0.15 else "   ")
            suffix = " [DISPUTED]" if disputed else ""
            print(
                f"{prefix}{vector:<12} "
                f"{data['self_referential_mean']:>12.2f} "
                f"{data['grounded_mean']:>10.2f} "
                f"{sign}{gap:>7.2f} "
                f"{data['grounded_evidence']:>10}"
                f"{suffix}"
            )
        print("-" * 70)

        # Show dispute details
        if open_disputes:
            print()
            print("⚖️  OPEN DISPUTES (measurement artifacts flagged by AI):")
            for vector, dispute in open_disputes.items():
                print(f"   {vector}: reported={dispute['reported']:.2f}, "
                      f"expected={dispute['expected']:.2f} — {dispute['reason']}")
            print("   (Disputed vectors have reduced weight in Bayesian updates)")
    else:
        print("   No grounded data yet. Run POSTFLIGHT sessions to collect evidence.")

    if grounded_adjustments:
        print()
        print("📋 BIAS CORRECTIONS (apply to self-assessment):")
        for vector, adj in sorted(
            grounded_adjustments.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        ):
            sign = "+" if adj >= 0 else ""
            disputed = " [DISPUTED]" if vector in open_disputes else ""
            print(f"   {vector}: {sign}{adj:.2f}{disputed}")
    print()


def _show_grounded_calibration(args, ai_id: str, weeks: int, output_format: str, show_trajectory: bool):
    """Show grounded calibration (POSTFLIGHT → POST-TEST evidence comparison).

    This is the default output for calibration-report - real calibration
    measuring accuracy of self-assessment against objective evidence.
    """
    import json

    from empirica.data.session_database import SessionDatabase

    try:
        db = SessionDatabase()
        from empirica.core.post_test.grounded_calibration import GroundedCalibrationManager
        gcm = GroundedCalibrationManager(db)
        grounded_beliefs = gcm.get_grounded_beliefs(ai_id)
        grounded_adjustments = gcm.get_grounded_adjustments(ai_id)
        divergence = gcm.get_calibration_divergence(ai_id)

        # Load open disputes
        open_disputes = _get_open_disputes(db)

        total_grounded_evidence = sum(
            b.evidence_count for b in grounded_beliefs.values()
        )

        if output_format == 'json':
            result = {
                "ok": True,
                "calibration_type": "grounded",
                "note": "Grounded calibration: POSTFLIGHT self-assessment vs objective evidence",
                "observations": total_grounded_evidence,
                "adjustments": grounded_adjustments,
                "divergence": divergence,
            }
            if open_disputes:
                result["disputed_vectors"] = {
                    v: {"reason": d["reason"], "expected": d["expected"]}
                    for v, d in open_disputes.items()
                }
            print(json.dumps(result, indent=2))
        else:
            _print_grounded_calibration_human(
                total_grounded_evidence, open_disputes, divergence, grounded_adjustments
            )

        # Optional trajectory trend
        if show_trajectory:
            from empirica.core.post_test.trajectory_tracker import TrajectoryTracker
            tracker = TrajectoryTracker(db)
            summary = tracker.get_trajectory_summary(ai_id)

            if output_format == 'json':
                print(json.dumps({"trajectory": summary}, indent=2))
            else:
                print("=" * 70)
                print("📈 CALIBRATION TRAJECTORY (is gap closing/widening/stable?)")
                print("=" * 70)

                if summary['status'] == 'insufficient_data':
                    print(f"   {summary['message']}")
                else:
                    print(f"Overall direction: {summary['overall_direction']}")
                    print(f"Sessions analyzed: {summary['sessions_analyzed']}")
                    print()

                    trend_summary = summary.get('summary', {})
                    if trend_summary.get('closing'):
                        print(f"   ✅ Closing (improving): {', '.join(trend_summary['closing'])}")
                    if trend_summary.get('widening'):
                        print(f"   ⚠️  Widening (degrading): {', '.join(trend_summary['widening'])}")
                    if trend_summary.get('stable'):
                        print(f"   ➡️  Stable: {', '.join(trend_summary['stable'])}")
                print()

        db.close()

    except Exception as e:
        if output_format == 'json':
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        else:
            print(f"❌ Grounded calibration unavailable: {e}")
            print("   Hint: Run POSTFLIGHT sessions to collect evidence")


_VECTOR_EXPECTED = {
    'engagement': 1.0, 'know': 1.0, 'do': 1.0, 'context': 1.0,
    'clarity': 1.0, 'coherence': 1.0, 'signal': 1.0, 'density': 1.0,
    'state': 1.0, 'change': 1.0, 'completion': 1.0, 'impact': 1.0,
    'uncertainty': 0.0  # Special: should be 0, not 1
}


def _fetch_trajectory_rows(weeks, include_tests):
    """Fetch vector trajectory rows from the sessions database.

    Returns (rows, cutoff_str) or raises if no DB found.
    """
    import sqlite3
    from datetime import datetime, timedelta

    from empirica.config.path_resolver import get_session_db_path

    db_path = str(get_session_db_path())
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cutoff_date = datetime.now() - timedelta(weeks=weeks)
    cutoff_str = cutoff_date.strftime('%Y-%m-%d')

    test_filter = "" if include_tests else """
        AND (ai_id IS NULL OR (
            ai_id NOT LIKE 'test%'
            AND ai_id NOT LIKE '%%-test'
            AND ai_id NOT LIKE 'storage-%%'
        ))
    """

    query = f"""
        SELECT trajectory_id, session_id, ai_id, end_vectors, pattern, created_at
        FROM vector_trajectories
        WHERE end_vectors IS NOT NULL
            AND pattern != 'unknown'
            AND created_at >= ?
            {test_filter}
        ORDER BY created_at DESC
    """

    cursor.execute(query, (cutoff_str,))
    rows = cursor.fetchall()
    conn.close()
    return rows, cutoff_str


def _collect_vector_data(rows):
    """Parse trajectory rows into per-vector and per-week data.

    Returns (vector_data, weekly_data, valid_trajectories, filtered_trajectories).
    """
    import json
    from collections import defaultdict
    from datetime import datetime

    vector_data = defaultdict(list)
    weekly_data = defaultdict(lambda: defaultdict(list))
    valid_trajectories = 0
    filtered_trajectories = 0

    for row in rows:
        _, _, _, end_vectors_json, _, created_at = row

        try:
            end_vectors = json.loads(end_vectors_json)
        except json.JSONDecodeError:
            continue

        values = list(end_vectors.values())
        if values and all(v == 0.5 for v in values):
            filtered_trajectories += 1
            continue

        valid_trajectories += 1

        try:
            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            week_key = dt.strftime('%Y-W%W')
        except Exception:
            week_key = 'unknown'

        for vector_name, value in end_vectors.items():
            if vector_name in _VECTOR_EXPECTED and isinstance(value, (int, float)):
                vector_data[vector_name].append(value)
                weekly_data[week_key][vector_name].append(value)

    return vector_data, weekly_data, valid_trajectories, filtered_trajectories


def _compute_trajectory_metrics(vector_data, weekly_data, min_samples):
    """Calculate per-vector learning trajectory metrics.

    Returns dict mapping vector_name -> metrics dict.
    """
    trajectory = {}
    for vector_name, expected in _VECTOR_EXPECTED.items():
        values = vector_data.get(vector_name, [])
        if not values:
            continue

        count = len(values)
        mean = sum(values) / count

        if expected == 1.0:
            correction = expected - mean
        else:
            correction = -mean

        variance = sum((v - mean) ** 2 for v in values) / count if count > 1 else 0
        std_dev = variance ** 0.5
        std_error = std_dev / (count ** 0.5) if count > 0 else 0

        trend = _compute_trend(weekly_data, vector_name)

        if count >= min_samples:
            confidence = "high"
        elif count >= min_samples // 2:
            confidence = "medium"
        else:
            confidence = "low"

        trajectory[vector_name] = {
            "correction": round(correction, 2),
            "end_mean": round(mean, 2),
            "expected": expected,
            "count": count,
            "std_error": round(std_error, 3),
            "trend": trend,
            "confidence": confidence
        }
    return trajectory


def _compute_trend(weekly_data, vector_name):
    """Determine trend direction from weekly data for a given vector."""
    weeks_list = sorted(weekly_data.keys())
    if len(weeks_list) < 2:
        return "→ stable"

    early_weeks = weeks_list[:len(weeks_list)//2]
    late_weeks = weeks_list[len(weeks_list)//2:]

    early_values = []
    late_values = []
    for w in early_weeks:
        early_values.extend(weekly_data[w].get(vector_name, []))
    for w in late_weeks:
        late_values.extend(weekly_data[w].get(vector_name, []))

    early_mean = sum(early_values) / len(early_values) if early_values else 0
    late_mean = sum(late_values) / len(late_values) if late_values else 0

    delta = late_mean - early_mean
    if delta > 0.05:
        return "↑ improving"
    elif delta < -0.05:
        return "↓ declining"
    return "→ stable"


def _identify_key_issues(sorted_vectors):
    """Identify vectors with |correction| >= 0.15."""
    key_issues = []
    for vector_name, data in sorted_vectors:
        if abs(data['correction']) >= 0.15:
            if vector_name == 'uncertainty':
                meaning = "Residual doubt (should be ~0)"
            elif data['correction'] > 0:
                meaning = f"Underestimate {vector_name}"
            else:
                meaning = f"Overestimate {vector_name}"
            key_issues.append({
                "vector": vector_name,
                "correction": data['correction'],
                "meaning": meaning
            })
    return key_issues


def _correction_meaning(vector_name, correction):
    """Return human-readable meaning for a correction value."""
    if vector_name == 'uncertainty':
        return "Residual doubt (should be ~0)"
    elif abs(correction) < 0.08:
        return "Well calibrated"
    elif correction > 0:
        return f"Underestimate {vector_name}"
    else:
        return f"Overestimate {vector_name}"


def _format_correction_str(correction):
    """Format correction with sign and optional bold for markdown."""
    corr_str = f"+{correction:.2f}" if correction >= 0 else f"{correction:.2f}"
    if abs(correction) >= 0.15:
        corr_str = f"**{corr_str}**"
    return corr_str


def _print_markdown_table(sorted_vectors, valid_trajectories, weeks):
    """Print markdown-formatted learning trajectory table."""
    print(f"## Learning Trajectory ({valid_trajectories} trajectories over {weeks} weeks)")
    print()
    print("*PREFLIGHT→POSTFLIGHT deltas. NOT calibration (use grounded evidence for that).*")
    print()
    print("| Vector | Correction | End Mean | Trend | Meaning |")
    print("|--------|------------|----------|-------|---------|")

    for vector_name, data in sorted_vectors:
        corr_str = _format_correction_str(data['correction'])
        meaning = _correction_meaning(vector_name, data['correction'])
        print(f"| {vector_name} | {corr_str} | {data['end_mean']:.2f} | {data['trend']} | {meaning} |")

    print()
    print("**Apply corrections:** ADD the correction to your self-assessment.")
    print("**Readiness gate:** uncertainty <= 0.35 (meta uncertainty)")


def _print_human_trajectory(result, sorted_vectors, key_issues, filtered_trajectories,
                             valid_trajectories, weeks, weekly_data, verbose, update_prompt):
    """Print human-readable learning trajectory output."""
    print("=" * 70)
    print("📊 LEARNING TRAJECTORY (PREFLIGHT→POSTFLIGHT)")
    print("=" * 70)
    print("NOTE: This is learning data, NOT calibration. For grounded calibration,")
    print("      run: empirica calibration-report (without --learning-trajectory)")
    print()
    print(f"Data source: vector_trajectories ({valid_trajectories} trajectories)")
    print(f"Period: {result['date_range']} ({weeks} weeks)")
    if filtered_trajectories:
        print(f"Filtered: {filtered_trajectories} placeholder sessions excluded")
    print()

    if key_issues:
        print("🎯 KEY PATTERNS (|delta| >= 0.15):")
        for issue in key_issues:
            sign = "+" if issue['correction'] >= 0 else ""
            print(f"   {issue['vector']}: {sign}{issue['correction']:.2f} - {issue['meaning']}")
        print()

    print("📈 PER-VECTOR LEARNING DELTAS:")
    print("-" * 70)
    print(f"{'Vector':<15} {'Correction':>10} {'End Mean':>10} {'Samples':>8} {'Trend':>15}")
    print("-" * 70)

    for vector_name, data in sorted_vectors:
        correction = data['correction']
        sign = "+" if correction >= 0 else ""
        prefix = "⚠️ " if abs(correction) >= 0.15 else "   "
        print(f"{prefix}{vector_name:<12} {sign}{correction:>8.2f} {data['end_mean']:>10.2f} {data['count']:>8} {data['trend']:>15}")

    print("-" * 70)
    print()
    print("📋 READINESS GATE:")
    print("   uncertainty <= 0.35 (meta uncertainty, after bias correction)")
    print("   Apply: ADD corrections to your self-assessment")
    print()

    if verbose:
        print("📊 WEEKLY TREND DATA:")
        weeks_list = sorted(weekly_data.keys())
        for week in weeks_list[-4:]:
            week_vectors = weekly_data[week]
            if week_vectors:
                know_vals = week_vectors.get('know', [])
                unc_vals = week_vectors.get('uncertainty', [])
                know_mean = sum(know_vals) / len(know_vals) if know_vals else 0
                unc_mean = sum(unc_vals) / len(unc_vals) if unc_vals else 0
                print(f"   {week}: know={know_mean:.2f}, uncertainty={unc_mean:.2f} (n={len(know_vals)})")

    if update_prompt:
        print()
        print("=" * 70)
        print("📝 COPY-PASTE FOR SYSTEM PROMPT:")
        print("=" * 70)
        print()
        print("| Vector | Correction | End Mean | Trend | Meaning |")
        print("|--------|------------|----------|-------|---------|")
        for vector_name, data in sorted_vectors:
            corr_str = _format_correction_str(data['correction'])
            meaning = _correction_meaning(vector_name, data['correction'])
            print(f"| {vector_name} | {corr_str} | {data['end_mean']:.2f} | {data['trend']} | {meaning} |")

    print()
    print("=" * 70)
    print()
    print("Note: This shows learning trajectory (PREFLIGHT→POSTFLIGHT deltas).")
    print("      For actual calibration (grounded evidence), run without --learning-trajectory.")


def handle_calibration_report_command(args):
    """Handle calibration-report command.

    Default: Shows grounded calibration (POSTFLIGHT -> POST-TEST evidence comparison).
    This is real calibration - measuring accuracy of self-assessment against reality.

    Use --learning-trajectory to see PREFLIGHT->POSTFLIGHT deltas (learning, not calibration).
    """
    try:
        import json
        from datetime import datetime

        ai_id = getattr(args, 'ai_id', None) or 'claude-code'
        weeks = getattr(args, 'weeks', 8)
        include_tests = getattr(args, 'include_tests', False)
        min_samples = getattr(args, 'min_samples', 10)
        output_format = getattr(args, 'output', 'human')
        update_prompt = getattr(args, 'update_prompt', False)
        verbose = getattr(args, 'verbose', False)

        show_learning_trajectory = getattr(args, 'learning_trajectory', False)
        show_trajectory_trend = getattr(args, 'trajectory', False)
        list_disputes = getattr(args, 'list_disputes', False)
        show_brier = getattr(args, 'brier', False)

        if list_disputes:
            return _show_disputes(output_format)
        if show_brier:
            return _show_brier_profile(args, ai_id, output_format)
        if not show_learning_trajectory:
            return _show_grounded_calibration(args, ai_id, weeks, output_format, show_trajectory_trend)

        # LEGACY: Show learning trajectory (PREFLIGHT->POSTFLIGHT deltas)
        try:
            rows, cutoff_str = _fetch_trajectory_rows(weeks, include_tests)
        except FileNotFoundError:
            print(json.dumps({"ok": False, "error": "No sessions database found"}, indent=2))
            return

        if not rows:
            result = {
                "ok": False,
                "error": f"No trajectories found in last {weeks} weeks",
                "hint": "Run more epistemic transaction workflows to build calibration data"
            }
            if output_format == 'json':
                print(json.dumps(result, indent=2))
            else:
                print(f"❌ No calibration data found in last {weeks} weeks")
            return

        vector_data, weekly_data, valid_trajectories, filtered_trajectories = \
            _collect_vector_data(rows)

        if valid_trajectories == 0:
            result = {
                "ok": False,
                "error": "No valid trajectories after filtering",
                "filtered": filtered_trajectories,
                "hint": "All trajectories had 0.5 placeholder values"
            }
            if output_format == 'json':
                print(json.dumps(result, indent=2))
            else:
                print(f"❌ No valid calibration data (filtered {filtered_trajectories} placeholder sessions)")
            return

        trajectory = _compute_trajectory_metrics(vector_data, weekly_data, min_samples)

        sorted_vectors = sorted(
            trajectory.items(),
            key=lambda x: abs(x[1]['correction']),
            reverse=True
        )

        key_issues = _identify_key_issues(sorted_vectors)

        know_data = trajectory.get('know', {})
        uncertainty_data = trajectory.get('uncertainty', {})
        result = {
            "ok": True,
            "type": "learning_trajectory",
            "note": "PREFLIGHT→POSTFLIGHT deltas (NOT calibration - use grounded evidence for that)",
            "data_source": "vector_trajectories",
            "total_trajectories": valid_trajectories,
            "filtered_trajectories": filtered_trajectories,
            "weeks_analyzed": weeks,
            "date_range": f"{cutoff_str} to {datetime.now().strftime('%Y-%m-%d')}",
            "ai_id_filter": ai_id if ai_id else "all",
            "learning_trajectory": dict(sorted_vectors),
            "key_issues": key_issues,
            "readiness_gate": {
                "threshold": "uncertainty <= 0.35 (meta uncertainty gate)",
                "know_correction": know_data.get('correction', 0),
                "uncertainty_correction": uncertainty_data.get('correction', 0),
                "note": "Apply corrections: ADD to self-assessment"
            }
        }

        if output_format == 'json':
            print(json.dumps(result, indent=2))
        elif output_format == 'markdown' or update_prompt:
            _print_markdown_table(sorted_vectors, valid_trajectories, weeks)
        else:
            _print_human_trajectory(
                result, sorted_vectors, key_issues, filtered_trajectories,
                valid_trajectories, weeks, weekly_data, verbose, update_prompt
            )

    except Exception as e:
        handle_cli_error(e, "Calibration Report", getattr(args, 'verbose', False))


def handle_system_status_command(args):
    """
    Unified Noetic OS system status.

    Aggregates all kernel subsystems into a single view:
    config, memory, bus, attention, integrity, gate.
    """
    try:
        output_format = getattr(args, 'output', 'human')
        summary_mode = getattr(args, 'summary', False)
        session_id = getattr(args, 'session_id', None)

        # Auto-detect session if not provided
        if not session_id:
            try:
                session_id = R.latest_session_id(ai_id='claude-code', active_only=True)
            except Exception:
                pass

        if not session_id:
            if output_format == 'json':
                print(json.dumps({"ok": False, "error": "No active session found"}))
            else:
                print("\n  No active Empirica session found.")
                print("  Run: empirica session-create --ai-id claude-code")
            return

        # Create dashboard and get status
        from empirica.core.system_dashboard import SystemDashboard
        dashboard = SystemDashboard(
            session_id=session_id,
            auto_subscribe=False,  # CLI is one-shot, no bus subscription
        )
        status = dashboard.get_system_status()

        if output_format == 'json':
            print(json.dumps(status.to_dict(), indent=2, default=str))
        elif summary_mode:
            print(status.format_summary())
        else:
            print(status.format_display())

    except Exception as e:
        handle_cli_error(e, "System Status", getattr(args, 'verbose', False))


def handle_calibration_dispute_command(args):
    """Handle calibration-dispute command — AI pushback on measurement artifacts.

    When grounded calibration reports a gap that's a measurement bug (not a real
    overestimate), the AI files a dispute. Disputes are stored in SQLite and
    flagged in subsequent calibration reports.
    """
    import json
    import sys
    import time
    import uuid

    try:
        from empirica.data.session_database import SessionDatabase

        vector = args.vector
        reported = args.reported
        expected = args.expected
        reason = args.reason
        evidence = getattr(args, 'evidence', None) or ''
        output_format = getattr(args, 'output', 'json')

        # Validate vector name
        valid_vectors = {'know', 'uncertainty', 'context', 'engagement', 'clarity',
                         'coherence', 'signal', 'density', 'state', 'change',
                         'completion', 'impact', 'do'}
        if vector not in valid_vectors:
            result = {"ok": False, "error": f"Invalid vector: {vector}. Must be one of: {', '.join(sorted(valid_vectors))}"}
            print(json.dumps(result))
            sys.exit(1)

        # Resolve session
        session_id = getattr(args, 'session_id', None)
        if not session_id:
            session_id = R.session_id()
        if not session_id:
            result = {"ok": False, "error": "No active session. Use --session-id or start a session first."}
            print(json.dumps(result))
            sys.exit(1)

        # Read work_context from active transaction if available
        work_context = None
        try:
            suffix = R.instance_suffix()
            project_path = R.project_path()
            if project_path:
                tx_file = Path(project_path) / '.empirica' / f'active_transaction{suffix}.json'
                if tx_file.exists():
                    with open(tx_file) as f:
                        tx = json.load(f)
                    work_context = tx.get('work_context')
        except Exception:
            pass

        # Store the dispute
        db = SessionDatabase()
        dispute_id = str(uuid.uuid4())
        db.conn.execute("""
            INSERT INTO calibration_disputes
                (dispute_id, session_id, vector, reported_value, expected_value,
                 reason, evidence, work_context, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """, (dispute_id, session_id, vector, reported, expected,
              reason, evidence, work_context, time.time()))
        db.conn.commit()
        db.close()

        result = {
            "ok": True,
            "dispute_id": dispute_id,
            "vector": vector,
            "reported": reported,
            "expected": expected,
            "reason": reason,
            "work_context": work_context,
        }

        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            gap = abs(reported - expected)
            print(f"Dispute filed: {vector}")
            print(f"  Reported (grounded): {reported:.2f}")
            print(f"  Expected (actual):   {expected:.2f}")
            print(f"  Gap:                 {gap:.2f}")
            print(f"  Reason: {reason}")
            if work_context:
                print(f"  Context: {work_context}")
            print(f"  ID: {dispute_id[:8]}...")

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


def handle_workflow_patterns_command(args):
    """Detect and display repeated workflow patterns across transactions."""
    try:
        output_format = getattr(args, 'output', 'human')
        limit = getattr(args, 'limit', 50)
        min_freq = getattr(args, 'min_frequency', 2)

        from empirica.config.path_resolver import get_session_db_path
        from empirica.core.workflow_patterns import (
            detect_patterns,
            format_patterns_human,
            format_suggestions_human,
            generate_suggestions,
            load_traces_from_db,
            load_transaction_outcomes,
        )

        db_path = str(get_session_db_path())
        traces = load_traces_from_db(db_path, limit=limit)

        if not traces:
            if output_format == 'json':
                print(json.dumps({"ok": True, "patterns": [], "suggestions": [],
                                  "traces_loaded": 0,
                                  "message": "No tool traces found. Traces are recorded after POSTFLIGHT."}))
            else:
                print("No tool traces found yet. Traces are recorded at POSTFLIGHT.")
                print("Complete a full transaction (PREFLIGHT → work → POSTFLIGHT) to start recording.")
            return

        patterns = detect_patterns(traces, min_frequency=min_freq)

        # Load outcomes for suggestions (traces + vectors + calibration)
        outcomes = load_transaction_outcomes(db_path, limit=limit)
        suggestions = generate_suggestions(outcomes)

        if output_format == 'json':
            print(json.dumps({
                "ok": True,
                "patterns": [p.to_dict() for p in patterns],
                "suggestions": [s.to_dict() for s in suggestions],
                "traces_loaded": len(traces),
                "outcomes_loaded": len(outcomes),
                "patterns_detected": len(patterns),
                "suggestions_generated": len(suggestions),
            }, indent=2))
        else:
            print(f"Analyzed {len(traces)} transactions ({len(outcomes)} with vectors):\n")
            print(format_patterns_human(patterns))
            if suggestions:
                print("\n" + format_suggestions_human(suggestions))

    except Exception as e:
        if getattr(args, 'output', 'human') == 'json':
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"Error: {e}")
