"""CHECK phase — gate the noetic → praxic transition with vector + drift evidence."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from empirica.core.canonical.empirica_git.sentinel_hooks import SentinelDecision, SentinelHooks
from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error, parse_json_safely
from ._workflow_shared import (
    _auto_bootstrap,
    _build_retrospective,
    _build_weave_guidance,
    _check_bootstrap_status,
    _get_db_for_session,
    _invoke_sentinel_hook,
    _parse_workflow_input,
    _resolve_and_validate_session,
)

logger = logging.getLogger(__name__)


def _check_patterns_for_warnings(project_id, config_data, checkpoints, current_vectors, suggestions):
    """Check current approach against known failure patterns. Returns warnings or None."""
    if not project_id:
        return None
    try:
        from empirica.core.qdrant.pattern_retrieval import check_against_patterns

        approach = None
        if config_data:
            approach = config_data.get("approach") or config_data.get("reasoning")
        if not approach and checkpoints:
            approach = checkpoints[0].get("metadata", {}).get("reasoning")
        warnings = check_against_patterns(project_id, approach or "", current_vectors)
        if warnings and warnings.get("has_warnings"):
            for de in warnings.get("dead_end_matches", []):
                suggestions.append(
                    f"⚠️ Similar to dead end: {de.get('approach', '')[:50]}... (why: {de.get('why_failed', '')[:50]})"
                )
            if warnings.get("mistake_risk"):
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
            return (
                "proceed",
                "moderate",
                f"Readiness sufficient, but {unknowns_count} unknowns and drift ({drift:.2f}) suggest caution",
                [
                    "Readiness met - you may proceed",
                    f"Be aware: {unknowns_count} unknowns remain and drift is {drift:.2f}",
                ],
            )
        return (
            "proceed",
            "strong",
            f"Readiness strong, low drift ({drift:.2f}), {unknowns_count} unknowns",
            ["Evidence supports proceeding to action phase"],
        )
    if unknowns_count > 5 or drift > 0.3:
        return (
            "investigate",
            "strong",
            f"Readiness insufficient with {unknowns_count} unknowns and drift ({drift:.2f}) - investigation required",
            ["More investigation needed before proceeding", f"Address {unknowns_count} unknowns to increase readiness"],
        )
    return (
        "investigate",
        "moderate",
        f"Readiness insufficient, but only {unknowns_count} unknowns and drift ({drift:.2f}) - investigate to validate",
        ["Investigate further or recalibrate your assessment", "Evidence doesn't fully explain low readiness"],
    )


def _check_cmd_parse_inputs(args):
    """Parse CHECK command inputs from config/stdin/CLI flags.

    Returns dict with session_id, cycle, round_num, verbose, explicit_confidence,
    config_data, output_format.
    """
    config_data, output_format = _parse_workflow_input(args, "CHECK")

    session_id = getattr(args, "session_id", None) or (config_data.get("session_id") if config_data else None)
    cycle = getattr(args, "cycle", None) or (config_data.get("cycle") if config_data else None)
    round_num = getattr(args, "round", None) or (config_data.get("round") if config_data else None)
    verbose = getattr(args, "verbose", False) or (config_data.get("verbose", False) if config_data else False)
    explicit_confidence = config_data.get("confidence") if config_data else None

    return {
        "session_id": session_id,
        "cycle": cycle,
        "round_num": round_num,
        "verbose": verbose,
        "explicit_confidence": explicit_confidence,
        "config_data": config_data,
        "output_format": output_format,
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

    current_vectors = checkpoints[0].get("vectors", {})
    deltas = {}
    drift_sum = 0.0
    drift_count = 0

    for key in ["know", "uncertainty", "engagement", "impact", "completion"]:
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
        project_id = session_data.get("project_id") if session_data else None

        if project_id:
            findings_list = db.breadcrumbs.get_project_findings(project_id)
            unknowns_list = db.breadcrumbs.get_project_unknowns(project_id, resolved=False)
            findings = [{"finding": f.get("finding", ""), "impact": f.get("impact")} for f in findings_list]
            unknowns = [u.get("unknown", "") for u in unknowns_list]
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
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No PREFLIGHT found for session",
                        "hint": "Run PREFLIGHT first to establish baseline",
                    }
                )
            )
            sys.exit(1)

        baseline_vectors = preflight.get("vectors", preflight) if isinstance(preflight, dict) else preflight

        # 2. Compute drift
        checkpoints = git_logger.list_checkpoints(limit=1)
        current_vectors, drift, deltas = _check_cmd_compute_drift(baseline_vectors, checkpoints)

        # 3. Load evidence
        findings, unknowns, project_id = _check_cmd_load_evidence(db, session_id)
        findings_count = len(findings)
        unknowns_count = len(unknowns)
        uncertainty = current_vectors.get("uncertainty", 0.5)

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
            phase="CHECK",
            round_num=inputs["cycle"] or 1,
            vectors=current_vectors,
            metadata={
                "decision": decision,
                "suggestion_strength": strength,
                "drift": drift,
                "findings_count": findings_count,
                "unknowns_count": unknowns_count,
                "reasoning": reasoning,
                "transaction_id": check_transaction_id,
            },
        )

        # 6. Build result
        confidence_value = (
            inputs["explicit_confidence"] if inputs["explicit_confidence"] is not None else (1.0 - uncertainty)
        )
        result = {
            "ok": True,
            "session_id": session_id,
            "checkpoint_id": checkpoint_id,
            "decision": decision,
            "suggestion_strength": strength,
            "confidence": confidence_value,
            "drift_analysis": {
                "overall_drift": drift,
                "drift_level": drift_level,
                "baseline": baseline_vectors,
                "current": current_vectors,
                "deltas": deltas,
            },
            "evidence": {"findings_count": findings_count, "unknowns_count": unknowns_count},
            "investigation_progress": {
                "cycle": inputs["cycle"],
                "round": inputs["round_num"],
                "total_checkpoints": len(git_logger.list_checkpoints(limit=100)),
            },
            "recommendation": {
                "type": "suggestive",
                "message": reasoning,
                "suggestions": suggestions,
                "note": "This is an evidence-based suggestion. Override if task context warrants it.",
            },
            "pattern_warnings": None,
            "timestamp": time.time(),
        }

        if inputs["verbose"]:
            result["evidence"]["findings"] = findings
            result["evidence"]["unknowns"] = unknowns

        if inputs["output_format"] == "json":
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
        handle_cli_error(e, "CHECK", getattr(args, "verbose", False))


def _check_parse_inputs(args):
    """Parse and resolve CHECK inputs from config/stdin/CLI flags.

    Returns a dict with keys: session_id, vectors, decision, reasoning, cycle,
    output_format.
    """
    config_data, output_format = _parse_workflow_input(args, "CHECK")

    if config_data:
        session_id = config_data.get("session_id") or getattr(args, "session_id", None)
        vectors = config_data.get("vectors")
        decision = config_data.get("decision")
        reasoning = config_data.get("reasoning", "")
        config_data.get("approach", reasoning)
    else:
        session_id = args.session_id
        vectors = parse_json_safely(args.vectors) if isinstance(args.vectors, str) else args.vectors
        decision = args.decision
        reasoning = args.reasoning
        getattr(args, "approach", reasoning)
        output_format = getattr(args, "output", "human")
    cycle = getattr(args, "cycle", 1)

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
    if isinstance(_vectors_for_check, dict) and "vectors" in _vectors_for_check:
        _vectors_for_check = _vectors_for_check["vectors"]

    context_val = _vectors_for_check.get("context", 0.7) if isinstance(_vectors_for_check, dict) else 0.7
    uncertainty_val = _vectors_for_check.get("uncertainty", 0.3) if isinstance(_vectors_for_check, dict) else 0.3

    needs_reground = False
    reground_reason = None
    if not bootstrap_status.get("has_bootstrap"):
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

        if bootstrap_result.get("ok"):
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
        cursor.execute(
            """
            SELECT COUNT(*) FROM reflexes
            WHERE session_id = ? AND phase = 'CHECK'
        """,
            (session_id,),
        )
        check_count = cursor.fetchone()[0]
        round_num = check_count + 1

        if check_count > 0:
            cursor.execute(
                """
                SELECT engagement, know, do, context, clarity, coherence,
                       signal, density, state, change, completion, impact, uncertainty
                FROM reflexes
                WHERE session_id = ? AND phase = 'CHECK'
                ORDER BY timestamp DESC
                LIMIT 3
            """,
                (session_id,),
            )
            rows = cursor.fetchall()
            vector_names = [
                "engagement",
                "know",
                "do",
                "context",
                "clarity",
                "coherence",
                "signal",
                "density",
                "state",
                "change",
                "completion",
                "impact",
                "uncertainty",
            ]
            for row in rows:
                prev_vectors = {}
                for i, name in enumerate(vector_names):
                    if row[i] is not None:
                        prev_vectors[name] = row[i]
                if prev_vectors:
                    previous_check_vectors.append(prev_vectors)
        db.close()
    except Exception:
        round_num = getattr(args, "round", 1)

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

    if isinstance(vectors, dict) and "vectors" in vectors and isinstance(vectors.get("vectors"), dict):
        vectors = vectors["vectors"]

    if isinstance(vectors, dict) and any(k in vectors for k in ("foundation", "comprehension", "execution")):
        flat = {}
        for k in ("engagement", "uncertainty"):
            if k in vectors:
                flat[k] = vectors[k]
        flat.update(vectors.get("foundation") or {})
        flat.update(vectors.get("comprehension") or {})
        flat.update(vectors.get("execution") or {})
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
                cascade_profile = tx_data.get("cascade_profile")
        if cascade_profile and cascade_profile != "default":
            from empirica.config.threshold_loader import ThresholdLoader

            loader = ThresholdLoader.get_instance()
            if loader.load_profile(cascade_profile):
                profile_base_thresholds = {
                    "ready_know_threshold": loader.get("cascade.ready_know_threshold", 0.70),
                    "ready_uncertainty_threshold": loader.get("cascade.ready_uncertainty_threshold", 0.35),
                }
                logger.info(f"CHECK using cascade profile '{cascade_profile}' baselines: {profile_base_thresholds}")
    except Exception:
        pass

    # Calibration-config override (practice → global): explicit per-practice/global
    # tuning becomes the BASE gate; Brier still tightens on top. Fail-safe — a
    # missing/bad calibration.yaml leaves the default 0.35 untouched.
    try:
        from empirica.core.calibration_config import override_thresholds

        _cal = override_thresholds(R.project_path())
        if "ready_uncertainty" in _cal:
            ready_uncertainty_threshold = _cal["ready_uncertainty"]
            profile_base_thresholds = dict(profile_base_thresholds or {})
            profile_base_thresholds["ready_uncertainty_threshold"] = ready_uncertainty_threshold
    except Exception:
        pass

    # Dynamic thresholds from calibration history
    try:
        from empirica.core.post_test.dynamic_thresholds import compute_dynamic_thresholds

        dt_db = _get_db_for_session(session_id)
        dt_result = compute_dynamic_thresholds(
            ai_id="claude-code",
            db=dt_db,
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
        "recommend_proceed": False,
    }

    if len(previous_check_vectors) < 2:
        return diminishing_returns

    # Compute deltas between consecutive rounds (newest first)
    for i in range(len(previous_check_vectors)):
        if i == 0:
            prev_know = previous_check_vectors[i].get("know", 0.5)
            prev_uncertainty = previous_check_vectors[i].get("uncertainty", 0.5)
            delta_know = know - prev_know
            delta_uncertainty = uncertainty - prev_uncertainty
            diminishing_returns["know_deltas"].append(delta_know)
            diminishing_returns["uncertainty_deltas"].append(delta_uncertainty)
        elif i < len(previous_check_vectors):
            curr = previous_check_vectors[i - 1]
            prev = previous_check_vectors[i]
            delta_know = curr.get("know", 0.5) - prev.get("know", 0.5)
            delta_uncertainty = curr.get("uncertainty", 0.5) - prev.get("uncertainty", 0.5)
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
            diminishing_returns["reason"] = (
                f"know stagnant ({recent_know_deltas}), uncertainty not decreasing ({recent_uncertainty_deltas})"
            )

            # Per the meta-uncertainty design (2026-04-07): the gate is
            # uncertainty-only — uncertainty IS the meta confidence summary.
            if uncertainty <= 0.45:
                diminishing_returns["recommend_proceed"] = True
                diminishing_returns["reason"] += " - uncertainty acceptable, investigation plateaued"
            else:
                diminishing_returns["reason"] += " - uncertainty too high for proceed override"

    return diminishing_returns


def _check_gate_decision(vectors, ready_uncertainty_threshold, diminishing_returns, round_num, decision):
    """Compute the CHECK gate decision and apply autopilot enforcement.

    Gate semantic (2026-04-07): The CHECK gate uses META UNCERTAINTY ONLY.
    Uncertainty is the unified confidence summary -- it subsumes the AI's
    epistemic state across all 12 other vectors.

    NOTE: Use RAW vectors, not bias-corrected. Biases are INFORMATIONAL.

    Returns (decision, computed_decision, autopilot_mode, decision_binding).
    """

    uncertainty = vectors.get("uncertainty", 0.5)

    # Compute gate decision
    computed_decision = None
    if uncertainty <= ready_uncertainty_threshold:
        computed_decision = "proceed"
    elif diminishing_returns["recommend_proceed"]:
        computed_decision = "proceed"
        logger.info(f"CHECK decision override: proceed due to diminishing returns ({diminishing_returns['reason']})")
    elif round_num >= 5 and uncertainty <= 0.40:
        computed_decision = "proceed"
        logger.info(
            f"CHECK decision override: proceed due to max investigate rounds (round={round_num}, uncertainty={uncertainty:.2f})"
        )
    else:
        computed_decision = "investigate"

    # AUTOPILOT MODE
    autopilot_mode = os.getenv("EMPIRICA_AUTOPILOT_MODE", "false").lower() in ("true", "1", "yes")
    decision_binding = autopilot_mode

    if not decision or (autopilot_mode and decision != computed_decision):
        if autopilot_mode and decision and decision != computed_decision:
            logger.info(f"AUTOPILOT override: {decision} → {computed_decision} (autopilot enforcement)")
        decision = computed_decision
        logger.info(
            f"CHECK auto-computed decision: {decision} (uncertainty={uncertainty:.2f} vs threshold={ready_uncertainty_threshold:.2f}, gate uses META uncertainty only)"
        )

    return decision, computed_decision, autopilot_mode, decision_binding


def _check_store_and_publish(session_id, round_num, vectors, decision, reasoning, cycle):
    """Store CHECK checkpoint (3-layer) and publish bus event.

    Returns (checkpoint_id, check_transaction_id, confidence, gaps).
    """
    from empirica.core.canonical.git_enhanced_reflex_logger import GitEnhancedReflexLogger

    logger_instance = GitEnhancedReflexLogger(session_id=session_id, enable_git_notes=True)

    uncertainty = vectors.get("uncertainty", 0.5)
    confidence = 1.0 - uncertainty

    gaps = []
    for key, value in vectors.items():
        if isinstance(value, (int, float)) and value < 0.5:
            gaps.append(f"{key}: {value:.2f}")

    check_transaction_id = None
    try:
        check_transaction_id = R.transaction_id()
        if check_transaction_id is None:
            logger.warning(
                "R.transaction_id() returned None — CHECK will be stored without transaction_id. "
                "This may cause Sentinel to not find this CHECK. Check instance_projects/ state."
            )
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
            "transaction_id": check_transaction_id,
        },
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
        bus.publish(
            EpistemicEvent(
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
            )
        )
    except Exception as e:
        logger.debug(f"Bus publish (CHECK) failed (non-fatal): {e}")

    return checkpoint_id, check_transaction_id, confidence, gaps


def _check_apply_sentinel(
    session_id,
    decision,
    decision_binding,
    vectors,
    reasoning,
    confidence,
    gaps,
    cycle,
    round_num,
    checkpoint_id,
    check_transaction_id,
):
    """Invoke Sentinel hook and apply override if warranted.

    Returns (decision, sentinel_decision, sentinel_override).
    """
    sentinel_override = False
    sentinel_decision = _invoke_sentinel_hook(
        "CHECK",
        session_id,
        {
            "vectors": vectors,
            "decision": decision,
            "reasoning": reasoning,
            "confidence": confidence,
            "gaps": gaps,
            "cycle": cycle,
            "round": round_num,
            "checkpoint_id": checkpoint_id,
        },
    )

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
                    db2.conn.execute(
                        """
                        UPDATE reflexes SET reflex_data = json_set(reflex_data, '$.decision', ?)
                        WHERE id = (
                            SELECT id FROM reflexes
                            WHERE session_id = ? AND phase = 'CHECK'
                            AND transaction_id = ?
                            ORDER BY timestamp DESC LIMIT 1
                        )
                    """,
                        (new_decision, session_id, check_transaction_id),
                    )
                    db2.conn.commit()
                    db2.close()
                    logger.info(f"DB synced: CHECK decision updated to '{new_decision}'")
                except Exception as e:
                    logger.warning(f"Failed to sync sentinel override to DB: {e}")
    elif sentinel_decision and decision_binding:
        logger.info(
            f"Autopilot binding active - Sentinel override blocked (sentinel wanted: {sentinel_decision.value})"
        )

    return decision, sentinel_decision, sentinel_override


def _check_auto_checkpoint(session_id, vectors, decision, gaps, cycle, round_num):
    """Create git checkpoint if uncertainty > 0.5 (risky decision).

    Non-fatal — failures are logged and swallowed.
    """
    import json
    import subprocess

    uncertainty = vectors.get("uncertainty", 0.5)
    if uncertainty > 0.5:
        try:
            subprocess.run(
                [
                    "empirica",
                    "checkpoint-create",
                    "--session-id",
                    session_id,
                    "--phase",
                    "CHECK",
                    "--round",
                    str(round_num),
                    "--metadata",
                    json.dumps(
                        {
                            "auto_checkpoint": True,
                            "reason": "risky_decision",
                            "uncertainty": uncertainty,
                            "decision": decision,
                            "gaps": gaps,
                            "cycle": cycle,
                            "round": round_num,
                        }
                    ),
                ],
                capture_output=True,
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"Auto-checkpoint after CHECK (uncertainty > 0.5) failed (non-fatal): {e}")


def _check_create_snapshot(session_id, vectors, decision, reasoning, round_num, checkpoint_id):
    """Capture CHECK phase vectors as an epistemic snapshot for calibration.

    Returns snapshot_id or None on failure.
    """
    try:
        from empirica.data.epistemic_snapshot import ContextSummary
        from empirica.data.snapshot_provider import EpistemicSnapshotProvider

        uncertainty = vectors.get("uncertainty", 0.5)
        db = _get_db_for_session(session_id)
        snapshot_provider = EpistemicSnapshotProvider()

        check_confidence = 1.0 - uncertainty
        context_summary = ContextSummary(
            semantic={"phase": "CHECK", "decision": decision, "confidence": check_confidence},
            narrative=reasoning or f"CHECK round {round_num}: {decision}",
            evidence_refs=[checkpoint_id] if checkpoint_id else [],
        )

        snapshot = snapshot_provider.create_snapshot_from_session(
            session_id=session_id,
            context_summary=context_summary,
            cascade_phase="CHECK",
            domain_vectors={"round": round_num, "decision": decision} if round_num else None,
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


def _check_run_blindspot_scan(result, decision, session_id, bootstrap_result, bootstrap_status):
    """Run negative-space inference on knowledge topology.

    Modifies result in-place. Returns updated decision.
    """
    try:
        from empirica_prediction.blindspots.predictor import BlindspotPredictor  # pyright: ignore[reportMissingImports]

        project_id = (bootstrap_result or {}).get("project_id") or bootstrap_status.get("project_id")
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
                        "reason": f"{bs_report.critical_count} critical blindspot(s) detected",
                    }
                    decision = "investigate"
                    result["decision"] = decision

                logger.info(
                    f"Blindspot scan: {len(bs_report.predictions)} predictions, "
                    f"uncertainty_adj={bs_report.uncertainty_adjustment}"
                )
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
            check_project_id,
            reasoning or "",
            vectors=vectors,
            include_findings=True,
            include_eidetic=True,
            include_goals=True,
            include_assumptions=True,
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
                        {
                            "rule": c["rule_name"],
                            "type": c["constraint_type"],
                            "violations": c["violation_count"],
                            "description": c["description"],
                        }
                        for c in constraints[:5]
                    ]
                    if constraints
                    else [],
                }
        finally:
            codebase_db.close()
    except Exception as e:
        logger.debug(f"Codebase context injection skipped: {e}")


def _check_enrich_context(result, bootstrap_result, bootstrap_status, vectors, reasoning):
    """Enrich result with pattern retrieval and codebase model context.

    Modifies result in-place.
    """
    check_project_id = (bootstrap_result or {}).get("project_id") or bootstrap_status.get("project_id")
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
                    'before POSTFLIGHT: empirica finding-log --finding "..." --impact 0.5'
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


def _check_format_output(output_format, result, session_id, decision, cycle, vectors, reasoning):
    """Format and print CHECK output in JSON or human-readable format."""
    import json

    if output_format == "json":
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
        _, ready_uncertainty_threshold, dynamic_thresholds_info = _check_load_dynamic_thresholds(session_id)

        # Stage 6: Detect diminishing returns across rounds
        know = vectors.get("know", 0.5)
        uncertainty = vectors.get("uncertainty", 0.5)
        diminishing_returns = _check_detect_diminishing_returns(previous_check_vectors, know, uncertainty)

        # Stage 7: Compute gate decision (proceed/investigate) + autopilot
        decision, computed_decision, _, decision_binding = _check_gate_decision(
            vectors, ready_uncertainty_threshold, diminishing_returns, round_num, decision
        )

        # Stage 8: Store checkpoint + publish bus event (inner try for storage errors)
        try:
            checkpoint_id, check_transaction_id, confidence, gaps = _check_store_and_publish(
                session_id, round_num, vectors, decision, reasoning, cycle
            )

            # NOTE: Bayesian belief updates during CHECK were REMOVED (2026-01-21)
            # Calibration now uses vector_trajectories table.

            # Stage 9: Sentinel hook + override
            decision, sentinel_decision, sentinel_override = _check_apply_sentinel(
                session_id,
                decision,
                decision_binding,
                vectors,
                reasoning,
                confidence,
                gaps,
                cycle,
                round_num,
                checkpoint_id,
                check_transaction_id,
            )

            # Stage 10: Auto-checkpoint for risky decisions
            _check_auto_checkpoint(session_id, vectors, decision, gaps, cycle, round_num)

            # Stage 11: Epistemic snapshot
            _check_create_snapshot(session_id, vectors, decision, reasoning, round_num, checkpoint_id)

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
                    "brier_reliability": dynamic_thresholds_info.get("brier_reliability")
                    if dynamic_thresholds_info
                    else None,
                    "threshold_inflation": dynamic_thresholds_info.get("threshold_inflation")
                    if dynamic_thresholds_info
                    else None,
                    "diminishing_returns": diminishing_returns.get("detected", False),
                },
                "sentinel": {
                    "decision": sentinel_decision.value if sentinel_decision else None,
                    "override_applied": sentinel_override,
                }
                if SentinelHooks.is_enabled() and sentinel_override
                else None,
            }

            # Stage 13: Blindspot scan (may override decision)
            decision = _check_run_blindspot_scan(result, decision, session_id, bootstrap_result, bootstrap_status)

            # Stage 14: Pattern retrieval + codebase context
            _check_enrich_context(result, bootstrap_result, bootstrap_status, vectors, reasoning)

            # Stage 15: Praxic reminders + weave guidance (only when proceeding)
            if decision == "proceed":
                result["praxic_reminders"] = _check_build_praxic_reminders(session_id, check_transaction_id)
                # Gated Artifact-Graph map, work-stream 2 (schema-injection): give
                # the log-artifacts node/relation vocabulary at the gate so weaving
                # is cheap (no more guessing the shape / unknown-relation errors).
                result["weave_guidance"] = _build_weave_guidance()

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
                "error": str(e),
            }

        # Stage 16: Format output
        _check_format_output(output_format, result, session_id, decision, cycle, vectors, reasoning)

        return None

    except Exception as e:
        handle_cli_error(e, "Check submit", getattr(args, "verbose", False))
