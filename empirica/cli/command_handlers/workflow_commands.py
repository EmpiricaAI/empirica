"""
Workflow Commands - MCP v2 Integration Commands

Handles CLI commands for:
- preflight-submit: Submit preflight assessment results
- check: Execute epistemic check assessment
- check-submit: Submit check assessment results
- postflight-submit: Submit postflight assessment results

These commands provide JSON output for MCP v2 server integration.
"""

import json
import logging
from typing import Any

from empirica.config.path_resolver import resolve_session_db_path
from empirica.core.canonical.empirica_git.sentinel_hooks import SentinelDecision, SentinelHooks, auto_enable_sentinel
from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error, parse_json_safely, run_empirica_subprocess
from ..validation import PreflightInput, safe_validate

# Auto-enable Sentinel with default evaluator on module load
auto_enable_sentinel()

logger = logging.getLogger(__name__)


def _remap_trajectory_summary(calibration_summary):
    """Remap Bayesian calibration_summary keys to learning trajectory language.

    The BayesianBeliefManager uses calibration terms (overestimates/underestimates)
    but these represent learning patterns, not accuracy corrections.
    Remap to make the distinction clear in PREFLIGHT output.
    """
    if not calibration_summary:
        return None
    return {
        "typically_increases": calibration_summary.get("underestimates", []),
        "typically_decreases": calibration_summary.get("overestimates", []),
        "stable": calibration_summary.get("well_calibrated", []),
    }


def _get_db_for_session(session_id: str):
    """
    Get SessionDatabase for a specific session_id.

    Resolves the session to its correct project database, allowing
    CLI commands to work correctly even when CWD is different from
    the session's project.

    Args:
        session_id: The session UUID

    Returns:
        SessionDatabase instance connected to the correct project's DB
    """
    from empirica.data.session_database import SessionDatabase

    db_path = resolve_session_db_path(session_id)
    if db_path:
        return SessionDatabase(db_path=str(db_path))
    else:
        # Fallback to CWD-based detection (legacy behavior)
        return SessionDatabase()


def _check_bootstrap_status(session_id: str) -> dict:
    """
    Check if project-bootstrap has been run for this session.

    Returns:
        {
            "has_bootstrap": bool,
            "project_id": str or None,
            "session_exists": bool
        }
    """
    try:
        db = _get_db_for_session(session_id)
        cursor = db.conn.cursor()

        # Check if session exists and has project_id
        cursor.execute("""
            SELECT session_id, project_id FROM sessions
            WHERE session_id = ?
        """, (session_id,))
        row = cursor.fetchone()
        db.close()

        if not row:
            return {
                "has_bootstrap": False,
                "project_id": None,
                "session_exists": False
            }

        project_id = row[1] if row else None
        return {
            "has_bootstrap": project_id is not None,
            "project_id": project_id,
            "session_exists": True
        }
    except Exception as e:
        return {
            "has_bootstrap": False,
            "project_id": None,
            "session_exists": False,
            "error": str(e)
        }


