"""
Artifact Log Commands - Noetic artifact logging (findings, unknowns, dead-ends, etc.)

Split from project_commands.py for maintainability.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional
from empirica.utils.session_resolver import InstanceResolver as R
from ..cli_utils import handle_cli_error
from .project_commands import get_workspace_db_path

logger = logging.getLogger(__name__)


def _is_uuid(s: str) -> bool:
    """Check if a string looks like a UUID."""
    import re
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', s, re.I))


def _parse_config_input(args):
    """Parse config from stdin, file, or None. Shared across all artifact handlers."""
    import os
    import sys
    from empirica.cli.cli_utils import parse_json_safely

    config_data = None
    if hasattr(args, 'config') and args.config:
        if args.config == '-':
            config_data = parse_json_safely(sys.stdin.read())
        else:
            if not os.path.exists(args.config):
                print(json.dumps({"ok": False, "error": f"Config file not found: {args.config}"}))
                sys.exit(1)
            with open(args.config, 'r') as f:
                config_data = parse_json_safely(f.read())
    return config_data


def _resolve_artifact_context(config_data, args, required_fields=None):
    """Resolve common context needed by all artifact handlers.

    Consolidates session resolution, project resolution, entity params,
    transaction ID, goal auto-link, ai_id lookup, and subject detection
    into a single call. Each handler previously did this independently.

    Returns dict with: session_id, project_id, goal_id, transaction_id,
    entity_type, entity_id, via, ai_id, subject, output_format, db.
    Caller is responsible for closing db.
    """
    import sys
    from empirica.data.session_database import SessionDatabase

    # Extract common fields from config or args
    output_format = 'json' if config_data else getattr(args, 'output', 'json')
    session_id = (config_data or {}).get('session_id') or getattr(args, 'session_id', None)
    project_id = (config_data or {}).get('project_id') or getattr(args, 'project_id', None)
    goal_id = (config_data or {}).get('goal_id') or getattr(args, 'goal_id', None)
    subtask_id = (config_data or {}).get('subtask_id') or getattr(args, 'subtask_id', None)
    impact = (config_data or {}).get('impact') or getattr(args, 'impact', None)

    # Entity scoping
    entity_type, entity_id, via = _extract_entity_params(config_data, args)

    # Session auto-derive
    if not session_id:
        session_id = R.session_id()

    # Cross-project detection + synthetic provenance
    is_cross_project = False
    if project_id:
        try:
            current_path = R.project_path()
            current_project_id = R.project_id_from_db(current_path) if current_path else None
            is_cross_project = current_project_id is None or project_id != current_project_id
        except Exception:
            is_cross_project = True
    if not session_id and is_cross_project:
        session_id = "cross-project"

    # Validate required fields
    if required_fields:
        missing = [f for f in required_fields if not ((config_data or {}).get(f) or getattr(args, f, None))]
        if not session_id or missing:
            print(json.dumps({
                "ok": False,
                "error": f"Missing required: {', '.join(['session_id'] + missing) if not session_id else ', '.join(missing)}",
                "hint": "Either run PREFLIGHT first, or provide --session-id explicitly"
            }))
            sys.exit(1)

    # Subject auto-detect
    subject = (config_data or {}).get('subject') or getattr(args, 'subject', None)
    if subject is None:
        try:
            from empirica.config.project_config_loader import get_current_subject
            subject = get_current_subject()
        except Exception:
            pass

    # Resolve DB (cross-project aware)
    db, project_id = _resolve_db_for_artifact(project_id)

    # Project ID resolution cascade
    if not project_id and session_id:
        try:
            cursor = db.conn.cursor()
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row and row['project_id']:
                project_id = row['project_id']
        except Exception:
            pass

    if not project_id:
        try:
            ctx = R.context()
            if ctx and ctx.get('project_id'):
                project_id = ctx['project_id']
        except Exception:
            pass

    if not project_id:
        try:
            from empirica.cli.utils.project_resolver import resolve_project_id
            project_id = resolve_project_id(project_id, db)
        except Exception:
            pass

    if not project_id and session_id:
        import hashlib
        project_id = hashlib.md5(f"session-{session_id}".encode()).hexdigest()

    # Goal auto-link
    if not goal_id and session_id:
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT id FROM goals WHERE session_id = ? AND is_completed = 0 ORDER BY created_timestamp DESC LIMIT 1",
                (session_id,))
            row = cursor.fetchone()
            if row:
                goal_id = row['id'] if hasattr(row, 'keys') else row[0]
        except Exception:
            pass

    # Transaction ID
    transaction_id = None
    try:
        transaction_id = R.transaction_id()
    except Exception:
        pass

    # AI ID from session
    ai_id = 'claude-code'
    try:
        cursor = db.conn.cursor()
        cursor.execute("SELECT ai_id FROM sessions WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            val = row['ai_id'] if hasattr(row, 'keys') else row[0]
            if val:
                ai_id = val
    except Exception:
        pass

    # Entity defaults
    resolved_entity_type = entity_type or 'project'
    resolved_entity_id = entity_id or (project_id if resolved_entity_type == 'project' else None)

    return {
        'session_id': session_id,
        'project_id': project_id,
        'goal_id': goal_id,
        'subtask_id': subtask_id,
        'impact': impact,
        'subject': subject,
        'output_format': output_format,
        'entity_type': resolved_entity_type,
        'entity_id': resolved_entity_id,
        'via': via,
        'transaction_id': transaction_id,
        'ai_id': ai_id,
        'db': db,
        'is_cross_project': is_cross_project,
    }


def _resolve_db_for_artifact(project_id: Optional[str]):
    """Resolve the correct SessionDatabase for artifact writing.

    If project_id is a project name (not UUID), attempts cross-project
    write by resolving the target project's DB. Falls back to local DB.

    Returns (db, resolved_project_id) tuple.
    """
    from empirica.data.session_database import SessionDatabase

    if project_id and not _is_uuid(project_id):
        cross_db = _get_db_for_project(project_id)
        if cross_db:
            # Resolve the name to UUID in the target DB
            resolved = cross_db.resolve_project_id(project_id)
            logger.info(f"Cross-project write: targeting '{project_id}' → {resolved[:8] if resolved else '?'}...")
            return cross_db, resolved
        else:
            logger.warning(f"Could not resolve project '{project_id}' for cross-project write, using local DB")

    return SessionDatabase(), project_id


def _get_db_for_project(project_name_or_id: str):
    """Get SessionDatabase for a specific project by name or UUID.

    Resolves project → trajectory_path (from workspace.db) → sessions.db.
    Used for cross-project artifact writing without project-switch.

    Args:
        project_name_or_id: Project name (e.g., "empirica-cortex") or UUID

    Returns:
        SessionDatabase instance connected to the target project's DB,
        or None if the project can't be resolved.
    """
    from empirica.data.session_database import SessionDatabase
    import sqlite3

    workspace_db = get_workspace_db_path()
    if not workspace_db.exists():
        return None

    try:
        conn = sqlite3.connect(str(workspace_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Try by name first, then by UUID
        cursor.execute(
            "SELECT trajectory_path FROM global_projects WHERE name = ? OR id = ?",
            (project_name_or_id, project_name_or_id)
        )
        row = cursor.fetchone()
        conn.close()

        if not row or not row['trajectory_path']:
            return None

        trajectory_path = row['trajectory_path']
        # trajectory_path may point to .empirica/ dir or project root
        if trajectory_path.endswith('.empirica'):
            db_path = Path(trajectory_path) / 'sessions' / 'sessions.db'
        else:
            db_path = Path(trajectory_path) / '.empirica' / 'sessions' / 'sessions.db'

        if not db_path.exists():
            logger.warning(f"Cross-project DB not found: {db_path}")
            return None

        return SessionDatabase(db_path=str(db_path))

    except Exception as e:
        logger.warning(f"Failed to resolve project DB for '{project_name_or_id}': {e}")
        return None


def _create_entity_artifact_link(
    artifact_type: str,
    artifact_id: str,
    entity_type: str,
    entity_id: str,
    project_path: Optional[str] = None,
    discovered_via: Optional[str] = None,
    transaction_id: Optional[str] = None,
    engagement_id: Optional[str] = None,
):
    """Create cross-reference in workspace.db entity_artifacts table.

    Called after artifact insert when entity_type is not 'project'.
    Links artifacts in sessions.db to entities (org, contact, engagement)
    in workspace.db for cross-entity discovery.
    """
    if not entity_type or entity_type == 'project':
        return  # No cross-link needed for project-scoped artifacts

    import time
    import uuid

    workspace_db = get_workspace_db_path()
    if not workspace_db.exists():
        logger.debug("Workspace DB not found, skipping entity_artifacts link")
        return

    # Resolve artifact_source (trajectory_path for this project)
    if not project_path:
        try:

            project_path = R.project_path()
        except Exception:
            pass

    # artifact_source = trajectory_path (.empirica dir), NOT full sessions.db path
    # EntityArtifactStore._populate_content() appends /sessions/sessions.db
    artifact_source = str(Path(project_path) / '.empirica') if project_path else None

    try:
        conn = sqlite3.connect(str(workspace_db))
        conn.execute("""
            INSERT OR IGNORE INTO entity_artifacts (
                id, artifact_type, artifact_id, artifact_source,
                entity_type, entity_id, relationship, relevance,
                discovered_via, engagement_id, transaction_id,
                created_at, created_by_ai
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            artifact_type,
            artifact_id,
            artifact_source,
            entity_type,
            entity_id,
            'about',  # default relationship
            1.0,
            discovered_via,
            engagement_id,
            transaction_id,
            time.time(),
            'claude-code',
        ))
        conn.commit()
        conn.close()
        logger.info(f"🔗 Entity artifact linked: {artifact_type} → {entity_type}/{entity_id[:8]}...")
    except Exception as e:
        logger.debug(f"Entity artifact link failed (non-fatal): {e}")


