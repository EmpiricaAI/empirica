"""POSTFLIGHT phase — close the measurement window, compute deltas, run the
storage pipeline (Qdrant + breadcrumbs + Cortex + global learnings + snapshots),
run grounded verification, evaluate goal criteria, run compliance loop."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from empirica.config.path_resolver import resolve_session_db_path
from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error, parse_json_safely
from ._workflow_shared import (
    _build_retrospective,
    _extract_all_vectors,
    _get_db_for_session,
    _invoke_sentinel_hook,
    _parse_workflow_input,
    _resolve_and_validate_session,
    _soft_run,
)

logger = logging.getLogger(__name__)

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

def _cortex_resolve_project_metadata(session_id: str) -> dict:
    """Resolve {project_id, name, repo_url} for the session's project.

    Used to enrich the Cortex /v1/sync payload so Cortex's auto-create path
    (triggered when /v1/sync references an unknown project_id) populates
    proper name + repo_url instead of falling back to name=<UUID>. Without
    this enrichment, every POSTFLIGHT for a fresh project seeds Cortex with
    UUID-named rows that bulk-register later can't override (the existing
    idempotent register_or_get_project skips already-existing IDs).

    Returns empty dict on any miss — caller treats missing fields as
    "Cortex will use whatever default it has", which preserves backward-
    compat for older Cortex versions that don't honor the extra fields.
    """
    if not session_id:
        return {}
    try:
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT p.id, p.name, p.repos "
                "FROM sessions s JOIN projects p ON s.project_id = p.id "
                "WHERE s.session_id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if not row:
                return {}
            project_id, name, repos_json = row
            repo_url = None
            if repos_json:
                try:
                    repos = json.loads(repos_json)
                    if repos and isinstance(repos, list):
                        raw = repos[0]
                        if raw.endswith(".git"):
                            raw = raw[:-4]
                        repo_url = raw
                except (json.JSONDecodeError, AttributeError):
                    pass
            return {"project_id": project_id, "name": name, "repo_url": repo_url}
        finally:
            db.close()
    except Exception:
        return {}


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

def _extract_evidence_bundle(grounded_verification):
    """Pull a real EvidenceBundle out of the grounded_verification result.

    The bundle lives at grounded_verification['_internal_phases'][phase]
    ['_internal_bundle']. Prefers praxic > combined > noetic since praxic
    has the broadest evidence (code_quality, git, etc). Returns None if
    no bundle is available — caller falls back to empty bundle.
    """
    if not grounded_verification:
        return None
    phases = grounded_verification.get('_internal_phases', {})
    for phase_name in ('praxic', 'combined', 'noetic'):
        phase = phases.get(phase_name)
        if phase and phase.get('_internal_bundle') is not None:
            return phase['_internal_bundle']
    return None

def _run_postflight_goal_criteria(session_id, transaction_id, evidence_bundle=None):
    """Evaluate active goals' success_criteria against POSTFLIGHT state.

    Bridge from goal-declared criteria → live POSTFLIGHT signal. Loads
    active criteria for the session, dispatches each to its registered
    evaluator (keyed on validation_method), persists is_met. Returns
    the goal_criteria response block, or None if nothing was evaluated.

    `evidence_bundle` should be the bundle collected during grounded
    verification (extracted via _extract_evidence_bundle). When None or
    when grounded_verification failed, an empty bundle is used —
    quality_gate evaluators that need named metrics will skip cleanly.
    """
    try:
        from empirica.core.post_test.collector import EvidenceBundle
        from empirica.core.post_test.criterion_evaluators import evaluate_goal_criteria

        bundle = evidence_bundle if evidence_bundle is not None else EvidenceBundle(
            session_id=session_id
        )
        block = evaluate_goal_criteria(
            session_id=session_id,
            evidence=bundle,
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

    try:
        from empirica.config.credentials_loader import get_credentials_loader
        _cfg = get_credentials_loader().get_cortex_config()
        _cortex_url = _cfg.get("url") or ""
        _cortex_key = _cfg.get("api_key") or ""
        if not (_cortex_url and _cortex_key):
            return

        import urllib.request

        _meta = _cortex_resolve_project_metadata(session_id)
        _sync_pid = _meta.get("project_id") or _cortex_resolve_project_id(session_id)
        _tx_delta = _cortex_extract_transaction_delta(session_id)
        _cal = _cortex_read_calibration_summary(resolved_project_path)

        _body = {
            "project_id": _sync_pid,
            "task_context": reasoning[:200] if reasoning else "",
            "calibration_summary": _cal,
            "delta": _tx_delta,
        }
        # Enrich with name + repo_url so Cortex's auto-create path on
        # unknown project_ids gets proper metadata instead of name=<UUID>.
        # Fix for the bulk-register / postflight-sync contamination bug
        # (v0.7.8 extension handoff EC-2).
        if _meta.get("name"):
            _body["name"] = _meta["name"]
        if _meta.get("repo_url"):
            _body["repo_url"] = _meta["repo_url"]
        _payload = json.dumps(_body).encode("utf-8")

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
                _extract_evidence_bundle(grounded_verification),
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