def _auto_bootstrap(session_id: str) -> dict:
    """
    Auto-run project-bootstrap for a session.

    Returns:
        {"ok": bool, "project_id": str, "message": str}
    """
    try:
        result = run_empirica_subprocess(
            ['empirica', 'project-bootstrap', '--session-id', session_id, '--output', 'json'],
            timeout=30
        )

        if result.returncode == 0:
            try:
                output = json.loads(result.stdout)
                return {
                    "ok": True,
                    "project_id": output.get('project_id'),
                    "message": "Auto-bootstrap completed"
                }
            except json.JSONDecodeError:
                return {"ok": True, "project_id": None, "message": "Bootstrap ran (non-JSON output)"}
        else:
            return {"ok": False, "error": result.stderr[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _parse_workflow_input(args, phase: str):
    """Parse and validate workflow input from config file, stdin, or CLI flags.

    Shared across PREFLIGHT, CHECK, and POSTFLIGHT handlers.
    Returns (config_data, output_format) where config_data is parsed JSON
    or None if using legacy CLI flags.
    """
    import os
    import sys

    config_data = None

    # AI-FIRST MODE: Check if config file provided or stdin piped
    if hasattr(args, 'config') and args.config:
        if args.config == '-':
            config_data = parse_json_safely(sys.stdin.read())
        else:
            if not os.path.exists(args.config):
                print(json.dumps({"ok": False, "error": f"Config file not found: {args.config}"}))
                sys.exit(1)
            with open(args.config) as f:
                config_data = parse_json_safely(f.read())
    elif not sys.stdin.isatty():
        config_data = parse_json_safely(sys.stdin.read())

    if config_data:
        # Merge CLI session_id as fallback
        if not config_data.get('session_id') and getattr(args, 'session_id', None):
            config_data['session_id'] = args.session_id
        # Auto-resolve session_id from active session
        if not config_data.get('session_id'):
            try:
                auto_sid = R.session_id()
                if auto_sid:
                    config_data['session_id'] = auto_sid
                    logger.debug(f"{phase}: Auto-derived session_id: {auto_sid[:8]}...")
            except Exception:
                pass
        return config_data, 'json'

    return None, getattr(args, 'output', 'json')


def _resolve_and_validate_session(session_id: str, phase: str) -> str:
    """Resolve partial session IDs to full UUIDs with consistent error handling.

    Shared across PREFLIGHT, CHECK, and POSTFLIGHT.
    Returns the resolved session_id or exits with error JSON.
    """
    import sys

    try:
        return R.resolve_session(session_id)
    except ValueError as e:
        print(json.dumps({
            "ok": False,
            "error": f"Invalid session_id: {e}",
            "hint": "Use full UUID, partial UUID (8+ chars), or 'latest'"
        }))
        sys.exit(1)


def _invoke_sentinel_hook(phase: str, session_id: str, checkpoint_data: dict):
    """Invoke Sentinel post-checkpoint hook if enabled.

    Returns SentinelDecision or None.
    """
    if SentinelHooks.is_enabled():
        return SentinelHooks.post_checkpoint_hook(
            session_id=session_id,
            ai_id=None,
            phase=phase,
            checkpoint_data=checkpoint_data
        )
    return None


def _preflight_parse_and_validate(args):
    """Parse input and validate for PREFLIGHT. Returns dict with parsed fields.

    Returns:
        dict with keys: session_id, vectors, reasoning, task_context,
        work_context, work_type, domain, criticality, predicted_check_outcomes,
        output_format
    """
    import sys

    config_data, output_format = _parse_workflow_input(args, "PREFLIGHT")

    if config_data:
        validated, error = safe_validate(config_data, PreflightInput)
        if error:
            print(json.dumps({
                "ok": False,
                "error": f"Invalid input: {error}",
                "hint": "Required: session_id (str), vectors (dict with know, uncertainty)"
            }))
            sys.exit(1)
        session_id = validated.session_id
        vectors = validated.vectors
        reasoning = validated.reasoning or ''
        task_context = validated.task_context or ''
        work_context = getattr(validated, 'work_context', None)
        work_type = getattr(validated, 'work_type', None)
        domain = getattr(validated, 'domain', None)
        criticality = getattr(validated, 'criticality', None)
        predicted_check_outcomes = getattr(validated, 'predicted_check_outcomes', None)
        voice = getattr(validated, 'voice', None)
    else:
        session_id = args.session_id
        vectors = parse_json_safely(args.vectors) if isinstance(args.vectors, str) else args.vectors
        reasoning = args.reasoning
        task_context = getattr(args, 'task_context', '') or ''
        work_context = None
        work_type = None
        domain = None
        criticality = None
        predicted_check_outcomes = None
        voice = getattr(args, 'voice', None)

        if not session_id or not vectors:
            print(json.dumps({
                "ok": False,
                "error": "Legacy mode requires --session-id and --vectors flags",
                "hint": "For AI-first mode, use: empirica preflight-submit config.json"
            }))
            sys.exit(1)

        legacy_data = {'session_id': session_id, 'vectors': vectors, 'reasoning': reasoning}
        validated, error = safe_validate(legacy_data, PreflightInput)
        if error:
            print(json.dumps({
                "ok": False,
                "error": f"Invalid vectors: {error}",
                "hint": "Vectors must include 'know' and 'uncertainty' (0.0-1.0)"
            }))
            sys.exit(1)
        vectors = validated.vectors

    session_id = _resolve_and_validate_session(session_id, "PREFLIGHT")
    vectors = _extract_all_vectors(vectors)

    return {
        "session_id": session_id,
        "vectors": vectors,
        "reasoning": reasoning,
        "task_context": task_context,
        "work_context": work_context,
        "work_type": work_type,
        "domain": domain,
        "criticality": criticality,
        "predicted_check_outcomes": predicted_check_outcomes,
        "voice": voice,
        "output_format": output_format,
    }


def _preflight_check_unclosed_transaction():
    """Check for unclosed transaction and return warning dict or None.

    Auto-closing would poison vector states (fabricated POSTFLIGHT vectors),
    so we warn but don't block.
    """
    import time

    try:
        existing_tx = R.transaction_read()
        if existing_tx and existing_tx.get('status') == 'open':
            existing_tx_id = existing_tx.get('transaction_id', 'unknown')
            existing_tx_time = existing_tx.get('preflight_timestamp', 0)
            age_minutes = int((time.time() - existing_tx_time) / 60) if existing_tx_time else 0
            return {
                "previous_transaction_id": existing_tx_id[:12] + "...",
                "age_minutes": age_minutes,
                "message": "Previous transaction was not closed with POSTFLIGHT. Learning delta from that work is lost. Run POSTFLIGHT before PREFLIGHT to measure learning.",
                "impact": "Unmeasured work = epistemic dark matter. Calibration cannot improve without POSTFLIGHT."
            }
    except Exception:
        pass  # Non-fatal — proceed with new transaction
    return None


def _preflight_create_checkpoint(session_id, vectors, reasoning, transaction_id):
    """Create GitEnhancedReflexLogger checkpoint for PREFLIGHT.

    Writes to ALL 3 storage layers (SQLite + Git Notes + JSON).
    Returns checkpoint_id.
    """
    from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

    logger_instance = GitEnhancedReflexLogger(
        session_id=session_id,
        enable_git_notes=True  # Enable git notes for cross-AI features
    )

    return logger_instance.add_checkpoint(
        phase="PREFLIGHT",
        vectors=vectors,
        metadata={
            "reasoning": reasoning,
            "prompt": reasoning or "Preflight assessment",
            "transaction_id": transaction_id
        }
    )


def _preflight_enrich_transaction_file(resolved_project_path, parsed):
    """Inject work parameters and cascade profile into the transaction file. Non-fatal."""
    import json as _json
    from pathlib import Path

    work_context = parsed["work_context"]
    work_type = parsed["work_type"]
    domain = parsed["domain"]
    criticality = parsed["criticality"]
    predicted_check_outcomes = parsed["predicted_check_outcomes"]

    if not (work_context or work_type or domain or criticality):
        return

    try:
        suffix = R.instance_suffix()
        tx_file = Path(resolved_project_path) / '.empirica' / f'active_transaction{suffix}.json'
        if not tx_file.exists():
            logger.warning(f"Transaction file not found for enrichment: {tx_file}")
            return

        with open(tx_file) as f:
            tx_d = _json.load(f)
        for key, val in [('work_context', work_context), ('work_type', work_type),
                         ('domain', domain), ('criticality', criticality),
                         ('predicted_check_outcomes', predicted_check_outcomes)]:
            if val:
                tx_d[key] = val

        from empirica.config.threshold_loader import ThresholdLoader
        selected_profile = ThresholdLoader.select_profile_for_work(
            work_type=work_type, work_context=work_context
        )
        tx_d['cascade_profile'] = selected_profile
        with open(tx_file, 'w') as f:
            _json.dump(tx_d, f, indent=2)
        logger.debug(f"Transaction enriched: work_type={work_type}, domain={domain}, criticality={criticality}")
        if selected_profile != 'default':
            logger.info(f"Cascade profile: {selected_profile} (from work_type={work_type}, work_context={work_context})")
    except Exception as e:
        logger.warning(f"Transaction enrichment failed: {e}")


def _preflight_write_transaction_file(session_id, transaction_id, parsed):
    """Persist active transaction file and enrich with work parameters.

    Includes session_id and project_path so operations work regardless of CWD.
    Returns resolved_project_path or None.
    """
    import time

    from empirica.utils.session_resolver import update_active_context

    context = R.context()
    claude_session_id = context.get('claude_session_id')
    resolved_project_path = context.get('project_path') or R.project_path(claude_session_id)
    if not resolved_project_path:
        logger.warning("Cannot determine project_path for transaction file - no context found")
        return None

    R.transaction_write(
        transaction_id=transaction_id,
        session_id=session_id,
        preflight_timestamp=time.time(),
        status="open",
        project_path=resolved_project_path
    )

    _preflight_enrich_transaction_file(resolved_project_path, parsed)

    # CRITICAL: Update active context with the session_id used by PREFLIGHT
    # This ensures sentinel reads the SAME session_id that PREFLIGHT wrote to
    if claude_session_id:
        update_active_context(
            claude_session_id=claude_session_id,
            empirica_session_id=session_id,
            project_path=resolved_project_path
        )

    # AUTONOMY CALIBRATION: Calculate avg_turns from past transactions
    # and inject into the new transaction for Sentinel nudge thresholds
    _preflight_inject_avg_turns(session_id, resolved_project_path)

    return resolved_project_path


def _preflight_inject_avg_turns(session_id, resolved_project_path):
    """Calculate avg_turns from past transactions and inject into transaction file."""
    import json as _json
    import os
    from pathlib import Path

    from empirica.data.session_database import SessionDatabase

    try:
        avg_db = SessionDatabase()
        avg_cursor = avg_db.conn.cursor()
        # Query past POSTFLIGHT reflex_data for tool_call_count
        avg_cursor.execute("""
            SELECT json_extract(reflex_data, '$.tool_call_count')
            FROM reflexes
            WHERE session_id = ? AND phase = 'POSTFLIGHT'
              AND json_extract(reflex_data, '$.tool_call_count') IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 20
        """, (session_id,))
        past_counts = [row[0] for row in avg_cursor.fetchall() if row[0] and row[0] > 0]
        avg_db.close()

        if past_counts:
            avg_turns = int(sum(past_counts) / len(past_counts))
        else:
            avg_turns = 0  # No history yet — nudge disabled until first complete cycle

        # Update the transaction file with avg_turns
        tx_data = R.transaction_read()
        if tx_data:
            tx_data['avg_turns'] = avg_turns
            suffix = R.instance_suffix()
            tx_path = Path(resolved_project_path) / '.empirica' / f'active_transaction{suffix}.json'
            if tx_path.exists():
                import tempfile as _tempfile
                fd, tmp = _tempfile.mkstemp(dir=str(tx_path.parent))
                with os.fdopen(fd, 'w') as tf:
                    _json.dump(tx_data, tf, indent=2)
                os.replace(tmp, str(tx_path))
    except Exception as e_avg:
        logger.debug(f"Avg turns calculation failed (non-fatal): {e_avg}")


def _preflight_publish_bus_event(session_id, transaction_id, vectors, task_context, work_type, work_context):
    """Wire persistent observers and publish PREFLIGHT event on the epistemic bus.

    This enables cross-instance event subscription via SQLite + Qdrant.
    """
    try:
        from empirica.core.bus_persistence import wire_persistent_observers
        from empirica.core.epistemic_bus import (
            EpistemicEvent,
            EventTypes,
            get_global_bus,
        )
        wire_persistent_observers(session_id=session_id)
        bus = get_global_bus()
        bus.publish(EpistemicEvent(
            event_type=EventTypes.PREFLIGHT_COMPLETE,
            agent_id="claude-code",
            session_id=session_id,
            data={
                "transaction_id": transaction_id,
                "vectors": vectors,
                "task_context": task_context,
                "work_type": work_type,
                "work_context": work_context,
            },
        ))
    except Exception as e:
        logger.debug(f"Bus publish (PREFLIGHT) failed (non-fatal): {e}")


def _preflight_load_calibration(db, session_id):
    """Load Bayesian calibration adjustments and project_id from DB.

    Returns dict with keys: calibration_adjustments, calibration_report,
    ai_id, project_id.
    """
    calibration_adjustments = {}
    calibration_report = None
    ai_id = 'unknown'

    # BAYESIAN CALIBRATION: Load calibration adjustments based on historical performance
    # This informs the AI about its known biases from past sessions
    try:
        from empirica.core.bayesian_beliefs import BayesianBeliefManager

        # Get AI ID from session
        cursor = db.conn.cursor()
        cursor.execute("SELECT ai_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        ai_id = row[0] if row else 'unknown'

        if ai_id != 'unknown':
            belief_manager = BayesianBeliefManager(db)
            calibration_adjustments = belief_manager.get_calibration_adjustments(ai_id)
            calibration_report = belief_manager.get_calibration_report(ai_id)

            if calibration_adjustments:
                logger.debug(f"Loaded calibration adjustments for {len(calibration_adjustments)} vectors")
    except Exception as e:
        logger.debug(f"Calibration loading failed (non-fatal): {e}")

    # Get project_id for pattern retrieval
    project_id = None
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        project_id = row[0] if row else None
    except Exception:
        pass

    return {
        "calibration_adjustments": calibration_adjustments,
        "calibration_report": calibration_report,
        "ai_id": ai_id,
        "project_id": project_id,
    }


def _feedback_extract_retrospective(cursor, session_id):
    """Extract behavioral feedback from last POSTFLIGHT retrospective.

    Returns (feedback_dict, pf_meta) or (None, None) if no retrospective found.
    """
    cursor.execute("""
        SELECT meta FROM reflexes
        WHERE session_id = ? AND phase = 'POSTFLIGHT'
        ORDER BY timestamp DESC LIMIT 1
    """, (session_id,))
    pf_row = cursor.fetchone()
    if not (pf_row and pf_row[0]):
        return None, None

    pf_meta = json.loads(pf_row[0]) if isinstance(pf_row[0], str) else pf_row[0]
    retro = pf_meta.get('retrospective', {})
    feedback = {}

    artifact_counts = retro.get('artifact_counts', {})
    missing = [k for k, v in artifact_counts.items() if v == 0] if artifact_counts else []
    if missing:
        feedback["artifact_gaps"] = missing

    if retro.get('breadth_note'):
        feedback["breadth_warning"] = retro['breadth_note']
    if retro.get('edge_density_note'):
        feedback["edge_density_warning"] = retro['edge_density_note']
    if retro.get('sources_discipline_note'):
        feedback["sources_discipline_warning"] = retro['sources_discipline_note']
    if retro.get('commit_warning'):
        feedback["commit_discipline"] = retro['commit_warning']

    cs = pf_meta.get('context_shifts')
    if cs and cs.get('unsolicited_prompts', 0) > 0:
        feedback["context_shifts"] = (
            f"{cs['unsolicited_prompts']} unsolicited context shift(s) in previous transaction."
        )

    return feedback, pf_meta


def _feedback_collect_suggestions(cursor, session_id, project_id, retro_meta):
    """Collect actionable suggestions from behavioral gaps. Returns list of strings."""
    if not retro_meta:
        return []

    retro = retro_meta.get('retrospective', {})
    artifact_counts = retro.get('artifact_counts', {})
    missing = [k for k, v in artifact_counts.items() if v == 0] if artifact_counts else []

    suggestions = []
    if missing and len(missing) >= 4:
        suggestions.append("Load /epistemic-transaction for artifact discipline guidance")
    if retro.get('commit_warning'):
        suggestions.append("Commit per subtask — don't batch to end")

    try:
        cursor.execute("""
            SELECT COUNT(*) FROM project_unknowns
            WHERE session_id = ? AND is_resolved = 0
        """, (session_id,))
        open_unknowns = cursor.fetchone()[0]
        if open_unknowns >= 3:
            suggestions.append(f"{open_unknowns} unresolved unknowns — run: empirica unknown-list")
    except Exception:
        pass

    try:
        if project_id:
            cursor.execute("""
                SELECT COUNT(*) FROM goals
                WHERE session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)
                AND status = 'in_progress'
            """, (project_id,))
            active_goals = cursor.fetchone()[0]
            if active_goals == 0:
                suggestions.append("No active goals — run: empirica goals-create --objective '...'")
    except Exception:
        pass

    return suggestions


def _feedback_compute_calibration_trend(cursor, ai_id, project_id):
    """Compute calibration trend from recent grounded verifications.

    Returns trend string ('improving', 'widening', 'stable') or None.
    """
    if not project_id:
        return None
    try:
        cursor.execute("""
            SELECT overall_calibration_score FROM grounded_verifications
            WHERE ai_id = ? AND project_id = ?
            AND overall_calibration_score IS NOT NULL
            AND overall_calibration_score > 0
            ORDER BY created_at DESC LIMIT 10
        """, (ai_id, project_id))
        recent_scores = [r[0] for r in cursor.fetchall()]
        if len(recent_scores) < 3:
            return None
        mid = len(recent_scores) // 2
        recent_half = sum(recent_scores[:mid]) / mid
        older_half = sum(recent_scores[mid:]) / (len(recent_scores) - mid)
        if recent_half < older_half * 0.85:
            return "improving"
        elif recent_half > older_half * 1.15:
            return "widening"
        return "stable"
    except Exception:
        return None


def _preflight_collect_behavioral_feedback(db, session_id, ai_id, project_id):
    """Pull discipline observations from last POSTFLIGHT.

    Vectors are beliefs about epistemic state -- deterministic services inform
    work discipline, not vector adjustments. The feedback drives work decisions
    (more noetic? another transaction? better artifact discipline?) not scores.

    Returns feedback dict or None.
    """
    import os

    calibration_feedback_enabled = os.environ.get(
        'EMPIRICA_CALIBRATION_FEEDBACK', 'true'
    ).lower() == 'true'

    previous_transaction_feedback = None
    try:
        if not (calibration_feedback_enabled and ai_id and ai_id != 'unknown'):
            return None

        cursor = db.conn.cursor()

        # 1. Extract retrospective from last POSTFLIGHT
        feedback, pf_meta = _feedback_extract_retrospective(cursor, session_id)
        if feedback is not None:
            previous_transaction_feedback = feedback

            # 2. Collect suggestions
            suggestions = _feedback_collect_suggestions(cursor, session_id, project_id, pf_meta)
            if suggestions:
                previous_transaction_feedback["suggestions"] = suggestions

        # 3. Calibration trend
        trend = _feedback_compute_calibration_trend(cursor, ai_id, project_id)
        if trend:
            if previous_transaction_feedback is None:
                previous_transaction_feedback = {}
            previous_transaction_feedback["calibration_trend"] = trend

        if previous_transaction_feedback:
            previous_transaction_feedback["note"] = (
                "Behavioral feedback from last transaction. Address through work "
                "discipline (more noetic work, better artifact logging, commit cadence) "
                "— not by adjusting vector values."
            )
            logger.debug(
                f"Previous transaction feedback: gaps={previous_transaction_feedback.get('artifact_gaps', [])}, "
                f"trend={previous_transaction_feedback.get('calibration_trend', 'N/A')}"
            )
    except Exception as e:
        logger.debug(f"Previous transaction feedback lookup failed (non-fatal): {e}")

    return previous_transaction_feedback


def _preflight_get_last_session_ts(db, project_id, session_id):
    """Get the last session timestamp for adaptive pattern retrieval depth."""
    try:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT MAX(updated_at) FROM sessions
            WHERE project_id = ? AND session_id != ?
        """, (project_id, session_id))
        row = cursor.fetchone()
        if row and row[0]:
            from datetime import datetime
            return datetime.fromisoformat(row[0].replace('Z', '+00:00')).timestamp()
    except Exception:
        pass
    return None


def _preflight_persist_pattern_count(patterns, resolved_project_path):
    """Persist pattern count in the transaction file for context evidence. Non-fatal."""
    if not (patterns and resolved_project_path):
        return
    try:
        import json as _pjson
        from pathlib import Path
        pattern_count = sum(
            len(v) for k, v in patterns.items()
            if isinstance(v, list) and k != 'time_gap'
        )
        suffix = R.instance_suffix()
        tx_file = Path(resolved_project_path) / '.empirica' / f'active_transaction{suffix}.json'
        if tx_file.exists():
            with open(tx_file) as f:
                tx_d = _pjson.load(f)
            tx_d['preflight_pattern_count'] = pattern_count
            with open(tx_file, 'w') as f:
                _pjson.dump(tx_d, f, indent=2)
    except Exception:
        pass


def _preflight_retrieve_patterns(db, session_id, project_id, task_context, reasoning, vectors, resolved_project_path):
    """Load relevant patterns based on task_context or reasoning.

    Arms the AI with lessons, dead_ends, and findings BEFORE starting work.
    Returns patterns dict or None.
    """
    search_context = task_context or reasoning
    if not (search_context and project_id):
        return None

    try:
        from empirica.core.qdrant.pattern_retrieval import retrieve_task_patterns

        last_session_ts = _preflight_get_last_session_ts(db, project_id, session_id)

        patterns = retrieve_task_patterns(
            project_id, search_context,
            last_session_timestamp=last_session_ts,
            include_eidetic=True, include_episodic=True,
            include_related_docs=True, include_goals=True,
            include_assumptions=True, include_decisions=True,
            vectors=vectors,
        )
        if patterns and any(v for k, v in patterns.items() if k != 'time_gap'):
            time_gap = patterns.get('time_gap', {})
            gap_note = time_gap.get('note', '') if time_gap else ''
            logger.debug(f"Retrieved patterns ({gap_note}): {len(patterns.get('lessons', []))} lessons, "
                       f"{len(patterns.get('dead_ends', []))} dead_ends, "
                       f"{len(patterns.get('relevant_findings', []))} findings, "
                       f"{len(patterns.get('eidetic_facts', []))} eidetic, "
                       f"{len(patterns.get('episodic_narratives', []))} episodic, "
                       f"{len(patterns.get('related_docs', []))} docs, "
                       f"{len(patterns.get('related_goals', []))} goals, "
                       f"{len(patterns.get('unverified_assumptions', []))} assumptions, "
                       f"{len(patterns.get('prior_decisions', []))} decisions")

        _preflight_persist_pattern_count(patterns, resolved_project_path)
        return patterns
    except Exception as e:
        logger.debug(f"Pattern retrieval failed (optional): {e}")
        return None


# Investigation-heavy work_types where noetic_batch is most useful.
# Action-pure types (release, comms) and short-form types (data) skip the hint.
_NOETIC_BATCH_WORK_TYPES = frozenset({
    "code", "research", "debug", "audit", "docs", "infra", "config", "design",
})


def _build_noetic_guidance(work_type: str | None) -> dict | None:
    """Surface the noetic-batch schema when the work_type benefits from it.

    Returns a dict with tool name, CLI form, schema, and hint — or None
    when the work_type isn't investigation-prone (release, comms).
    """
    if not work_type or work_type not in _NOETIC_BATCH_WORK_TYPES:
        return None
    return {
        "tool": "mcp__empirica__noetic_batch",
        "cli": "empirica noetic-batch -",
        "schema": {
            "intent": "<one-line investigation goal>",
            "reads": [{"path": "<file>", "lines": "<optional 'N-M'>"}],
            "greps": [{
                "pattern": "<regex>",
                "glob": "<optional path glob>",
                "context": "<optional 0-5>",
                "max_matches": "<optional ≤500>",
            }],
            "globs": ["<pattern>", {"pattern": "<...>", "root": "<optional dir>"}],
            "investigate": [{"query": "<...>", "scope": "session|project|global", "limit": "<optional ≤20>"}],
        },
        "hint": (
            "Use ONLY when batching ≥3 investigation operations together — the "
            "value is one merged result for your conversation, fewer round-trips. "
            "Individual Read/Grep/Glob are noetic anywhere (any phase) — use them "
            "freely. noetic-batch is NOT a Sentinel bypass; calling it once for a "
            "single read is misuse."
        ),
        "skip_if": (
            "Fewer than 3 investigation operations. Use Read/Grep/Glob/investigate "
            "directly — they're already noetic and don't need batching."
        ),
    }


def _build_voice_guidance(work_type: str | None, voice: str | None) -> dict | None:
    """Surface the voice profile when work_type=comms or --voice was set.

    Loads the profile via the same resolver as `empirica voice apply` —
    project-local .empirica/voice/ overrides ~/.empirica/voice/. Output
    block mirrors voice_commands.handle_voice_apply's payload shape so
    the AI can treat it as if it had run `voice apply` directly.

    Resolution policy:
      • voice='<name>' explicit  → load that profile (any work_type)
      • work_type='comms' alone  → no auto-load (no opinionated default)
      • neither                  → return None

    Choosing not to auto-pick a profile when work_type=comms is set
    without --voice keeps the surface explicit and avoids the wrong-voice
    bug at scale (multi-user sessions, project-shared voice profiles).
    The voice_guidance block in that case nudges the AI toward
    `empirica voice list` so the right profile gets named.
    """
    if not voice:
        # work_type=comms without explicit voice → nudge, don't auto-pick
        if work_type == "comms":
            return {
                "hint": (
                    "work_type=comms — consider naming a voice profile via "
                    "the 'voice' field in PREFLIGHT (or --voice flag). Run "
                    "'empirica voice list' to see available profiles."
                ),
                "profile": None,
            }
        return None

    # Resolve and load the profile
    try:
        import yaml

        from empirica.cli.command_handlers.voice_commands import _resolve_profile_path
        path = _resolve_profile_path(voice)
        if path is None:
            return {
                "hint": f"voice profile {voice!r} not found (no .yaml in project or global voice dirs).",
                "profile": None,
                "error": "profile_not_found",
            }
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        return {
            "hint": f"voice profile load failed: {type(e).__name__}: {e}",
            "profile": None,
            "error": "load_failed",
        }

    # Default register: if work_type=comms, prefer email register; otherwise natural
    natural = data.get("natural_register", "unspecified")
    register_key = "email" if work_type == "comms" else None
    platform_conf = (data.get("platforms") or {}).get(register_key or "") or {}
    effective_register = platform_conf.get("register") or natural

    return {
        "profile": data.get("name", voice),
        "profile_path": str(path),
        "register_effective": effective_register,
        "depth": platform_conf.get("depth", "medium"),
        "framing": platform_conf.get("framing", "unspecified"),
        "tendencies_foreground": data.get("tendencies") or [],
        "anti_patterns_suppress": data.get("anti_patterns") or [],
        "natural_register_fallback": natural,
        "hint": (
            "Apply these tendencies and avoid the anti-patterns when drafting "
            "in this register. The guidance is descriptive of the source "
            "voice, not aspirational — match what the person actually does."
        ),
    }


def _preflight_build_result(session_id, transaction_id, calibration_adjustments,
                            calibration_report, previous_transaction_feedback,
                            sentinel_decision, patterns, unclosed_transaction_warning,
                            work_type=None, voice=None):
    """Assemble the final PREFLIGHT result dict."""
    result: dict = {
        "ok": True,
        "session_id": session_id,
        "transaction_id": transaction_id,
        "learning_trajectory": {
            "typical_deltas": calibration_adjustments if calibration_adjustments else None,
            "total_observations": calibration_report.get('total_evidence', 0) if calibration_report else 0,
            "summary": _remap_trajectory_summary(
                calibration_report.get('calibration_summary')
            ) if calibration_report else None,
            "note": "INFORMATIONAL: How your vectors typically change (PREFLIGHT->POSTFLIGHT deltas). NOT accuracy corrections."
        } if calibration_adjustments or calibration_report else None,
        "previous_transaction_feedback": previous_transaction_feedback,
        "sentinel": sentinel_decision.value if sentinel_decision else None,
        "patterns": patterns if patterns and any(patterns.values()) else None,
        "unclosed_transaction_warning": unclosed_transaction_warning
    }
    noetic_guidance = _build_noetic_guidance(work_type)
    if noetic_guidance is not None:
        result["noetic_guidance"] = noetic_guidance
    voice_guidance = _build_voice_guidance(work_type, voice)
    if voice_guidance is not None:
        result["voice_guidance"] = voice_guidance
    return result


def handle_preflight_submit_command(args):
    """Handle preflight-submit command - AI-first with config file support"""
    try:
        import time
        import uuid

        # Stage 1: Parse input, validate, resolve session
        parsed = _preflight_parse_and_validate(args)
        session_id = parsed["session_id"]
        vectors = parsed["vectors"]
        reasoning = parsed["reasoning"]
        task_context = parsed["task_context"]
        output_format = parsed["output_format"]

        # Stage 2: Check for unclosed transaction — warn but don't block
        unclosed_transaction_warning = _preflight_check_unclosed_transaction()

        # Stage 3: Create checkpoint and transaction
        try:
            transaction_id = str(uuid.uuid4())

            # Stage 3a: Write checkpoint to 3-layer storage
            checkpoint_id = _preflight_create_checkpoint(
                session_id, vectors, reasoning, transaction_id
            )

            # Stage 3b: Persist transaction file
            resolved_project_path = None
            try:
                resolved_project_path = _preflight_write_transaction_file(
                    session_id, transaction_id, parsed
                )
            except Exception as e:
                logger.debug(f"Active transaction file write failed (non-fatal): {e}")

            # Stage 4: Sentinel hook
            sentinel_decision = _invoke_sentinel_hook("PREFLIGHT", session_id, {
                "vectors": vectors,
                "reasoning": reasoning,
                "checkpoint_id": checkpoint_id
            })

            # Stage 5: Create DB transaction record
            db = _get_db_for_session(session_id)
            cascade_id = str(uuid.uuid4())
            now = time.time()

            db.conn.execute("""
                INSERT INTO cascades
                (cascade_id, session_id, task, started_at)
                VALUES (?, ?, ?, ?)
            """, (cascade_id, session_id, "PREFLIGHT assessment", now))

            db.conn.commit()

            # Stage 6: Publish bus event
            _preflight_publish_bus_event(
                session_id, transaction_id, vectors, task_context,
                parsed["work_type"], parsed["work_context"]
            )

            # Stage 7: Load calibration and project metadata
            cal = _preflight_load_calibration(db, session_id)

            # Stage 8: Collect behavioral feedback from last transaction
            previous_transaction_feedback = _preflight_collect_behavioral_feedback(
                db, session_id, cal["ai_id"], cal["project_id"]
            )

            # Stage 9: Retrieve patterns for task context
            patterns = _preflight_retrieve_patterns(
                db, session_id, cal["project_id"], task_context,
                reasoning, vectors, resolved_project_path
            )

            db.close()

            # Stage 10: Build result
            result = _preflight_build_result(
                session_id, transaction_id,
                cal["calibration_adjustments"], cal["calibration_report"],
                previous_transaction_feedback, sentinel_decision,
                patterns, unclosed_transaction_warning,
                work_type=parsed.get("work_type"),
                voice=parsed.get("voice"),
            )

            # NOTE: Statusline cache was removed (2026-02-06). Statusline reads directly from DB.
        except Exception as e:
            logger.error(f"Failed to save preflight assessment: {e}")
            result = {
                "ok": False,
                "session_id": session_id,
                "message": f"Failed to save PREFLIGHT assessment: {e!s}",
                "vectors_submitted": 0,
                "persisted": False,
                "error": str(e)
            }

        # Format output (AI-first = JSON by default)
        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            # Human-readable output (legacy)
            if result['ok']:
                print("✅ PREFLIGHT assessment submitted successfully")
                print(f"   Session: {session_id[:8]}...")
                print(f"   Vectors: {len(vectors)} submitted")
                print("   Storage: Database + Git Notes")
                if reasoning:
                    print(f"   Reasoning: {reasoning[:80]}...")
            else:
                print(f"❌ {result.get('message', 'Failed to submit PREFLIGHT assessment')}")

        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Preflight submit", getattr(args, 'verbose', False))


def _check_patterns_for_warnings(project_id, config_data, checkpoints, current_vectors, suggestions):
    """Check current approach against known failure patterns. Returns warnings or None."""
    if not project_id:
        return None
    try:
        from empirica.core.qdrant.pattern_retrieval import check_against_patterns
        approach = None
        if config_data:
            approach = config_data.get('approach') or config_data.get('reasoning')
        if not approach and checkpoints:
            approach = checkpoints[0].get('metadata', {}).get('reasoning')
        warnings = check_against_patterns(project_id, approach or "", current_vectors)
        if warnings and warnings.get('has_warnings'):
            for de in warnings.get('dead_end_matches', []):
                suggestions.append(f"⚠️ Similar to dead end: {de.get('approach', '')[:50]}... (why: {de.get('why_failed', '')[:50]})")
            if warnings.get('mistake_risk'):
                suggestions.append(f"⚠️ {warnings['mistake_risk']}")
        return warnings
    except Exception:
        return None


def _compute_check_decision(confidence: float, drift: float, unknowns_count: int) -> tuple:
    """Compute CHECK gate decision from confidence, drift, and unknowns.

    Returns (decision, strength, reasoning, suggestions).
    """
    if confidence >= 0.70:
        if drift > 0.3 or unknowns_count > 5:
            return ("proceed", "moderate",
                    f"Readiness sufficient, but {unknowns_count} unknowns and drift ({drift:.2f}) suggest caution",
                    ["Readiness met - you may proceed",
                     f"Be aware: {unknowns_count} unknowns remain and drift is {drift:.2f}"])
        return ("proceed", "strong",
                f"Readiness strong, low drift ({drift:.2f}), {unknowns_count} unknowns",
                ["Evidence supports proceeding to action phase"])
    if unknowns_count > 5 or drift > 0.3:
        return ("investigate", "strong",
                f"Readiness insufficient with {unknowns_count} unknowns and drift ({drift:.2f}) - investigation required",
                ["More investigation needed before proceeding",
                 f"Address {unknowns_count} unknowns to increase readiness"])
    return ("investigate", "moderate",
            f"Readiness insufficient, but only {unknowns_count} unknowns and drift ({drift:.2f}) - investigate to validate",
            ["Investigate further or recalibrate your assessment",
             "Evidence doesn't fully explain low readiness"])


def _check_cmd_parse_inputs(args):
    """Parse CHECK command inputs from config/stdin/CLI flags.

    Returns dict with session_id, cycle, round_num, verbose, explicit_confidence,
    config_data, output_format.
    """
    config_data, output_format = _parse_workflow_input(args, "CHECK")

    session_id = getattr(args, 'session_id', None) or (config_data.get('session_id') if config_data else None)
    cycle = getattr(args, 'cycle', None) or (config_data.get('cycle') if config_data else None)
    round_num = getattr(args, 'round', None) or (config_data.get('round') if config_data else None)
    verbose = getattr(args, 'verbose', False) or (config_data.get('verbose', False) if config_data else False)
    explicit_confidence = config_data.get('confidence') if config_data else None

    return {
        "session_id": session_id, "cycle": cycle, "round_num": round_num,
        "verbose": verbose, "explicit_confidence": explicit_confidence,
        "config_data": config_data, "output_format": output_format,
    }


def _check_cmd_compute_drift(baseline_vectors, checkpoints):
    """Calculate drift from baseline using latest checkpoint.

    Returns (current_vectors, drift, deltas).
    """
    if not checkpoints:
        current_vectors = baseline_vectors
        drift = 0.0
        deltas = {k: 0.0 for k in baseline_vectors if isinstance(baseline_vectors.get(k), (int, float))}
        return current_vectors, drift, deltas

    current_vectors = checkpoints[0].get('vectors', {})
    deltas = {}
    drift_sum = 0.0
    drift_count = 0

    for key in ['know', 'uncertainty', 'engagement', 'impact', 'completion']:
        if key in baseline_vectors and key in current_vectors:
            delta = current_vectors[key] - baseline_vectors[key]
            deltas[key] = delta
            drift_sum += abs(delta)
            drift_count += 1

    drift = drift_sum / drift_count if drift_count > 0 else 0.0
    return current_vectors, drift, deltas


def _check_cmd_load_evidence(db, session_id):
    """Load findings and unknowns from database.

    Returns (findings, unknowns, project_id).
    """
    try:
        session_data = db.get_session(session_id)
        project_id = session_data.get('project_id') if session_data else None

        if project_id:
            findings_list = db.breadcrumbs.get_project_findings(project_id)
            unknowns_list = db.breadcrumbs.get_project_unknowns(project_id, resolved=False)
            findings = [{"finding": f.get('finding', ''), "impact": f.get('impact')} for f in findings_list]
            unknowns = [u.get('unknown', '') for u in unknowns_list]
        else:
            findings, unknowns = [], []
    except Exception as e:
        logger.warning(f"Could not load findings/unknowns: {e}")
        findings, unknowns, project_id = [], [], None

    return findings, unknowns, project_id


def handle_check_command(args):
    """
    Handle CHECK command - Evidence-based mid-session grounding

    Auto-loads PREFLIGHT baseline, current checkpoint, and accumulated
    findings/unknowns. Returns evidence-based decision, drift analysis,
    and reasoning.
    """
    try:
        import sys
        import time

        from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

        inputs = _check_cmd_parse_inputs(args)
        session_id = inputs["session_id"]

        if not session_id:
            print(json.dumps({"ok": False, "error": "session_id is required"}))
            sys.exit(1)

        db = _get_db_for_session(session_id)
        git_logger = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

        # 1. Load PREFLIGHT baseline
        preflight = db.get_preflight_vectors(session_id)
        if not preflight:
            print(json.dumps({
                "ok": False, "error": "No PREFLIGHT found for session",
                "hint": "Run PREFLIGHT first to establish baseline"
            }))
            sys.exit(1)

        baseline_vectors = preflight.get('vectors', preflight) if isinstance(preflight, dict) else preflight

        # 2. Compute drift
        checkpoints = git_logger.list_checkpoints(limit=1)
        current_vectors, drift, deltas = _check_cmd_compute_drift(baseline_vectors, checkpoints)

        # 3. Load evidence
        findings, unknowns, project_id = _check_cmd_load_evidence(db, session_id)
        findings_count = len(findings)
        unknowns_count = len(unknowns)
        uncertainty = current_vectors.get('uncertainty', 0.5)

        confidence = inputs["explicit_confidence"] if inputs["explicit_confidence"] is not None else (1.0 - uncertainty)

        # 4. Gate logic
        decision, strength, reasoning, suggestions = _compute_check_decision(confidence, drift, unknowns_count)
        drift_level = "high" if drift > 0.3 else ("medium" if drift > 0.1 else "low")

        _check_patterns_for_warnings(project_id, inputs["config_data"], checkpoints, current_vectors, suggestions)

        # 5. Read transaction_id and create checkpoint
        check_transaction_id = None
        try:
            check_transaction_id = R.transaction_id()
            if check_transaction_id is None:
                logger.warning("R.transaction_id() returned None for CHECK checkpoint")
        except Exception as e:
            logger.warning(f"Failed to read active transaction: {e}")

        checkpoint_id = git_logger.add_checkpoint(
            phase="CHECK", round_num=inputs["cycle"] or 1, vectors=current_vectors,
            metadata={
                "decision": decision, "suggestion_strength": strength, "drift": drift,
                "findings_count": findings_count, "unknowns_count": unknowns_count,
                "reasoning": reasoning, "transaction_id": check_transaction_id
            }
        )

        # 6. Build result
        confidence_value = inputs["explicit_confidence"] if inputs["explicit_confidence"] is not None else (1.0 - uncertainty)
        result = {
            "ok": True, "session_id": session_id, "checkpoint_id": checkpoint_id,
            "decision": decision, "suggestion_strength": strength, "confidence": confidence_value,
            "drift_analysis": {
                "overall_drift": drift, "drift_level": drift_level,
                "baseline": baseline_vectors, "current": current_vectors, "deltas": deltas
            },
            "evidence": {"findings_count": findings_count, "unknowns_count": unknowns_count},
            "investigation_progress": {
                "cycle": inputs["cycle"], "round": inputs["round_num"],
                "total_checkpoints": len(git_logger.list_checkpoints(limit=100))
            },
            "recommendation": {
                "type": "suggestive", "message": reasoning, "suggestions": suggestions,
                "note": "This is an evidence-based suggestion. Override if task context warrants it."
            },
            "pattern_warnings": None,
            "timestamp": time.time()
        }

        if inputs["verbose"]:
            result["evidence"]["findings"] = findings
            result["evidence"]["unknowns"] = unknowns

        if inputs["output_format"] == 'json':
            print(json.dumps(result, indent=2))
        else:
            print("\n🔍 CHECK - Mid-Session Grounding")
            print("=" * 70)
            print(f"Session: {session_id}")
            print(f"Decision: {decision.upper()} ({strength} suggestion)")
            print(f"\n📊 Drift Analysis:\n   Overall drift: {drift:.2%} ({drift_level})")
            print(f"   Know: {deltas.get('know', 0):+.2f}\n   Uncertainty: {deltas.get('uncertainty', 0):+.2f}")
            print(f"   Completion: {deltas.get('completion', 0):+.2f}")
            print(f"\n📚 Evidence:\n   Findings: {findings_count}\n   Unknowns: {unknowns_count}")
            print(f"\n💡 Recommendation:\n   {reasoning}")
            for suggestion in suggestions:
                print(f"   • {suggestion}")

    except Exception as e:
        handle_cli_error(e, "CHECK", getattr(args, 'verbose', False))


# ---------------------------------------------------------------------------
# CHECK-SUBMIT stage helpers (extracted from handle_check_submit_command)
# Each function represents a sequential stage; the main handler orchestrates.
# ---------------------------------------------------------------------------


def _check_parse_inputs(args):
    """Parse and resolve CHECK inputs from config/stdin/CLI flags.

    Returns a dict with keys: session_id, vectors, decision, reasoning, cycle,
    output_format.
    """
    config_data, output_format = _parse_workflow_input(args, "CHECK")

    if config_data:
        session_id = config_data.get('session_id') or getattr(args, 'session_id', None)
        vectors = config_data.get('vectors')
        decision = config_data.get('decision')
        reasoning = config_data.get('reasoning', '')
        config_data.get('approach', reasoning)
    else:
        session_id = args.session_id
        vectors = parse_json_safely(args.vectors) if isinstance(args.vectors, str) else args.vectors
        decision = args.decision
        reasoning = args.reasoning
        getattr(args, 'approach', reasoning)
        output_format = getattr(args, 'output', 'human')
    cycle = getattr(args, 'cycle', 1)

    # Auto-resolve session_id
    if not session_id:
        try:
            session_id = R.session_id()
        except Exception:
            pass

    session_id = _resolve_and_validate_session(session_id, "CHECK")

    return {
        "session_id": session_id,
        "vectors": vectors,
        "decision": decision,
        "reasoning": reasoning,
        "cycle": cycle,
        "output_format": output_format,
    }


def _check_bootstrap_gate(session_id, vectors):
    """Ensure project context is loaded before CHECK.

    Returns (bootstrap_status, bootstrap_result).
    bootstrap_result is None when no re-bootstrap was needed.
    """
    import sys as _sys

    bootstrap_status = _check_bootstrap_status(session_id)
    bootstrap_result = None

    # Parse vectors early to check for reground triggers
    _vectors_for_check = vectors
    if isinstance(_vectors_for_check, str):
        _vectors_for_check = parse_json_safely(_vectors_for_check)
    if isinstance(_vectors_for_check, dict) and 'vectors' in _vectors_for_check:
        _vectors_for_check = _vectors_for_check['vectors']

    context_val = _vectors_for_check.get('context', 0.7) if isinstance(_vectors_for_check, dict) else 0.7
    uncertainty_val = _vectors_for_check.get('uncertainty', 0.3) if isinstance(_vectors_for_check, dict) else 0.3

    needs_reground = False
    reground_reason = None
    if not bootstrap_status.get('has_bootstrap'):
        needs_reground = True
        reground_reason = "initial bootstrap"
    elif context_val < 0.5:
        needs_reground = True
        reground_reason = f"low context ({context_val:.2f} < 0.50)"
    elif uncertainty_val > 0.6:
        needs_reground = True
        reground_reason = f"high uncertainty ({uncertainty_val:.2f} > 0.60)"

    if needs_reground:
        print(f"\U0001f504 Auto-running project-bootstrap ({reground_reason})...", file=_sys.stderr)
        bootstrap_result = _auto_bootstrap(session_id)

        if bootstrap_result.get('ok'):
            print(f"\u2705 Bootstrap complete: project_id={bootstrap_result.get('project_id')}", file=_sys.stderr)
        else:
            print(f"\u26a0\ufe0f  Bootstrap failed: {bootstrap_result.get('error', 'unknown')}", file=_sys.stderr)
            print("   CHECK will proceed but vectors may be hollow.", file=_sys.stderr)

    return bootstrap_status, bootstrap_result


def _check_get_round_and_history(session_id, args):
    """Get the next CHECK round number and previous CHECK vectors.

    Returns (round_num, previous_check_vectors).
    """
    previous_check_vectors = []
    try:
        db = _get_db_for_session(session_id)
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM reflexes
            WHERE session_id = ? AND phase = 'CHECK'
        """, (session_id,))
        check_count = cursor.fetchone()[0]
        round_num = check_count + 1

        if check_count > 0:
            cursor.execute("""
                SELECT engagement, know, do, context, clarity, coherence,
                       signal, density, state, change, completion, impact, uncertainty
                FROM reflexes
                WHERE session_id = ? AND phase = 'CHECK'
                ORDER BY timestamp DESC
                LIMIT 3
            """, (session_id,))
            rows = cursor.fetchall()
            vector_names = ['engagement', 'know', 'do', 'context', 'clarity', 'coherence',
                           'signal', 'density', 'state', 'change', 'completion', 'impact', 'uncertainty']
            for row in rows:
                prev_vectors = {}
                for i, name in enumerate(vector_names):
                    if row[i] is not None:
                        prev_vectors[name] = row[i]
                if prev_vectors:
                    previous_check_vectors.append(prev_vectors)
        db.close()
    except Exception:
        round_num = getattr(args, 'round', 1)

    return round_num, previous_check_vectors


def _check_normalize_vectors(vectors):
    """Normalize vectors into a flat dict of 13 canonical keys.

    Accepts flat dict, structured dict with foundation/comprehension/execution
    groups, wrapped {vectors: {...}}, or JSON string.

    Returns the normalized flat dict.
    Raises ValueError if vectors is not a dict after normalization.
    """
    if isinstance(vectors, str):
        vectors = parse_json_safely(vectors)

    if isinstance(vectors, dict) and 'vectors' in vectors and isinstance(vectors.get('vectors'), dict):
        vectors = vectors['vectors']

    if isinstance(vectors, dict) and any(k in vectors for k in ('foundation', 'comprehension', 'execution')):
        flat = {}
        for k in ('engagement', 'uncertainty'):
            if k in vectors:
                flat[k] = vectors[k]
        flat.update(vectors.get('foundation') or {})
        flat.update(vectors.get('comprehension') or {})
        flat.update(vectors.get('execution') or {})
        vectors = flat

    if not isinstance(vectors, dict):
        raise ValueError("Vectors must be a dictionary")

    return vectors


def _check_load_dynamic_thresholds(session_id):
    """Compute dynamic readiness thresholds from Brier score calibration.

    Returns (ready_know_threshold, ready_uncertainty_threshold, dynamic_thresholds_info).
    dynamic_thresholds_info is None when only static defaults are used.
    """
    ready_know_threshold = 0.70
    ready_uncertainty_threshold = 0.35
    dynamic_thresholds_info = None
    profile_base_thresholds = None

    # Profile-aware baselines
    try:
        cascade_profile = None
        tx_id = R.transaction_id()
        if tx_id:
            tx_data = R.transaction_read()
            if tx_data:
                cascade_profile = tx_data.get('cascade_profile')
        if cascade_profile and cascade_profile != 'default':
            from empirica.config.threshold_loader import ThresholdLoader
            loader = ThresholdLoader.get_instance()
            if loader.load_profile(cascade_profile):
                profile_base_thresholds = {
                    'ready_know_threshold': loader.get('cascade.ready_know_threshold', 0.70),
                    'ready_uncertainty_threshold': loader.get('cascade.ready_uncertainty_threshold', 0.35),
                }
                logger.info(f"CHECK using cascade profile '{cascade_profile}' baselines: {profile_base_thresholds}")
    except Exception:
        pass

    # Dynamic thresholds from calibration history
    try:
        from empirica.core.post_test.dynamic_thresholds import compute_dynamic_thresholds
        dt_db = _get_db_for_session(session_id)
        dt_result = compute_dynamic_thresholds(
            ai_id="claude-code", db=dt_db,
            base_thresholds=profile_base_thresholds,
        )
        dt_db.close()

        if dt_result.get("source") == "dynamic":
            noetic = dt_result.get("noetic", {})
            if noetic.get("brier_score") is not None:
                ready_know_threshold = noetic["ready_know_threshold"]
                ready_uncertainty_threshold = noetic["ready_uncertainty_threshold"]
                dynamic_thresholds_info = {
                    "source": "dynamic",
                    "know_threshold": ready_know_threshold,
                    "uncertainty_threshold": ready_uncertainty_threshold,
                    "brier_score": noetic["brier_score"],
                    "brier_reliability": noetic["brier_reliability"],
                    "brier_resolution": noetic["brier_resolution"],
                    "threshold_inflation": noetic["threshold_inflation"],
                    "transactions_analyzed": noetic["transactions_analyzed"],
                }
                logger.info(
                    f"Dynamic thresholds: know>={ready_know_threshold:.3f}, "
                    f"uncertainty<={ready_uncertainty_threshold:.3f} "
                    f"(brier={noetic['brier_score']:.3f}, "
                    f"reliability={noetic['brier_reliability']:.3f}, "
                    f"inflation={noetic['threshold_inflation']:.3f}, "
                    f"n={noetic['transactions_analyzed']})"
                )
    except Exception as e:
        logger.debug(f"Dynamic thresholds unavailable (using static): {e}")

    return ready_know_threshold, ready_uncertainty_threshold, dynamic_thresholds_info


def _check_detect_diminishing_returns(previous_check_vectors, know, uncertainty):
    """Analyze whether investigation is still improving across rounds.

    Returns a diminishing_returns dict with detection results.
    """
    diminishing_returns: dict[str, Any] = {
        "detected": False,
        "rounds_analyzed": 0,
        "know_deltas": [],
        "uncertainty_deltas": [],
        "reason": None,
        "recommend_proceed": False
    }

    if len(previous_check_vectors) < 2:
        return diminishing_returns

    # Compute deltas between consecutive rounds (newest first)
    for i in range(len(previous_check_vectors)):
        if i == 0:
            prev_know = previous_check_vectors[i].get('know', 0.5)
            prev_uncertainty = previous_check_vectors[i].get('uncertainty', 0.5)
            delta_know = know - prev_know
            delta_uncertainty = uncertainty - prev_uncertainty
            diminishing_returns["know_deltas"].append(delta_know)
            diminishing_returns["uncertainty_deltas"].append(delta_uncertainty)
        elif i < len(previous_check_vectors):
            curr = previous_check_vectors[i - 1]
            prev = previous_check_vectors[i]
            delta_know = curr.get('know', 0.5) - prev.get('know', 0.5)
            delta_uncertainty = curr.get('uncertainty', 0.5) - prev.get('uncertainty', 0.5)
            diminishing_returns["know_deltas"].append(delta_know)
            diminishing_returns["uncertainty_deltas"].append(delta_uncertainty)

    diminishing_returns["rounds_analyzed"] = len(previous_check_vectors) + 1

    if len(diminishing_returns["know_deltas"]) >= 2:
        recent_know_deltas = diminishing_returns["know_deltas"][:2]
        recent_uncertainty_deltas = diminishing_returns["uncertainty_deltas"][:2]

        DELTA_THRESHOLD = 0.05

        know_stagnant = all(abs(d) < DELTA_THRESHOLD for d in recent_know_deltas)
        uncertainty_stagnant = all(d >= -DELTA_THRESHOLD for d in recent_uncertainty_deltas)

        if know_stagnant and uncertainty_stagnant:
            diminishing_returns["detected"] = True
            diminishing_returns["reason"] = f"know stagnant ({recent_know_deltas}), uncertainty not decreasing ({recent_uncertainty_deltas})"

            # Per the meta-uncertainty design (2026-04-07): the gate is
            # uncertainty-only — uncertainty IS the meta confidence summary.
            if uncertainty <= 0.45:
                diminishing_returns["recommend_proceed"] = True
                diminishing_returns["reason"] += " - uncertainty acceptable, investigation plateaued"
            else:
                diminishing_returns["reason"] += " - uncertainty too high for proceed override"

    return diminishing_returns


def _check_gate_decision(vectors, ready_uncertainty_threshold, diminishing_returns,
                         round_num, decision):
    """Compute the CHECK gate decision and apply autopilot enforcement.

    Gate semantic (2026-04-07): The CHECK gate uses META UNCERTAINTY ONLY.
    Uncertainty is the unified confidence summary -- it subsumes the AI's
    epistemic state across all 12 other vectors.

    NOTE: Use RAW vectors, not bias-corrected. Biases are INFORMATIONAL.

    Returns (decision, computed_decision, autopilot_mode, decision_binding).
    """
    import os

    know = vectors.get('know', 0.5)
    uncertainty = vectors.get('uncertainty', 0.5)

    # Load grounded corrections (informational only)
    try:
        from empirica.core.bayesian_beliefs import load_grounded_corrections
        _corrections = load_grounded_corrections()
    except Exception:
        _corrections = {}
    know + _corrections.get('know', 0.0)
    uncertainty + _corrections.get('uncertainty', 0.0)

    # Compute gate decision
    computed_decision = None
    if uncertainty <= ready_uncertainty_threshold:
        computed_decision = "proceed"
    elif diminishing_returns["recommend_proceed"]:
        computed_decision = "proceed"
        logger.info(f"CHECK decision override: proceed due to diminishing returns ({diminishing_returns['reason']})")
    elif round_num >= 5 and uncertainty <= 0.40:
        computed_decision = "proceed"
        logger.info(f"CHECK decision override: proceed due to max investigate rounds (round={round_num}, uncertainty={uncertainty:.2f})")
    else:
        computed_decision = "investigate"

    # AUTOPILOT MODE
    autopilot_mode = os.getenv('EMPIRICA_AUTOPILOT_MODE', 'false').lower() in ('true', '1', 'yes')
    decision_binding = autopilot_mode

    if not decision or (autopilot_mode and decision != computed_decision):
        if autopilot_mode and decision and decision != computed_decision:
            logger.info(f"AUTOPILOT override: {decision} → {computed_decision} (autopilot enforcement)")
        decision = computed_decision
        logger.info(f"CHECK auto-computed decision: {decision} (uncertainty={uncertainty:.2f} vs threshold={ready_uncertainty_threshold:.2f}, gate uses META uncertainty only)")

    return decision, computed_decision, autopilot_mode, decision_binding


def _check_store_and_publish(session_id, round_num, vectors, decision, reasoning,
                             cycle):
    """Store CHECK checkpoint (3-layer) and publish bus event.

    Returns (checkpoint_id, check_transaction_id, confidence, gaps).
    """
    from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

    logger_instance = GitEnhancedReflexLogger(
        session_id=session_id,
        enable_git_notes=True
    )

    uncertainty = vectors.get('uncertainty', 0.5)
    confidence = 1.0 - uncertainty

    gaps = []
    for key, value in vectors.items():
        if isinstance(value, (int, float)) and value < 0.5:
            gaps.append(f"{key}: {value:.2f}")

    check_transaction_id = None
    try:
        check_transaction_id = R.transaction_id()
        if check_transaction_id is None:
            logger.warning("R.transaction_id() returned None — CHECK will be stored without transaction_id. "
                           "This may cause Sentinel to not find this CHECK. Check instance_projects/ state.")
    except Exception as e:
        logger.warning(f"Failed to read active transaction: {e}")

    checkpoint_id = logger_instance.add_checkpoint(
        phase="CHECK",
        round_num=round_num,
        vectors=vectors,
        metadata={
            "decision": decision,
            "reasoning": reasoning,
            "confidence": confidence,
            "gaps": gaps,
            "cycle": cycle,
            "round": round_num,
            "transaction_id": check_transaction_id
        }
    )

    # EPISTEMIC BUS: Publish CHECK_COMPLETE event
    try:
        from empirica.core.bus_persistence import wire_persistent_observers
        from empirica.core.epistemic_bus import (
            EpistemicEvent,
            EventTypes,
            get_global_bus,
        )
        wire_persistent_observers(session_id=session_id)
        bus = get_global_bus()
        bus.publish(EpistemicEvent(
            event_type=EventTypes.CHECK_COMPLETE,
            agent_id="claude-code",
            session_id=session_id,
            data={
                "transaction_id": check_transaction_id,
                "vectors": vectors,
                "decision": decision,
                "round": round_num,
                "confidence": confidence,
            },
        ))
    except Exception as e:
        logger.debug(f"Bus publish (CHECK) failed (non-fatal): {e}")

    return checkpoint_id, check_transaction_id, confidence, gaps


def _check_apply_sentinel(session_id, decision, decision_binding, vectors, reasoning,
                          confidence, gaps, cycle, round_num, checkpoint_id,
                          check_transaction_id):
    """Invoke Sentinel hook and apply override if warranted.

    Returns (decision, sentinel_decision, sentinel_override).
    """
    sentinel_override = False
    sentinel_decision = _invoke_sentinel_hook("CHECK", session_id, {
        "vectors": vectors,
        "decision": decision,
        "reasoning": reasoning,
        "confidence": confidence,
        "gaps": gaps,
        "cycle": cycle,
        "round": round_num,
        "checkpoint_id": checkpoint_id
    })

    if sentinel_decision and not decision_binding:
        sentinel_map = {
            SentinelDecision.PROCEED: "proceed",
            SentinelDecision.INVESTIGATE: "investigate",
            SentinelDecision.BRANCH: "investigate",
            SentinelDecision.HALT: "investigate",
            SentinelDecision.REVISE: "investigate",
        }
        if sentinel_decision in sentinel_map:
            new_decision = sentinel_map[sentinel_decision]
            if new_decision != decision:
                logger.info(f"Sentinel override: {decision} → {new_decision} (sentinel={sentinel_decision.value})")
                decision = new_decision
                sentinel_override = True

                # UPDATE DB: Sync the overridden decision to the stored reflex
                try:
                    db2 = _get_db_for_session(session_id)
                    db2.conn.execute("""
                        UPDATE reflexes SET reflex_data = json_set(reflex_data, '$.decision', ?)
                        WHERE id = (
                            SELECT id FROM reflexes
                            WHERE session_id = ? AND phase = 'CHECK'
                            AND transaction_id = ?
                            ORDER BY timestamp DESC LIMIT 1
                        )
                    """, (new_decision, session_id, check_transaction_id))
                    db2.conn.commit()
                    db2.close()
                    logger.info(f"DB synced: CHECK decision updated to '{new_decision}'")
                except Exception as e:
                    logger.warning(f"Failed to sync sentinel override to DB: {e}")
    elif sentinel_decision and decision_binding:
        logger.info(f"Autopilot binding active - Sentinel override blocked (sentinel wanted: {sentinel_decision.value})")

    return decision, sentinel_decision, sentinel_override


def _check_auto_checkpoint(session_id, vectors, decision, gaps, cycle, round_num):
    """Create git checkpoint if uncertainty > 0.5 (risky decision).

    Non-fatal — failures are logged and swallowed.
    """
    import json
    import subprocess

    uncertainty = vectors.get('uncertainty', 0.5)
    if uncertainty > 0.5:
        try:
            subprocess.run(
                [
                    "empirica", "checkpoint-create",
                    "--session-id", session_id,
                    "--phase", "CHECK",
                    "--round", str(round_num),
                    "--metadata", json.dumps({
                        "auto_checkpoint": True,
                        "reason": "risky_decision",
                        "uncertainty": uncertainty,
                        "decision": decision,
                        "gaps": gaps,
                        "cycle": cycle,
                        "round": round_num
                    })
                ],
                capture_output=True,
                timeout=10
            )
        except Exception as e:
            logger.warning(f"Auto-checkpoint after CHECK (uncertainty > 0.5) failed (non-fatal): {e}")


def _check_create_snapshot(session_id, vectors, decision, reasoning, round_num,
                           checkpoint_id):
    """Capture CHECK phase vectors as an epistemic snapshot for calibration.

    Returns snapshot_id or None on failure.
    """
    try:
        from empirica.data.epistemic_snapshot import ContextSummary
        from empirica.data.snapshot_provider import EpistemicSnapshotProvider

        uncertainty = vectors.get('uncertainty', 0.5)
        db = _get_db_for_session(session_id)
        snapshot_provider = EpistemicSnapshotProvider()

        check_confidence = 1.0 - uncertainty
        context_summary = ContextSummary(
            semantic={"phase": "CHECK", "decision": decision, "confidence": check_confidence},
            narrative=reasoning or f"CHECK round {round_num}: {decision}",
            evidence_refs=[checkpoint_id] if checkpoint_id else []
        )

        snapshot = snapshot_provider.create_snapshot_from_session(
            session_id=session_id,
            context_summary=context_summary,
            cascade_phase="CHECK",
            domain_vectors={"round": round_num, "decision": decision} if round_num else None
        )

        snapshot.vectors = vectors
        snapshot_provider.save_snapshot(snapshot)
        snapshot_id = snapshot.snapshot_id

        logger.debug(f"Created CHECK epistemic snapshot {snapshot_id} for session {session_id}")
        db.close()
        return snapshot_id
    except Exception as e:
        logger.debug(f"CHECK epistemic snapshot creation skipped: {e}")
        return None


def _check_run_blindspot_scan(result, decision, session_id, bootstrap_result,
                              bootstrap_status):
    """Run negative-space inference on knowledge topology.

    Modifies result in-place. Returns updated decision.
    """
    try:
        from empirica_prediction.blindspots.predictor import BlindspotPredictor  # pyright: ignore[reportMissingImports]
        project_id = (bootstrap_result or {}).get('project_id') or bootstrap_status.get('project_id')
        if project_id:
            bs_predictor = BlindspotPredictor(project_id=project_id)
            bs_report = bs_predictor.predict(
                session_id=session_id,
                max_predictions=5,
                min_confidence=0.5,
            )
            bs_predictor.close()

            if bs_report.predictions:
                result["blindspots"] = {
                    "count": len(bs_report.predictions),
                    "critical_count": bs_report.critical_count,
                    "high_count": bs_report.high_count,
                    "uncertainty_adjustment": bs_report.uncertainty_adjustment,
                    "missing_layers": bs_report.missing_layers,
                    "predictions": [
                        {
                            "severity": p.severity,
                            "description": p.description,
                            "suggested_action": p.suggested_action,
                            "confidence": p.confidence,
                        }
                        for p in bs_report.predictions[:5]
                    ],
                }

                if bs_report.critical_count > 0 and decision == "proceed":
                    result["blindspots"]["override"] = {
                        "original_decision": decision,
                        "new_decision": "investigate",
                        "reason": f"{bs_report.critical_count} critical blindspot(s) detected"
                    }
                    decision = "investigate"
                    result["decision"] = decision

                logger.info(f"Blindspot scan: {len(bs_report.predictions)} predictions, "
                            f"uncertainty_adj={bs_report.uncertainty_adjustment}")
    except ImportError:
        pass  # empirica-prediction not installed
    except Exception as e:
        logger.debug(f"Blindspot scan skipped: {e}")

    return decision


def _check_enrich_patterns(result, check_project_id, vectors, reasoning):
    """Enrich result with pattern retrieval from Qdrant. Modifies result in-place."""
    if not check_project_id:
        return
    try:
        from empirica.core.qdrant.pattern_retrieval import check_against_patterns
        check_patterns = check_against_patterns(
            check_project_id, reasoning or "", vectors=vectors,
            include_findings=True, include_eidetic=True,
            include_goals=True, include_assumptions=True,
        )
        if check_patterns and check_patterns.get("has_warnings"):
            result["patterns"] = check_patterns
    except Exception as e:
        logger.debug(f"CHECK pattern retrieval failed (optional): {e}")


def _check_enrich_codebase_model(result, check_project_id):
    """Enrich result with codebase model entity/constraint context. Modifies result in-place."""
    if not check_project_id:
        return
    try:
        from empirica.config.path_resolver import get_session_db_path
        from empirica.data.session_database import SessionDatabase
        codebase_db_path = get_session_db_path()
        if not codebase_db_path:
            return
        codebase_db = SessionDatabase(codebase_db_path)
        try:
            entity_count = codebase_db.codebase_model.count_entities(check_project_id, active_only=True)
            if entity_count > 0:
                constraints = codebase_db.codebase_model.get_constraints(project_id=check_project_id)
                result["codebase_context"] = {
                    "active_entities": entity_count,
                    "constraints": [
                        {"rule": c['rule_name'], "type": c['constraint_type'],
                         "violations": c['violation_count'], "description": c['description']}
                        for c in constraints[:5]
                    ] if constraints else [],
                }
        finally:
            codebase_db.close()
    except Exception as e:
        logger.debug(f"Codebase context injection skipped: {e}")


def _check_enrich_context(result, bootstrap_result, bootstrap_status, vectors,
                          reasoning):
    """Enrich result with pattern retrieval and codebase model context.

    Modifies result in-place.
    """
    check_project_id = (bootstrap_result or {}).get('project_id') or bootstrap_status.get('project_id')
    _check_enrich_patterns(result, check_project_id, vectors, reasoning)
    _check_enrich_codebase_model(result, check_project_id)


def _check_build_praxic_reminders(session_id, check_transaction_id):
    """Build proceed advisory reminders including calibration nudge.

    Returns reminders dict.
    """
    reminders = {
        "commit": "Commit before POSTFLIGHT — uncommitted edits are invisible to grounded calibration (change/state/do will ground near-zero).",
        "artifacts": "Log the full breadth: assumption-log (beliefs), decision-log (choices), deadend-log (failures), mistake-log (errors) — not just findings.",
        "sources": "When findings/decisions come from external material (docs, URLs, papers, conversations, attachments) — log the origin via source-add and link with sourced_from in log-artifacts. Especially important on Claude Desktop where artifacts often originate outside code that git already tracks.",
        "completion": "Rate completion for THIS TRANSACTION only, not the overall plan. If the transaction's objective is met, completion = 1.0 regardless of remaining transactions.",
    }

    try:
        current_tx = check_transaction_id
        if current_tx:
            retro = _build_retrospective(session_id, current_tx)
            counts = retro.get("artifact_counts", {})
            total_artifacts = sum(counts.values())

            if total_artifacts == 0:
                reminders["calibration_nudge"] = (
                    "\u26a0 Current transaction has 0 epistemic artifacts logged. "
                    "Your grounded calibration score depends on artifact breadth — "
                    "zero artifacts means grounded verification has nothing to check "
                    "your self-assessment against, which inflates perceived competence "
                    "and leaves calibration gaps uncorrected. Log at least one finding "
                    "before POSTFLIGHT: empirica finding-log --finding \"...\" --impact 0.5"
                )
            elif total_artifacts < 3 and len([k for k, v in counts.items() if v > 0]) == 1:
                types_used = [k for k, v in counts.items() if v > 0]
                reminders["calibration_nudge"] = (
                    f"\u26a0 Only {total_artifacts} {types_used[0]} logged in this transaction. "
                    "Breadth matters: assumptions, decisions, and dead-ends each ground "
                    "different aspects of calibration. Consider what you're assuming "
                    "(assumption-log), what you've chosen (decision-log), and what "
                    "didn't work (deadend-log)."
                )

            # Edge-density nudge: complements breadth, fires when artifacts exist
            # but none declare edges. Walker reach scales with edge declaration.
            if total_artifacts >= 2 and retro.get("edges_with_artifacts") == 0:
                reminders["edge_density_nudge"] = (
                    f"\u26a0 {total_artifacts} artifacts in this transaction declare 0 edges. "
                    "Anchor them in the graph: --related-to <id> for soft links, "
                    "--edge ID:RELATION for typed (supports/contradicts/derives/...). "
                    "Without edges, artifacts are unreachable from the commit-context walker."
                )

            # Sources discipline nudge: same pattern, different dimension.
            if total_artifacts >= 2 and retro.get("artifacts_with_sources") == 0:
                reminders["sources_discipline_nudge"] = (
                    f"\u26a0 {total_artifacts} artifacts in this transaction declare 0 source_refs. "
                    "Where did this evidence come from? Use --source <id> to anchor in "
                    "external material, or `empirica source-add` to register a source first. "
                    "Compliance audits and provenance trails depend on sourced evidence."
                )
    except Exception as e:
        logger.debug(f"Calibration nudge computation failed (non-fatal): {e}")

    return reminders


def _check_format_output(output_format, result, session_id, decision, cycle,
                         vectors, reasoning):
    """Format and print CHECK output in JSON or human-readable format."""
    import json

    if output_format == 'json':
        print(json.dumps(result, indent=2))
    else:
        print("\u2705 CHECK assessment submitted successfully")
        print(f"   Session: {session_id[:8]}...")
        print(f"   Decision: {decision.upper()}")
        print(f"   Cycle: {cycle}")
        print(f"   Vectors: {len(vectors)} submitted")
        print("   Storage: SQLite + Git Notes + JSON")
        if reasoning:
            print(f"   Reasoning: {reasoning[:80]}...")


def handle_check_submit_command(args):
    """Handle check-submit command.

    Orchestrates sequential stages: parse inputs, bootstrap gate, round history,
    vector normalization, dynamic thresholds, diminishing returns detection,
    gate decision, checkpoint storage, sentinel override, snapshot, enrichment,
    and output formatting.
    """
    try:
        # Stage 1: Parse and resolve inputs
        inputs = _check_parse_inputs(args)
        session_id = inputs["session_id"]
        vectors = inputs["vectors"]
        decision = inputs["decision"]
        reasoning = inputs["reasoning"]
        cycle = inputs["cycle"]
        output_format = inputs["output_format"]

        # Stage 2: Bootstrap gate — ensure project context is loaded
        bootstrap_status, bootstrap_result = _check_bootstrap_gate(session_id, vectors)

        # Stage 3: Get round number and previous CHECK vectors
        round_num, previous_check_vectors = _check_get_round_and_history(session_id, args)

        # Stage 4: Normalize vectors to flat canonical dict
        vectors = _check_normalize_vectors(vectors)

        # Stage 5: Compute dynamic readiness thresholds
        _ready_know_threshold, ready_uncertainty_threshold, dynamic_thresholds_info = (
            _check_load_dynamic_thresholds(session_id)
        )

        # Stage 6: Detect diminishing returns across rounds
        know = vectors.get('know', 0.5)
        uncertainty = vectors.get('uncertainty', 0.5)
        diminishing_returns = _check_detect_diminishing_returns(
            previous_check_vectors, know, uncertainty
        )

        # Stage 7: Compute gate decision (proceed/investigate) + autopilot
        decision, computed_decision, _autopilot_mode, decision_binding = (
            _check_gate_decision(vectors, ready_uncertainty_threshold,
                                diminishing_returns, round_num, decision)
        )

        # Stage 8: Store checkpoint + publish bus event (inner try for storage errors)
        try:
            checkpoint_id, check_transaction_id, confidence, gaps = (
                _check_store_and_publish(session_id, round_num, vectors, decision,
                                        reasoning, cycle)
            )

            # NOTE: Bayesian belief updates during CHECK were REMOVED (2026-01-21)
            # Calibration now uses vector_trajectories table.

            # Stage 9: Sentinel hook + override
            decision, sentinel_decision, sentinel_override = _check_apply_sentinel(
                session_id, decision, decision_binding, vectors, reasoning,
                confidence, gaps, cycle, round_num, checkpoint_id,
                check_transaction_id
            )

            # Stage 10: Auto-checkpoint for risky decisions
            _check_auto_checkpoint(session_id, vectors, decision, gaps, cycle,
                                   round_num)

            # Stage 11: Epistemic snapshot
            _check_create_snapshot(session_id, vectors, decision, reasoning,
                                  round_num, checkpoint_id)

            # Stage 12: Build result dict
            result = {
                "ok": True,
                "session_id": session_id,
                "decision": decision,
                "round": round_num,
                "cycle": cycle,
                "metacog": {
                    "computed_decision": computed_decision,
                    "gate_passed": computed_decision == "proceed",
                    "brier_score": dynamic_thresholds_info.get("brier_score") if dynamic_thresholds_info else None,
                    "brier_reliability": dynamic_thresholds_info.get("brier_reliability") if dynamic_thresholds_info else None,
                    "threshold_inflation": dynamic_thresholds_info.get("threshold_inflation") if dynamic_thresholds_info else None,
                    "diminishing_returns": diminishing_returns.get("detected", False),
                },
                "sentinel": {
                    "decision": sentinel_decision.value if sentinel_decision else None,
                    "override_applied": sentinel_override,
                } if SentinelHooks.is_enabled() and sentinel_override else None,
            }

            # Stage 13: Blindspot scan (may override decision)
            decision = _check_run_blindspot_scan(
                result, decision, session_id, bootstrap_result, bootstrap_status
            )

            # Stage 14: Pattern retrieval + codebase context
            _check_enrich_context(result, bootstrap_result, bootstrap_status,
                                 vectors, reasoning)

            # Stage 15: Praxic reminders (only when proceeding)
            if decision == "proceed":
                result["praxic_reminders"] = _check_build_praxic_reminders(
                    session_id, check_transaction_id
                )

            # AUTO-POSTFLIGHT REMOVED (2026-03-02):
            # CHECK is a noetic->praxic gate, not a completion event.
            # POSTFLIGHT should only happen after actual work is done.

        except Exception as e:
            logger.error(f"Failed to save check assessment: {e}")
            result = {
                "ok": False,
                "session_id": session_id,
                "message": f"Failed to save CHECK assessment: {e!s}",
                "persisted": False,
                "error": str(e)
            }

        # Stage 16: Format output
        _check_format_output(output_format, result, session_id, decision, cycle,
                            vectors, reasoning)

        return None

    except Exception as e:
        handle_cli_error(e, "Check submit", getattr(args, 'verbose', False))



# _check_goal_completion and _auto_postflight REMOVED (2026-03-02)
# See comment in handle_check_submit_command for rationale.


def _extract_numeric_value(value):
    """
    Extract numeric value from vector data.

    Handles multiple formats:
    - Simple float: 0.85
    - Nested dict: {"score": 0.85, "rationale": "...", "evidence": "..."}
    - String numbers: "0.85"

    Returns:
        float or None if value cannot be extracted
    """
    if isinstance(value, (int, float)):
        return float(value)
    elif isinstance(value, dict):
        # Extract 'score' key if present
        if 'score' in value:
            return float(value['score'])
        # Extract 'value' key as fallback
        if 'value' in value:
            return float(value['value'])
        # Try to find any numeric value in nested structure
        for _k, v in value.items():
            if isinstance(v, (int, float)):
                return float(v)
            elif isinstance(v, str) and v.replace('.', '').replace('-', '').isdigit():
                try:
                    return float(v)
                except ValueError:
                    continue
        # Try to convert entire dict to float if it looks like a single number
        for v in value.values():
            if isinstance(v, (int, float)):
                return float(v)
    elif isinstance(value, str):
        # Try to convert string to float
        try:
            return float(value)
        except ValueError:
            pass
    return None


def _extract_all_vectors(vectors):
    """
    Extract all numeric values from vectors dict, handling nested structures.
    Flattens nested dicts to extract individual vector values.

    Args:
        vectors: Dict containing vector data (simple or nested)

    Returns:
        Dict with all vector names mapped to numeric values

    Example:
        Input: {"engagement": 0.85, "foundation": {"know": 0.75, "do": 0.80}}
        Output: {"engagement": 0.85, "know": 0.75, "do": 0.80}
    """
    extracted = {}

    for key, value in vectors.items():
        if isinstance(value, dict):
            # Nested structure - recursively extract all sub-vectors
            for nested_key, nested_value in value.items():
                numeric_value = _extract_numeric_value(nested_value)
                if numeric_value is not None:
                    extracted[nested_key] = numeric_value
                else:
                    # Fallback to default if extraction fails
                    extracted[nested_key] = 0.5
        else:
            # Simple value - extract directly
            numeric_value = _extract_numeric_value(value)
            if numeric_value is not None:
                extracted[key] = numeric_value
            else:
                # Fallback to default if extraction fails
                extracted[key] = 0.5

    return extracted

_TYPE_TO_DOMAIN = {
    "product": "software", "application": "software",
    "feature": "software", "infrastructure": "operations",
    "operations": "operations", "research": "research",
    "documentation": "consulting",
}


def _pipeline_embed_grounded_calibration(
    session_id, vectors, grounded_verification, project_id, ai_id, goal_id, now,
):
    """Stage 1: Grounded calibration embedding to Qdrant."""
    import uuid as uuid_mod

    try:
        if not grounded_verification or grounded_verification.get('evidence_count', 0) <= 0:
            return
        from empirica.core.qdrant.vector_store import (
            _check_qdrant_available,
            embed_calibration_trajectory,
            embed_grounded_verification,
        )
        if not _check_qdrant_available():
            return

        grounded_vectors = {}
        for v_name, gap in grounded_verification.get('gaps', {}).items():
            grounded_vectors[v_name] = round(vectors.get(v_name, 0.5) - gap, 4)

        embed_grounded_verification(
            project_id=project_id, verification_id=str(uuid_mod.uuid4()),
            session_id=session_id, ai_id=ai_id,
            self_assessed=vectors, grounded_vectors=grounded_vectors,
            calibration_gaps=grounded_verification.get('gaps', {}),
            grounded_coverage=grounded_verification.get('grounded_coverage', 0),
            calibration_score=grounded_verification.get('calibration_score', 0),
            evidence_count=grounded_verification.get('evidence_count', 0),
            sources=grounded_verification.get('sources', []),
            goal_id=goal_id, timestamp=now,
        )
        embed_calibration_trajectory(
            project_id=project_id, session_id=session_id, ai_id=ai_id,
            self_assessed=vectors, grounded_vectors=grounded_vectors,
            calibration_gaps=grounded_verification.get('gaps', {}),
            goal_id=goal_id, timestamp=now,
        )
        logger.debug(f"Embedded grounded calibration for {session_id[:8]}")
    except Exception as e:
        logger.debug(f"Grounded calibration embedding skipped: {e}")


def _pipeline_cortex_cache_feedback(session_id, vectors, grounded_verification):
    """Stage 2: Cortex cache feedback for low-calibration sessions."""
    import os

    try:
        if not grounded_verification or grounded_verification.get('calibration_score', 1.0) >= 0.3:
            return
        import urllib.request
        cortex_url = os.environ.get('EMPIRICA_CORTEX_URL', 'http://localhost:8420')
        payload = json.dumps({
            'session_id': session_id,
            'calibration_score': grounded_verification.get('calibration_score'),
            'grounded_coverage': grounded_verification.get('grounded_coverage'),
            'evidence_count': grounded_verification.get('evidence_count'),
            'vectors': vectors,
            'gaps': grounded_verification.get('gaps', {}),
            'sources': grounded_verification.get('sources', []),
        }).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        api_key = os.environ.get('CORTEX_API_KEY', '')
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        req = urllib.request.Request(f'{cortex_url}/postflight', data=payload, headers=headers, method='POST')
        urllib.request.urlopen(req, timeout=1.0)
        logger.debug("Wrote verified predictions to Cortex cache")
    except Exception:
        pass  # Cortex not running


def _pipeline_trajectory_storage(session_id, project_id):
    """Stage 3: Epistemic trajectory storage."""
    try:
        db = _get_db_for_session(session_id)
        from empirica.core.epistemic_trajectory import store_trajectory
        store_trajectory(project_id, session_id, db)
        db.close()
    except Exception as e:
        logger.debug(f"Trajectory storage skipped: {e}")


def _build_episodic_narrative(reasoning, deltas, grounded_verification):
    """Build narrative string for episodic memory, enriched with calibration gaps."""
    narrative = reasoning or f"Session completed with learning delta: {deltas}"

    if not grounded_verification or grounded_verification.get('evidence_count', 0) <= 0:
        return narrative

    cal_score = grounded_verification.get('calibration_score', 0)
    coverage = grounded_verification.get('grounded_coverage', 0)
    gaps = grounded_verification.get('gaps', {})
    sig = {v: g for v, g in gaps.items() if abs(g) > 0.15}
    if sig:
        gap_desc = "; ".join(
            f"{v}: {'over' if g > 0 else 'under'} by {abs(g):.2f}" for v, g in sig.items()
        )
        narrative += f" Grounded calibration: score={cal_score:.3f}, coverage={coverage:.0%}. Significant gaps: {gap_desc}."

    return narrative


def _pipeline_episodic_memory(
    session_id, vectors, deltas, reasoning, grounded_verification,
    project_id, ai_id, goal_id, now,
):
    """Stage 4: Episodic memory embedding."""
    import uuid as uuid_mod

    try:
        db = _get_db_for_session(session_id)
        from empirica.core.qdrant.vector_store import embed_episodic
        try:
            findings = db.get_project_findings(project_id, limit=5)
        except Exception:
            findings = []

        outcome = "success" if deltas.get("know", 0) > 0.1 else (
            "partial" if deltas.get("completion", 0) > 0 else "abandoned")
        narrative = _build_episodic_narrative(reasoning, deltas, grounded_verification)

        embed_episodic(
            project_id=project_id, episode_id=str(uuid_mod.uuid4()),
            narrative=narrative, episode_type="session_arc",
            session_id=session_id, ai_id=ai_id, goal_id=goal_id,
            learning_delta=deltas, outcome=outcome,
            key_moments=[f.get('finding', '')[:100] for f in findings[:3]] if findings else [],
            tags=[ai_id], timestamp=now,
        )
        db.close()
    except Exception as e:
        logger.debug(f"Episodic memory skipped: {e}")


def _pipeline_auto_embed_memories(session_id, project_id):
    """Stage 5: Auto-embed findings/unknowns to Qdrant."""
    try:
        from empirica.core.qdrant.vector_store import _check_qdrant_available, init_collections, upsert_memory
        db = _get_db_for_session(session_id)
        if not _check_qdrant_available():
            db.close()
            return

        init_collections(project_id)
        try:
            sf = db.get_project_findings(project_id, limit=10)
            su = db.get_project_unknowns(project_id, resolved=False, limit=10)
        except Exception:
            sf, su = [], []

        mem_items = []
        for f in sf:
            fid = f.get('finding_id') or str(f.get('id', ''))
            if fid:
                mem_items.append({'id': fid, 'text': f.get('finding', ''), 'type': 'finding',
                                  'session_id': f.get('session_id', session_id), 'goal_id': f.get('goal_id'),
                                  'timestamp': f.get('created_timestamp')})
        for u in su:
            uid = u.get('unknown_id') or str(u.get('id', ''))
            if uid:
                mem_items.append({'id': uid, 'text': u.get('unknown', ''), 'type': 'unknown',
                                  'session_id': u.get('session_id', session_id), 'goal_id': u.get('goal_id'),
                                  'timestamp': u.get('created_timestamp'), 'is_resolved': u.get('is_resolved', False)})
        if mem_items:
            upsert_memory(project_id, mem_items)
            logger.debug(f"Auto-embedded {len(mem_items)} memory items")
        db.close()
    except Exception as e:
        logger.debug(f"Memory sync skipped: {e}")


def _pipeline_workspace_index_sync(session_id, project_id):
    """Stage 6: Workspace index sync."""
    try:
        from empirica.core.qdrant.connection import _check_qdrant_available as _ws_check
        from empirica.utils.session_resolver import InstanceResolver as _R
        _ws_tx = _R.transaction_read()
        _ws_tx_id = _ws_tx.get('transaction_id') if _ws_tx else None
        if _ws_check() and _ws_tx_id:
            from empirica.core.qdrant.workspace_index import sync_transaction_to_index
            sync_transaction_to_index(project_id=project_id, session_id=session_id, transaction_id=_ws_tx_id)
    except Exception as e:
        logger.debug(f"Workspace index sync skipped: {e}")


def _pipeline_decay_and_global_sync(session_id, project_id):
    """Stage 7: Decay triggers + global sync."""
    try:
        from empirica.core.qdrant.vector_store import (
            _check_qdrant_available,
            apply_staleness_signal,
            auto_sync_session_to_global,
            update_assumption_urgency,
        )
        if not _check_qdrant_available():
            return
        try:
            auto_sync_session_to_global(project_id, session_id)
        except Exception:
            pass
        try:
            apply_staleness_signal(project_id)
        except Exception:
            pass
        try:
            update_assumption_urgency(project_id)
        except Exception:
            pass
    except Exception:
        pass


def _pipeline_epistemic_snapshot(
    session_id, vectors, deltas, reasoning, postflight_confidence, checkpoint_id,
):
    """Stage 8: Epistemic snapshot creation."""
    try:
        from empirica.data.epistemic_snapshot import ContextSummary
        from empirica.data.snapshot_provider import EpistemicSnapshotProvider
        db = _get_db_for_session(session_id)
        session = db.get_session(session_id)
        if session:
            provider = EpistemicSnapshotProvider()
            context_summary = ContextSummary(
                semantic={"phase": "POSTFLIGHT", "confidence": postflight_confidence},
                narrative=reasoning or "Session completed",
                evidence_refs=[checkpoint_id] if checkpoint_id else [],
            )
            snapshot = provider.create_snapshot_from_session(
                session_id=session_id, context_summary=context_summary,
                cascade_phase="POSTFLIGHT", domain_vectors={"deltas": deltas} if deltas else None,
            )
            snapshot.vectors = vectors
            snapshot.delta = deltas
            provider.save_snapshot(snapshot)
            logger.debug(f"Created epistemic snapshot for {session_id[:8]}")
        db.close()
    except Exception as e:
        logger.debug(f"Snapshot creation skipped: {e}")


def _run_postflight_storage_pipeline(
    session_id: str, vectors: dict, deltas: dict, reasoning: str,
    grounded_verification: dict | None, postflight_confidence: float,
    checkpoint_id: str | None,
) -> None:
    """Run all POSTFLIGHT storage operations: Qdrant embedding, Cortex push,
    trajectory, episodic memory, auto-embed, workspace index, decay, snapshot.

    All operations are non-fatal — failures are logged and skipped.
    """
    import time

    # Get session context (shared across all stages)
    try:
        db = _get_db_for_session(session_id)
        session = db.get_session(session_id)
        project_id = session.get('project_id') if session else None
        ai_id = session.get('ai_id', 'claude-code') if session else 'claude-code'
        goal_id = session.get('current_goal_id') if session else None
        db.close()
    except Exception:
        return  # Can't do anything without session

    if not project_id:
        return

    now = time.time()

    _pipeline_embed_grounded_calibration(
        session_id, vectors, grounded_verification, project_id, ai_id, goal_id, now,
    )
    _pipeline_cortex_cache_feedback(session_id, vectors, grounded_verification)
    _pipeline_trajectory_storage(session_id, project_id)
    _pipeline_episodic_memory(
        session_id, vectors, deltas, reasoning, grounded_verification,
        project_id, ai_id, goal_id, now,
    )
    _pipeline_auto_embed_memories(session_id, project_id)
    _pipeline_workspace_index_sync(session_id, project_id)
    _pipeline_decay_and_global_sync(session_id, project_id)
    _pipeline_epistemic_snapshot(
        session_id, vectors, deltas, reasoning, postflight_confidence, checkpoint_id,
    )


def _run_grounded_verification(
    session_id: str, vectors: dict, phase_tool_counts: dict,
    work_context: str | None, work_type: str | None, transaction_id: str | None,
    project_path: str | None = None,
) -> dict | None:
    """Run grounded verification: phase-aware evidence collection + calibration.

    Args:
        project_path: Canonical project root (typically resolved_project_path
            from tx_info — the path the open transaction is bound to). Used
            to scope EvidenceProfile.resolve and the project.yaml read.
            Falls back to os.getcwd() only when unset, mirroring the cortex
            sync fix (T7) — avoids the multi-.empirica CWD-misroute hazard.

    Returns grounded_verification dict or None if unavailable. Non-fatal.
    """
    try:
        import os

        from empirica.core.post_test.collector import EvidenceProfile
        from empirica.core.post_test.grounded_calibration import run_grounded_verification
        from empirica.core.post_test.phase_boundary import detect_phase_boundary

        db = _get_db_for_session(session_id)
        session = db.get_session(session_id)
        project_id = session.get('project_id') if session else None

        # Prefer caller-supplied project_path (tx-bound, canonical); only fall
        # back to cwd when unset. Same shape as T7's cortex_read_calibration
        # fix — eliminates the CWD-misroute pattern from #95.
        _resolve_path = project_path or os.getcwd()
        evidence_profile = EvidenceProfile.resolve(project_path=_resolve_path)

        # Detect CHECK phase boundary for noetic/praxic split
        phase_boundary = None
        try:
            phase_boundary = detect_phase_boundary(session_id, db)
            if phase_boundary and phase_boundary.get("has_check"):
                logger.debug(f"Phase boundary: check_count={phase_boundary['check_count']}")
        except Exception as e:
            logger.debug(f"Phase boundary detection failed (non-fatal): {e}")

        # Resolve domain + Tier 2 weights
        project_type = session.get("project_type", "") if session else ""
        domain = _TYPE_TO_DOMAIN.get(project_type, "default")

        tier2_weights = None
        try:
            from pathlib import Path as _Path
            # Same canonical-path principle: project.yaml lives next to the
            # transaction-bound project, not the calling cwd.
            proj_yaml = _Path(_resolve_path) / ".empirica" / "project.yaml"
            if proj_yaml.exists():
                import yaml
                with open(proj_yaml) as _f:
                    tier2_weights = (yaml.safe_load(_f) or {}).get("calibration_weights")
            if not tier2_weights:
                from .project_init import _seed_calibration_weights
                tier2_weights = _seed_calibration_weights(project_type or "software")
        except Exception:
            pass

        result = run_grounded_verification(
            session_id=session_id, postflight_vectors=vectors, db=db,
            project_id=project_id, domain=domain, phase_boundary=phase_boundary,
            evidence_profile=evidence_profile, phase_tool_counts=phase_tool_counts,
            work_context=work_context, work_type=work_type,
            per_vector_weights=tier2_weights, transaction_id=transaction_id,
        )

        if result:
            logger.debug(f"Grounded verification: {result['evidence_count']} evidence items")
        db.close()
        return result
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.warning(f"Grounded verification failed (non-fatal): {e}")
        logger.debug(f"Grounded verification traceback:\n{tb}")
        # Write traceback to file for debugging (visible to user)
        try:
            from pathlib import Path
            crash_log = Path.home() / ".empirica" / "grounded_verification_error.log"
            crash_log.parent.mkdir(parents=True, exist_ok=True)
            with open(crash_log, "w") as f:
                f.write(f"Error: {e}\n\n{tb}")
        except Exception:
            pass
        return None


def _retro_count_artifacts(cursor, session_id, transaction_id):
    """Count artifact types logged in this transaction. Returns dict."""
    artifact_counts = {}
    all_tables = [
        ("project_findings", "findings"), ("project_unknowns", "unknowns"),
        ("project_dead_ends", "dead_ends"), ("mistakes_made", "mistakes"),
        ("assumptions", "assumptions"), ("decisions", "decisions"),
    ]
    for table, label in all_tables:
        try:
            if transaction_id:
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ? AND transaction_id = ?",
                               (session_id, transaction_id))
            else:
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (session_id,))
            artifact_counts[label] = cursor.fetchone()[0]
        except Exception:
            artifact_counts[label] = 0
    return artifact_counts