def _extract_entity_params(config_data, args):
    """Extract entity_type, entity_id, via from config or CLI args.

    Falls back to active engagement if no entity explicitly specified.
    Returns (entity_type, entity_id, via) tuple.
    """
    if config_data:
        entity_type = config_data.get('entity_type')
        entity_id = config_data.get('entity_id')
        via = config_data.get('via')
    else:
        entity_type = getattr(args, 'entity_type', None)
        entity_id = getattr(args, 'entity_id', None)
        via = getattr(args, 'via', None)

    # Auto-inherit from active engagement if no entity explicitly specified
    if not entity_type or entity_type == 'project':
        try:

            active_eng = R.engagement()
            if active_eng:
                entity_type = 'engagement'
                entity_id = active_eng
        except Exception:
            pass

    return entity_type, entity_id, via


def handle_engagement_focus_command(args):
    """Handle engagement-focus command — set active engagement for auto-linking."""
    try:
        from empirica.utils.session_resolver import set_active_engagement

        if getattr(args, 'clear', False):
            # Clear engagement by setting to None
            tx_data = R.transaction_read()
            if tx_data and tx_data.get('active_engagement'):
                import os
                import tempfile
                from pathlib import Path

                tx_data.pop('active_engagement', None)
                tx_data['updated_at'] = __import__('time').time()

                # Find the transaction file path (same logic as set_active_engagement)
                suffix = R.instance_suffix()
                project_path = R.project_path()
                if project_path:
                    tx_path = Path(project_path) / '.empirica' / f'active_transaction{suffix}.json'
                else:
                    tx_path = Path.home() / '.empirica' / f'active_transaction{suffix}.json'

                tmp_fd, tmp_path = tempfile.mkstemp(dir=str(tx_path.parent))
                try:
                    with os.fdopen(tmp_fd, 'w') as tmp_f:
                        json.dump(tx_data, tmp_f, indent=2)
                    os.rename(tmp_path, str(tx_path))
                except BaseException:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

                print(json.dumps({"ok": True, "action": "cleared"}))
            else:
                print(json.dumps({"ok": True, "action": "no_engagement_set"}))
            return

        engagement_id = getattr(args, 'engagement_id', None)
        if not engagement_id:
            print(json.dumps({"ok": False, "error": "engagement_id required"}))
            return

        ok = set_active_engagement(engagement_id)
        if ok:
            print(json.dumps({
                "ok": True,
                "engagement_id": engagement_id,
                "message": f"Engagement focused: {engagement_id}. All artifacts will auto-link.",
            }))
        else:
            print(json.dumps({
                "ok": False,
                "error": "No active transaction. Run PREFLIGHT first.",
            }))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))

