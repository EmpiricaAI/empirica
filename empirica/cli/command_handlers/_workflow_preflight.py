"""PREFLIGHT phase — open the measurement window, retrieve patterns, surface
behavioral feedback from prior transactions."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error, parse_json_safely
from ..validation import PreflightInput, safe_validate
from ._workflow_shared import (
    _build_noetic_guidance,
    _build_voice_guidance,
    _extract_all_vectors,
    _get_db_for_session,
    _invoke_sentinel_hook,
    _parse_workflow_input,
    _remap_trajectory_summary,
    _resolve_and_validate_session,
)

logger = logging.getLogger(__name__)


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
        retrospective_reason = getattr(validated, 'retrospective_reason', None)
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
        retrospective_reason = getattr(args, 'retrospective_reason', None)

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
        "retrospective_reason": retrospective_reason,
        "output_format": output_format,
    }

def _preflight_check_unclosed_transaction():
    """Check for unclosed transaction and return warning dict or None.

    Auto-closing would poison vector states (fabricated POSTFLIGHT vectors),
    so we warn but don't block.
    """

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
        SELECT reflex_data FROM reflexes
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
        suggestions.append("Commit per task — don't batch to end")

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
        # grounded_verifications has no project_id column — join through sessions.
        cursor.execute("""
            SELECT gv.overall_calibration_score
            FROM grounded_verifications gv
            JOIN sessions s ON gv.session_id = s.session_id
            WHERE gv.ai_id = ? AND s.project_id = ?
            AND gv.overall_calibration_score IS NOT NULL
            AND gv.overall_calibration_score > 0
            ORDER BY gv.created_at DESC LIMIT 10
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

# Work types where zero artifacts is expected, not a discipline gap. `release`
# is a scripted mechanical pipeline (all evidence is excluded from its
# calibration anyway). Extend deliberately.
_RETROSPECTIVE_GATE_EXEMPT_WORK_TYPES = frozenset({'release'})