def _retro_count_sources(cursor, session_id: str, transaction_id: str | None) -> int:
    """Count artifacts in this transaction that declare at least one source_ref.

    Sources are tracked via source_refs column (JSON list of source IDs from
    source-add). 0% adoption today (per goal d290bc3c) — this helper drives
    the nudge that surfaces when artifacts skip --source.
    """
    by_table = ("project_findings", "project_unknowns", "project_dead_ends",
                "mistakes_made", "assumptions", "decisions")
    total = 0
    for table in by_table:
        try:
            sql = (
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE session_id = ? "
                f"AND COALESCE(source_refs, '') NOT IN ('', '[]', 'null')"
            )
            params: tuple = (session_id,)
            if transaction_id:
                sql += " AND transaction_id = ?"
                params = (session_id, transaction_id)
            cursor.execute(sql, params)
            total += cursor.fetchone()[0]
        except Exception:
            # Column missing on some tables; ignore silently.
            pass
    return total


def _retro_count_edges(cursor, session_id: str, transaction_id: str | None) -> int:
    """Count artifacts in this transaction that have at least one declared edge.

    Edges live in <type>_data.edges (or just data.edges for assumptions/decisions).
    Counts rows whose data column has json_array_length(edges) > 0.
    """
    by_table = [
        ("project_findings", "finding_data"),
        ("project_unknowns", "unknown_data"),
        ("project_dead_ends", "dead_end_data"),
        ("mistakes_made", "mistake_data"),
        ("assumptions", "data"),
        ("decisions", "data"),
    ]
    total = 0
    for table, data_col in by_table:
        try:
            sql = (
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE session_id = ? "
                f"AND COALESCE(json_array_length(json_extract({data_col}, '$.edges')), 0) > 0"
            )
            params: tuple = (session_id,)
            if transaction_id:
                sql += " AND transaction_id = ?"
                params = (session_id, transaction_id)
            cursor.execute(sql, params)
            total += cursor.fetchone()[0]
        except Exception:
            # Column may not exist on older rows / table; ignore silently.
            pass
    return total