def handle_finding_log_command(args):
    """Handle finding-log command - AI-first with config file support"""
    db = None
    try:
        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=['finding'])
        db = ctx['db']

        # Extract finding-specific fields
        finding = (config_data or {}).get('finding') or getattr(args, 'finding', None)

        # Show project context (quiet mode)
        if ctx['output_format'] != 'json':
            from empirica.cli.cli_utils import print_project_context
            print_project_context(quiet=True)

        # Store to SQLite (durable)
        finding_id = db.log_finding(
            project_id=ctx['project_id'],
            session_id=ctx['session_id'],
            finding=finding,
            goal_id=ctx['goal_id'],
            subtask_id=ctx['subtask_id'],
            subject=ctx['subject'],
            impact=ctx['impact'],
            transaction_id=ctx['transaction_id'],
            entity_type=ctx['entity_type'],
            entity_id=ctx['entity_id']
        )

        # Entity cross-link
        if ctx['via'] and ctx['entity_type'] != 'project' and ctx['entity_id']:
            _create_entity_artifact_link(
                artifact_type='finding', artifact_id=finding_id,
                entity_type=ctx['entity_type'], entity_id=ctx['entity_id'],
                discovered_via=ctx['via'], transaction_id=ctx['transaction_id'],
            )

        # Aliases for readability in unique logic below
        project_id = ctx['project_id']
        session_id = ctx['session_id']
        ai_id = ctx['ai_id']
        goal_id = ctx['goal_id']
        subtask_id = ctx['subtask_id']
        subject = ctx['subject']
        impact = ctx['impact']
        output_format = ctx['output_format']
        entity_type = ctx['entity_type']
        entity_id = ctx['entity_id']
        via = ctx['via']

        db.close()
        db = None  # Prevent double-close in finally

        # GIT NOTES: Store finding in git notes for sync (canonical source)
        git_stored = False
        try:
            from empirica.core.canonical.empirica_git.finding_store import GitFindingStore
            git_store = GitFindingStore()

            git_stored = git_store.store_finding(
                finding_id=finding_id,
                project_id=project_id,
                session_id=session_id,
                ai_id=ai_id,
                finding=finding,
                impact=impact,
                goal_id=goal_id,
                subtask_id=subtask_id,
                subject=subject
            )
            if git_stored:
                logger.info(f"✓ Finding {finding_id[:8]} stored in git notes")
        except Exception as git_err:
            # Non-fatal - log but continue
            logger.warning(f"Git notes storage failed: {git_err}")

        # AUTO-EMBED: Add finding to Qdrant for semantic search
        embedded = False
        if project_id and finding_id:
            try:
                from empirica.core.qdrant.vector_store import embed_single_memory_item
                from datetime import datetime
                embedded = embed_single_memory_item(
                    project_id=project_id,
                    item_id=finding_id,
                    text=finding,
                    item_type='finding',
                    session_id=session_id,
                    goal_id=goal_id,
                    subtask_id=subtask_id,
                    subject=subject,
                    impact=impact,
                    timestamp=datetime.now().isoformat()
                )
            except Exception as embed_err:
                # Non-fatal - log but continue
                logger.warning(f"Auto-embed failed: {embed_err}")

        # EIDETIC MEMORY: Extract fact and add to eidetic layer for confidence tracking
        eidetic_result = None
        if project_id and finding_id:
            try:
                from empirica.core.qdrant.vector_store import (
                    embed_eidetic,
                    confirm_eidetic_fact,
                )
                import hashlib

                # Content hash for deduplication
                content_hash = hashlib.md5(finding.encode()).hexdigest()

                # Try to confirm existing fact first
                confirmed = confirm_eidetic_fact(project_id, content_hash, session_id)
                if confirmed:
                    eidetic_result = "confirmed"
                    logger.debug(f"Confirmed existing eidetic fact: {content_hash[:8]}")
                else:
                    # Create new eidetic entry
                    eidetic_created = embed_eidetic(
                        project_id=project_id,
                        fact_id=finding_id,
                        content=finding,
                        fact_type="fact",
                        domain=subject,  # Use subject as domain hint
                        confidence=0.5 + ((impact or 0.5) * 0.2),  # Higher impact → higher initial confidence
                        confirmation_count=1,
                        source_sessions=[session_id] if session_id else [],
                        source_findings=[finding_id],
                        tags=[subject] if subject else [],
                    )
                    if eidetic_created:
                        eidetic_result = "created"
                        logger.debug(f"Created new eidetic fact: {finding_id}")
            except Exception as eidetic_err:
                # Non-fatal - log but continue
                logger.warning(f"Eidetic ingestion failed: {eidetic_err}")

        # IMMUNE SYSTEM: Decay related lessons when findings are logged
        # This implements the pattern where new learnings naturally supersede old lessons
        # CENTRAL TOLERANCE: Scope decay to finding's domain to prevent autoimmune attacks
        decayed_lessons = []
        try:
            from empirica.core.lessons.storage import LessonStorageManager
            lesson_storage = LessonStorageManager()
            decayed_lessons = lesson_storage.decay_related_lessons(
                finding_text=finding,
                domain=subject,  # Central tolerance: only decay lessons in same domain
                decay_amount=0.05,  # 5% decay per related finding
                min_confidence=0.3,  # Floor at 30%
                keywords_threshold=2  # Require at least 2 keyword matches
            )
            if decayed_lessons:
                logger.info(f"IMMUNE: Decayed {len(decayed_lessons)} related lessons in domain '{subject}'")

                # Cross-layer sync: propagate YAML lesson decay to Qdrant payloads
                try:
                    from empirica.core.qdrant.vector_store import propagate_lesson_confidence_to_qdrant
                    if project_id:
                        for dl in decayed_lessons:
                            propagate_lesson_confidence_to_qdrant(
                                project_id,
                                dl.get('name', ''),
                                dl.get('new_confidence', 0.3)
                            )
                except Exception as qdrant_sync_err:
                    logger.debug(f"Lesson→Qdrant sync skipped: {qdrant_sync_err}")
        except Exception as decay_err:
            # Non-fatal - log but continue
            logger.debug(f"Lesson decay check failed: {decay_err}")

        # Cross-layer: decay eidetic facts that contradict this finding
        # Only when a domain/subject is provided — domainless findings spray too broadly
        eidetic_decayed = 0
        try:
            from empirica.core.qdrant.vector_store import decay_eidetic_by_finding
            if project_id and subject:
                eidetic_decayed = decay_eidetic_by_finding(
                    project_id,
                    finding,
                    domain=subject,
                )
                if eidetic_decayed:
                    logger.info(f"IMMUNE: Decayed {eidetic_decayed} eidetic facts by finding in domain '{subject}'")
        except Exception as eidetic_err:
            logger.debug(f"Eidetic decay skipped: {eidetic_err}")

        result = {
            "ok": True,
            "finding_id": finding_id,
            "project_id": project_id if project_id else None,
            "session_id": session_id,
            "entity_type": entity_type or 'project',
            "entity_id": entity_id,
            "via": via,
            "git_stored": git_stored,  # Git notes for sync
            "embedded": embedded,
            "eidetic": eidetic_result,  # "created" | "confirmed" | None
            "immune_decay": decayed_lessons if decayed_lessons else None,  # Lessons affected by this finding
            "eidetic_decayed": eidetic_decayed if eidetic_decayed else None,
            "message": "Finding logged to project scope"
        }

        # Format output (AI-first = JSON by default)
        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            # Human-readable output (legacy)
            print(f"✅ Finding logged successfully")
            print(f"   Finding ID: {finding_id}")
            if project_id:
                print(f"   Project: {project_id[:8]}...")
            if git_stored:
                print(f"   📝 Stored in git notes for sync")
            if embedded:
                print(f"   🔍 Auto-embedded for semantic search")
            if decayed_lessons:
                print(f"   🛡️ IMMUNE: Decayed {len(decayed_lessons)} related lesson(s)")
                for dl in decayed_lessons:
                    print(f"      - {dl['name']}: {dl['previous_confidence']:.2f} → {dl['new_confidence']:.2f}")

        return 0  # Success

    except Exception as e:
        handle_cli_error(e, "Finding log", getattr(args, 'verbose', False))
        return None
    finally:
        if db is not None:
            db.close()


