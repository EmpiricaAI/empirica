# pyright: reportUnusedImport=false
"""
Qdrant vector store for Empirica projects — backward-compatible re-export shim.

All implementations have been modularized into sub-modules:
  connection.py      - Client, availability, embedding infrastructure
  collections.py     - Collection naming, initialization, migration
  memory.py          - Core memory embed/upsert/search
  epistemics_store.py - Epistemic learning trajectories
  global_sync.py     - Cross-project knowledge aggregation
  eidetic.py         - Stable facts with confidence scoring
  episodic.py        - Session narratives with temporal decay
  goals.py           - Goal/subtask semantic search
  calibration.py     - Grounded verification & trajectory
  intent_layer.py    - Assumptions, decisions, intent edges
  decay.py           - Confidence decay, staleness, urgency

For new code, import directly from sub-modules.
This shim re-exports everything for backward compatibility.
"""

# --- Connection infrastructure ---
# --- Calibration ---
from empirica.core.qdrant.calibration import (  # noqa: F401
    embed_calibration_trajectory,
    embed_grounded_verification,
    search_calibration_patterns,
)

# --- Collection naming + init ---
from empirica.core.qdrant.collections import (  # noqa: F401
    _assumptions_collection,
    _calibration_collection,
    _decisions_collection,
    _docs_collection,
    _eidetic_collection,
    _episodic_collection,
    _epistemics_collection,
    _global_eidetic_collection,
    _global_learnings_collection,
    _goals_collection,
    _intents_collection,
    _memory_collection,
    cleanup_empty_collections,
    get_collection_info,
    init_collections,
    init_global_collection,
    recreate_collection,
    recreate_global_collections,
    recreate_project_collections,
)
from empirica.core.qdrant.connection import (  # noqa: F401
    _check_qdrant_available,
    _get_embedding_safe,
    _get_qdrant_client,
    _get_qdrant_imports,
    _get_vector_size,
    _rest_search,
    _service_url,
)

# --- Decay & lifecycle ---
from empirica.core.qdrant.decay import (  # noqa: F401
    apply_staleness_signal,
    auto_sync_session_to_global,
    decay_eidetic_by_finding,
    decay_eidetic_fact,
    propagate_lesson_confidence_to_qdrant,
    update_assumption_urgency,
)

# --- Eidetic memory ---
from empirica.core.qdrant.eidetic import (  # noqa: F401
    confirm_eidetic_fact,
    embed_eidetic,
    search_eidetic,
)

# --- Episodic memory ---
from empirica.core.qdrant.episodic import (  # noqa: F401
    create_session_episode,
    embed_episodic,
    search_episodic,
)

# --- Epistemic trajectories ---
from empirica.core.qdrant.epistemics_store import (  # noqa: F401
    search_epistemics,
    upsert_epistemics,
)

# --- Global learnings ---
from empirica.core.qdrant.global_sync import (  # noqa: F401
    embed_dead_end_with_branch_context,
    embed_to_global,
    search_cross_project,
    search_global,
    search_global_dead_ends,
    search_similar_dead_ends,
    sync_high_impact_to_global,
    sync_lessons_to_global,
)

# --- Goals & subtasks ---
from empirica.core.qdrant.goals import (  # noqa: F401
    embed_goal,
    embed_subtask,
    search_goals,
    sync_goals_to_qdrant,
    update_goal_status,
)

# --- Intent layer ---
from empirica.core.qdrant.intent_layer import (  # noqa: F401
    embed_assumption,
    embed_decision,
    embed_intent_edge,
    search_assumptions,
    search_decisions,
    search_intents,
)

# --- Core memory operations ---
from empirica.core.qdrant.memory import (  # noqa: F401
    embed_single_memory_item,
    search,
    upsert_docs,
    upsert_memory,
)

# --- Rebuild from DB ---
from empirica.core.qdrant.rebuild import (  # noqa: F401
    rebuild_qdrant_from_db,
)