def _build_retrospective(session_id: str, transaction_id: str | None) -> dict:
    """Build retrospective feedback: artifact breadth, commit discipline, completion hints.

    Returns dict with artifact_counts, optional breadth_note, commit_warning, completion_hint.
    Non-fatal -- returns empty dict on any error.
    """
    import subprocess as _sp

    try:
        db = _get_db_for_session(session_id)
        cursor = db.conn.cursor()

        artifact_counts = _retro_count_artifacts(cursor, session_id, transaction_id)
        retro: dict = {"artifact_counts": artifact_counts}

        types_used = [k for k, v in artifact_counts.items() if v > 0]
        types_missing = [k for k, v in artifact_counts.items() if v == 0]

        if len(types_used) <= 1 and sum(artifact_counts.values()) > 0:
            retro["breadth_note"] = (
                f"Only {', '.join(types_used) or 'no'} artifacts logged. "
                f"Missing: {', '.join(types_missing)}. "
                "Unlogged artifact types are ungrounded prediction domains — "
                "were there assumptions, decisions, dead-ends, or mistakes worth capturing?"
            )

        # Edge density nudge — surfaces when artifacts exist but no edges declared.
        total_artifacts = sum(artifact_counts.values())
        if total_artifacts >= 2:
            try:
                edges_count = _retro_count_edges(cursor, session_id, transaction_id)
                retro["edges_with_artifacts"] = edges_count
                if edges_count == 0:
                    retro["edge_density_note"] = (
                        f"{total_artifacts} artifacts logged with 0 declared edges. "
                        "Anchor them in the graph: --related-to <id> on any *-log command, "
                        "or --edge ID:RELATION for typed links. Unlinked artifacts are "
                        "invisible to the commit-context walker."
                    )
            except Exception:
                pass

        # Sources discipline nudge — surfaces when artifacts exist but no source_refs declared.
        if total_artifacts >= 2:
            try:
                sources_count = _retro_count_sources(cursor, session_id, transaction_id)
                retro["artifacts_with_sources"] = sources_count
                if sources_count == 0:
                    retro["sources_discipline_note"] = (
                        f"{total_artifacts} artifacts logged with 0 source_refs. "
                        "Where did the evidence come from? Use --source <id> on any *-log "
                        "command, or `empirica source-add` to register the source first. "
                        "Sourced artifacts get full provenance trail in audit + compliance."
                    )
            except Exception:
                pass

        try:
            _gr = _sp.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5)
            if _gr.returncode == 0 and _gr.stdout.strip():
                retro["commit_warning"] = (
                    "Uncommitted changes detected. Grounded calibration for change/state/do "
                    "will be based on committed work only — uncommitted edits are invisible."
                )
        except Exception:
            pass

        try:
            if transaction_id:
                cursor.execute("SELECT COUNT(*) FROM project_goals WHERE is_completed = 1 AND completed_transaction_id = ?",
                               (transaction_id,))
            else:
                cursor.execute("SELECT COUNT(*) FROM project_goals WHERE is_completed = 1 AND session_id = ?", (session_id,))
            goals_completed = cursor.fetchone()[0]
            if goals_completed > 0:
                retro["completion_hint"] = (
                    f"{goals_completed} goal(s) completed in this transaction — "
                    "completion for this transaction should be near 1.0."
                )
        except Exception:
            pass

        db.close()
        return retro
    except Exception as e:
        logger.debug(f"Retrospective feedback failed (non-fatal): {e}")
        return {}