def handle_unknown_log_command(args):
    """Handle unknown-log command - AI-first with config file support"""
    db = None
    try:
        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=['unknown'])
        db = ctx['db']

        # Extract unknown-specific fields
        unknown = (config_data or {}).get('unknown') or getattr(args, 'unknown', None)

        # Show project context (quiet mode)
        if ctx['output_format'] != 'json':
            from empirica.cli.cli_utils import print_project_context
            print_project_context(quiet=True)

        # Store to SQLite (durable)
        unknown_id = db.log_unknown(
            project_id=ctx['project_id'],
            session_id=ctx['session_id'],
            unknown=unknown,
            goal_id=ctx['goal_id'],
            subtask_id=ctx['subtask_id'],
            subject=ctx['subject'],
            impact=ctx['impact'],
            transaction_id=ctx['transaction_id'],
            entity_type=ctx['entity_type'],
            entity_id=ctx['entity_id']
        )

        # Entity cross-link
        if ctx['via'] and ctx['entity_type'] != 'project' and ctx['entity_id']:
            _create_entity_artifact_link(
                artifact_type='unknown',
                artifact_id=unknown_id,
                entity_type=ctx['entity_type'],
                entity_id=ctx['entity_id'],
                discovered_via=ctx['via'],
                transaction_id=ctx['transaction_id'],
            )

        # Aliases for readability in unique logic below
        project_id = ctx['project_id']
        session_id = ctx['session_id']
        ai_id = ctx['ai_id']
        goal_id = ctx['goal_id']
        subtask_id = ctx['subtask_id']
        subject = ctx['subject']
        impact = ctx['impact']
        output_format = ctx['output_format']

        db.close()
        db = None  # Prevent double-close in finally

        # GIT NOTES: Store unknown in git notes for sync (canonical source)
        git_stored = False
        try:
            from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore
            git_store = GitUnknownStore()

            git_stored = git_store.store_unknown(
                unknown_id=unknown_id,
                project_id=project_id,
                session_id=session_id,
                ai_id=ai_id,
                unknown=unknown,
                goal_id=goal_id,
                subtask_id=subtask_id
            )
            if git_stored:
                logger.info(f"✓ Unknown {unknown_id[:8]} stored in git notes")
        except Exception as git_err:
            # Non-fatal - log but continue
            logger.warning(f"Git notes storage failed: {git_err}")

        # AUTO-EMBED: Add unknown to Qdrant for semantic search
        embedded = False
        if project_id and unknown_id:
            try:
                from empirica.core.qdrant.vector_store import embed_single_memory_item
                from datetime import datetime
                embedded = embed_single_memory_item(
                    project_id=project_id,
                    item_id=unknown_id,
                    text=unknown,
                    item_type='unknown',
                    session_id=session_id,
                    goal_id=goal_id,
                    subtask_id=subtask_id,
                    subject=subject,
                    impact=impact,
                    is_resolved=False,
                    timestamp=datetime.now().isoformat()
                )
            except Exception as embed_err:
                # Non-fatal - log but continue
                logger.warning(f"Auto-embed failed: {embed_err}")

        result = {
            "ok": True,
            "unknown_id": unknown_id,
            "project_id": project_id if project_id else None,
            "session_id": session_id,
            "entity_type": ctx['entity_type'],
            "entity_id": ctx['entity_id'],
            "via": ctx['via'],
            "git_stored": git_stored,  # Git notes for sync
            "embedded": embedded,
            "message": "Unknown logged to project scope"
        }

        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Unknown logged successfully")
            print(f"   Unknown ID: {unknown_id}")
            if project_id:
                print(f"   Project: {project_id[:8]}...")
            if git_stored:
                print(f"   📝 Stored in git notes for sync")
            if embedded:
                print(f"   🔍 Auto-embedded for semantic search")

        return 0  # Success

    except Exception as e:
        handle_cli_error(e, "Unknown log", getattr(args, 'verbose', False))
        return None
    finally:
        if db is not None:
            db.close()


def handle_unknown_resolve_command(args):
    """Handle unknown-resolve command"""
    try:
        from empirica.data.session_database import SessionDatabase

        unknown_id = getattr(args, 'unknown_id', None)
        resolved_by = getattr(args, 'resolved_by', None)
        output_format = getattr(args, 'output', 'json')

        if not unknown_id or not resolved_by:
            result = {
                "ok": False,
                "error": "unknown_id and resolved_by are required"
            }
            print(json.dumps(result))
            return 1

        # Resolve the unknown
        db = SessionDatabase()
        db.resolve_unknown(unknown_id=unknown_id, resolved_by=resolved_by)
        db.close()

        # Format output
        result = {
            "ok": True,
            "unknown_id": unknown_id,
            "resolved_by": resolved_by,
            "message": "Unknown resolved successfully"
        }

        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Unknown resolved successfully")
            print(f"   Unknown ID: {unknown_id[:8]}...")
            print(f"   Resolved by: {resolved_by}")

        return 0

    except Exception as e:
        handle_cli_error(e, "Unknown resolve", getattr(args, 'verbose', False))
        return 1


