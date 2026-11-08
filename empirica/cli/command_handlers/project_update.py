"""
Project Update Command - Update project.yaml fields after initialization

Updates static project identity fields and syncs changes to the database.
Supports adding/removing contacts, engagements, edges, tags, and all v2.0 fields.

Usage:
    empirica project-update --type research --domain research --add-tag bio/genomics
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


def _as_list(value) -> list[str]:
    """A repeated flag arrives as a list, a single value as text, an absent one as None."""
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def _comment_lines(text: str) -> int:
    """Lines that hold only a comment. A rewrite through yaml.dump keeps none of them."""
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))


def _refuse_unknown_domain(requested: str | None, domains: list[str], output_format: str) -> bool:
    """Print the refusal for a `--domain` that names no domain file. True when the command must stop."""
    from empirica.config.domain_registry import domain_refusal

    error = domain_refusal(requested, domains)
    if not error:
        return False
    if output_format == "json":
        print(json.dumps({"ok": False, "error": error}))
    else:
        print(f"❌ {error}")
    return True


def is_old_free_text_domain(value: str) -> bool:
    """Text from before the domain was a name (`ai/measurement`). An unknown plain name may be a domain file on another
    machine (user domains live in ~/.empirica/domains, project.yaml is shared), so it is never taken for free text."""
    return "/" in value


def _take_free_text_domain(config, domains: list[str]) -> str | None:
    """Clear a stored domain that is old free text and return it. An unknown name is left where it is."""
    if not config.domain or config.domain in domains or not is_old_free_text_domain(config.domain):
        return None
    free_text, config.domain = config.domain, ""
    return free_text


def _keep_free_text_domain_as_tag(config, free_text: str | None) -> list[str]:
    """The old free-text domain is kept, as a tag. Returns the change line."""
    if not free_text:
        return []
    if free_text not in config.tags:
        config.tags.append(free_text)
    return [f"domain: '{free_text}' is not a domain name, moved to tags"]


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

    # Both flags repeat. A caller that passes one tag as text is still accepted.
    for add_tag in _as_list(getattr(args, "add_tag", None)):
        if add_tag not in config.tags:
            config.tags.append(add_tag)
            changes.append(f"+tag: {add_tag}")

    for remove_tag in _as_list(getattr(args, "remove_tag", None)):
        if remove_tag in config.tags:
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


def _print_update_result(
    output_format: str, changes: list, updated: dict, synced: dict, comment_lines_dropped: int
) -> None:
    """Report a completed update: what changed, what each store did, and the comments the rewrite did not keep."""
    if output_format == "json":
        print(
            json.dumps(
                {
                    "ok": _synced_ok(synced),
                    "changes": changes,
                    "config": updated,
                    "synced": synced,
                    "comment_lines_dropped": comment_lines_dropped,
                },
                indent=2,
                default=str,
            )
        )
        return
    print(f"✅ Updated project.yaml ({len(changes)} changes)")
    for change in changes:
        print(f"   • {change}")
    if comment_lines_dropped:
        print(
            f"   ⚠️  {comment_lines_dropped} comment lines in project.yaml were not kept: the file is rewritten from its values"
        )
    for store, result in synced.items():
        if result != "updated" and not (store == "sessions_db" and result == "no database"):
            print(f"   ⚠️  {store} not updated: {result}")


def _write_practice_facts(git_root) -> None:
    """Refresh .empirica/practice-facts.md, the generated part of the practice's instructions. Never fails the update."""
    try:
        from empirica.core.practice_facts import write

        write(git_root)
    except Exception as exc:  # the facts are derived; a failure here must not undo the update
        logger.warning("practice-facts not refreshed: %s", exc)


def handle_project_update_command(args):
    """Handle project-update command - update project.yaml fields.

    Returns an exit code, not the result dict. The handler prints its own result; a dict returned here was
    printed a second time by the dispatcher, so `--output json` emitted two documents, and a refusal that
    returned None exited 0.
    """
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
            return 1

        config_path = git_root / ".empirica" / "project.yaml"
        if not config_path.exists():
            if output_format == "json":
                print(json.dumps({"ok": False, "error": "No project.yaml found. Run 'empirica project-init' first."}))
            else:
                print("❌ No project.yaml found. Run 'empirica project-init' first.")
            return 1

        original_text = config_path.read_text()
        raw_config = yaml.safe_load(original_text) or {}
        # The write below goes through yaml.dump, which keeps no comment. Count them so the loss is reported.
        comment_lines_dropped = _comment_lines(original_text)

        config = ProjectConfig(raw_config)
        changes = []

        # A domain is the name of a domain file (docs/reference/TAXONOMY.md 4.6). Free text is refused before anything is written.
        from empirica.config.domain_registry import DomainRegistry

        domains = DomainRegistry(project_path=git_root).list_domains()
        if _refuse_unknown_domain(getattr(args, "domain", None), domains, output_format):
            return 1
        free_text_domain = _take_free_text_domain(config, domains)

        if getattr(args, "migrate", False):
            changes.extend(_migrate_v1_to_v2(config, git_root))

        changes.extend(_apply_simple_field_updates(args, config))
        changes.extend(_apply_list_updates(args, config))
        # After the list updates, so `--tags` (replace all) cannot drop it.
        changes.extend(_keep_free_text_domain_as_tag(config, free_text_domain))

        if not changes:
            if output_format == "json":
                print(json.dumps({"ok": True, "changes": [], "message": "No changes specified"}))
            else:
                print("ℹ️  No changes specified. Use --help to see available options.")
            return 0

        # to_dict() carries only the keys ProjectConfig models. Writing it alone deleted every other
        # top-level key (ai_id, the mesh seat, publish_channels, cockpit, artifact_graph, ...), so a routine
        # `project-update --status archived` removed a practice's identity. Merge instead: modelled keys take
        # their updated value, anything else survives.
        updated = {**raw_config, **config.to_dict()}
        if not config.domain:
            updated.pop("domain", None)  # to_dict() omits an empty domain, so the merge would keep the old one
        with open(config_path, "w") as f:
            yaml.dump(updated, f, default_flow_style=False, sort_keys=False)
        _write_practice_facts(git_root)

        synced = _sync_to_db(config, git_root)

        _print_update_result(output_format, changes, updated, synced, comment_lines_dropped)

        return 0 if _synced_ok(synced) else 1

    except Exception as e:
        from ..cli_utils import handle_cli_error

        handle_cli_error(e, "Project update", getattr(args, "verbose", False))
        return 1


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


def _synced_ok(synced: dict[str, str]) -> bool:
    """Did every store sync without error? 'no database' and 'no row' are honest states, not failures."""
    return not any(str(v).startswith("error") for v in synced.values())


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
        owned = {
            "domain": config.domain,
            "classification": config.classification,
            "evidence_profile": config.evidence_profile,
            "languages": config.languages,
            "contacts": config.contacts,
            "engagements": config.engagements,
            "edges": config.edges,
        }
        with WorkspaceDBRepository.open(ensure_schema=False) as repo:
            # Merge into what is there: keys this command does not own survive.
            row = repo.conn.execute(
                "SELECT metadata FROM global_projects WHERE id = ?", (config.project_id,)
            ).fetchone()
            try:
                existing = json_mod.loads(row[0]) if row and row[0] else {}
            except (TypeError, ValueError):
                existing = {}
            metadata = json_mod.dumps({**(existing if isinstance(existing, dict) else {}), **owned})
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