def _postflight_parse_config_or_legacy(args):
    """Parse postflight input from config data or legacy CLI flags.

    Returns (session_id, vectors, reasoning, grounded_vectors,
    grounded_rationale, coverage, output_format). Exits on validation
    failure. ``coverage`` is the optional agent self-coverage block
    (paper section 4.1), only available via the JSON-input path because
    legacy CLI flags don't carry structured nested data.
    """
    import sys

    config_data, output_format = _parse_workflow_input(args, "POSTFLIGHT")

    if config_data:
        session_id = config_data.get('session_id') or getattr(args, 'session_id', None)
        vectors = config_data.get('vectors')
        reasoning = config_data.get('reasoning', '')
        grounded_vectors = config_data.get('grounded_vectors')
        grounded_rationale = config_data.get('grounded_rationale')
        coverage = config_data.get('coverage')

        if not session_id or not vectors:
            print(json.dumps({
                "ok": False,
                "error": "Config file must include 'vectors' field" + (
                    " and 'session_id' (could not auto-derive from active transaction)"
                    if not session_id else ""
                ),
                "hint": "Run PREFLIGHT first to open a transaction, or provide session_id explicitly"
            }))
            sys.exit(1)
    else:
        session_id = args.session_id
        vectors = parse_json_safely(args.vectors) if isinstance(args.vectors, str) else args.vectors
        reasoning = args.reasoning
        output_format = getattr(args, 'output', 'json')
        grounded_vectors = None
        grounded_rationale = None
        coverage = None

        if not session_id:
            try:
                session_id = R.session_id()
            except Exception:
                pass

        if not session_id or not vectors:
            print(json.dumps({
                "ok": False,
                "error": "Legacy mode requires --vectors flag (--session-id auto-derived if in transaction)",
                "hint": "For AI-first mode, use: empirica postflight-submit config.json"
            }))
            sys.exit(1)

    return session_id, vectors, reasoning, grounded_vectors, grounded_rationale, coverage, output_format


