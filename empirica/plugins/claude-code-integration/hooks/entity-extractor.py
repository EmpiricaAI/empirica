#!/usr/bin/env python3
"""
Empirica PostToolUse Hook — Entity Extraction from File Edits

Fires after successful Edit/Write tool calls. Extracts codebase entities
(functions, classes, APIs, imports) from the modified file and stores them
in the temporal entity graph.

Input: tool_name, tool_input, tool_result, session_id
Can block: No (tool already succeeded)
"""

import json
import logging
import sys
import time
from pathlib import Path

LOG_DIR = Path.home() / ".empirica" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("empirica.entity-extractor")
handler = logging.FileHandler(LOG_DIR / "entity-extractor.log")
handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

# Add lib folder to path for shared modules
_lib_path = Path(__file__).parent.parent / "lib"
if str(_lib_path) not in sys.path:
    sys.path.insert(0, str(_lib_path))

# Supported file extensions for entity extraction
EXTRACTABLE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".sh",
    ".bash",
}

# Rate limit: don't re-extract same file within this window (seconds)
EXTRACT_COOLDOWN = 5.0
_STATE_FILE = Path.home() / ".empirica" / "entity_extractor_state.json"


def _load_last_extracted() -> dict:
    """Load persisted extraction timestamps. Survives compaction."""
    try:
        if _STATE_FILE.exists():
            with open(_STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_last_extracted(state: dict) -> None:
    """Persist extraction timestamps."""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            # Only keep entries from last hour to prevent unbounded growth
            cutoff = time.time() - 3600
            trimmed = {k: v for k, v in state.items() if v > cutoff}
            json.dump(trimmed, f)
    except Exception:
        pass


_last_extracted: dict = _load_last_extracted()


def _should_extract(file_path: str) -> bool:
    """Check if file is extractable and not rate-limited."""
    ext = Path(file_path).suffix.lower()
    if ext not in EXTRACTABLE_EXTENSIONS:
        return False

    now = time.time()
    last = _last_extracted.get(file_path, 0)
    if now - last < EXTRACT_COOLDOWN:
        logger.debug(f"  Rate-limited: {file_path} (extracted {now - last:.1f}s ago)")
        return False

    return True


def _get_file_content(file_path: str) -> str:
    """Read file content, returning empty string on failure."""
    try:
        return Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug(f"  Cannot read {file_path}: {e}")
        return ""


def _extract_and_store(file_path: str, session_id: str) -> dict:
    """Extract entities from file and store in codebase model.

    Returns summary dict with counts.
    """
    from project_resolver import get_active_project_path, get_active_session_id

    # Resolve project context
    project_path = get_active_project_path(session_id)
    empirica_session_id = get_active_session_id(session_id)

    if not project_path:
        logger.debug("  No active project path, skipping extraction")
        return {"skipped": "no_project"}

    # Read file content
    content = _get_file_content(file_path)
    if not content.strip():
        return {"skipped": "empty_file"}

    # Import extraction and storage
    try:
        from empirica.core.codebase_model.extractor import extract_entities_from_content
        from empirica.data.session_database import SessionDatabase
    except ImportError as e:
        logger.debug(f"  Import failed: {e}")
        return {"skipped": "import_error"}

    # Get project_id from database
    db_path = Path(project_path) / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        logger.debug(f"  No sessions.db at {db_path}")
        return {"skipped": "no_db"}

    try:
        db = SessionDatabase(str(db_path))
    except Exception as e:
        logger.debug(f"  Cannot open DB: {e}")
        return {"skipped": "db_error"}

    try:
        # Get project_id from session
        project_id = None
        if empirica_session_id:
            cursor = db.conn.cursor()
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (empirica_session_id,))
            row = cursor.fetchone()
            if row:
                project_id = row[0] if isinstance(row, tuple) else row["project_id"]

        # Make file_path relative to project root for cleaner storage
        try:
            rel_path = str(Path(file_path).relative_to(project_path))
        except ValueError:
            rel_path = file_path

        # Extract entities and relationships
        entities, relationships = extract_entities_from_content(
            rel_path,
            content,
            project_id=project_id,
            session_id=empirica_session_id,
        )

        if not entities and not relationships:
            return {"entities": 0, "relationships": 0}

        # Store entities
        entity_id_map = {}  # name -> entity_id (for relationship resolution)
        for entity in entities:
            eid = db.codebase_model.upsert_entity(
                name=entity.name,
                entity_type=entity.entity_type,
                file_path=entity.file_path,
                signature=entity.signature,
                project_id=project_id,
                session_id=empirica_session_id,
                metadata=entity.metadata,
            )
            entity_id_map[entity.name] = eid

        # Store relationships (resolve names to IDs where possible)
        rel_count = 0
        for rel in relationships:
            source_id = entity_id_map.get(rel.source_entity_id)
            target_id = entity_id_map.get(rel.target_entity_id)

            # Skip if we can't resolve both ends
            if not source_id or not target_id:
                continue

            db.codebase_model.upsert_relationship(
                source_entity_id=source_id,
                target_entity_id=target_id,
                relationship_type=rel.relationship_type,
                project_id=project_id,
            )
            rel_count += 1

        # Invalidate entities in this file that weren't seen
        existing = db.codebase_model.entities_for_file(rel_path, project_id=project_id)
        current_names = {e.name for e in entities}
        for existing_entity in existing:
            if existing_entity["name"] not in current_names:
                db.codebase_model.invalidate_entity(existing_entity["id"])

        return {
            "entities": len(entities),
            "relationships": rel_count,
            "invalidated": sum(1 for e in existing if e["name"] not in current_names),
        }

    finally:
        db.close()


def _decision_record():
    """empirica/core/decision_record.py, loaded from its file. Importing it through the package runs empirica/core's own
    imports (about 22 ms measured), and this runs after every call. None when empirica is not installed. The same function is
    copied into each hook that records an outcome; tests/test_decision_record.py keeps the copies identical."""
    try:
        import importlib.util

        spec = importlib.util.find_spec("empirica")
        if spec is None or not spec.submodule_search_locations:
            return None
        path = Path(next(iter(spec.submodule_search_locations))) / "core" / "decision_record.py"
        module_spec = importlib.util.spec_from_file_location("_empirica_decision_record", path)
        if module_spec is None or module_spec.loader is None:
            return None
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _record_outcome(hook_input, outcome: str) -> None:
    record = _decision_record()
    if record is not None:
        record.record_outcome(hook_input, outcome)


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    _record_outcome(hook_input, "success")

    tool_name = hook_input.get("tool_name", "unknown")
    tool_input = hook_input.get("tool_input", {})
    session_id = hook_input.get("session_id", "")

    # Only process Edit and Write
    if tool_name not in ("Edit", "Write"):
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    logger.info(f"PostToolUse: {tool_name} {file_path}")

    if not _should_extract(file_path):
        logger.debug(f"  Skipping extraction for {file_path}")
        sys.exit(0)

    try:
        result = _extract_and_store(file_path, session_id)
        _last_extracted[file_path] = time.time()
        _save_last_extracted(_last_extracted)
        logger.info(f"  Extracted: {result}")
    except Exception as e:
        logger.warning(f"  Extraction failed: {e}")

    sys.exit(0)


if __name__ == "__main__":
    main()