def handle_unknown_list_command(args):
    """Handle unknown-list command - list project unknowns with optional filters.

    Unknowns are PROJECT-SCOPED. Auto-derives project_id from active context.
    """
    try:
        from empirica.data.session_database import SessionDatabase

        session_id = getattr(args, 'session_id', None)
        project_id = getattr(args, 'project_id', None)
        show_resolved = getattr(args, 'resolved', False)
        show_all = getattr(args, 'show_all', False)
        subject = getattr(args, 'subject', None)
        limit = getattr(args, 'limit', 30)
        output_format = getattr(args, 'output', 'human')

        db = SessionDatabase()
        cursor = db.conn.cursor()

        # Auto-derive project_id from context
        if not project_id:
            if session_id:
                cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    project_id = row[0]

            if not project_id:
                try:
        
                    context = R.context()
                    ctx_session = context.get('empirica_session_id')
                    if ctx_session:
                        cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (ctx_session,))
                        row = cursor.fetchone()
                        if row and row[0]:
                            project_id = row[0]
                except Exception:
                    pass

        # Build query
        query = """
            SELECT id, unknown, is_resolved, resolved_by, impact, subject,
                   created_timestamp, resolved_timestamp, goal_id
            FROM project_unknowns
            WHERE 1=1
        """
        params = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)

        if not show_all:
            if show_resolved:
                query += " AND is_resolved = 1"
            else:
                query += " AND is_resolved = 0"

        if subject:
            query += " AND subject = ?"
            params.append(subject)

        query += " ORDER BY created_timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        unknowns = []
        for row in rows:
            unknowns.append({
                "id": row[0],
                "unknown": row[1],
                "is_resolved": bool(row[2]),
                "resolved_by": row[3],
                "impact": row[4],
                "subject": row[5],
                "created_at": row[6],
                "resolved_at": row[7],
                "goal_id": row[8],
            })

        db.close()

        # Build filter description
        filters_applied = []
        if project_id:
            filters_applied.append(f"project={project_id[:8]}...")
        if subject:
            filters_applied.append(f"subject={subject}")
        filter_desc = ", ".join(filters_applied) if filters_applied else "all"
        status_desc = "all" if show_all else ("resolved" if show_resolved else "open")

        result = {
            "ok": True,
            "unknowns_count": len(unknowns),
            "unknowns": unknowns,
            "filters": {
                "project_id": project_id,
                "status": status_desc,
                "subject": subject,
            },
        }

        if output_format == 'json':
            return result
        else:
            print(f"{'=' * 70}")
            print(f"❓ UNKNOWNS ({status_desc.upper()}) - {len(unknowns)} found [{filter_desc}]")
            print(f"{'=' * 70}")
            print()

            if not unknowns:
                print("   (No unknowns found)")
            else:
                for i, u in enumerate(unknowns, 1):
                    status_emoji = "✅" if u['is_resolved'] else "❓"
                    impact_str = f" [impact={u['impact']:.1f}]" if u['impact'] else ""
                    print(f"{status_emoji} {i}. {u['unknown'][:75]}")
                    resolved_info = f" | Resolved: {u['resolved_by'][:30]}" if u['resolved_by'] else ""
                    goal_info = f" | Goal: {u['goal_id'][:8]}" if u['goal_id'] else ""
                    print(f"   ID: {u['id'][:8]}...{impact_str}{goal_info}{resolved_info}")
                    print()

            return None

    except Exception as e:
        handle_cli_error(e, "Unknown list", getattr(args, 'verbose', False))
        return 1


def handle_deadend_log_command(args):
    """Handle deadend-log command - AI-first with config file support"""
    db = None
    try:
        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=['approach', 'why_failed'])
        db = ctx['db']

        # Extract deadend-specific fields
        approach = (config_data or {}).get('approach') or getattr(args, 'approach', None)
        why_failed = (config_data or {}).get('why_failed') or getattr(args, 'why_failed', None)

        # Store to SQLite (durable)
        dead_end_id = db.log_dead_end(
            project_id=ctx['project_id'],
            session_id=ctx['session_id'],
            approach=approach,
            why_failed=why_failed,
            goal_id=ctx['goal_id'],
            subtask_id=ctx['subtask_id'],
            subject=ctx['subject'],
            impact=ctx['impact'],
            transaction_id=ctx['transaction_id'],
            entity_type=ctx['entity_type'],
            entity_id=ctx['entity_id']
        )

        # Entity cross-link
        if ctx['via'] and ctx['entity_type'] != 'project' and ctx['entity_id']:
            _create_entity_artifact_link(
                artifact_type='dead_end',
                artifact_id=dead_end_id,
                entity_type=ctx['entity_type'],
                entity_id=ctx['entity_id'],
                discovered_via=ctx['via'],
                transaction_id=ctx['transaction_id'],
            )

        # Aliases for readability in unique logic below
        project_id = ctx['project_id']
        session_id = ctx['session_id']
        ai_id = ctx['ai_id']
        goal_id = ctx['goal_id']
        subtask_id = ctx['subtask_id']
        output_format = ctx['output_format']

        db.close()
        db = None  # Prevent double-close in finally

        # GIT NOTES: Store dead end in git notes for sync (canonical source)
        git_stored = False
        try:
            from empirica.core.canonical.empirica_git.dead_end_store import GitDeadEndStore
            git_store = GitDeadEndStore()

            git_stored = git_store.store_dead_end(
                dead_end_id=dead_end_id,
                project_id=project_id,
                session_id=session_id,
                ai_id=ai_id,
                approach=approach,
                why_failed=why_failed,
                goal_id=goal_id,
                subtask_id=subtask_id
            )
            if git_stored:
                logger.info(f"✓ Dead end {dead_end_id[:8]} stored in git notes")
        except Exception as git_err:
            # Non-fatal - log but continue
            logger.warning(f"Git notes storage failed: {git_err}")

        # AUTO-EMBED: Add dead end to Qdrant for semantic search
        # Without this, dead-ends are invisible to pattern_retrieval.py
        # at PREFLIGHT/CHECK — the most valuable anti-pattern data is unsearchable.
        embedded = False
        if project_id and dead_end_id:
            try:
                from empirica.core.qdrant.memory import embed_single_memory_item
                from datetime import datetime
                text = f"DEAD END: {approach} Why failed: {why_failed}"
                embedded = embed_single_memory_item(
                    project_id=project_id,
                    item_id=dead_end_id,
                    text=text,
                    item_type='dead_end',
                    session_id=session_id,
                    goal_id=goal_id,
                    subtask_id=subtask_id,
                    timestamp=datetime.now().isoformat()
                )
            except Exception as embed_err:
                logger.warning(f"Auto-embed failed: {embed_err}")

        result = {
            "ok": True,
            "dead_end_id": dead_end_id,
            "project_id": project_id if project_id else None,
            "session_id": session_id,
            "entity_type": ctx['entity_type'],
            "entity_id": ctx['entity_id'],
            "via": ctx['via'],
            "git_stored": git_stored,
            "embedded": embedded,
            "message": "Dead end logged to project scope"
        }

        if output_format == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Dead end logged successfully")
            print(f"   Dead End ID: {dead_end_id[:8]}...")
            if project_id:
                print(f"   Project: {project_id[:8]}...")
            if git_stored:
                print(f"   📝 Stored in git notes for sync")
            if embedded:
                print(f"   🔍 Auto-embedded for semantic search")

        return 0  # Success

    except Exception as e:
        handle_cli_error(e, "Dead end log", getattr(args, 'verbose', False))
        return None
    finally:
        if db is not None:
            db.close()