def _postflight_resolve_preflight_session(session_id):
    """Find the original PREFLIGHT session_id for cross-compaction continuity.

    Returns preflight_session_id.
    """
    import json as _json
    from pathlib import Path

    preflight_session_id = session_id
    try:
        global_home = Path.home() / '.empirica'
        for active_file in global_home.glob('active_work_*.json'):
            try:
                data = _json.loads(active_file.read_text())
                if data.get('empirica_session_id'):
                    db_path = resolve_session_db_path(data['empirica_session_id'])
                    if db_path:
                        import sqlite3
                        conn = sqlite3.connect(str(db_path))
                        cursor = conn.cursor()
                        cursor.execute("SELECT 1 FROM reflexes WHERE session_id = ? AND phase = 'PREFLIGHT'",
                                      (data['empirica_session_id'],))
                        if cursor.fetchone():
                            preflight_session_id = data['empirica_session_id']
                            logger.debug(f"Using PREFLIGHT session from transaction: {preflight_session_id[:8]}...")
                        conn.close()
                        break
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Transaction context lookup failed (using current session): {e}")

    return preflight_session_id


def _parse_postflight_input(args) -> dict[str, Any]:
    """Parse and validate postflight input from config file or CLI args.

    Returns dict with keys: session_id, vectors, reasoning, preflight_session_id,
    grounded_vectors, grounded_rationale, coverage, output_format.
    """
    session_id, vectors, reasoning, grounded_vectors, grounded_rationale, coverage, output_format = (
        _postflight_parse_config_or_legacy(args)
    )

    # Transaction continuity: override session_id from active transaction
    try:
        tx_data = R.transaction_read()
        if tx_data and tx_data.get('session_id'):
            tx_session_id = tx_data['session_id']
            if tx_session_id != session_id:
                logger.debug(f"POSTFLIGHT: Overriding session_id: {session_id[:8]}... -> {tx_session_id[:8]}...")
                session_id = tx_session_id
    except Exception as e:
        logger.debug(f"Transaction session lookup failed (using provided session_id): {e}")

    if not isinstance(vectors, dict):
        raise ValueError("Vectors must be a dictionary")

    session_id = _resolve_and_validate_session(session_id, "POSTFLIGHT")
    vectors = _extract_all_vectors(vectors)

    preflight_session_id = _postflight_resolve_preflight_session(session_id)

    return {
        "session_id": session_id,
        "vectors": vectors,
        "reasoning": reasoning,
        "preflight_session_id": preflight_session_id,
        "grounded_vectors": grounded_vectors,
        "grounded_rationale": grounded_rationale,
        "coverage": coverage,
        "output_format": output_format,
    }