def _feedback_count_untriaged_notes(cursor, session_id, pf_meta):
    """Count untriaged scratchpad notes for the previous transaction.

    Scoped to pf_meta's transaction_id (the transaction that just POSTFLIGHTed)
    when available, else the session. Tolerates the notes table not existing on
    older DBs. Returns 0 on any error.
    """
    try:
        prev_tx = (pf_meta or {}).get('transaction_id')
        if prev_tx:
            row = cursor.execute(
                "SELECT COUNT(*) FROM notes WHERE session_id = ? AND "
                "transaction_id = ? AND triaged = 0",
                (session_id, prev_tx),
            ).fetchone()
        else:
            row = cursor.execute(
                "SELECT COUNT(*) FROM notes WHERE session_id = ? AND triaged = 0",
                (session_id,),
            ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def _feedback_compute_retrospective_gate(pf_meta, retrospective_reason,
                                         untriaged_note_count=0):
    """The retrospective soft-gate (Piece 2, Part C).

    A breather between transactions. Fires at PREFLIGHT ONLY on the narrow,
    high-signal pattern: the previous transaction made substantive praxic tool
    calls but logged ZERO epistemic artifacts, on a non-mechanical work_type.
    That combination means real work happened with nothing recorded — invisible
    to grounded calibration.

    Note-aware: scratchpad notes-to-self earn partial credit (intent WAS
    captured, just not yet structured). With notes present the gate softens to
    "promote your notes"; with 0 artifacts AND 0 notes it is the strongest
    "logged literally nothing" signal.

    Deliberately NOT generic PREFLIGHT nagging (a prior decision rejected that):
    the trigger is specific, it is SOFT (a response field, never a hard block),
    it is env-toggleable (EMPIRICA_RETROSPECTIVE_GATE=false), and it is cleared
    either by logging the missed artifacts or by passing `retrospective_reason`
    in this PREFLIGHT to acknowledge.

    Returns a gate dict, or None when it should not fire.
    """
    if os.environ.get('EMPIRICA_RETROSPECTIVE_GATE', 'true').lower() != 'true':
        return None
    if not pf_meta:
        return None

    work_type = pf_meta.get('work_type')
    # Unknown work_type can't be judged; exempt mechanical pipelines.
    if not work_type or work_type in _RETROSPECTIVE_GATE_EXEMPT_WORK_TYPES:
        return None

    phase_tool_counts = pf_meta.get('phase_tool_counts') or {}
    praxic_calls = phase_tool_counts.get('praxic_tool_calls', 0) or 0

    retro = pf_meta.get('retrospective') or {}
    counts = retro.get('artifact_counts') or {}
    artifact_total = sum(v for v in counts.values() if isinstance(v, (int, float)))

    # The high-signal pattern: real praxic activity, nothing logged.
    if praxic_calls <= 0 or artifact_total > 0:
        return None

    note_count = untriaged_note_count or 0
    gate = {
        "trigger": (
            f"Previous transaction made {praxic_calls} praxic tool call(s) "
            f"(work_type={work_type}) but logged 0 epistemic artifacts. "
            "Substantive work with no findings/decisions/dead-ends/mistakes is "
            "invisible to grounded calibration."
        ),
        "soft": True,
        # Notes earn partial credit: 0 artifacts + 0 notes is the strongest
        # "logged literally nothing" signal; notes mean intent was captured.
        "severity": "strong" if note_count == 0 else "soft",
        "untriaged_note_count": note_count,
        "env_toggle": "Set EMPIRICA_RETROSPECTIVE_GATE=false to disable.",
    }
    if retrospective_reason:
        gate["acknowledged"] = True
        gate["retrospective_reason"] = retrospective_reason
        gate["breather"] = "Acknowledged — proceeding. Reason recorded."
    elif note_count > 0:
        gate["acknowledged"] = False
        gate["breather"] = (
            f"You left {note_count} untriaged note(s)-to-self last transaction "
            "but logged 0 structured artifacts. Promote the keepers to "
            "findings/decisions/goals (then `empirica note --clear`), or pass "
            "retrospective_reason to acknowledge."
        )
    else:
        gate["acknowledged"] = False
        gate["breather"] = (
            "Take a breather before continuing: log what the last transaction "
            "learned — a finding, decision, dead-end, or mistake (empirica "
            "finding-log / decision-log / deadend-log / mistake-log); they "
            "attach to the prior transaction. If there was genuinely nothing "
            "to record, pass retrospective_reason in this PREFLIGHT to "
            "acknowledge and clear."
        )
    return gate

def _preflight_collect_behavioral_feedback(db, session_id, ai_id, project_id,
                                           retrospective_reason=None):
    """Pull discipline observations from last POSTFLIGHT.

    Vectors are beliefs about epistemic state -- deterministic services inform
    work discipline, not vector adjustments. The feedback drives work decisions
    (more noetic? another transaction? better artifact discipline?) not scores.

    Returns feedback dict or None.
    """

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

        # 4. Retrospective soft-gate — breather on real-work-zero-artifacts.
        #    Note-aware: count the prior transaction's untriaged notes (partial
        #    credit — intent captured even if not yet structured).
        note_count = _feedback_count_untriaged_notes(cursor, session_id, pf_meta)
        gate = _feedback_compute_retrospective_gate(
            pf_meta, retrospective_reason, untriaged_note_count=note_count
        )
        if gate:
            if previous_transaction_feedback is None:
                previous_transaction_feedback = {}
            previous_transaction_feedback["retrospective_gate"] = gate

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
        # sessions has no updated_at column; start_time is the ISO-8601 row time.
        cursor.execute("""
            SELECT MAX(start_time) FROM sessions
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
                db, session_id, cal["ai_id"], cal["project_id"],
                retrospective_reason=parsed.get("retrospective_reason"),
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