def handle_assumption_log_command(args):
    """Handle assumption-log command — log unverified assumptions."""
    db = None
    try:
        import time

        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=['assumption'])
        db = ctx['db']

        # Extract assumption-specific fields
        assumption = (config_data or {}).get('assumption') or getattr(args, 'assumption', None)
        confidence = (config_data or {}).get('confidence', 0.5) or getattr(args, 'confidence', 0.5)
        domain = (config_data or {}).get('domain') or getattr(args, 'domain', None)

        # Store to SQLite (durable)
        assumption_id = db.log_assumption(
            project_id=ctx['project_id'],
            session_id=ctx['session_id'],
            assumption=assumption,
            confidence=confidence,
            domain=domain,
            goal_id=ctx['goal_id'],
            transaction_id=ctx['transaction_id'],
            entity_type=ctx['entity_type'],
            entity_id=ctx['entity_id'],
        )

        # GIT NOTES: Store in git notes for sync
        git_stored = False
        try:
            from empirica.core.canonical.empirica_git.assumption_store import GitAssumptionStore
            git_stored = GitAssumptionStore().store_assumption(
                assumption_id=assumption_id,
                project_id=ctx['project_id'],
                session_id=ctx['session_id'],
                ai_id=ctx['ai_id'],
                assumption=assumption,
                confidence=confidence,
                domain=domain,
                goal_id=ctx['goal_id'],
            )
        except Exception as e:
            logger.debug(f"Git notes storage failed (non-fatal): {e}")

        # Store to Qdrant (semantic search)
        embedded = False
        try:
            from empirica.core.qdrant.vector_store import embed_assumption, _check_qdrant_available
            if _check_qdrant_available():
                embed_assumption(
                    project_id=ctx['project_id'],
                    assumption_id=assumption_id,
                    assumption=assumption,
                    confidence=confidence,
                    status="unverified",
                    entity_type=ctx['entity_type'],
                    entity_id=ctx['entity_id'],
                    session_id=ctx['session_id'],
                    transaction_id=ctx['transaction_id'],
                    domain=domain,
                    timestamp=time.time(),
                )
                embedded = True
        except Exception as e:
            logger.debug(f"Qdrant embed failed (non-fatal): {e}")

        # Entity cross-link
        if ctx['via'] and ctx['entity_type'] != 'project' and ctx['entity_id']:
            _create_entity_artifact_link(
                artifact_type='assumption', artifact_id=assumption_id,
                entity_type=ctx['entity_type'], entity_id=ctx['entity_id'],
                discovered_via=ctx['via'], transaction_id=ctx['transaction_id'],
            )

        result = {
            "ok": True,
            "assumption_id": assumption_id,
            "project_id": ctx['project_id'],
            "entity_type": ctx['entity_type'],
            "entity_id": ctx['entity_id'],
            "assumption": assumption,
            "confidence": confidence,
            "status": "unverified",
            "embedded": embedded,
            "git_stored": git_stored,
            "message": "Assumption logged",
        }

        if ctx['output_format'] == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"Assumption logged: {assumption_id[:8]}...")
            print(f"   Confidence: {confidence}")
            if embedded:
                print(f"   Stored in Qdrant")

        return 0

    except Exception as e:
        handle_cli_error(e, "Assumption log", getattr(args, 'verbose', False))
        return None
    finally:
        if db is not None:
            db.close()