def _calculate_postflight_deltas(logger_instance, vectors, preflight_session_id):
    """Calculate deltas from preflight vectors and detect trajectory issues.

    Returns:
        tuple of (preflight_vectors, deltas, trajectory_issues)
    """
    deltas = {}
    trajectory_issues = []  # Learning trajectory pattern issues (NOT calibration)
    preflight_vectors = None

    try:
        # Get preflight checkpoint from git notes or SQLite for delta calculation
        preflight_checkpoint = logger_instance.get_last_checkpoint(phase="PREFLIGHT")

        # Fallback: Query SQLite reflexes table directly if git notes unavailable
        # Use preflight_session_id to handle cross-session transactions (compaction)
        if not preflight_checkpoint:
            db = _get_db_for_session(preflight_session_id)
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT engagement, know, do, context, clarity, coherence, signal, density,
                       state, change, completion, impact, uncertainty
                FROM reflexes
                WHERE session_id = ? AND phase = 'PREFLIGHT'
                ORDER BY timestamp DESC LIMIT 1
            """, (preflight_session_id,))
            preflight_row = cursor.fetchone()
            db.close()

            if preflight_row:
                vector_names = ["engagement", "know", "do", "context", "clarity", "coherence",
                               "signal", "density", "state", "change", "completion", "impact", "uncertainty"]
                preflight_vectors = {name: preflight_row[i] for i, name in enumerate(vector_names)}
            else:
                preflight_vectors = None
        elif 'vectors' in preflight_checkpoint:
            preflight_vectors = preflight_checkpoint['vectors']
        else:
            preflight_vectors = None

        if preflight_vectors:

            # Calculate deltas (system calculates growth, not AI's claimed growth)
            for key in vectors:
                if key in preflight_vectors:
                    pre_val = preflight_vectors.get(key, 0.5)
                    post_val = vectors.get(key, 0.5)
                    delta = post_val - pre_val
                    deltas[key] = round(delta, 3)

                    # Note: Within-session vector decreases removed
                    # (PREFLIGHT->POSTFLIGHT decreases are calibration corrections, not memory gaps)
                    # True memory gap detection requires cross-session comparison:
                    # Previous session POSTFLIGHT -> Current session PREFLIGHT
                    # This requires forced session restart before context fills and using
                    # handoff-query/project-bootstrap to measure retention

                    # TRAJECTORY ISSUE DETECTION: Identify learning patterns in PREFLIGHT->POSTFLIGHT deltas
                    # Note: These are trajectory issues, NOT calibration (which requires grounded evidence)
                    if key == "know" and delta > 0.2:
                        do_delta = deltas.get("do", 0)
                        if do_delta < -0.1:
                            trajectory_issues.append({
                                "pattern": "know_up_do_down",
                                "description": "Knowledge increased but capability decreased - possible theoretical learning without application"
                            })

                    # If completion high but uncertainty also high, misalignment
                    if key == "completion" and post_val > 0.8:
                        uncertainty_post = vectors.get("uncertainty", 0.5)
                        if uncertainty_post > 0.5:
                            trajectory_issues.append({
                                "pattern": "completion_high_uncertainty_high",
                                "description": "High completion with high uncertainty - possible overconfidence or incomplete self-assessment"
                            })
        else:
            logger.warning("No PREFLIGHT checkpoint found - cannot calculate deltas or detect memory gaps")

    except Exception as e:
        logger.debug(f"Delta calculation failed: {e}")
        # Delta calculation is optional

    return preflight_vectors, deltas, trajectory_issues


def _postflight_close_and_capture_counters(result, resolved_project_path, suffix):
    """Read transaction file, capture counters, close transaction. Modifies result in-place."""
    import json as _json
    from pathlib import Path

    if resolved_project_path:
        tx_file = Path(resolved_project_path) / '.empirica' / f'active_transaction{suffix}.json'
    else:
        tx_file = Path.home() / '.empirica' / f'active_transaction{suffix}.json'

    if not tx_file.exists():
        return

    with open(tx_file) as f:
        tx_data = _json.load(f)
    result["transaction_id"] = tx_data.get('transaction_id')
    result["avg_turns"] = tx_data.get('avg_turns', 0)
    result["work_context"] = tx_data.get('work_context')
    result["work_type"] = tx_data.get('work_type')

    # Read hook counters
    counters_file = tx_file.parent / f'hook_counters{suffix}.json'
    counters = {}
    if counters_file.exists():
        try:
            with open(counters_file) as f:
                counters = _json.load(f)
        except Exception:
            pass

    result["tool_call_count"] = counters.get('tool_call_count', 0)
    result["phase_tool_counts"] = {
        'noetic_tool_calls': counters.get('noetic_tool_calls', 0),
        'praxic_tool_calls': counters.get('praxic_tool_calls', 0),
    }
    result["context_shifts"] = {
        'solicited_prompts': counters.get('solicited_prompt_count', 0),
        'unsolicited_prompts': counters.get('unsolicited_prompt_count', 0),
    }
    result["tool_trace"] = counters.get('tool_trace', [])

    # Close transaction, preserving enrichment fields
    _enrichment_keys = ('domain', 'criticality', 'work_type', 'work_context',
                        'cascade_profile', 'predicted_check_outcomes')
    _saved_enrichment = {k: tx_data[k] for k in _enrichment_keys if tx_data.get(k)}

    R.transaction_write(
        transaction_id=result["transaction_id"],
        session_id=tx_data.get('session_id'),
        preflight_timestamp=tx_data.get('preflight_timestamp'),
        status="closed",
        project_path=tx_data.get('project_path') or resolved_project_path
    )

    if _saved_enrichment:
        try:
            _closed_tx = R.transaction_read() or {}
            _closed_tx.update(_saved_enrichment)
            _tx_suffix = R.instance_suffix()
            _tx_proj = _closed_tx.get('project_path', resolved_project_path)
            _tx_path = Path(_tx_proj) / '.empirica' / f'active_transaction{_tx_suffix}.json'
            with open(_tx_path, 'w') as f:
                _json.dump(_closed_tx, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to preserve enrichment on close: {e}")

    R.counters_clear()


def _close_postflight_transaction(session_id):
    """Read and close active transaction, capture counters, entity context.

    Returns dict with transaction_id, tool_call_count, avg_turns, phase_tool_counts,
    context_shifts, tool_trace, work_context, work_type, entity_context,
    resolved_project_path.
    """
    result: dict[str, Any] = {
        "transaction_id": None, "tool_call_count": 0, "avg_turns": 0,
        "phase_tool_counts": None,
        "context_shifts": {'solicited_prompts': 0, 'unsolicited_prompts': 0},
        "tool_trace": [], "work_context": None, "work_type": None,
        "entity_context": [], "resolved_project_path": None,
    }

    try:
        suffix = R.instance_suffix()
        resolved_project_path = R.project_path()
        result["resolved_project_path"] = resolved_project_path
        _postflight_close_and_capture_counters(result, resolved_project_path, suffix)
    except Exception as e:
        logger.debug(f"Transaction close failed (non-fatal): {e}")
        result["tool_call_count"] = 0
        result["avg_turns"] = 0

    # Collect entity context for git notes (cross-project provenance)
    try:
        from empirica.data.repositories.workspace_db import WorkspaceDBRepository
        _pf_tx = R.transaction_read()
        if _pf_tx and _pf_tx.get('transaction_id'):
            with WorkspaceDBRepository.open() as _pf_ws:
                _pf_links = _pf_ws.get_entity_artifacts_by_transaction(_pf_tx['transaction_id'])
                seen = set()
                for _l in _pf_links:
                    key = f"{_l['entity_type']}:{_l['entity_id']}"
                    if key not in seen:
                        seen.add(key)
                        result["entity_context"].append({
                            "entity_type": _l['entity_type'],
                            "entity_id": _l['entity_id'],
                            "artifact_type": _l['artifact_type'],
                        })
    except Exception:
        pass

    return result


def _run_postflight_beliefs_and_exports(session_id, preflight_vectors, vectors):
    """Run Bayesian belief updates and breadcrumbs export.

    Returns:
        tuple of (belief_updates, calibration_exported)
    """
    import uuid

    belief_updates = {}
    calibration_exported = False
    try:
        if preflight_vectors:
            from empirica.core.bayesian_beliefs import BayesianBeliefManager

            db = _get_db_for_session(session_id)
            belief_manager = BayesianBeliefManager(db)

            # Get cascade_id and ai_id for this session
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT cascade_id FROM cascades
                WHERE session_id = ?
                ORDER BY started_at DESC LIMIT 1
            """, (session_id,))
            cascade_row = cursor.fetchone()
            cascade_id = cascade_row[0] if cascade_row else str(uuid.uuid4())

            # Get ai_id for calibration export
            cursor.execute("SELECT ai_id FROM sessions WHERE session_id = ?", (session_id,))
            ai_row = cursor.fetchone()
            ai_id = ai_row[0] if ai_row else 'claude-code'

            # Update beliefs with PREFLIGHT -> POSTFLIGHT comparison
            belief_updates = belief_manager.update_beliefs(
                cascade_id=cascade_id,
                session_id=session_id,
                preflight_vectors=preflight_vectors,
                postflight_vectors=vectors
            )

            if belief_updates:
                logger.debug(f"Updated Bayesian beliefs for {len(belief_updates)} vectors")

                # BREADCRUMBS CALIBRATION EXPORT: Write to .breadcrumbs.yaml for instant session-start
                # This creates a calibration cache layer - no DB queries needed at startup
                try:
                    from empirica.core.bayesian_beliefs import export_calibration_to_breadcrumbs
                    calibration_exported = export_calibration_to_breadcrumbs(ai_id, db)
                    if calibration_exported:
                        logger.debug(f"Exported calibration to .breadcrumbs.yaml for {ai_id}")
                except Exception as cal_e:
                    logger.debug(f"Calibration export to breadcrumbs skipped: {cal_e}")

                # BRIER CALIBRATION EXPORT: Write Brier decomposition to .breadcrumbs.yaml
                try:
                    from empirica.core.post_test.dynamic_thresholds import export_brier_to_breadcrumbs
                    brier_exported = export_brier_to_breadcrumbs(ai_id, db)
                    if brier_exported:
                        logger.debug(f"Exported Brier calibration to .breadcrumbs.yaml for {ai_id}")
                except Exception as brier_e:
                    logger.debug(f"Brier calibration export to breadcrumbs skipped: {brier_e}")

            db.close()
    except Exception as e:
        logger.debug(f"Bayesian belief update failed (non-fatal): {e}")

    return belief_updates, calibration_exported


def _run_postflight_compliance(session_id, transaction_id, work_type, resolved_project_path):
    """Run compliance loop execution.

    Returns:
        tuple of (compliance_result, compliance_error)
    """
    compliance_result = None
    compliance_error = None
    try:
        from empirica.config.service_registry import ServiceRegistry
        from empirica.core.post_test.compliance_loop import run_compliance_checks
        if not ServiceRegistry.list_all():
            ServiceRegistry.load_builtins()
        # Read domain/criticality from transaction file
        _tx = R.transaction_read() or {}
        _pf_domain = _tx.get('domain')
        _pf_criticality = _tx.get('criticality')
        _pf_work_type = _tx.get('work_type', work_type)
        if _pf_domain or _pf_criticality:
            # Goal-scoped: read edited_files from hook counters
            _edited = []
            try:
                _hc = R.hook_counters_read() if hasattr(R, 'hook_counters_read') else None  # pyright: ignore[reportAttributeAccessIssue]
                if _hc:
                    _edited = _hc.get('edited_files', [])
                elif _tx:
                    _edited = _tx.get('edited_files', [])
            except Exception:
                pass
            compliance_result = run_compliance_checks(
                session_id=session_id,
                transaction_id=transaction_id,
                work_type=_pf_work_type,
                domain=_pf_domain,
                criticality=_pf_criticality,
                project_path=resolved_project_path,
                changed_files=_edited,
            )
    except Exception as e:
        import traceback
        logger.warning(f"Compliance loop failed: {e}")
        logger.debug(traceback.format_exc())
        # Surface the error so the AI knows compliance didn't run
        compliance_result = None
        compliance_error = str(e)

    return compliance_result, compliance_error


def _postflight_add_compliance_block(result, compliance_result, compliance_error):
    """Add compliance and Brier blocks to postflight result. Modifies result in-place."""
    if compliance_result is None and compliance_error:
        result["compliance_error"] = compliance_error
        return

    if compliance_result is None:
        return

    compliance_dict = compliance_result.to_dict()
    _tx = R.transaction_read() or {}
    _predictions = _tx.get('predicted_check_outcomes', {})
    if _predictions and compliance_result.check_results:
        for cr in compliance_result.check_results:
            check_id = cr.get("check_id")
            if check_id and check_id in _predictions:
                cr["predicted_pass"] = _predictions[check_id]
        try:
            from empirica.core.post_test.dynamic_thresholds import compute_check_brier
            check_brier = compute_check_brier(compliance_result.check_results)
            if check_brier:
                compliance_dict["check_brier"] = check_brier
        except Exception:
            pass
    result["compliance"] = compliance_dict


def _postflight_update_memory_hot_cache(session_id, resolved_project_path):
    """Update MEMORY.md hot cache, promote/demote eidetic facts. Non-fatal."""
    from pathlib import Path

    try:
        from empirica.core.memory_manager import update_hot_cache
        _mem_updated = update_hot_cache(
            session_id, project_path=resolved_project_path,
            db_path=str(Path(resolved_project_path) / '.empirica' / 'sessions' / 'sessions.db') if resolved_project_path else None,
        )
        if _mem_updated:
            logger.debug("Updated MEMORY.md hot cache at POSTFLIGHT")

        from empirica.core.memory_manager import promote_eidetic_to_memory
        _promo_db = _get_db_for_session(session_id)
        _promo_session = _promo_db.get_session(session_id)
        _promo_pid = _promo_session.get('project_id') if _promo_session else None
        _promo_db.close()
        _promoted = promote_eidetic_to_memory(project_id=_promo_pid, project_path=resolved_project_path)
        if _promoted:
            logger.debug(f"Promoted {len(_promoted)} eidetic facts to memory: {_promoted}")

        from empirica.core.memory_manager import demote_stale_memories, enforce_memory_md_cap
        _demoted = demote_stale_memories(project_path=resolved_project_path)
        if _demoted:
            logger.debug(f"Demoted {len(_demoted)} stale memory files: {_demoted}")
        _evicted = enforce_memory_md_cap(project_path=resolved_project_path)
        if _evicted:
            logger.debug(f"Evicted {_evicted} lines from MEMORY.md")
    except Exception as e:
        logger.debug(f"MEMORY.md hot cache update skipped: {e}")


def _build_postflight_result(
    session_id, postflight_confidence, internal_consistency, deltas,
    trajectory_issues, grounded_verification, sentinel_decision,
    compliance_result, compliance_error, postflight_grounded_vectors,
    postflight_grounded_rationale, vectors, resolved_project_path,
    postflight_coverage=None,
    goal_criteria=None,
):
    """Build the postflight result dict including compliance, three-vector, memory hot-cache.

    Returns result dict.
    """
    # Extract evidence_summary from grounded verification to surface
    # prominently — this is what the AI should attend to for calibration,
    # not the per-vector observation scores buried in the calibration dict.
    evidence_summary = None
    calibration_for_ai = None
    if grounded_verification:
        evidence_summary = grounded_verification.get('evidence_summary')
        # Strip _internal_* keys from AI-facing output.
        # These go to DB/breadcrumbs for trajectory tracking, not to the AI.
        # The AI should calibrate from evidence_summary + calibration_reflection,
        # not from per-vector divergence scores (Goodhart's Law).
        calibration_for_ai = {
            k: v for k, v in grounded_verification.items()
            if not k.startswith('_internal_')
        }

    result = {
        "ok": True,
        "session_id": session_id,
        "postflight_confidence": postflight_confidence,
        "internal_consistency": internal_consistency,
        "evidence_summary": evidence_summary,
        "deltas": deltas,
        "trajectory_issues": trajectory_issues if trajectory_issues else None,
        "calibration": calibration_for_ai,
        "sentinel": sentinel_decision.value if sentinel_decision else None,
    }

    _postflight_add_compliance_block(result, compliance_result, compliance_error)

    if postflight_grounded_vectors:
        result["three_vector"] = {
            "self_assessed": vectors,
            "grounded": postflight_grounded_vectors,
            "rationale_present": bool(postflight_grounded_rationale),
        }

    # Phase 2 T3: echo claimed agent self-coverage back to the AI.
    # Informative, not gating — the AI can see "95% confidence with 8%
    # file coverage" plainly so subsequent transactions self-correct.
    if postflight_coverage:
        result["coverage"] = postflight_coverage

    if goal_criteria:
        result["goal_criteria"] = goal_criteria

    _postflight_update_memory_hot_cache(session_id, resolved_project_path)

    return result


def _cortex_resolve_project_id(session_id: str) -> str:
    """Resolve project UUID from the session row for Cortex sync.

    The session row's project_id is the canonical source — set at session
    creation, validated and healed at session boundaries (post-compact +
    session-init), and pre-validated at POSTFLIGHT Stage 0 before this
    function ever runs. The DB-of-record is authoritative.

    Previous implementation read project.yaml from Path.cwd() and routed
    the UUID through resolve_project_id() (a CLI helper that calls
    sys.exit(1) on miss). That introduced (a) a CWD-dependent leak that
    misrouted to sibling-folder project.yaml files and (b) a SystemExit
    propagation path that escaped every `except Exception` wrapper above
    it. Both were exercised by #95 (pschwinger). Reading from the session
    row eliminates both failure modes.
    """
    if not session_id:
        return ""
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT project_id FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else ""
        finally:
            db.close()
    except Exception:
        return ""


