"""
Epistemic trajectory tracking: Store and query learning deltas (PREFLIGHT → POSTFLIGHT).

This module enables:
- Pattern recognition across sessions
- Calibration training data generation
- Multi-agent learning sharing
- Skill gap early detection
"""

from __future__ import annotations

import json
from typing import Any

from empirica.data.session_database import SessionDatabase


def compute_deltas(preflight: dict[str, Any], postflight: dict[str, Any]) -> dict[str, float]:
    """
    Compute epistemic vector deltas between PREFLIGHT and POSTFLIGHT.

    Args:
        preflight: PREFLIGHT vectors {engagement, foundation, comprehension, execution, uncertainty}
        postflight: POSTFLIGHT vectors (same structure)

    Returns:
        Flat dict of deltas {engagement: +0.1, know: +0.25, uncertainty: -0.2, ...}
    """
    deltas = {}

    # Top-level vectors
    for key in ["engagement", "uncertainty"]:
        pre_val = preflight.get(key, 0.0)
        post_val = postflight.get(key, 0.0)
        deltas[key] = post_val - pre_val

    # Nested vectors (foundation, comprehension, execution)
    for category in ["foundation", "comprehension", "execution"]:
        pre_cat = preflight.get(category, {})
        post_cat = postflight.get(category, {})

        for sub_key in pre_cat:
            pre_val = pre_cat.get(sub_key, 0.0)
            post_val = post_cat.get(sub_key, 0.0)
            deltas[sub_key] = post_val - pre_val

    return deltas


def flatten_vectors(vectors: dict[str, Any]) -> dict[str, float]:
    """
    Flatten nested epistemic vectors to single-level dict for Qdrant payload.

    Args:
        vectors: {engagement, foundation: {know, do, context}, ...}

    Returns:
        {engagement, know, do, context, clarity, ...}
    """
    flat = {}

    for key, value in vectors.items():
        if isinstance(value, dict):
            # Nested category - flatten
            flat.update(value)
        else:
            # Top-level scalar
            flat[key] = value

    return flat


def extract_trajectory(session_id: str, db: SessionDatabase) -> dict[str, Any] | None:
    """
    Extract complete epistemic trajectory for a session from SQLite.

    Args:
        session_id: Session UUID
        db: SessionDatabase instance

    Returns:
        Complete trajectory data or None if PREFLIGHT/POSTFLIGHT missing
    """
    # Get session metadata
    session = db.get_session(session_id)
    if not session:
        return None

    # Get PREFLIGHT assessment
    reflexes = db.get_vectors_by_phase(session_id=session_id, phase="PREFLIGHT")
    if not reflexes:
        return None
    preflight = reflexes[-1]  # Latest PREFLIGHT

    # Get POSTFLIGHT assessment
    reflexes = db.get_vectors_by_phase(session_id=session_id, phase="POSTFLIGHT")
    if not reflexes:
        return None
    postflight = reflexes[-1]  # Latest POSTFLIGHT

    # Parse JSON vectors
    try:
        pre_vectors = json.loads(preflight["vectors_json"])
        post_vectors = json.loads(postflight["vectors_json"])
    except (json.JSONDecodeError, KeyError):
        return None

    # Compute deltas
    deltas = compute_deltas(pre_vectors, post_vectors)

    # Get mistakes count for this session
    mistakes = db.conn.execute("SELECT COUNT(*) FROM mistakes_made WHERE session_id = ?", (session_id,)).fetchone()[0]

    # Check if investigation phase happened (any CHECK gates)
    check_count = db.conn.execute(
        "SELECT COUNT(*) FROM reflexes WHERE session_id = ? AND phase = 'CHECK'", (session_id,)
    ).fetchone()[0]

    # Build complete trajectory
    trajectory = {
        "session_id": session_id,
        "ai_id": session["ai_id"],
        "timestamp": postflight["timestamp"],
        "task_description": postflight.get("reasoning", "No description"),
        # Flattened vectors for filtering
        "preflight": flatten_vectors(pre_vectors),
        "postflight": flatten_vectors(post_vectors),
        "deltas": deltas,
        # Calibration metadata
        "calibration_accuracy": postflight.get("calibration_accuracy", "unknown"),
        "investigation_phase": check_count > 0,
        "mistakes_count": mistakes,
        # Outcome
        "completion": post_vectors.get("execution", {}).get("completion", 0.0),
        "impact": post_vectors.get("execution", {}).get("impact", 0.0),
        # Combined reasoning for embedding
        "reasoning_combined": f"{preflight.get('reasoning', '')} {postflight.get('reasoning', '')}",
    }

    return trajectory


def store_trajectory(project_id: str, session_id: str, db: SessionDatabase) -> bool:
    """
    Extract and store epistemic trajectory to Qdrant.

    Args:
        project_id: Project UUID
        session_id: Session UUID
        db: SessionDatabase instance

    Returns:
        True if stored successfully, False otherwise
    """
    from empirica.core.qdrant.vector_store import upsert_epistemics

    trajectory = extract_trajectory(session_id, db)
    if not trajectory:
        return False

    # Format for Qdrant
    item = {
        "id": f"session_{session_id}",
        "text": trajectory["reasoning_combined"],
        "metadata": {k: v for k, v in trajectory.items() if k != "reasoning_combined"},
    }

    try:
        upsert_epistemics(project_id, [item])
        return True
    except Exception as e:
        print(f"Failed to store trajectory: {e}")
        return False


def search_trajectories(
    project_id: str,
    query: str,
    min_learning_delta: float | None = None,
    calibration_quality: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Search epistemic learning trajectories with optional filters.

    Args:
        project_id: Project UUID
        query: Semantic query (e.g., "OAuth2 authentication")
        min_learning_delta: Minimum know delta (e.g., 0.2 for high learning)
        calibration_quality: Filter by "good", "fair", or "poor"
        limit: Max results

    Returns:
        List of trajectory matches with scores
    """
    import warnings

    # Nothing in the package calls it; documented as an API, so it gets a release of notice (goal 95c3ee16).
    warnings.warn(
        "search_trajectories is deprecated and goes away in 1.18.0; use `empirica project-search`",
        DeprecationWarning,
        stacklevel=2,
    )
    from empirica.core.qdrant.vector_store import search_epistemics

    # Build filters (simplified for now - can extend)
    filters = {}
    if min_learning_delta is not None:
        filters["deltas.know"] = {"$gte": min_learning_delta}
    if calibration_quality:
        filters["calibration_accuracy"] = calibration_quality

    return search_epistemics(project_id, query, filters=filters, limit=limit)