def handle_decision_log_command(args):
    """Handle decision-log command — log decisions with alternatives."""
    db = None
    try:
        import time

        config_data = _parse_config_input(args)
        ctx = _resolve_artifact_context(config_data, args, required_fields=['choice'])
        db = ctx['db']

        # Extract decision-specific fields
        choice = (config_data or {}).get('choice') or getattr(args, 'choice', None)
        rationale = (config_data or {}).get('rationale', '') or getattr(args, 'rationale', '')
        alternatives = (config_data or {}).get('alternatives', '') or getattr(args, 'alternatives', '')
        confidence = (config_data or {}).get('confidence', 0.7) if config_data else getattr(args, 'confidence', 0.7)
        reversibility = (config_data or {}).get('reversibility', 'exploratory') or getattr(args, 'reversibility', 'exploratory')

        # Parse alternatives (comma-separated or JSON list)
        if isinstance(alternatives, str) and alternatives:
            try:
                alternatives_list = json.loads(alternatives)
            except (json.JSONDecodeError, ValueError):
                alternatives_list = [a.strip() for a in alternatives.split(',') if a.strip()]
        elif isinstance(alternatives, list):
            alternatives_list = alternatives
        else:
            alternatives_list = []

        # Store to SQLite (durable)
        decision_id = db.log_decision(
            project_id=ctx['project_id'],
            session_id=ctx['session_id'],
            choice=choice,
            rationale=rationale,
            alternatives=json.dumps(alternatives_list) if alternatives_list else None,
            confidence=confidence,
            reversibility=reversibility,
            goal_id=ctx['goal_id'],
            transaction_id=ctx['transaction_id'],
            entity_type=ctx['entity_type'],
            entity_id=ctx['entity_id'],
        )

        # GIT NOTES
        git_stored = False
        try:
            from empirica.core.canonical.empirica_git.decision_store import GitDecisionStore
            git_stored = GitDecisionStore().store_decision(
                decision_id=decision_id,
                project_id=ctx['project_id'],
                session_id=ctx['session_id'],
                ai_id=ctx['ai_id'],
                choice=choice,
                rationale=rationale,
                alternatives=json.dumps(alternatives_list) if alternatives_list else None,
                confidence=confidence,
                reversibility=reversibility,
                goal_id=ctx['goal_id'],
            )
        except Exception as e:
            logger.debug(f"Git notes storage failed (non-fatal): {e}")

        # Qdrant (semantic search)
        embedded = False
        try:
            from empirica.core.qdrant.vector_store import embed_decision, _check_qdrant_available
            if _check_qdrant_available():
                embed_decision(
                    project_id=ctx['project_id'],
                    decision_id=decision_id,
                    choice=choice,
                    alternatives=json.dumps(alternatives_list),
                    rationale=rationale,
                    confidence_at_decision=confidence,
                    reversibility=reversibility,
                    entity_type=ctx['entity_type'],
                    entity_id=ctx['entity_id'],
                    session_id=ctx['session_id'],
                    transaction_id=ctx['transaction_id'],
                    timestamp=time.time(),
                )
                embedded = True
        except Exception as e:
            logger.debug(f"Qdrant embed failed (non-fatal): {e}")

        # Entity cross-link
        if ctx['via'] and ctx['entity_type'] != 'project' and ctx['entity_id']:
            _create_entity_artifact_link(
                artifact_type='decision', artifact_id=decision_id,
                entity_type=ctx['entity_type'], entity_id=ctx['entity_id'],
                discovered_via=ctx['via'], transaction_id=ctx['transaction_id'],
            )

        result = {
            "ok": True,
            "decision_id": decision_id,
            "project_id": ctx['project_id'],
            "entity_type": ctx['entity_type'],
            "entity_id": ctx['entity_id'],
            "choice": choice,
            "alternatives": alternatives_list,
            "rationale": rationale,
            "confidence": confidence,
            "reversibility": reversibility,
            "embedded": embedded,
            "git_stored": git_stored,
            "message": "Decision logged",
        }

        if ctx['output_format'] == 'json':
            print(json.dumps(result, indent=2))
        else:
            print(f"Decision logged: {decision_id[:8]}...")
            print(f"   Choice: {choice}")
            print(f"   Alternatives: {', '.join(alternatives_list) if alternatives_list else 'none'}")
            print(f"   Reversibility: {reversibility}")
            if embedded:
                print(f"   Stored in Qdrant")

        return 0

    except Exception as e:
        handle_cli_error(e, "Decision log", getattr(args, 'verbose', False))
        return None
    finally:
        if db is not None:
            db.close()
        return None


def handle_refdoc_add_command(args):
    """Handle refdoc-add command (DEPRECATED — use source-add instead)"""
    import sys as _sys
    print("⚠️  refdoc-add is deprecated. Use 'empirica source-add' instead.", file=_sys.stderr)
    print("   Example: empirica source-add --title 'My Doc' --path ./doc.md --noetic", file=_sys.stderr)
    try:
        from empirica.data.session_database import SessionDatabase
        from empirica.cli.utils.project_resolver import resolve_project_id

        # Get project_id from args (required argument)
        project_id = args.project_id
        doc_path = args.doc_path
        doc_type = getattr(args, 'doc_type', None)
        description = getattr(args, 'description', None)

        db = SessionDatabase()

        # Resolve project name to UUID
        project_id = resolve_project_id(project_id, db)

        doc_id = db.add_reference_doc(
            project_id=project_id,
            doc_path=doc_path,
            doc_type=doc_type,
            description=description
        )
        db.close()

        if hasattr(args, 'output') and args.output == 'json':
            result = {
                "ok": True,
                "doc_id": doc_id,
                "project_id": project_id,
                "message": "Reference doc added successfully"
            }
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Reference doc added successfully")
            print(f"   Doc ID: {doc_id}")
            print(f"   Path: {doc_path}")

        return 0  # Success

    except Exception as e:
        handle_cli_error(e, "Reference doc add", getattr(args, 'verbose', False))
        return None