def _cortex_format_rows(rows, table, key):
    """Format DB rows for a specific artifact table into sync-ready dicts."""
    if table == "project_findings":
        return [{"id": r["id"], "finding": r[key], "impact": r["impact"] or 0.5} for r in rows if r[key]]
    if table == "decisions":
        return [{"id": r["id"], "choice": r[key], "rationale": r["rationale"] or ""} for r in rows if r[key]]
    return [{"id": r["id"], "unknown": r[key]} for r in rows if r[key]]


def _cortex_extract_transaction_delta(session_id):
    """Extract this transaction's artifacts for Cortex sync. Returns dict."""
    _tx_delta = {}
    try:
        _tx_data = R.transaction_read()
        _tx_id = _tx_data.get('transaction_id', '') if _tx_data else ''
        if not _tx_id:
            return _tx_delta
        _sdb = _get_db_for_session(session_id)
        tables = [
            ("project_findings", "finding", "findings", ", impact"),
            ("project_unknowns", "unknown", "unknowns", ""),
            ("decisions", "choice", "decisions", ", rationale"),
        ]
        for _tbl, _key, _delta_key, extra_col in tables:
            _rows = _sdb.conn.execute(
                f"SELECT id, {_key}{extra_col} FROM {_tbl} WHERE transaction_id = ? LIMIT 20",
                (_tx_id,)
            ).fetchall()
            if _rows:
                _tx_delta[_delta_key] = _cortex_format_rows(_rows, _tbl, _key)
    except Exception:
        pass
    return _tx_delta


def _cortex_read_calibration_summary(project_path: str | None = None) -> dict:
    """Read calibration summary from .breadcrumbs.yaml. Returns dict.

    Reads from project_path/.breadcrumbs.yaml when provided, falling back
    to Path.cwd() only when project_path is unset (last-resort path,
    matches the resolved_project_path that POSTFLIGHT itself uses).
    Avoids the CWD-dependence that caused the multi-.empirica misroute.
    """
    from pathlib import Path

    try:
        import yaml as _yaml
        _root = Path(project_path) if project_path else Path.cwd()
        _bcf = _root / ".breadcrumbs.yaml"
        if _bcf.exists():
            with open(_bcf) as _bf:
                _bcd = _yaml.safe_load(_bf) or {}
            _gc = _bcd.get("grounded_calibration", {})
            if _gc:
                return {
                    "calibration_score": _gc.get("_internal_calibration_score", _gc.get("holistic_calibration_score", 0.5)),
                    "observations": _gc.get("observations", 0),
                    "grounded_coverage": _gc.get("grounded_coverage", 0),
                }
    except Exception:
        pass
    return {}


def _run_postflight_goal_criteria(session_id, transaction_id):
    """Evaluate active goals' success_criteria against POSTFLIGHT state.

    Bridge from goal-declared criteria → live POSTFLIGHT signal. Loads
    active criteria for the session, dispatches each to its registered
    evaluator (keyed on validation_method), persists is_met. Returns
    the goal_criteria response block, or None if nothing was evaluated.

    Empty EvidenceBundle is passed for G1 — SubtaskCompletionEvaluator
    queries the goal/task DB directly and doesn't need bundle items.
    G2's EvidenceMetricEvaluator will need bundle plumbing from the
    grounded-verification pipeline.
    """
    try:
        from empirica.core.post_test.collector import EvidenceBundle
        from empirica.core.post_test.criterion_evaluators import evaluate_goal_criteria

        empty_bundle = EvidenceBundle(session_id=session_id)
        block = evaluate_goal_criteria(
            session_id=session_id,
            evidence=empty_bundle,
            transaction_id=transaction_id,
        )
        return block if block.get("evaluated", 0) > 0 else None
    except Exception as e:
        logger.debug(f"Goal-criteria evaluation skipped: {e}")
        return None


def _run_postflight_cortex_sync(session_id, reasoning, resolved_project_path):
    """Push this transaction's artifacts to remote Cortex.

    Each POSTFLIGHT is a sync boundary -- artifacts flow to the
    cloud intelligence layer at the natural measurement cadence.
    """
    import os

    try:
        _cortex_url = os.environ.get('CORTEX_REMOTE_URL', '')
        _cortex_key = os.environ.get('CORTEX_API_KEY', '')
        if not (_cortex_url and _cortex_key):
            return

        import urllib.request

        _sync_pid = _cortex_resolve_project_id(session_id)
        _tx_delta = _cortex_extract_transaction_delta(session_id)
        _cal = _cortex_read_calibration_summary(resolved_project_path)

        _payload = json.dumps({
            "project_id": _sync_pid,
            "task_context": reasoning[:200] if reasoning else "",
            "calibration_summary": _cal,
            "delta": _tx_delta,
        }).encode("utf-8")

        _req = urllib.request.Request(
            f"{_cortex_url.rstrip('/')}/v1/sync",
            data=_payload,
            headers={"Authorization": f"Bearer {_cortex_key}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(_req, timeout=5)
        logger.debug("Cortex sync push at POSTFLIGHT boundary")
    except Exception:
        pass  # Non-fatal


def _postflight_publish_bus_event(session_id, transaction_id, vectors, deltas,
                                  postflight_confidence, internal_consistency):
    """Publish POSTFLIGHT_COMPLETE event on epistemic bus. Non-fatal."""
    try:
        from empirica.core.bus_persistence import wire_persistent_observers
        from empirica.core.epistemic_bus import EpistemicEvent, EventTypes, get_global_bus
        wire_persistent_observers(session_id=session_id)
        bus = get_global_bus()
        bus.publish(EpistemicEvent(
            event_type=EventTypes.POSTFLIGHT_COMPLETE, agent_id="claude-code",
            session_id=session_id,
            data={
                "transaction_id": transaction_id, "vectors": vectors,
                "deltas": deltas, "postflight_confidence": postflight_confidence,
                "internal_consistency": internal_consistency,
            },
        ))
    except Exception as e:
        logger.debug(f"Bus publish (POSTFLIGHT) failed (non-fatal): {e}")


def _postflight_print_project_context(session_id):
    """Print project context summary for next session. Non-fatal."""
    try:
        db = _get_db_for_session(session_id)
        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row and row['project_id']:
            breadcrumbs = db.bootstrap_project_breadcrumbs(row['project_id'], mode="session_start")
            db.close()
            if "error" not in breadcrumbs:
                print("\n📚 Project Context (for next session):")
                if breadcrumbs.get('findings'):
                    print(f"   Recent findings recorded: {len(breadcrumbs['findings'])}")
                if breadcrumbs.get('unknowns'):
                    unresolved = [u for u in breadcrumbs['unknowns'] if not u['is_resolved']]
                    if unresolved:
                        print(f"   Unresolved unknowns: {len(unresolved)}")
        else:
            db.close()
    except Exception:
        pass


def _postflight_format_human_output(result, session_id, vectors, reasoning,
                                     deltas, trajectory_issues, grounded_verification):
    """Print human-readable POSTFLIGHT output with project context."""
    if result['ok']:
        print("✅ POSTFLIGHT assessment submitted successfully")
        print(f"   Session: {session_id[:8]}...")
        print(f"   Vectors: {len(vectors)} submitted")
        print("   Storage: Database + Git Notes")
        if reasoning:
            print(f"   Reasoning: {reasoning[:80]}...")
        if deltas:
            print(f"   Learning deltas: {len(deltas)} vectors changed")
        if grounded_verification:
            cal_score = grounded_verification.get('calibration_score', 0)
            print(f"   Grounded calibration: {cal_score:.2f}")
            # Display evidence summary signals if available
            evidence_summary = grounded_verification.get('evidence_summary', {})
            signals = evidence_summary.get('signals', [])
            if signals:
                print("   Evidence signals:")
                for signal in signals:
                    print(f"     • {signal}")
        if trajectory_issues:
            print(f"\n⚠️  Trajectory issues detected: {len(trajectory_issues)}")
            for issue in trajectory_issues:
                print(f"   • {issue['pattern']}: {issue['description']}")
    else:
        print(f"❌ {result.get('message', 'Failed to submit POSTFLIGHT assessment')}")

    _postflight_print_project_context(session_id)


def _validate_postflight_preconditions(session_id: str) -> tuple[bool, str | None]:
    """Pre-mutation validation for POSTFLIGHT.

    Checks session row exists and resolves to a valid project context.
    Runs BEFORE any state mutation (transaction close, reflex write) so
    a precondition failure leaves the loop open and the user can fix
    context with project-switch.

    Returns (ok, error_msg). On success, error_msg is None.
    """
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT project_id FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return False, f"session {session_id[:8]} not found in project DB"
            project_id = row[0]
            if not project_id:
                return False, (
                    f"session {session_id[:8]} has no project_id — run "
                    "'empirica project-switch <project>' before POSTFLIGHT"
                )
            return True, None
        finally:
            db.close()
    except Exception as e:
        # Validation failure shouldn't itself block POSTFLIGHT — fail open.
        # The downstream stages will still gracefully handle their errors
        # via the soft-warn wrappers below.
        return True, f"precondition check skipped ({type(e).__name__}: {e})"


def _soft_run(stage_name: str, warnings: list, fn, *args, **kwargs):
    """Run a downstream POSTFLIGHT stage; collect failures as warnings.

    Stages 5-7 (bus, beliefs, storage, compliance, cortex sync) are
    informational — they enrich the result but their failures must not
    erase the reflex that already landed in stages 3-4. Pre-fix, an
    exception in any of them surfaced as exit-code-1 with persisted=false
    even though the loop had actually closed (ghost-success bug).

    Post-fix: catch, log, accumulate into warnings[].

    SystemExit is caught explicitly. Some library helpers (like
    `cli.utils.project_resolver.resolve_project_id`) call `sys.exit(1)`
    on miss instead of raising — and SystemExit derives from
    BaseException, not Exception. Without explicit handling, those
    sys.exit calls would walk straight through every `except Exception`
    above us and kill POSTFLIGHT, defeating the soft-stage contract.
    See #95 (pschwinger) for the repro.

    KeyboardInterrupt is intentionally NOT caught — it's a user signal
    to stop, and we should let it propagate through the whole call stack.
    """
    try:
        return fn(*args, **kwargs)
    except SystemExit as e:
        # Library code that called sys.exit(N) — treat as a soft failure
        # equivalent to a normal exception. Do NOT exit the process.
        warnings.append({
            "stage": stage_name,
            "error_type": "SystemExit",
            "error": f"library called sys.exit({e.code!r})",
        })
        logger.warning(f"POSTFLIGHT {stage_name} soft-failed: SystemExit({e.code!r})")
        return None
    except Exception as e:
        warnings.append({
            "stage": stage_name,
            "error_type": type(e).__name__,
            "error": str(e),
        })
        logger.warning(f"POSTFLIGHT {stage_name} soft-failed: {type(e).__name__}: {e}")
        return None


def handle_postflight_submit_command(args):
    """Handle postflight-submit command - AI-first with config file support.

    Pipeline restructure (#95 Issue 3):
      Stage 0: Pre-validation (no state mutation)
      Stages 1-4: Hard mutation (close transaction + write reflex)
      Stages 5-7: Soft mutation (downstream pipeline; failures collected as
                  warnings, never erase the reflex from stages 3-4)
    """
    try:
        from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

        # Stage 1: Parse and validate input
        parsed = _parse_postflight_input(args)
        session_id = parsed["session_id"]
        vectors = parsed["vectors"]
        reasoning = parsed["reasoning"]
        output_format = parsed["output_format"]

        # Stage 0: Pre-validation — fail BEFORE any state mutation
        precondition_ok, precondition_error = _validate_postflight_preconditions(session_id)
        if not precondition_ok:
            result = {
                "ok": False, "session_id": session_id,
                "message": f"POSTFLIGHT pre-validation failed: {precondition_error}",
                "persisted": False, "error": precondition_error,
                "loop_state": "open",  # unchanged — user can fix and retry
            }
            if output_format == 'json':
                print(json.dumps(result, indent=2))
            return None

        # warnings: collects soft-failures from stages 5-7
        warnings: list[dict] = []
        if precondition_error:  # validator skipped (not a fail) — log it
            warnings.append({
                "stage": "pre-validation",
                "error_type": "skipped",
                "error": precondition_error,
            })

        try:
            logger_instance = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

            uncertainty = vectors.get('uncertainty', 0.5)
            postflight_confidence = 1.0 - uncertainty
            completion = vectors.get('completion', 0.5)
            diff = abs(completion - postflight_confidence)
            internal_consistency = "good" if diff < 0.2 else ("moderate" if diff < 0.4 else "poor")

            # Stage 2: Deltas
            preflight_vectors, deltas, trajectory_issues = _calculate_postflight_deltas(
                logger_instance, vectors, parsed["preflight_session_id"]
            )

            # ─── HARD MUTATION (stages 3-4) ───
            # Stage 3: Close transaction
            tx_info = _close_postflight_transaction(session_id)
            resolved_project_path = tx_info["resolved_project_path"]

            # Stage 4: Checkpoint
            retrospective = _build_retrospective(session_id, tx_info["transaction_id"])
            postflight_coverage = parsed.get("coverage")
            checkpoint_id = logger_instance.add_checkpoint(
                phase="POSTFLIGHT", vectors=vectors,
                metadata={
                    "reasoning": reasoning, "task_summary": reasoning or "Task completed",
                    "postflight_confidence": postflight_confidence,
                    "internal_consistency": internal_consistency,
                    "deltas": deltas, "trajectory_issues": trajectory_issues,
                    "transaction_id": tx_info["transaction_id"],
                    "tool_call_count": tx_info["tool_call_count"],
                    "avg_turns_at_start": tx_info["avg_turns"],
                    "context_shifts": tx_info["context_shifts"] if tx_info["context_shifts"].get('unsolicited_prompts', 0) > 0 else None,
                    "entity_context": tx_info["entity_context"] or None,
                    "tool_trace": tx_info["tool_trace"] if tx_info["tool_trace"] else None,
                    "retrospective": retrospective if retrospective else None,
                    # Phase 2 T3 — agent self-coverage (paper section 4.1).
                    # Informative; not gating. Persisted alongside the
                    # retrospective so future PREFLIGHTs can recall it.
                    "coverage": postflight_coverage if postflight_coverage else None,
                }
            )

            # ─── SOFT MUTATION (stages 5-7) — failures become warnings ───
            # Stage 5: Bus + Sentinel
            _soft_run("bus_publish", warnings,
                _postflight_publish_bus_event,
                session_id, tx_info["transaction_id"], vectors, deltas,
                postflight_confidence, internal_consistency,
            )
            sentinel_decision = _soft_run("sentinel_hook", warnings,
                _invoke_sentinel_hook, "POSTFLIGHT", session_id, {
                    "vectors": vectors, "reasoning": reasoning,
                    "postflight_confidence": postflight_confidence,
                    "internal_consistency": internal_consistency,
                    "deltas": deltas, "trajectory_issues": trajectory_issues,
                    "checkpoint_id": checkpoint_id,
                },
            )

            # Stage 6: Beliefs + Grounded verification + Storage pipeline
            _soft_run("beliefs_export", warnings,
                _run_postflight_beliefs_and_exports, session_id, preflight_vectors, vectors,
            )
            grounded_verification = _soft_run("grounded_verification", warnings,
                _run_grounded_verification,
                session_id, vectors, tx_info["phase_tool_counts"],
                tx_info["work_context"], tx_info["work_type"], tx_info["transaction_id"],
                project_path=resolved_project_path,
            )
            goal_criteria_block = _soft_run("goal_criteria", warnings,
                _run_postflight_goal_criteria,
                session_id, tx_info["transaction_id"],
            )
            _soft_run("storage_pipeline", warnings,
                _run_postflight_storage_pipeline,
                session_id=session_id, vectors=vectors, deltas=deltas,
                reasoning=reasoning, grounded_verification=grounded_verification,
                postflight_confidence=postflight_confidence,
                checkpoint_id=checkpoint_id,
            )

            # Stage 7: Compliance + Result
            compliance_outcome = _soft_run("compliance_check", warnings,
                _run_postflight_compliance,
                session_id, tx_info["transaction_id"], tx_info["work_type"], resolved_project_path,
            )
            if compliance_outcome:
                compliance_result, compliance_error = compliance_outcome
            else:
                compliance_result, compliance_error = None, None

            result = _build_postflight_result(
                session_id=session_id, postflight_confidence=postflight_confidence,
                internal_consistency=internal_consistency, deltas=deltas,
                trajectory_issues=trajectory_issues, grounded_verification=grounded_verification,
                sentinel_decision=sentinel_decision, compliance_result=compliance_result,
                compliance_error=compliance_error,
                postflight_grounded_vectors=parsed["grounded_vectors"],
                postflight_grounded_rationale=parsed["grounded_rationale"],
                postflight_coverage=parsed.get("coverage"),
                vectors=vectors, resolved_project_path=resolved_project_path,
                goal_criteria=goal_criteria_block,
            )
            if retrospective:
                result["retrospective"] = retrospective
            if warnings:
                # Soft-failures: visible to AI without erasing the closed loop
                result["warnings"] = warnings

            _soft_run("cortex_sync", warnings,
                _run_postflight_cortex_sync, session_id, reasoning, resolved_project_path,
            )

        except Exception as e:
            logger.error(f"Failed to save postflight assessment: {e}")
            result = {
                "ok": False, "session_id": session_id,
                "message": f"Failed to save POSTFLIGHT assessment: {e!s}",
                "persisted": False, "error": str(e)
            }

        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            _postflight_format_human_output(
                result, session_id, vectors, reasoning,
                deltas if 'deltas' in dir() else {},
                trajectory_issues if 'trajectory_issues' in dir() else [],
                grounded_verification if 'grounded_verification' in dir() else None,
            )

        return None

    except Exception as e:
        handle_cli_error(e, "Postflight submit", getattr(args, 'verbose', False))
