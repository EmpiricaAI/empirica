#!/usr/bin/env python3
"""
Empirica Statusline v2 - Unified Signaling with Moon Phases

Uses the shared signaling module for consistent emoji display.
Reads vectors from DB (real-time).

Display modes:
  - basic: Just confidence
  - default: Phase + key vectors + open counts
  - learning: Focus on vector changes
  - full: Everything with values

Environment:
  EMPIRICA_STATUS_MODE: basic|default|learning|full (default: default)
  EMPIRICA_AI_ID: AI identifier (default: claude-code)
  EMPIRICA_SIGNALING_LEVEL: basic|default|full (default: default)

Author: Claude Code
Date: 2025-12-30
Version: 2.1.0 (Unified Signaling)
"""

import os
import sys
from pathlib import Path

# Add empirica to path
EMPIRICA_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(EMPIRICA_ROOT))

from empirica.core.signaling import format_vectors_compact  # noqa: E402 — after sys.path setup
from empirica.data.session_database import SessionDatabase  # noqa: E402 — after sys.path setup


# ANSI color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    RED = '\033[31m'
    BLUE = '\033[34m'
    CYAN = '\033[36m'
    GRAY = '\033[90m'
    WHITE = '\033[37m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_CYAN = '\033[96m'


def get_ai_id() -> str:
    """Get AI identifier from environment."""
    return os.getenv('EMPIRICA_AI_ID', 'claude-code').strip()


def get_open_counts(db: SessionDatabase, session_id: str, project_id: str | None = None) -> dict:
    """
    Get counts of open goals and unknowns for a specific project.

    Goals and unknowns are project-scoped to prevent cross-project data leakage.
    Goal-linked unknowns indicate blockers that need resolution.

    Args:
        db: Database connection
        session_id: Current session ID
        project_id: Project ID to filter by (required for accurate counts)

    Returns:
        {
            'open_goals': int,           # Goals not yet completed (project-scoped)
            'open_unknowns': int,        # Unknowns not yet resolved (project-scoped)
            'goal_linked_unknowns': int, # Unresolved unknowns linked to goals (blockers)
            'completion': float,         # Latest completion vector (0.0-1.0)
        }
    """
    cursor = db.conn.cursor()

    # Count open goals for THIS PROJECT (project-scoped, not session-scoped)
    # Use is_completed (source of truth) not status column (can be inconsistent)
    if project_id:
        cursor.execute("""
            SELECT COUNT(*)
            FROM goals
            WHERE is_completed = 0 AND project_id = ?
        """, (project_id,))
    else:
        cursor.execute("""
            SELECT COUNT(*)
            FROM goals
            WHERE is_completed = 0
        """)
    open_goals = cursor.fetchone()[0] or 0

    # Count unresolved unknowns for THIS PROJECT
    # Use project_unknowns directly (has project_id column) - no session JOIN needed
    if project_id:
        cursor.execute("""
            SELECT COUNT(*)
            FROM project_unknowns
            WHERE is_resolved = 0 AND project_id = ?
        """, (project_id,))
    else:
        cursor.execute("""
            SELECT COUNT(*)
            FROM project_unknowns
            WHERE is_resolved = 0
        """)
    open_unknowns = cursor.fetchone()[0] or 0

    # Count goal-linked unresolved unknowns (blockers) for THIS PROJECT
    if project_id:
        cursor.execute("""
            SELECT COUNT(*)
            FROM project_unknowns
            WHERE is_resolved = 0 AND goal_id IS NOT NULL AND project_id = ?
        """, (project_id,))
    else:
        cursor.execute("""
            SELECT COUNT(*)
            FROM project_unknowns
            WHERE is_resolved = 0 AND goal_id IS NOT NULL
        """)
    goal_linked_unknowns = cursor.fetchone()[0] or 0

    # Get completion from latest vector state
    cursor.execute("""
        SELECT completion
        FROM reflexes
        WHERE session_id = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (session_id,))
    reflex_row = cursor.fetchone()
    completion = reflex_row[0] if reflex_row and reflex_row[0] is not None else 0.0

    return {
        'open_goals': open_goals,
        'open_unknowns': open_unknowns,
        'goal_linked_unknowns': goal_linked_unknowns,
        'completion': completion,
    }


def get_active_goal(db: SessionDatabase, session_id: str) -> dict | None:
    """
    Get the active goal for a session (legacy, kept for 'full' mode).

    Returns:
        {
            'goal_id': str,
            'objective': str,
            'completion': float (0.0-1.0) - from vector state, not subtasks
            'subtask_progress': (completed, total) - for reference only
        }
        or None if no active goal
    """
    cursor = db.conn.cursor()

    # Get active (non-completed) goal for this session
    cursor.execute("""
        SELECT id, objective, status
        FROM goals
        WHERE session_id = ? AND status != 'completed'
        ORDER BY created_timestamp DESC
        LIMIT 1
    """, (session_id,))
    row = cursor.fetchone()

    if not row:
        return None

    goal_id, objective, _ = row  # _ for unused status

    # Get completion from latest vector state (epistemic measure)
    # This is the AI's self-assessed completion, not mechanical subtask checkboxes
    cursor.execute("""
        SELECT completion
        FROM reflexes
        WHERE session_id = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (session_id,))
    reflex_row = cursor.fetchone()
    completion = reflex_row[0] if reflex_row and reflex_row[0] is not None else 0.0

    # Get subtask progress (for reference, not primary measure)
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
        FROM subtasks
        WHERE goal_id = ?
    """, (goal_id,))
    subtask_row = cursor.fetchone()

    total_subtasks = subtask_row[0] if subtask_row else 0
    completed_subtasks = subtask_row[1] if subtask_row else 0

    return {
        'goal_id': goal_id,
        'objective': objective,
        'completion': completion,
        'subtask_progress': (completed_subtasks, total_subtasks)
    }


def format_progress_bar(completion: float, width: int = 8) -> str:
    """
    Format completion as ASCII progress bar.

    Args:
        completion: 0.0 to 1.0
        width: number of characters for the bar

    Returns:
        String like "████░░░░ 45%"
    """
    filled = int(completion * width)
    empty = width - filled

    bar = "█" * filled + "░" * empty
    pct = int(completion * 100)

    # Color based on progress
    if completion >= 0.75:
        color = Colors.BRIGHT_GREEN
    elif completion >= 0.5:
        color = Colors.GREEN
    elif completion >= 0.25:
        color = Colors.YELLOW
    else:
        color = Colors.GRAY

    return f"{color}{bar}{Colors.RESET} {pct}%"


def format_open_counts(open_counts: dict | None) -> str:
    """
    Format open goals and unknowns as actionable counts.

    Shows what needs to be closed - useful for peeking into AI reality.

    Returns:
        String like "🎯3 ❓6/4" (3 goals, 6 unknowns total, 4 goal-linked blockers)
    """
    if not open_counts:
        return f"{Colors.GRAY}--{Colors.RESET}"

    goals = open_counts.get('open_goals', 0)
    unknowns = open_counts.get('open_unknowns', 0)
    goal_linked = open_counts.get('goal_linked_unknowns', 0)

    # Color code based on counts
    if goals == 0:
        goal_color = Colors.GREEN
    elif goals <= 2:
        goal_color = Colors.YELLOW
    else:
        goal_color = Colors.CYAN

    # Color unknowns based on goal-linked (blockers) count
    if goal_linked == 0:
        unknown_color = Colors.GREEN
    elif goal_linked <= 5:
        unknown_color = Colors.YELLOW
    else:
        unknown_color = Colors.CYAN

    # Format: ❓total/blockers (e.g., ❓119/70 means 119 unresolved, 70 blocking goals)
    if goal_linked > 0 and goal_linked != unknowns:
        unknown_str = f"❓{unknowns}/{goal_linked}"
    else:
        unknown_str = f"❓{unknowns}"

    return f"{goal_color}🎯{goals}{Colors.RESET} {unknown_color}{unknown_str}{Colors.RESET}"


def format_goal_progress(goal: dict, max_name_len: int = 12) -> str:
    """
    Format goal progress for statusline (legacy, used in 'full' mode).

    Returns:
        String like "auth-fix ████░░░░ 45%" or "████░░░░ 45%" if no goal name
    """
    if not goal:
        return f"{Colors.GRAY}no goal{Colors.RESET}"

    objective = goal.get('objective', '')
    completion = goal.get('completion', 0.0)

    # Truncate objective for display
    if len(objective) > max_name_len:
        name = objective[:max_name_len-2] + ".."
    else:
        name = objective

    # Simplify name: lowercase, replace spaces with dashes
    name = name.lower().replace(' ', '-')[:max_name_len]

    bar = format_progress_bar(completion, width=6)

    if name:
        return f"{name} {bar}"
    else:
        return bar


def calculate_confidence(vectors: dict) -> float:
    """
    Calculate overall confidence score from vectors.

    Formula: weighted average of key epistemic indicators
    - know (40%): How much we understand
    - 1-uncertainty (30%): Inverse of doubt
    - context (20%): How well we understand the situation
    - completion (10%): How much is done

    Returns: 0.0 to 1.0 (displayed as 0-100%)
    """
    if not vectors:
        return 0.0

    know = vectors.get('know', 0.5)
    uncertainty = vectors.get('uncertainty', 0.5)
    context = vectors.get('context', 0.5)
    completion = vectors.get('completion', 0.0)

    confidence = (
        0.40 * know +
        0.30 * (1.0 - uncertainty) +
        0.20 * context +
        0.10 * completion
    )

    return max(0.0, min(1.0, confidence))


def format_confidence(confidence: float) -> str:
    """Format confidence as colored percentage with tiered emoji."""
    pct = int(confidence * 100)

    if confidence >= 0.75:
        color = Colors.BRIGHT_GREEN
        emoji = "⚡"  # High energy/power
    elif confidence >= 0.50:
        color = Colors.GREEN
        emoji = "💡"  # Good understanding
    elif confidence >= 0.35:
        color = Colors.YELLOW
        emoji = "💫"  # Some uncertainty
    else:
        color = Colors.RED
        emoji = "🌑"  # Low confidence/dark

    return f"{emoji}{color}{pct}%{Colors.RESET}"


def _color_by_value(value: float) -> str:
    """Color a 0-1 value: green >= 0.75, yellow 0.50-0.74, red < 0.50."""
    if value >= 0.75:
        return Colors.BRIGHT_GREEN
    elif value >= 0.50:
        return Colors.YELLOW
    return Colors.RED


def get_dynamic_threshold(db) -> tuple:
    """Get Brier-based dynamic threshold for statusline display.

    Returns (know_threshold, uncertainty_threshold, threshold_color).
    Threshold color indicates calibration quality:
      Green = well-calibrated (threshold at baseline, numbers trusted)
      Yellow = moderate inflation (some miscalibration detected)
      Red = significant inflation (unreliable self-assessment)
    """
    try:
        from empirica.core.post_test.dynamic_thresholds import compute_dynamic_thresholds
        dt = compute_dynamic_thresholds(ai_id="claude-code", db=db)
        if dt.get("source") == "dynamic":
            noetic = dt.get("noetic", {})
            if noetic.get("brier_score") is not None:
                know_t = noetic["ready_know_threshold"]
                unc_t = noetic["ready_uncertainty_threshold"]
                inflation = noetic.get("threshold_inflation", 0)
                # Color by inflation level (how much the Sentinel distrusts the AI)
                if inflation <= 0.03:
                    color = Colors.BRIGHT_GREEN  # Trusted — at baseline
                elif inflation <= 0.10:
                    color = Colors.YELLOW         # Moderate inflation
                else:
                    color = Colors.RED             # Significant miscalibration
                return (know_t, unc_t, color)
    except Exception:
        pass
    return (0.70, 0.35, Colors.GRAY)  # Static fallback


def format_threshold(know_threshold: float, color: str) -> str:
    """Format threshold as colored percentage with ↕ indicator."""
    pct = int(know_threshold * 100)
    return f"{color}↕{pct}%{Colors.RESET}"


def calculate_phase_composite(vectors: dict, phase: str) -> float:
    """Calculate composite score for the current workflow phase.

    Noetic (investigating): avg of clarity, coherence, signal, density
    Praxic (acting): avg of state, change, completion, impact
    Check (readiness gate): avg of know, context, clarity, coherence, signal, density
      — CHECK evaluates readiness-to-act, not execution progress
    """
    if not vectors:
        return 0.0

    if phase == 'check':
        keys = ['know', 'context', 'clarity', 'coherence', 'signal', 'density']
    elif phase == 'noetic':
        keys = ['clarity', 'coherence', 'signal', 'density']
    else:
        keys = ['state', 'change', 'completion', 'impact']

    values = [vectors.get(k, 0.0) for k in keys if vectors.get(k) is not None]
    return sum(values) / len(values) if values else 0.0


def determine_work_phase(phase: str, gate_decision: str | None = None) -> str:
    """Determine if AI is in noetic (investigating) or praxic (acting) mode.

    Logic:
      PREFLIGHT / CHECK with investigate → noetic
      CHECK with proceed → transitioning to praxic
      POSTFLIGHT → last phase (show praxic since work completed)
      No phase → noetic (default to investigating)
    """
    if not phase:
        return 'noetic'
    if phase == 'PREFLIGHT':
        return 'noetic'
    if phase == 'CHECK':
        return 'praxic' if gate_decision == 'proceed' else 'noetic'
    if phase == 'POSTFLIGHT':
        return 'praxic'  # Work completed
    return 'noetic'


def format_phase_state(phase: str, work_phase: str, composite: float, gate_decision: str | None = None) -> str:
    """Format transaction phase + work state as compact indicator.

    Examples: PRE 🔍65% | CHK 🔍82%→ | CHK 🔨65%… | POST 🔨92% Δ ✓

    Praxic emoji is 🔨 (hammer, U+1F528 — east-asian-width: W). Was ⚙
    (U+2699, eaw: N/ambiguous) which terminal-rendered narrow on some
    surfaces and wide on others, causing the digits next to it to overlap
    or clip. 🔨 also matches the existing docs in CLAUDE_CODE_SETUP.md.
    """
    # Phase abbreviation
    phase_abbrev = {
        'PREFLIGHT': 'PRE',
        'CHECK': 'CHK',
        'POSTFLIGHT': 'POST',
    }.get(phase, phase[:3] if phase else '---')

    # Work state emoji + composite
    if work_phase == 'noetic':
        emoji = "🔍"
    else:
        emoji = "🔨"

    pct = int(composite * 100)
    color = _color_by_value(composite)

    # CHECK with decision: show percentage AND transition indicator
    if phase == 'CHECK' and gate_decision:
        if gate_decision == 'proceed':
            return f"{Colors.BLUE}{phase_abbrev}{Colors.RESET} {emoji}{color}{pct}%{Colors.GREEN}→{Colors.RESET}"
        else:
            return f"{Colors.BLUE}{phase_abbrev}{Colors.RESET} {emoji}{color}{pct}%{Colors.YELLOW}…{Colors.RESET}"

    return f"{Colors.BLUE}{phase_abbrev}{Colors.RESET} {emoji}{color}{pct}%{Colors.RESET}"


def get_provenance_share(db, transaction_id: str | None,
                         project_id: str | None = None,
                         window: int = 20) -> tuple[float, int] | None:
    """Compute external-grounding share over the last N project artifacts.

    Returns (ext_share, n_tagged) where ext_share ∈ [0.0, 1.0] is the
    fraction of tagged artifacts that came from external observation/search
    (not training-data intuition). `mixed` artifacts count as half-grounded.

    Lookup widens beyond the current transaction so the widget stays
    visible across sessions. Most recent `window` tagged artifacts in the
    project — gives a rolling trend that's always available once any
    tagged artifact exists, instead of going dark for fresh transactions.

    Returns None only when the project has zero tagged artifacts ever
    (cold start) or DB lookup fails. Hot-path cost ~3-8ms (6 small SELECTs
    bounded by `window`).
    """
    if not db or not getattr(db, 'conn', None):
        return None

    cursor = db.conn.cursor()
    artifact_tables = (
        'project_findings', 'project_unknowns', 'project_dead_ends',
        'mistakes_made', 'assumptions', 'decisions',
    )
    sources: list[str] = []
    for table in artifact_tables:
        try:
            if project_id:
                cursor.execute(
                    f"SELECT epistemic_source FROM {table} "
                    f"WHERE project_id = ? AND epistemic_source IS NOT NULL "
                    f"ORDER BY created_timestamp DESC LIMIT ?",
                    (project_id, window),
                )
            else:
                cursor.execute(
                    f"SELECT epistemic_source FROM {table} "
                    f"WHERE epistemic_source IS NOT NULL "
                    f"ORDER BY created_timestamp DESC LIMIT ?",
                    (window,),
                )
            sources.extend(row[0] for row in cursor.fetchall())
        except Exception:
            continue  # Pre-040 DB without epistemic_source column

    if not sources:
        return None

    # Take the N most-recent across all types (already top-N per table; trim again)
    sources = sources[:window]
    counts = {'intuition': 0, 'search': 0, 'mixed': 0}
    for s in sources:
        if s in counts:
            counts[s] += 1
    n_tagged = sum(counts.values())
    if n_tagged == 0:
        return None
    grounded = counts['search'] + 0.5 * counts['mixed']
    return (grounded / n_tagged, n_tagged)


def format_provenance(provenance: tuple[float, int] | None) -> str | None:
    """Render `ext:XX%` indicator. Returns None when there's nothing to show.

    Color: green ≥60% (mostly grounded), yellow 30-60% (mixed), red <30%
    (intuition-heavy — confidence is largely training-data intuition without
    external verification this transaction).
    """
    if provenance is None:
        return None
    ext_share, _n = provenance
    pct = int(ext_share * 100)
    if ext_share >= 0.60:
        color = Colors.BRIGHT_GREEN
    elif ext_share >= 0.30:
        color = Colors.YELLOW
    else:
        color = Colors.RED
    return f"{Colors.GRAY}ext:{Colors.RESET}{color}{pct}%{Colors.RESET}"


def format_vector_colored(label: str, value: float) -> str:
    """Format a single vector as colored label:value%."""
    pct = int(value * 100)
    color = _color_by_value(value)
    return f"{color}{label}:{pct}%{Colors.RESET}"


def read_statusline_extensions() -> str:
    """Read extension indicators from ~/.empirica/statusline_ext/*.json.

    External packages (empirica-workspace, etc.) write JSON files here.
    Each file should contain: {"label": "WS:4", "color": "cyan"} or similar.

    Delegates to _read_statusline_extensions_data() for file reading,
    then formats labels with ANSI colors for terminal display.
    """
    extensions = _read_statusline_extensions_data()
    if not extensions:
        return ""

    parts = []
    for data in extensions:
        label = data.get('label', '')
        if label:
            parts.append(f"{Colors.CYAN}{label}{Colors.RESET}")

    return ' '.join(parts)


def _read_statusline_extensions_data() -> list:
    """Read raw extension data from ~/.empirica/statusline_ext/*.json.

    Returns a list of extension dicts as written by external packages.
    Used by build_statusline_data() for structured JSON output.

    Returns:
        [{"label": "WS:4", "color": "cyan"}, ...] or [] if none
    """
    ext_dir = Path.home() / '.empirica' / 'statusline_ext'
    if not ext_dir.exists():
        return []

    results = []
    try:
        for ext_file in sorted(ext_dir.glob('*.json')):
            try:
                import json as _json
                data = _json.loads(ext_file.read_text())
                if data.get('label'):
                    results.append(data)
            except Exception:
                continue
    except Exception:
        pass

    return results


def _resolve_claude_session_id(stdin_claude_session_id: str | None = None):
    """
    Resolve the Claude session ID from available sources.

    Priority:
    1. stdin JSON (passed by Claude Code to statusline commands)
    2. TTY session (requires TMUX_PANE)

    Returns claude_session_id or None.
    """
    if stdin_claude_session_id:
        return stdin_claude_session_id

    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        tty_session = R.tty_session(warn_if_stale=False)
        if tty_session:
            return tty_session.get('claude_session_id')
    except Exception:
        pass

    return None


def _has_db(path_str: str) -> bool:
    """Check if a project path has a valid sessions.db."""
    return (Path(path_str) / '.empirica' / 'sessions' / 'sessions.db').exists()


def _read_project_path_from_json(file_path: Path, key: str = 'project_path') -> str | None:
    """Read and validate a project_path from a JSON file."""
    try:
        import json as _json
        if file_path.exists():
            with open(file_path) as f:
                pp = _json.load(f).get(key)
            if pp and _has_db(pp):
                return pp
    except Exception:
        pass
    return None


def _resolve_project_path(stdin_claude_session_id=None) -> str | None:
    """Resolve project path via 6-tier priority chain. Returns path or None."""
    # Priority 0: instance_projects
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        inst_id = R.instance_id()
        if inst_id:
            result = _read_project_path_from_json(
                Path.home() / '.empirica' / 'instance_projects' / f'{inst_id}.json')
            if result:
                return result
    except Exception:
        pass

    # Priority 1: active_work
    if stdin_claude_session_id:
        result = _read_project_path_from_json(
            Path.home() / '.empirica' / f'active_work_{stdin_claude_session_id}.json')
        if result:
            return result

    # Priority 2: env var
    env_path = os.getenv('EMPIRICA_PROJECT_PATH')
    if env_path:
        return env_path

    # Priority 3: TTY session
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        tty = R.tty_session(warn_if_stale=False)
        if tty:
            pp = tty.get('project_path')
            if pp and _has_db(pp):
                return pp
    except Exception:
        pass

    # Priority 4: path_resolver
    try:
        from empirica.config.path_resolver import get_empirica_root
        root = get_empirica_root()
        if root and root.exists() and (root / 'sessions' / 'sessions.db').exists():
            return str(root.parent)
    except Exception:
        pass

    # Priority 5: upward search
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        if (parent / '.empirica' / 'sessions' / 'sessions.db').exists():
            return str(parent)
        if parent == Path.home() or parent == parent.parent:
            break
    return None


def _read_session_file(path: Path) -> str | None:
    """Read session_id from a file (JSON or plain text format)."""
    try:
        content = path.read_text().strip()
        if not content:
            return None
        if content.startswith('{'):
            import json as _json
            return _json.loads(content).get('session_id', '')
        return content
    except Exception:
        return None


def _lookup_session_by_id(cursor, session_id: str, require_active: bool = True) -> dict | None:
    """Query sessions table by ID. Returns dict or None."""
    if not session_id:
        return None
    if require_active:
        cursor.execute("""
            SELECT session_id, ai_id, start_time FROM sessions
            WHERE session_id = ? AND end_time IS NULL
        """, (session_id,))
    else:
        cursor.execute("""
            SELECT session_id, ai_id, start_time FROM sessions
            WHERE session_id = ?
        """, (session_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def _search_session_files(cursor, start_dir: Path, filename: str) -> dict | None:
    """Search upward from start_dir for a session file, query DB if found."""
    for parent in [start_dir] + list(start_dir.parents):
        candidate = parent / '.empirica' / filename
        if candidate.exists():
            session_id = _read_session_file(candidate)
            result = _lookup_session_by_id(cursor, session_id)
            if result:
                return result
            break  # Found file but session ended
        if parent == Path.home() or parent == parent.parent:
            break
    return None


def _get_session_from_instance_projects(cursor):
    """Priority 0: instance_projects → empirica_session_id. Returns dict or None."""
    try:
        import json as _json

        from empirica.utils.session_resolver import InstanceResolver as R
        inst_id = R.instance_id()
        if inst_id:
            inst_file = Path.home() / '.empirica' / 'instance_projects' / f'{inst_id}.json'
            if inst_file.exists():
                with open(inst_file) as f:
                    session_id = _json.load(f).get('empirica_session_id')
                return _lookup_session_by_id(cursor, session_id, require_active=False)
    except Exception:
        pass
    return None


def _get_session_from_claude_id(cursor, stdin_claude_session_id):
    """Priority 1: Claude session_id → active_work/TTY → empirica_session_id. Returns dict or None."""
    try:
        import json as _json
        claude_session_id = _resolve_claude_session_id(stdin_claude_session_id)
        if not claude_session_id:
            return None

        empirica_session_id = None
        active_work_path = Path.home() / '.empirica' / f'active_work_{claude_session_id}.json'
        if active_work_path.exists():
            with open(active_work_path) as f:
                empirica_session_id = _json.load(f).get('empirica_session_id')

        if not empirica_session_id:
            try:
                from empirica.utils.session_resolver import InstanceResolver as R
                tty_session = R.tty_session(warn_if_stale=False)
                if tty_session:
                    empirica_session_id = tty_session.get('empirica_session_id')
            except Exception:
                pass

        return _lookup_session_by_id(cursor, empirica_session_id, require_active=False)
    except Exception:
        return None


def _get_session_from_files_or_db(cursor, ai_id):
    """Legacy priorities: instance-specific files, then DB query. Returns dict or None."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        current_instance_id = R.instance_id()
    except (ImportError, NameError):
        current_instance_id = None

    if current_instance_id:
        safe_instance = current_instance_id.replace(":", "_").replace("%", "")
        instance_suffix = f"_{safe_instance}"
        result = _search_session_files(cursor, Path.cwd(), f'active_session{instance_suffix}')
        if result:
            return result
        global_file = Path.home() / '.empirica' / f'active_session{instance_suffix}'
        if global_file.exists():
            session_id = _read_session_file(global_file)
            result = _lookup_session_by_id(cursor, session_id)
            if result:
                return result
    else:
        result = _search_session_files(cursor, Path.cwd(), 'active_session')
        if result:
            return result

    # DB query: exact ai_id + STRICT instance_id (no NULL fallback)
    if current_instance_id:
        cursor.execute("""
            SELECT session_id, ai_id, start_time FROM sessions
            WHERE end_time IS NULL AND ai_id = ? AND instance_id = ?
            ORDER BY start_time DESC LIMIT 1
        """, (ai_id, current_instance_id))
    else:
        cursor.execute("""
            SELECT session_id, ai_id, start_time FROM sessions
            WHERE end_time IS NULL AND ai_id = ?
            ORDER BY start_time DESC LIMIT 1
        """, (ai_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def get_active_session(db: SessionDatabase, ai_id: str, stdin_claude_session_id: str | None = None) -> dict | None:
    """
    Get the active session with strict pane isolation.

    Priority:
    0. instance_projects → empirica_session_id
    1. Claude session_id → active_work → empirica_session_id
    2. Instance-specific active_session files / DB query

    IMPORTANT: Never fall back to instance_id IS NULL - that causes
    cross-pane bleeding where any pane picks up legacy sessions.
    """
    cursor = db.conn.cursor()

    result = _get_session_from_instance_projects(cursor)
    if result:
        return result

    result = _get_session_from_claude_id(cursor, stdin_claude_session_id)
    if result:
        return result

    return _get_session_from_files_or_db(cursor, ai_id)


def get_latest_vectors(db: SessionDatabase, session_id: str, transaction_session_id: str | None = None, transaction_id: str | None = None) -> tuple:
    """
    Get latest vectors, phase, and gate decision from reflexes table.

    Args:
        db: Database connection
        session_id: Current session ID (for cache compatibility)
        transaction_session_id: The session ID where PREFLIGHT was submitted (survives compaction)
                               If provided, queries this session for vectors instead
        transaction_id: The specific transaction ID to filter by (for multi-instance isolation)
                       CRITICAL: Without this, shared sessions show wrong phase across instances

    This enables cross-session vector lookup when transactions span compaction boundaries.
    """
    cursor = db.conn.cursor()
    # Use transaction's session_id if available (survives compaction)
    lookup_session_id = transaction_session_id or session_id

    # CRITICAL: Filter by transaction_id to prevent cross-instance bleed
    # Without this, two Claudes sharing a session see each other's phases
    if transaction_id:
        cursor.execute("""
            SELECT phase, engagement, know, do, context,
                   clarity, coherence, signal, density,
                   state, change, completion, impact, uncertainty,
                   reflex_data
            FROM reflexes
            WHERE session_id = ? AND transaction_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (lookup_session_id, transaction_id))
    else:
        cursor.execute("""
            SELECT phase, engagement, know, do, context,
                   clarity, coherence, signal, density,
                   state, change, completion, impact, uncertainty,
                   reflex_data
            FROM reflexes
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (lookup_session_id,))
    row = cursor.fetchone()

    if not row:
        return None, {}, None

    phase = row[0]
    vectors = {
        'engagement': row[1],
        'know': row[2],
        'do': row[3],
        'context': row[4],
        'clarity': row[5],
        'coherence': row[6],
        'signal': row[7],
        'density': row[8],
        'state': row[9],
        'change': row[10],
        'completion': row[11],
        'impact': row[12],
        'uncertainty': row[13],
    }

    # Filter out None values
    vectors = {k: v for k, v in vectors.items() if v is not None}

    # Extract gate decision from reflex_data (CHECK phase)
    gate_decision = None
    if row[14]:  # reflex_data column
        try:
            import json
            reflex_data = json.loads(row[14])
            gate_decision = reflex_data.get('decision')
        except Exception:
            pass

    return phase, vectors, gate_decision


def get_vector_deltas(db: SessionDatabase, session_id: str) -> dict:
    """
    Get learning deltas: PREFLIGHT → POSTFLIGHT only.

    This measures actual learning across the session, ignoring CHECK
    phases which are for gating, not learning measurement.
    """
    cursor = db.conn.cursor()

    # Get PREFLIGHT baseline (first PREFLIGHT in session)
    cursor.execute("""
        SELECT know, uncertainty, context, completion, engagement
        FROM reflexes
        WHERE session_id = ? AND phase = 'PREFLIGHT'
        ORDER BY timestamp ASC
        LIMIT 1
    """, (session_id,))
    preflight = cursor.fetchone()

    # Get latest POSTFLIGHT (final state)
    cursor.execute("""
        SELECT know, uncertainty, context, completion, engagement
        FROM reflexes
        WHERE session_id = ? AND phase = 'POSTFLIGHT'
        ORDER BY timestamp DESC
        LIMIT 1
    """, (session_id,))
    postflight = cursor.fetchone()

    if not preflight or not postflight:
        # Fallback: if no complete cycle, show sequential delta
        cursor.execute("""
            SELECT know, uncertainty, context, completion, engagement
            FROM reflexes
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT 2
        """, (session_id,))
        rows = cursor.fetchall()
        if len(rows) < 2:
            return {}
        postflight = rows[0]
        preflight = rows[1]

    deltas = {}
    keys = ['know', 'uncertainty', 'context', 'completion', 'engagement']

    for i, key in enumerate(keys):
        post_val = postflight[i]
        pre_val = preflight[i]

        if post_val is not None and pre_val is not None:
            delta = post_val - pre_val
            if abs(delta) >= 0.05:  # Only show meaningful changes
                deltas[key] = delta

    return deltas


def format_deltas(deltas: dict) -> str:
    """Format deltas as a single summary symbol to prevent statusline overflow.

    Returns: green ✓ (net positive), red ⚠ (net negative), or white △ (neutral).
    """
    if not deltas:
        return ""

    # Calculate net direction across all vectors
    # For uncertainty, invert sign (lower uncertainty = improvement)
    net = 0.0
    for key, delta in deltas.items():
        if key == 'uncertainty':
            net -= delta  # Lower uncertainty is positive
        else:
            net += delta

    if net > 0.05:
        return f"{Colors.GREEN}✓{Colors.RESET}"
    elif net < -0.05:
        return f"{Colors.RED}⚠{Colors.RESET}"
    else:
        return f"{Colors.WHITE}△{Colors.RESET}"


def format_context_window(stdin_context: dict) -> str:
    """Format context window usage from Claude Code stdin data.

    Also writes usage to a state file so other tools can read it without
    receiving CC's stdin payload:
      - ~/.empirica/context_usage.json          (legacy shared, kept for
        backwards-compat with older readers)
      - ~/.empirica/context_usage_{id}.json    (per-instance, what the
        cockpit TUI and context-shift-tracker hook read)
    """
    ctx = stdin_context.get('context_window', {})
    used_pct = ctx.get('used_percentage', 0)
    if not used_pct:
        return ""

    try:
        import json as _json_ctx
        import time as _time
        empirica_dir = Path.home() / '.empirica'
        empirica_dir.mkdir(parents=True, exist_ok=True)
        payload = _json_ctx.dumps({
            'used_percentage': used_pct,
            'timestamp': _time.time(),
        })
        # Legacy shared (for older readers)
        (empirica_dir / 'context_usage.json').write_text(payload)
        # Per-instance (cockpit, context-shift-tracker)
        try:
            from project_resolver import get_instance_id  # type: ignore
            instance_id = get_instance_id()
        except Exception:
            try:
                from empirica.utils.session_resolver import get_instance_id
                instance_id = get_instance_id()
            except Exception:
                instance_id = None
        if instance_id:
            safe_id = instance_id.replace('/', '-').replace('%', '')
            (empirica_dir / f'context_usage_{safe_id}.json').write_text(payload)
    except Exception:
        pass

    # Color: green < 50%, yellow 50-80%, red > 80%
    if used_pct >= 80:
        color = Colors.RED
    elif used_pct >= 50:
        color = Colors.YELLOW
    else:
        color = Colors.GREEN
    return f"{color}{int(used_pct)}%ctx{Colors.RESET}"


def _append_postflight_deltas(parts, phase, deltas):
    """Append delta indicator to parts if phase is POSTFLIGHT and deltas exist."""
    if phase == 'POSTFLIGHT' and deltas:
        delta_str = format_deltas(deltas)
        if delta_str:
            parts.append(f"Δ {delta_str}")


def _format_statusline_header(project_name, vectors, threshold_info):
    """Build the common header: [label] confidence threshold + extensions.

    Returns (label, parts_list).
    """
    label = project_name or 'empirica'
    if len(label) > 20:
        label = label[:18] + '..'

    confidence = calculate_confidence(vectors)
    conf_str = format_confidence(confidence)

    # Threshold (↕XX%) intentionally not rendered in live statusline. It's
    # Sentinel-scoped (set/dynamically-adjusted at session level, not per
    # action) and not actionable for the AI mid-tool-call. Helpers
    # `get_dynamic_threshold` + `format_threshold` remain available for
    # `empirica sentinel-status` and debug surfaces. Pass threshold_info=None
    # from callers to keep the live statusline tight.
    parts = [f"{Colors.GREEN}[{label}]{Colors.RESET} {conf_str}"]

    ext_str = read_statusline_extensions()
    if ext_str:
        parts.append(ext_str)

    return label, parts


def _format_statusline_default(parts, phase, vectors, deltas, gate_decision, open_counts, stdin_context, provenance=None):
    """Format the 'default' mode statusline sections."""
    parts.append(format_open_counts(open_counts))

    if phase:
        work_phase = determine_work_phase(phase, gate_decision)
        composite_phase = 'check' if phase == 'CHECK' else work_phase
        composite = calculate_phase_composite(vectors, composite_phase)
        parts.append(format_phase_state(phase, work_phase, composite, gate_decision))

    prov_str = format_provenance(provenance)
    if prov_str:
        parts.append(prov_str)

    if vectors:
        know = vectors.get('know', 0.0)
        context = vectors.get('context', 0.0)
        parts.append(f"{format_vector_colored('K', know)} {format_vector_colored('C', context)}")

    _append_postflight_deltas(parts, phase, deltas)

    if stdin_context:
        ctx_str = format_context_window(stdin_context)
        if ctx_str:
            parts.append(ctx_str)

    return ' │ '.join(parts)


def _format_statusline_learning(parts, phase, vectors, deltas, open_counts):
    """Format the 'learning' mode statusline sections."""
    parts.append(format_open_counts(open_counts))

    if phase:
        parts.append(f"{phase}")

    if vectors:
        all_keys = ['know', 'uncertainty', 'context', 'clarity', 'completion']
        parts.append(format_vectors_compact(vectors, keys=all_keys, use_percentage=True))

    _append_postflight_deltas(parts, phase, deltas)
    return ' │ '.join(parts)


def _format_statusline_full(label, session, phase, vectors, deltas, goal):
    """Format the 'full' mode statusline."""
    ai_id = session.get('ai_id', 'unknown')
    session_id = session.get('session_id', '????')[:4]
    parts = [f"{Colors.BRIGHT_CYAN}[{label}:{ai_id}@{session_id}]{Colors.RESET}"]

    if goal:
        completed, total = goal.get('subtask_progress', (0, 0))
        goal_str = format_goal_progress(goal)
        if total > 0:
            goal_str += f" ({completed}/{total})"
        parts.append(goal_str)
    else:
        parts.append(f"{Colors.GRAY}no goal{Colors.RESET}")

    if phase:
        parts.append(f"{Colors.BLUE}{phase}{Colors.RESET}")

    if vectors:
        all_keys = ['know', 'uncertainty', 'context', 'clarity', 'engagement', 'completion', 'impact']
        parts.append(format_vectors_compact(vectors, keys=all_keys, use_percentage=True))

    _append_postflight_deltas(parts, phase, deltas)
    return ' │ '.join(parts)


def format_statusline(
    session: dict,
    phase: str,
    vectors: dict,
    deltas: dict | None = None,
    mode: str = 'default',
    gate_decision: str | None = None,
    goal: dict | None = None,
    open_counts: dict | None = None,
    project_name: str | None = None,
    threshold_info: tuple | None = None,
    stdin_context: dict | None = None,
    provenance: tuple[float, int] | None = None,
) -> str:
    """Format the statusline based on mode."""
    label, parts = _format_statusline_header(project_name, vectors, threshold_info)

    if mode == 'basic':
        return ' '.join(parts)
    elif mode == 'default':
        return _format_statusline_default(parts, phase, vectors, deltas, gate_decision, open_counts, stdin_context, provenance)
    elif mode == 'learning':
        return _format_statusline_learning(parts, phase, vectors, deltas, open_counts)
    else:
        return _format_statusline_full(label, session, phase, vectors, deltas, goal)


def build_statusline_data(
    session: dict,
    phase: str,
    vectors: dict,
    deltas: dict | None = None,
    gate_decision: str | None = None,
    goal: dict | None = None,
    open_counts: dict | None = None,
    project_name: str | None = None,
    project_path: str | None = None,
    ai_id: str | None = None,
) -> dict:
    """
    Build structured statusline data for JSON output.

    This enables TUI/GUI dashboards to consume statusline state.
    Extensions are read from ~/.empirica/statusline_ext/*.json (the statusline_ext protocol).

    Returns:
        {
            'project': {'name': str, 'path': str},
            'session': {'id': str, 'ai_id': str},
            'epistemic': {'phase': str, 'vectors': dict, 'deltas': dict, 'confidence': float},
            'goals': {'open': int, 'completion': float},
            'unknowns': {'open': int, 'blockers': int},
            'gate': {'decision': str},
            'extensions': [{'label': str, ...}, ...],
            'timestamp': float,
        }
    """
    import time

    confidence = calculate_confidence(vectors) if vectors else 0.0

    # Read extensions from statusline_ext JSON protocol
    extensions = _read_statusline_extensions_data()

    return {
        'project': {
            'name': project_name,
            'path': project_path,
        },
        'session': {
            'id': session.get('session_id') if session else None,
            'ai_id': ai_id or (session.get('ai_id') if session else None),
        },
        'epistemic': {
            'phase': phase,
            'vectors': vectors or {},
            'deltas': deltas or {},
            'confidence': confidence,
        },
        'goals': {
            'open': open_counts.get('open_goals', 0) if open_counts else 0,
            'active': goal.get('objective') if goal else None,
            'completion': open_counts.get('completion', 0.0) if open_counts else 0.0,
        },
        'unknowns': {
            'open': open_counts.get('open_unknowns', 0) if open_counts else 0,
            'blockers': open_counts.get('goal_linked_unknowns', 0) if open_counts else 0,
        },
        'gate': {
            'decision': gate_decision,
        },
        'extensions': extensions,
        'timestamp': time.time(),
    }


def format_tmux_statusline(confidence: float, phase: str) -> str:
    """
    Format a super-compact statusline for tmux status-right.

    Target: ~20 characters max for tmux status bar
    Format: "E:💡63% PRE"
    """
    pct = int(confidence * 100) if confidence else 0

    # Confidence emoji (no ANSI colors for tmux)
    if confidence >= 0.75:
        emoji = "⚡"
    elif confidence >= 0.50:
        emoji = "💡"
    elif confidence >= 0.35:
        emoji = "💫"
    else:
        emoji = "🌑"

    # Phase abbreviation
    phase_abbrev = {
        'PREFLIGHT': 'PRE',
        'CHECK': 'CHK',
        'POSTFLIGHT': 'POST',
        'INVESTIGATE': 'INV',
    }.get(phase, phase[:3] if phase else '---')

    return f"E:{emoji}{pct}% {phase_abbrev}"


def _check_off_record() -> bool:
    """Check if Empirica is paused (off-record). Prints status and returns True to exit.

    Resolves the per-instance pause file FIRST (matches sentinel-gate.py
    semantics): TUI / `empirica sentinel pause --instance <ID>` writes
    `~/.empirica/sentinel_paused_{instance_id}`. The global file is the
    fallback for "all instances paused" scope.
    """
    base_dir = Path.home() / '.empirica'
    instance_pause_file: Path | None = None
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        inst_id = R.instance_id()
        if inst_id:
            safe_id = inst_id.replace('/', '-').replace('%', '')
            instance_pause_file = base_dir / f'sentinel_paused_{safe_id}'
    except Exception:
        instance_pause_file = None

    pause_file: Path | None = None
    if instance_pause_file is not None and instance_pause_file.exists():
        pause_file = instance_pause_file
    else:
        global_file = base_dir / 'sentinel_paused'
        if global_file.exists():
            pause_file = global_file

    if pause_file is None:
        return False

    try:
        import json as _json
        import time as _time
        text = pause_file.read_text().strip()
        gap_str = ''
        # TUI / CLI write a plain reason string; the legacy global file
        # used a JSON blob with paused_at. Render age from mtime when no
        # JSON is parseable, falling back to no age string.
        try:
            pause_data = _json.loads(text) if text else {}
            paused_at = pause_data.get('paused_at', 0)
        except (ValueError, _json.JSONDecodeError):
            paused_at = pause_file.stat().st_mtime
        if paused_at:
            gap_minutes = int((_time.time() - paused_at) / 60)
            if gap_minutes < 60:
                gap_amount = f'{gap_minutes}m'
            else:
                gap_amount = f'{gap_minutes // 60}h{gap_minutes % 60}m'
            gap_str = f" {Colors.GRAY}({gap_amount}){Colors.RESET}"
        print(
            f"{Colors.GRAY}[empirica]{Colors.RESET} "
            f"{Colors.YELLOW}OFF-RECORD{Colors.RESET}{gap_str}"
        )
    except Exception:
        print(f"{Colors.GRAY}[empirica]{Colors.RESET} {Colors.YELLOW}OFF-RECORD{Colors.RESET}")
    return True


def _read_stdin_context() -> tuple:
    """Read Claude Code stdin context (JSON). Returns (stdin_context, claude_session_id)."""
    try:
        import json as _json
        import select
        if not sys.stdin.isatty():
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if ready:
                raw = sys.stdin.read()
                if raw and raw.strip():
                    ctx = _json.loads(raw.strip())
                    return ctx, ctx.get('session_id')
    except Exception:
        pass
    return {}, None


def _resolve_project_name(db, session) -> tuple:
    """Resolve project_id and project_name from session or most recent session.

    Returns (project_id, project_name).
    """
    project_id = None
    project_name = None
    cursor = db.conn.cursor()

    if session:
        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session['session_id'],))
        row = cursor.fetchone()
        if row and row[0]:
            project_id = row[0]
            cursor.execute("SELECT name FROM projects WHERE id = ?", (project_id,))
            prow = cursor.fetchone()
            if prow:
                project_name = prow[0]

    if not project_name:
        cursor.execute("""
            SELECT p.name FROM projects p
            JOIN sessions s ON s.project_id = p.id
            ORDER BY s.start_time DESC LIMIT 1
        """)
        prow = cursor.fetchone()
        if prow:
            project_name = prow[0]

    return project_id, project_name


def _read_open_transaction(project_path) -> tuple:
    """Read active transaction file for instance isolation.

    Returns (transaction_session_id, transaction_id).
    """
    try:
        import json as _json

        from empirica.utils.session_resolver import InstanceResolver as R
        suffix = R.instance_suffix()
        if project_path:
            tx_path = Path(project_path) / '.empirica' / f'active_transaction{suffix}.json'
        else:
            tx_path = Path.home() / '.empirica' / f'active_transaction{suffix}.json'
        if tx_path and tx_path.exists():
            with open(tx_path) as f:
                tx_data = _json.load(f)
            if tx_data.get('status') == 'open':
                return tx_data.get('session_id'), tx_data.get('transaction_id')
    except Exception:
        pass
    return None, None


def main():
    """Main statusline generation."""
    try:
        mode = os.getenv('EMPIRICA_STATUS_MODE', 'default').lower()
        output_json = '--json' in sys.argv or os.getenv('EMPIRICA_STATUS_JSON', '').lower() == 'true'
        output_tmux = '--tmux' in sys.argv or os.getenv('EMPIRICA_STATUS_TMUX', '').lower() == 'true'
        ai_id = get_ai_id()

        # HEADLESS CHECK
        try:
            from empirica.utils.session_resolver import InstanceResolver as R
            if R.is_headless():
                return
        except ImportError:
            pass

        if _check_off_record():
            return

        stdin_context, stdin_claude_session_id = _read_stdin_context()
        project_path = _resolve_project_path(stdin_claude_session_id)

        if not project_path:
            print(f"{Colors.GRAY}[no project]{Colors.RESET}")
            return

        db_path = Path(project_path) / '.empirica' / 'sessions' / 'sessions.db'
        db = SessionDatabase(db_path=str(db_path))

        session = get_active_session(db, ai_id, stdin_claude_session_id=stdin_claude_session_id)
        project_id, project_name = _resolve_project_name(db, session)

        if not session:
            label = project_name or ai_id
            if len(label) > 20:
                label = label[:18] + '..'
            print(f"{Colors.GRAY}[{label}:inactive]{Colors.RESET}")
            db.close()
            return

        session_id = session['session_id']
        transaction_session_id, transaction_id = _read_open_transaction(project_path)

        if transaction_id:
            phase, vectors, gate_decision = get_latest_vectors(db, session_id, transaction_session_id, transaction_id)
        else:
            phase, vectors, gate_decision = get_latest_vectors(db, session_id)

        deltas = get_vector_deltas(db, transaction_session_id or session_id)
        goal = get_active_goal(db, session_id)
        open_counts = get_open_counts(db, session_id, project_id=project_id)
        # threshold_info intentionally not fetched — Sentinel-scoped, not surfaced live
        provenance = get_provenance_share(db, transaction_id, project_id=project_id)
        db.close()

        if output_json:
            import json
            data = build_statusline_data(
                session, phase, vectors, deltas,
                gate_decision=gate_decision, goal=goal, open_counts=open_counts,
                project_name=project_name, project_path=project_path, ai_id=ai_id,
            )
            print(json.dumps(data, indent=2))
            return

        if output_tmux:
            confidence = calculate_confidence(vectors) if vectors else 0.0
            print(format_tmux_statusline(confidence, phase))
            return

        output = format_statusline(
            session, phase, vectors, deltas, mode,
            gate_decision=gate_decision, goal=goal, open_counts=open_counts,
            project_name=project_name, threshold_info=None,
            stdin_context=stdin_context, provenance=provenance,
        )
        print(output)

    except Exception as e:
        print(f"{Colors.GRAY}[empirica:error]{Colors.RESET}")
        try:
            from empirica.config.path_resolver import get_empirica_root
            with open(get_empirica_root() / 'statusline.log', 'a') as f:
                f.write(f"ERROR: {e}\n")
        except Exception:
            pass


if __name__ == '__main__':
    main()