def handle_source_add_command(args):
    """Handle source-add command — entity-agnostic epistemic source logging.

    Sources are bidirectional:
      --noetic: evidence IN (source_used — informed knowledge)
      --praxic: output OUT (source_created — produced by action)
    """
    try:
        import time
        import uuid
        from empirica.data.session_database import SessionDatabase
        from empirica.cli.utils.project_resolver import resolve_project_id

        title = args.title
        description = getattr(args, 'description', None)
        source_type = getattr(args, 'source_type', 'document')
        doc_path = getattr(args, 'path', None)
        source_url = getattr(args, 'url', None)
        confidence = getattr(args, 'confidence', 0.7)
        direction = 'noetic' if getattr(args, 'noetic', False) else 'praxic'
        session_id = getattr(args, 'session_id', None)
        project_id = getattr(args, 'project_id', None)
        output_format = getattr(args, 'output', 'human')

        # Entity scoping (cross-entity provenance)
        entity_type = getattr(args, 'entity_type', None)
        entity_id = getattr(args, 'entity_id', None)
        via = getattr(args, 'via', None)

        # Auto-derive session_id from active transaction
        if not session_id:

            session_id = R.session_id()

        if not session_id:
            print(json.dumps({
                "ok": False,
                "error": "No active transaction and --session-id not provided",
                "hint": "Either run PREFLIGHT first, or provide --session-id explicitly"
            }))
            return 1

        db = SessionDatabase()

        # Auto-resolve project_id from session if not provided
        if not project_id:
            cursor = db.conn.cursor()
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                project_id = row['project_id'] if isinstance(row, dict) else row[0]

        if not project_id:
            print(json.dumps({
                "ok": False,
                "error": "Could not resolve project_id",
                "hint": "Provide --project-id or ensure active session has a project"
            }))
            db.close()
            return 1

        # Auto-derive transaction_id
        transaction_id = None
        try:
            from empirica.cli.command_handlers.workflow_commands import read_active_transaction
            tx = read_active_transaction()
            if tx:
                transaction_id = tx.get('transaction_id')
        except Exception:
            pass

        source_id = str(uuid.uuid4())

        # Build metadata
        metadata = {
            "direction": direction,
            "doc_path": doc_path,
            "source_url": source_url,
            "transaction_id": transaction_id,
        }

        # Default entity scope
        resolved_entity_type = entity_type or 'project'
        resolved_entity_id = entity_id or (project_id if resolved_entity_type == 'project' else None)

        # Insert into epistemic_sources table
        db.conn.execute("""
            INSERT INTO epistemic_sources (
                id, project_id, session_id, source_type, source_url,
                title, description, confidence, epistemic_layer,
                discovered_by_ai, discovered_at, source_metadata,
                entity_type, entity_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            source_id, project_id, session_id, source_type,
            source_url or doc_path,
            title, description, confidence, direction,
            'claude-code', time.time(), json.dumps(metadata),
            resolved_entity_type, resolved_entity_id
        ))
        db.conn.commit()

        # ENTITY CROSS-LINK: If entity is not project, create workspace.db link
        if entity_type and entity_type != 'project' and entity_id:
            _create_entity_artifact_link(
                artifact_type='source',
                artifact_id=source_id,
                entity_type=entity_type,
                entity_id=entity_id,
                discovered_via=via,
                transaction_id=transaction_id,
            )

        # Also add to project_reference_docs for backwards compatibility
        if doc_path:
            try:
                db.add_reference_doc(
                    project_id=project_id,
                    doc_path=doc_path,
                    doc_type=source_type,
                    description=description
                )
            except Exception:
                pass  # Not critical if legacy table fails

        db.close()

        if output_format == 'json':
            print(json.dumps({
                "ok": True,
                "source_id": source_id,
                "project_id": project_id,
                "session_id": session_id,
                "transaction_id": transaction_id,
                "direction": direction,
                "title": title,
                "message": f"Source added ({direction})"
            }, indent=2))
        else:
            direction_emoji = "📥" if direction == 'noetic' else "📤"
            print(f"{direction_emoji} Source added ({direction})")
            print(f"   Source ID: {source_id[:12]}...")
            print(f"   Title: {title}")
            print(f"   Type: {source_type}")
            print(f"   Direction: {direction} ({'evidence IN' if direction == 'noetic' else 'output OUT'})")
            if doc_path:
                print(f"   Path: {doc_path}")
            if source_url:
                print(f"   URL: {source_url}")

        return 0

    except Exception as e:
        handle_cli_error(e, "Source add", getattr(args, 'verbose', False))
        return None


def handle_source_list_command(args):
    """Handle source-list command — list epistemic sources for a project."""
    db = None
    try:
        from empirica.data.session_database import SessionDatabase

        project_id = getattr(args, 'project_id', None)
        source_type_filter = getattr(args, 'source_type', None)
        direction_filter = getattr(args, 'direction', 'all')
        output_format = getattr(args, 'output', 'human')

        db = SessionDatabase()

        # Auto-resolve project_id
        if not project_id:
            try:
                project_path = R.project_path()
                if project_path:
                    project_id = R.project_id_from_db(project_path)
            except Exception:
                pass

        if not project_id:
            print(json.dumps({"ok": False, "error": "Could not resolve project_id"}))
            return 1

        # Query epistemic_sources table
        sources = []
        try:
            query = """
                SELECT id, source_type, title, description, confidence,
                       epistemic_layer, source_url, discovered_at, source_metadata
                FROM epistemic_sources
                WHERE project_id = ?
            """
            params = [project_id]

            if source_type_filter:
                query += " AND source_type = ?"
                params.append(source_type_filter)

            if direction_filter != 'all':
                query += " AND epistemic_layer = ?"
                params.append(direction_filter)

            query += " ORDER BY discovered_at DESC"

            cursor = db.conn.cursor()
            cursor.execute(query, params)
            for row in cursor.fetchall():
                r = dict(row) if hasattr(row, 'keys') else {
                    'id': row[0], 'source_type': row[1], 'title': row[2],
                    'description': row[3], 'confidence': row[4],
                    'direction': row[5], 'url': row[6],
                    'discovered_at': row[7], 'metadata': row[8]
                }
                r['source'] = 'epistemic_sources'
                sources.append(r)
        except Exception as e:
            logger.debug(f"epistemic_sources query failed (table may not exist): {e}")

        # Also query legacy project_reference_docs
        try:
            refdocs = db.get_project_reference_docs(project_id)
            for rd in refdocs:
                # Skip if already in epistemic_sources (by doc_path match)
                doc_path = rd.get('doc_path', '')
                if any(s.get('url') == doc_path or s.get('source_url') == doc_path for s in sources):
                    continue
                sources.append({
                    'id': rd.get('id', ''),
                    'source_type': rd.get('doc_type', 'document'),
                    'title': doc_path.split('/')[-1] if doc_path else 'unknown',
                    'description': rd.get('description', ''),
                    'confidence': None,
                    'direction': 'noetic',
                    'url': doc_path,
                    'discovered_at': None,
                    'source': 'refdoc_legacy',
                })
        except Exception as e:
            logger.debug(f"refdoc query failed: {e}")

        if output_format == 'json':
            print(json.dumps({
                "ok": True,
                "project_id": project_id,
                "count": len(sources),
                "sources": sources,
            }, indent=2))
        else:
            print(f"\n📚 Epistemic Sources ({len(sources)} total)")
            print("=" * 60)
            for s in sources:
                direction = s.get('direction') or s.get('epistemic_layer', '?')
                emoji = "📥" if direction == 'noetic' else "📤"
                conf = f" [{s['confidence']:.1f}]" if s.get('confidence') else ""
                source_tag = f" ({s['source']})" if s.get('source') == 'refdoc_legacy' else ""
                print(f"  {emoji} {s.get('title', '?')}{conf}{source_tag}")
                print(f"     Type: {s.get('source_type', '?')} | Direction: {direction}")
                url = s.get('url') or s.get('source_url', '')
                if url:
                    print(f"     Path: {url}")
                desc = s.get('description', '')
                if desc:
                    print(f"     Desc: {desc[:80]}")
                print()

        return 0

    except Exception as e:
        handle_cli_error(e, "Source list", getattr(args, 'verbose', False))
        return None
    finally:
        if db is not None:
            db.close()

