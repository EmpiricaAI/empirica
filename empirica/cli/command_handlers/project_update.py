"""
Project Update Command - Update project.yaml fields after initialization

Updates static project identity fields and syncs changes to the database.
Supports adding/removing contacts, engagements, edges, tags, and all v2.0 fields.

Usage:
    empirica project-update --type research --domain bio/genomics
    empirica project-update --add-contact alice --roles reviewer evaluator
    empirica project-update --add-edge project/empirica-iris --relation extends
    empirica project-update --migrate  # Upgrade v1.0 to v2.0
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from empirica.config.project_config_loader import ProjectConfig

logger = logging.getLogger(__name__)


def _apply_simple_field_updates(args, config):
    """Apply simple scalar field updates from args. Returns list of change descriptions."""
    changes = []
    for field, attr in [
        ("type", "type"),
        ("domain", "domain"),
        ("classification", "classification"),
        ("status", "status"),
        ("evidence_profile", "evidence_profile"),
    ]:
        value = getattr(args, field.replace("-", "_"), None)
        if value is not None and value != getattr(config, attr):
            setattr(config, attr, value)
            changes.append(f"{field}: {value}")
    return changes


def _apply_list_updates(args, config):
    """Apply languages, tags, contacts, edges updates. Returns list of change descriptions."""
    changes = []

    # Languages
    new_languages = getattr(args, "languages", None)
    if new_languages:
        config.languages = new_languages
        changes.append(f"languages: {new_languages}")

    # Tags
    new_tags = getattr(args, "tags", None)
    if new_tags:
        config.tags = new_tags
        changes.append(f"tags: {new_tags}")

    add_tag = getattr(args, "add_tag", None)
    if add_tag and add_tag not in config.tags:
        config.tags.append(add_tag)
        changes.append(f"+tag: {add_tag}")

    remove_tag = getattr(args, "remove_tag", None)
    if remove_tag and remove_tag in config.tags:
        config.tags.remove(remove_tag)
        changes.append(f"-tag: {remove_tag}")

    # Contacts
    add_contact = getattr(args, "add_contact", None)
    if add_contact:
        roles = getattr(args, "roles", []) or []
        existing = [c for c in config.contacts if c.get("id") == add_contact]
        if existing:
            existing[0]["roles"] = roles
            changes.append(f"updated contact: {add_contact} roles={roles}")
        else:
            config.contacts.append({"id": add_contact, "roles": roles})
            changes.append(f"+contact: {add_contact} roles={roles}")

    remove_contact = getattr(args, "remove_contact", None)
    if remove_contact:
        before = len(config.contacts)
        config.contacts = [c for c in config.contacts if c.get("id") != remove_contact]
        if len(config.contacts) < before:
            changes.append(f"-contact: {remove_contact}")

    # Edges
    add_edge = getattr(args, "add_edge", None)
    if add_edge:
        relation = getattr(args, "relation", "related") or "related"
        existing = [e for e in config.edges if e.get("entity") == add_edge]
        if existing:
            existing[0]["relation"] = relation
            changes.append(f"updated edge: {add_edge} relation={relation}")
        else:
            config.edges.append({"entity": add_edge, "relation": relation})
            changes.append(f"+edge: {add_edge} relation={relation}")
        _soft_validate_edge(add_edge)

    remove_edge = getattr(args, "remove_edge", None)
    if remove_edge:
        before = len(config.edges)
        config.edges = [e for e in config.edges if e.get("entity") != remove_edge]
        if len(config.edges) < before:
            changes.append(f"-edge: {remove_edge}")

    return changes


def handle_project_update_command(args):
    """Handle project-update command - update project.yaml fields."""
    try:
        from empirica.config.path_resolver import get_git_root
        from empirica.config.project_config_loader import ProjectConfig

        output_format = getattr(args, "output", "human")

        git_root = get_git_root()
        if not git_root:
            if output_format == "json":
                print(json.dumps({"ok": False, "error": "Not in a git repository"}))
            else:
                print("❌ Not in a git repository")
            return None

        config_path = git_root / ".empirica" / "project.yaml"
        if not config_path.exists():
            if output_format == "json":
                print(json.dumps({"ok": False, "error": "No project.yaml found. Run 'empirica project-init' first."}))
            else:
                print("❌ No project.yaml found. Run 'empirica project-init' first.")
            return None

        with open(config_path) as f:
            raw_config = yaml.safe_load(f) or {}

        config = ProjectConfig(raw_config)
        changes = []

        if getattr(args, "migrate", False):
            changes.extend(_migrate_v1_to_v2(config, git_root))

        changes.extend(_apply_simple_field_updates(args, config))
        changes.extend(_apply_list_updates(args, config))

        if not changes:
            if output_format == "json":
                print(json.dumps({"ok": True, "changes": [], "message": "No changes specified"}))
            else:
                print("ℹ️  No changes specified. Use --help to see available options.")
            return None

        updated = config.to_dict()
        with open(config_path, "w") as f:
            yaml.dump(updated, f, default_flow_style=False, sort_keys=False)

        synced = _sync_to_db(config, git_root)

        if output_format == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "changes": changes,
                        "config": updated,
                        "synced": synced,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            print(f"✅ Updated project.yaml ({len(changes)} changes)")
            for change in changes:
                print(f"   • {change}")
            for store, result in synced.items():
                if result.startswith("error") or result == "no row for project_id":
                    print(f"   ⚠️  {store} not updated: {result}")

        return {"ok": True, "changes": changes, "synced": synced}

    except Exception as e:
        from ..cli_utils import handle_cli_error

        handle_cli_error(e, "Project update", getattr(args, "verbose", False))
        return None


def _migrate_v1_to_v2(config: ProjectConfig, git_root: Path) -> list:
    """Migrate v1.0 config to v2.0 with auto-detected values."""
    changes = []

    if config.version == "2.0":
        return changes

    config.version = "2.0"
    changes.append("version: 1.0 -> 2.0")

    # Auto-detect languages
    if not config.languages:
        from .project_init import _auto_detect_languages

        config.languages = _auto_detect_languages(git_root)
        if config.languages:
            changes.append(f"languages: {config.languages} (auto-detected)")

    # Auto-detect repository
    if not config.repository:
        import subprocess

        try:
            result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                config.repository = result.stdout.strip()
                changes.append(f"repository: {config.repository}")
        except Exception:
            pass

    # Auto-set provenance
    if not config.created_by:
        config.created_by = os.environ.get("USER", "unknown")
        changes.append(f"created_by: {config.created_by}")

    # Set defaults for empty fields
    if not config.evidence_profile or config.evidence_profile == "auto":
        config.evidence_profile = "auto"

    return changes


def _soft_validate_edge(entity: str):
    """Warn if edge target not found in workspace. Non-fatal."""
    try:
        if not entity.startswith("project/"):
            return  # Only validate project references for now

        project_name = entity.split("/", 1)[1]
        from empirica.data.repositories.workspace_db import WorkspaceDBRepository

        with WorkspaceDBRepository.open(ensure_schema=False) as repo:
            projects = repo.list_projects()

        names = [p.get("name", "") for p in projects]
        folder_names = [Path(p.get("trajectory_path", "")).parent.name for p in projects]

        if project_name not in names and project_name not in folder_names:
            logger.warning(f"Edge target '{entity}' not found in workspace (may not be initialized yet)")
    except Exception:
        pass  # Workspace may not be available


def _sync_to_db(config: ProjectConfig, git_root: Path) -> dict[str, str]:
    """Sync updated config fields to sessions.db and workspace.db; say what each one did.

    The two stores are independent: a practice that was bootstrapped but never ran a session has a
    workspace row and no sessions.db, and the workspace row is the one other tools read. Each result
    is one of ``updated``, ``no database``, ``no row for project_id`` or ``error: <type>: <message>``.
    Failures stay non-fatal, but they are REPORTED. This used to swallow every exception, and the
    workspace half constructed its repository with no connection (a TypeError), so for as long as
    that signature had been so, ``project-update --status archived`` wrote project.yaml, reported
    ok, and left ``global_projects`` active (ecodex, prop_42avmw5tlnef7neqmw5vujsdou).
    """
    return {
        "sessions_db": _sync_sessions_db(config, git_root),
        "workspace_db": _sync_workspace_db(config),
    }


def _sync_sessions_db(config: ProjectConfig, git_root: Path) -> str:
    db_path = git_root / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return "no database"
    if not config.project_id:
        return "no row for project_id"
    try:
        import json as json_mod

        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase(db_path=str(db_path))
        try:
            project = db.get_project(config.project_id)
            if not project:
                return "no row for project_id"
            project_data = {}
            try:
                existing = project.get("project_data", "{}")
                project_data = json_mod.loads(existing) if isinstance(existing, str) else existing or {}
            except Exception:
                pass
            project_data.update(
                {
                    "type": config.type,
                    "domain": config.domain,
                    "classification": config.classification,
                    "evidence_profile": config.evidence_profile,
                    "languages": config.languages,
                    "tags": config.tags,
                    "contacts": config.contacts,
                    "engagements": config.engagements,
                    "edges": config.edges,
                }
            )
            db.conn.execute(
                "UPDATE projects SET project_type = ?, project_tags = ?, project_data = ?, status = ? WHERE id = ?",
                (
                    config.type,
                    json_mod.dumps(config.tags),
                    json_mod.dumps(project_data),
                    config.status,
                    config.project_id,
                ),
            )
            db.conn.commit()
            return "updated"
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"sessions.db sync failed (non-fatal): {e}")
        return f"error: {type(e).__name__}: {e}"


def _sync_workspace_db(config: ProjectConfig) -> str:
    if not config.project_id:
        return "no row for project_id"
    try:
        import json as json_mod
        import time

        from empirica.data.repositories.workspace_db import WorkspaceDBRepository, _get_workspace_db_path

        if not _get_workspace_db_path().exists():
            return "no database"  # opening would create an empty one, for a project nothing registered
        metadata = json_mod.dumps(
            {
                "domain": config.domain,
                "classification": config.classification,
                "evidence_profile": config.evidence_profile,
                "languages": config.languages,
                "contacts": config.contacts,
                "engagements": config.engagements,
                "edges": config.edges,
            }
        )
        with WorkspaceDBRepository.open(ensure_schema=False) as repo:
            cursor = repo.conn.execute(
                "UPDATE global_projects SET project_type = ?, project_tags = ?, status = ?, metadata = ?, "
                "updated_timestamp = ? WHERE id = ?",
                (config.type, json_mod.dumps(config.tags), config.status, metadata, time.time(), config.project_id),
            )
            repo.conn.commit()
            return "updated" if cursor.rowcount else "no row for project_id"
    except Exception as e:
        logger.warning(f"workspace.db sync failed (non-fatal): {e}")
        return f"error: {type(e).__name__}: {e}"
