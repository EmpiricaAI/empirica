"""
Sync Commands - Git notes synchronization for multi-device/multi-AI coordination

Commands:
- sync push: Push all epistemic notes to remote
- sync pull: Pull all epistemic notes from remote
- sync status: Show sync status (local vs remote)
- sync config: Configure sync settings
- rebuild: Reconstruct SQLite from git notes
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


# Default sync configuration
DEFAULT_SYNC_CONFIG = {
    "enabled": True,
    "remote": "forgejo",
    "visibility": "private",  # 'private' or 'public' - determines warnings
    "provider": "forgejo",  # 'github', 'gitlab', 'forgejo', 'bitbucket', 'auto'
    "auto_push_on": [],  # ['postflight', 'session_end'] - future auto-push triggers
    "code_remote": "origin",  # remote for code pushes (public)
    "notes_remote": "forgejo",  # remote for epistemic notes (private)
}


def _get_config_path() -> Path:
    """Get path to .empirica/config.yaml"""
    workspace_root = _get_workspace_root()
    return Path(workspace_root) / ".empirica" / "config.yaml"


def _load_sync_config() -> dict[str, Any]:
    """Load sync configuration from .empirica/config.yaml"""
    config_path = _get_config_path()

    if not config_path.exists():
        return DEFAULT_SYNC_CONFIG.copy()

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        sync_config = config.get("sync", {})

        # Merge with defaults
        result = DEFAULT_SYNC_CONFIG.copy()
        result.update(sync_config)
        return result
    except Exception as e:
        logger.warning(f"Failed to load sync config: {e}")
        return DEFAULT_SYNC_CONFIG.copy()


def _save_sync_config(sync_config: dict[str, Any]) -> bool:
    """Save sync configuration to .empirica/config.yaml"""
    config_path = _get_config_path()

    try:
        # Load existing config
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {"version": "2.0"}

        # Update sync section
        config["sync"] = sync_config

        # Ensure directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Write back
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return True
    except Exception as e:
        logger.error(f"Failed to save sync config: {e}")
        return False


def _detect_provider(remote_url: str) -> str:
    """Detect git provider from remote URL"""
    remote_lower = remote_url.lower()
    if "github.com" in remote_lower:
        return "github"
    elif "gitlab.com" in remote_lower or "gitlab" in remote_lower:
        return "gitlab"
    elif "forgejo" in remote_lower or "codeberg.org" in remote_lower or "getempirica.com" in remote_lower:
        return "forgejo"
    elif "bitbucket.org" in remote_lower:
        return "bitbucket"
    elif "gitea" in remote_lower:
        return "gitea"
    else:
        # Check configured provider as fallback
        sync_config = _load_sync_config()
        configured = sync_config.get("provider", "auto")
        if configured != "auto":
            return configured
        return "unknown"


def _get_remote_url(remote: str = "origin") -> str | None:
    """Get the URL for a remote"""
    try:
        result = subprocess.run(["git", "remote", "get-url", remote], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _list_remotes() -> dict[str, str]:
    """List all git remotes and their URLs"""
    try:
        result = subprocess.run(["git", "remote", "-v"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return {}

        remotes = {}
        for line in result.stdout.strip().split("\n"):
            if line and "(push)" in line:
                parts = line.split()
                if len(parts) >= 2:
                    remotes[parts[0]] = parts[1]
        return remotes
    except Exception:
        return {}


# All empirica git notes refs
EMPIRICA_NOTES_REFS = [
    "empirica/goals",
    "empirica/cascades",
    "empirica/handoffs",
    "empirica/findings",
    "empirica/unknowns",
    "empirica/dead_ends",
    "empirica/mistakes",
    "empirica/sessions",
    "empirica/checkpoints",
    "empirica/messages",
    "empirica-precompact",
    "breadcrumbs",
]


def _get_workspace_root() -> str:
    """Get workspace root - checks active context, then git root, then cwd"""
    import os

    # Priority 0: Check active project context (respects project-switch)
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        context_project = R.project_path()
        if context_project:
            return context_project
    except Exception:
        pass
    # Priority 1: Git root
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    # Priority 2: CWD fallback
    return os.getcwd()


def _check_remote(remote: str = "origin") -> bool:
    """Check if remote exists"""
    try:
        result = subprocess.run(["git", "remote", "get-url", remote], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _count_local_notes() -> dict[str, int]:
    """Count notes in each ref locally"""
    counts = {}
    for ref in EMPIRICA_NOTES_REFS:
        try:
            result = subprocess.run(
                ["git", "for-each-ref", f"refs/notes/{ref}/"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                counts[ref] = len(result.stdout.strip().split("\n"))
            else:
                counts[ref] = 0
        except Exception:
            counts[ref] = 0
    return counts


def _handle_sync_config_command_helper(key, output_format, sync_config, value):
    """Extracted from handle_sync_config_command to reduce complexity."""
    if key and value is not None:
        valid_keys = ["enabled", "remote", "visibility", "provider", "code_remote", "notes_remote"]
        if key not in valid_keys:
            result = {"ok": False, "error": f"Unknown config key: {key}", "valid_keys": valid_keys}
            print(json.dumps(result, indent=2))
            return 1

        # Parse boolean values
        if key == "enabled":
            value = value.lower() in ("true", "1", "yes", "on")

        # Validate visibility
        if key == "visibility" and value not in ("public", "private"):
            result = {"ok": False, "error": f"visibility must be 'public' or 'private', got '{value}'"}
            print(json.dumps(result, indent=2))
            return 1

        # Validate provider
        if key == "provider" and value not in ("github", "gitlab", "forgejo", "gitea", "bitbucket", "auto", "other"):
            result = {
                "ok": False,
                "error": "provider must be one of: github, gitlab, forgejo, gitea, bitbucket, auto, other",
            }
            print(json.dumps(result, indent=2))
            return 1

        # Update and save
        sync_config[key] = value
        if _save_sync_config(sync_config):
            result = {"ok": True, "message": f"Set sync.{key} = {value}", "config": sync_config}
        else:
            result = {"ok": False, "error": "Failed to save config"}

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Set sync.{key} = {value}")

        return 0 if result["ok"] else 1


def handle_sync_config_command(args):
    """Handle sync config command - show/set sync configuration"""
    try:
        output_format = getattr(args, "output", "json")
        key = getattr(args, "key", None)
        value = getattr(args, "value", None)

        # Load current config
        sync_config = _load_sync_config()

        # If setting a value
        _handle_sync_config_command_helper(key, output_format, sync_config, value)

        # Show config (with optional key filter)
        if key:
            if key in sync_config:
                result = {"ok": True, "key": key, "value": sync_config[key]}
            else:
                result = {"ok": False, "error": f"Unknown config key: {key}"}
        else:
            # Get remote info for context
            current_remote = sync_config.get("remote", "origin")
            remote_url = _get_remote_url(current_remote)
            detected_provider = _detect_provider(remote_url) if remote_url else "unknown"
            all_remotes = _list_remotes()

            result = {
                "ok": True,
                "config": sync_config,
                "remote_url": remote_url,
                "detected_provider": detected_provider,
                "available_remotes": all_remotes,
                "config_path": str(_get_config_path()),
            }

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print("📋 Sync Configuration")
            print(f"   enabled: {sync_config.get('enabled', True)}")
            print(f"   remote: {sync_config.get('remote', 'origin')}")
            print(f"   visibility: {sync_config.get('visibility', 'private')}")
            print(f"   provider: {sync_config.get('provider', 'auto')}")
            if remote_url:
                print(f"\n   Remote URL: {remote_url}")
                print(f"   Detected provider: {detected_provider}")

            # Show available remotes
            if all_remotes and len(all_remotes) > 1:
                print("\n   Available remotes:")
                for name, url in all_remotes.items():
                    marker = "→" if name == current_remote else " "
                    print(f"   {marker} {name}: {url}")

            print(f"\n   Config file: {_get_config_path()}")

            # Show dual-remote config
            notes_remote = sync_config.get("notes_remote", current_remote)
            code_remote = sync_config.get("code_remote", "origin")
            if notes_remote != code_remote:
                print("\n   Dual-remote mode:")
                print(f"      Code:  {code_remote} (public)")
                print(f"      Notes: {notes_remote} (private)")

            # Show private sync hint if notes remote is a public provider
            if detected_provider in ("github", "gitlab", "bitbucket"):
                print("\n   WARNING: Notes remote points to a public provider!")
                print("      Epistemic notes contain private data (findings, mistakes, messages).")
                print("      Switch to a private remote:")
                print("      empirica sync-config remote forgejo")
                print("      empirica sync-config notes_remote <private-remote>")

            print("\n   Set with: empirica sync-config <key> <value>")
            print("   Keys: enabled, remote, visibility, provider")

        return 0 if result["ok"] else 1

    except Exception as e:
        handle_cli_error(e, "Sync config", getattr(args, "verbose", False))
        return 1


def _handle_sync_push_command_helper(errors, output_format, push_results, remote, result, success):
    """Extracted from handle_sync_push_command to reduce complexity."""
    if output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        if success:
            print(f"✅ Pushed epistemic notes to {remote}")
            for ref, ok in push_results.items():
                status = "✓" if ok else "✗"
                print(f"   {status} {ref}")
        else:
            print(f"❌ Push failed to {remote}")
            for err in errors:
                print(f"   Error: {err}")


def handle_sync_push_command(args):
    """Handle sync push command - push all epistemic notes to remote"""
    try:
        # Load config
        sync_config = _load_sync_config()

        # Use CLI arg if provided, otherwise use config
        remote = getattr(args, "remote", None) or sync_config.get("remote", "origin")
        output_format = getattr(args, "output", "json")
        dry_run = getattr(args, "dry_run", False)
        getattr(args, "verbose", False)
        force = getattr(args, "force", False)

        # Check if sync is enabled
        if not sync_config.get("enabled", True) and not force:
            result = {
                "ok": False,
                "error": "Sync is disabled in config",
                "hint": "Run 'empirica sync-config enabled true' to enable or use --force",
            }
            print(json.dumps(result, indent=2))
            return 1

        # Check remote exists
        if not _check_remote(remote):
            result = {
                "ok": False,
                "error": f"Remote '{remote}' not found",
                "hint": "Run 'git remote add origin <url>' to add a remote",
            }
            print(json.dumps(result, indent=2))
            return 1

        # Safety check: block pushing notes to public providers unless forced
        remote_url = _get_remote_url(remote)
        detected = _detect_provider(remote_url) if remote_url else "unknown"
        public_providers = ("github", "gitlab", "bitbucket")
        if detected in public_providers and not force:
            result = {
                "ok": False,
                "error": f"Refusing to push epistemic notes to public provider ({detected})",
                "remote": remote,
                "remote_url": remote_url,
                "hint": (
                    "Notes contain private epistemic data (findings, mistakes, messages). "
                    "Use a private remote: 'empirica sync-config remote forgejo' or "
                    "'empirica sync-config notes_remote <private-remote>'. "
                    "Use --force to override."
                ),
            }
            if output_format == "json":
                print(json.dumps(result, indent=2))
            else:
                print(f"BLOCKED: Won't push notes to {detected} ({remote_url})")
                print("   Notes contain private epistemic data.")
                print("   Set a private remote: empirica sync-config remote forgejo")
                print("   Or use --force to override.")
            return 1

        # Count local notes
        local_counts = _count_local_notes()
        total_refs = sum(1 for c in local_counts.values() if c > 0)

        if dry_run:
            result = {
                "ok": True,
                "dry_run": True,
                "remote": remote,
                "refs_to_push": total_refs,
                "note_counts": local_counts,
                "command": f"git push {remote} 'refs/notes/empirica/*:refs/notes/empirica/*'",
            }
            if output_format == "json":
                print(json.dumps(result, indent=2))
            else:
                print(f"🔍 Dry run - would push {total_refs} note refs to {remote}")
                for ref, count in local_counts.items():
                    if count > 0:
                        print(f"   refs/notes/{ref}: {count} notes")
            return 0

        # Execute push
        push_results = {}
        errors = []

        # Push all empirica notes at once
        try:
            result = subprocess.run(
                ["git", "push", remote, "refs/notes/empirica/*:refs/notes/empirica/*"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            push_results["empirica/*"] = result.returncode == 0
            if result.returncode != 0 and result.stderr:
                errors.append(f"empirica/*: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            errors.append("Push timed out")
        except Exception as e:
            errors.append(str(e))

        # Push breadcrumbs separately (different namespace)
        try:
            result = subprocess.run(
                ["git", "push", remote, "refs/notes/breadcrumbs:refs/notes/breadcrumbs"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            push_results["breadcrumbs"] = result.returncode == 0
        except Exception:
            push_results["breadcrumbs"] = False

        # Push empirica-precompact separately
        try:
            result = subprocess.run(
                ["git", "push", remote, "refs/notes/empirica-precompact:refs/notes/empirica-precompact"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            push_results["empirica-precompact"] = result.returncode == 0
        except Exception:
            push_results["empirica-precompact"] = False

        success = push_results.get("empirica/*", False)

        result = {
            "ok": success,
            "remote": remote,
            "push_results": push_results,
            "errors": errors if errors else None,
            "message": f"Pushed epistemic notes to {remote}" if success else "Push failed",
        }

        _handle_sync_push_command_helper(errors, output_format, push_results, remote, result, success)

        return 0 if success else 1

    except Exception as e:
        handle_cli_error(e, "Sync push", getattr(args, "verbose", False))
        return 1


def _handle_sync_pull_command_helper(changes, errors, output_format, rebuild, remote, result, success):
    """Extracted from handle_sync_pull_command to reduce complexity."""
    if output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        if success:
            print(f"✅ Pulled epistemic notes from {remote}")
            if changes:
                for ref, change in changes.items():
                    print(f"   {ref}: {change['before']} → {change['after']} ({change['delta']:+d})")
            else:
                print("   No changes (already up to date)")
            if rebuild and "rebuild" in result:
                print("   🔄 Rebuilt SQLite from notes")
        else:
            print(f"❌ Pull failed from {remote}")
            for err in errors:
                print(f"   Error: {err}")


def handle_sync_pull_command(args):
    """Handle sync pull command - pull all epistemic notes from remote"""
    try:
        # Load config
        sync_config = _load_sync_config()

        # Use CLI arg if provided, otherwise use config
        remote = getattr(args, "remote", None) or sync_config.get("remote", "origin")
        output_format = getattr(args, "output", "json")
        rebuild = getattr(args, "rebuild", False)
        getattr(args, "verbose", False)
        force = getattr(args, "force", False)

        # Check if sync is enabled
        if not sync_config.get("enabled", True) and not force:
            result = {
                "ok": False,
                "error": "Sync is disabled in config",
                "hint": "Run 'empirica sync-config enabled true' to enable or use --force",
            }
            print(json.dumps(result, indent=2))
            return 1

        # Check remote exists
        if not _check_remote(remote):
            result = {"ok": False, "error": f"Remote '{remote}' not found"}
            print(json.dumps(result, indent=2))
            return 1

        # Count local notes before pull
        local_before = _count_local_notes()

        # Execute fetch
        fetch_results = {}
        errors = []

        # Fetch all empirica notes at once
        try:
            result = subprocess.run(
                ["git", "fetch", remote, "refs/notes/empirica/*:refs/notes/empirica/*"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            fetch_results["empirica/*"] = result.returncode == 0
            if result.returncode != 0 and result.stderr:
                # Check if it's just "no matching refs" (not an error)
                if "no matching refs" not in result.stderr.lower():
                    errors.append(f"empirica/*: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            errors.append("Fetch timed out")
        except Exception as e:
            errors.append(str(e))

        # Fetch breadcrumbs separately
        try:
            result = subprocess.run(
                ["git", "fetch", remote, "refs/notes/breadcrumbs:refs/notes/breadcrumbs"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            fetch_results["breadcrumbs"] = result.returncode == 0
        except Exception:
            fetch_results["breadcrumbs"] = False

        # Fetch empirica-precompact separately
        try:
            result = subprocess.run(
                ["git", "fetch", remote, "refs/notes/empirica-precompact:refs/notes/empirica-precompact"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            fetch_results["empirica-precompact"] = result.returncode == 0
        except Exception:
            fetch_results["empirica-precompact"] = False

        # Count local notes after pull
        local_after = _count_local_notes()

        # Calculate changes
        changes = {}
        for ref in EMPIRICA_NOTES_REFS:
            before = local_before.get(ref, 0)
            after = local_after.get(ref, 0)
            if after != before:
                changes[ref] = {"before": before, "after": after, "delta": after - before}

        success = fetch_results.get("empirica/*", False) or not errors

        result = {
            "ok": success,
            "remote": remote,
            "fetch_results": fetch_results,
            "changes": changes if changes else None,
            "errors": errors if errors else None,
            "message": f"Pulled epistemic notes from {remote}",
        }

        # Rebuild if requested
        if rebuild and success:
            rebuild_result = _rebuild_from_notes()
            result["rebuild"] = rebuild_result

        _handle_sync_pull_command_helper(changes, errors, output_format, rebuild, remote, result, success)

        return 0 if success else 1

    except Exception as e:
        handle_cli_error(e, "Sync pull", getattr(args, "verbose", False))
        return 1


def handle_sync_status_command(args):
    """Handle sync status command - show sync status"""
    try:
        remote = getattr(args, "remote", "origin") or "origin"
        output_format = getattr(args, "output", "json")

        # Check remote exists
        remote_configured = _check_remote(remote)

        # Count local notes
        local_counts = _count_local_notes()
        total_notes = sum(local_counts.values())
        refs_with_data = sum(1 for c in local_counts.values() if c > 0)

        result = {
            "ok": True,
            "remote": remote,
            "remote_configured": remote_configured,
            "local_refs": refs_with_data,
            "total_notes": total_notes,
            "note_counts": {k: v for k, v in local_counts.items() if v > 0},
            "sync_available": remote_configured,
        }

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print("📊 Empirica Sync Status")
            print(f"   Remote: {remote} ({'configured' if remote_configured else 'NOT configured'})")
            print(f"   Local refs with data: {refs_with_data}")
            print(f"   Total notes: {total_notes}")
            if local_counts:
                print("\n   Note counts:")
                for ref, count in sorted(local_counts.items()):
                    if count > 0:
                        print(f"      refs/notes/{ref}: {count}")

            if not remote_configured:
                print("\n   ⚠️ No remote configured. Run 'git remote add origin <url>' to enable sync.")

        return 0

    except Exception as e:
        handle_cli_error(e, "Sync status", getattr(args, "verbose", False))
        return 1


def _rebuild_collect_ids(all_items_lists):
    """Collect unique project_ids, session_ids, goal_ids from all breadcrumbs."""
    project_ids = set()
    session_ids = set()
    goal_ids_needed = set()
    for items in all_items_lists:
        for item in items:
            pid = item.get("project_id")
            sid = item.get("session_id")
            gid = item.get("goal_id")
            if pid:
                project_ids.add(pid)
            if sid:
                session_ids.add(sid)
            if gid:
                goal_ids_needed.add(gid)
    return project_ids, session_ids, goal_ids_needed


def _rebuild_ensure_projects(db, project_ids, now, rebuilt):
    """Create stub project records to satisfy FK constraints."""
    import json as _json

    for pid in project_ids:
        try:
            db.adapter.execute(
                "INSERT INTO projects (id, name, description, created_timestamp, project_data) VALUES (?, ?, ?, ?, ?)",
                (pid, f"project-{pid[:8]}", "Rebuilt from git notes", now, _json.dumps({"rebuilt": True})),
            )
            db.adapter.commit()
            rebuilt["projects"] += 1
        except Exception:
            db.adapter.conn.rollback() if hasattr(db.adapter, "conn") else None


def _rebuild_ensure_sessions(db, session_ids, all_items_lists, rebuilt):
    """Create stub session records to satisfy FK constraints."""
    from datetime import datetime

    for sid in session_ids:
        try:
            pid = None
            for items in all_items_lists:
                for item in items:
                    if item.get("session_id") == sid and item.get("project_id"):
                        pid = item.get("project_id")
                        break
                if pid:
                    break

            now_ts = datetime.utcnow().isoformat()
            db.adapter.execute(
                "INSERT INTO sessions (session_id, ai_id, start_time, "
                "components_loaded, project_id) VALUES (?, ?, ?, ?, ?)",
                (sid, "rebuilt", now_ts, 0, pid),
            )
            db.adapter.commit()
            rebuilt["sessions"] += 1
        except Exception:
            try:
                db.adapter.conn.rollback()
            except Exception:
                pass


def _rebuild_ensure_goals(db, now, rebuilt):
    """Insert goals from git notes. Returns set of inserted goal IDs."""
    import json as _json

    from empirica.core.canonical.empirica_git.goal_store import GitGoalStore

    goal_store = GitGoalStore()
    goals = goal_store.discover_goals()
    for g in goals:
        try:
            gid = g.get("goal_id")
            gsid = g.get("session_id", "")
            gdata = g.get("goal_data", {})
            db.adapter.execute(
                "INSERT INTO goals (id, session_id, objective, scope, estimated_complexity, "
                "created_timestamp, goal_data, status, project_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    gid,
                    gsid,
                    gdata.get("objective", "Rebuilt from notes"),
                    _json.dumps(gdata.get("scope", {})),
                    gdata.get("estimated_complexity"),
                    now,
                    _json.dumps(gdata),
                    gdata.get("status", "in_progress"),
                    gdata.get("project_id"),
                ),
            )
            db.adapter.commit()
            rebuilt["goals"] += 1
        except Exception:
            try:
                db.adapter.conn.rollback()
            except Exception:
                pass

    return goals, {g.get("goal_id") for g in goals}


def _rebuild_ensure_orphan_goals(db, orphan_goal_ids, all_items_lists, now, rebuilt):
    """Create stub goal records for goal IDs referenced by breadcrumbs but not in git notes."""
    import json as _json

    for gid in orphan_goal_ids:
        try:
            gsid = ""
            for items in all_items_lists:
                for item in items:
                    if item.get("goal_id") == gid and item.get("session_id"):
                        gsid = item.get("session_id")
                        break
                if gsid:
                    break
            db.adapter.execute(
                "INSERT INTO goals (id, session_id, objective, scope, "
                "created_timestamp, goal_data) VALUES (?, ?, ?, ?, ?, ?)",
                (gid, gsid, "Rebuilt stub (orphaned ref)", "{}", now, _json.dumps({"rebuilt": True, "orphan": True})),
            )
            db.adapter.commit()
            rebuilt["goals"] += 1
        except Exception:
            try:
                db.adapter.conn.rollback()
            except Exception:
                pass


def _rebuild_insert_breadcrumbs(db, findings, unknowns, dead_ends, mistakes, valid_goal_ids, rebuilt):
    """Insert breadcrumb records using table-driven approach."""
    handlers = [
        (
            "findings",
            findings,
            lambda db, item, vg: db.log_finding(
                project_id=item.get("project_id"),
                session_id=item.get("session_id"),
                finding=item.get("finding"),
                subject=item.get("subject"),
                impact=item.get("impact"),
                goal_id=item.get("goal_id") if item.get("goal_id") in vg else None,
                subtask_id=None,
            ),
        ),
        (
            "unknowns",
            unknowns,
            lambda db, item, vg: db.log_unknown(
                project_id=item.get("project_id"),
                session_id=item.get("session_id"),
                unknown=item.get("unknown"),
                subtask_id=None,
                goal_id=item.get("goal_id") if item.get("goal_id") in vg else None,
            ),
        ),
        (
            "dead_ends",
            dead_ends,
            lambda db, item, vg: db.log_dead_end(
                project_id=item.get("project_id"),
                session_id=item.get("session_id"),
                approach=item.get("approach"),
                why_failed=item.get("why_failed"),
                subtask_id=None,
                goal_id=item.get("goal_id") if item.get("goal_id") in vg else None,
            ),
        ),
        (
            "mistakes",
            mistakes,
            lambda db, item, vg: db.log_mistake(
                session_id=item.get("session_id"),
                project_id=item.get("project_id"),
                mistake=item.get("mistake"),
                why_wrong=item.get("why_wrong"),
                prevention=item.get("prevention"),
                cost_estimate=item.get("cost_estimate"),
                root_cause_vector=item.get("root_cause_vector"),
                goal_id=item.get("goal_id") if item.get("goal_id") in vg else None,
            ),
        ),
    ]
    for key, items, handler in handlers:
        for item in items:
            try:
                new_id = handler(db, item, valid_goal_ids)
                # B2: a from-notes rebuild re-creates artifacts OPEN (log_* make
                # fresh rows). Re-apply the resolution the note carries so
                # resolved/superseded state survives rebuild + multi-device sync.
                _rebuild_apply_resolution(db, key, new_id, item)
                rebuilt[key] += 1
            except Exception as e:
                logger.debug(f"{key} rebuild skip: {e}")
                try:
                    db.adapter.conn.rollback()
                except Exception:
                    pass


def _rebuild_apply_resolution(db, key, new_id, item) -> None:
    """B2: after a from-notes rebuild re-creates a finding/unknown as OPEN,
    re-apply the resolution its git note carries. Direct SQL (not the
    note-writing resolve) since the note IS the source here. Best-effort."""
    if not new_id or key not in ("findings", "unknowns"):
        return
    import time as _t

    try:
        cur = db.adapter.conn.cursor()
        if key == "findings" and item.get("is_resolved"):
            cur.execute(
                "UPDATE project_findings SET is_resolved = 1, resolution = ?, "
                "superseded_by = ?, resolved_timestamp = ? WHERE id = ?",
                (item.get("resolution") or "resolved", item.get("superseded_by"), _t.time(), new_id),
            )
            db.adapter.conn.commit()
        elif key == "unknowns" and item.get("resolved"):
            cur.execute(
                "UPDATE project_unknowns SET is_resolved = 1, resolved_by = ?, resolved_timestamp = ? WHERE id = ?",
                (item.get("resolved_by") or "resolved", _t.time(), new_id),
            )
            db.adapter.conn.commit()
    except Exception as e:
        logger.debug(f"rebuild resolution re-apply skip ({key} {new_id}): {e}")


def _rebuild_from_notes() -> dict[str, Any]:
    """
    Rebuild database from git notes.

    This reconstructs the derived database tables from canonical git notes.
    Handles FK dependencies by ensuring referenced projects and sessions exist
    before inserting breadcrumbs.
    """
    rebuilt = {"projects": 0, "sessions": 0, "findings": 0, "unknowns": 0, "dead_ends": 0, "mistakes": 0, "goals": 0}

    try:
        import time

        from empirica.core.canonical.empirica_git.dead_end_store import GitDeadEndStore
        from empirica.core.canonical.empirica_git.finding_store import GitFindingStore
        from empirica.core.canonical.empirica_git.mistake_store import GitMistakeStore
        from empirica.core.canonical.empirica_git.unknown_store import GitUnknownStore
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()

        finding_store = GitFindingStore()
        unknown_store = GitUnknownStore()
        dead_end_store = GitDeadEndStore()
        mistake_store = GitMistakeStore()

        findings = finding_store.discover_findings()
        unknowns = unknown_store.discover_unknowns(include_resolved=True)
        dead_ends = dead_end_store.discover_dead_ends()
        mistakes = mistake_store.discover_mistakes()

        all_items_lists = [findings, unknowns, dead_ends, mistakes]

        # Phase 0: Collect IDs and create stub records for FK constraints
        project_ids, session_ids, goal_ids_needed = _rebuild_collect_ids(all_items_lists)

        now = time.time()
        _rebuild_ensure_projects(db, project_ids, now, rebuilt)
        _rebuild_ensure_sessions(db, session_ids, all_items_lists, rebuilt)
        _goals, inserted_goal_ids = _rebuild_ensure_goals(db, now, rebuilt)

        orphan_goal_ids = goal_ids_needed - inserted_goal_ids
        _rebuild_ensure_orphan_goals(db, orphan_goal_ids, all_items_lists, now, rebuilt)

        logger.info(
            f"Rebuild Phase 0: {rebuilt['projects']} projects, {rebuilt['sessions']} sessions, {rebuilt['goals']} goals"
        )

        # Phase 1: Insert breadcrumbs
        inserted_all_goal_ids = inserted_goal_ids | set(orphan_goal_ids)
        _rebuild_insert_breadcrumbs(db, findings, unknowns, dead_ends, mistakes, inserted_all_goal_ids, rebuilt)

        db.close()

    except Exception as e:
        logger.warning(f"Rebuild failed: {e}")
        rebuilt["error"] = str(e)

    return rebuilt


def handle_rebuild_command(args):
    """Handle rebuild command - reconstruct SQLite from git notes"""
    try:
        output_format = getattr(args, "output", "json")
        from_notes = getattr(args, "from_notes", True)
        qdrant = getattr(args, "qdrant", False)
        qdrant_only = getattr(args, "qdrant_only", False)

        # --qdrant-only: re-embed Qdrant from CURRENT SQLite WITHOUT the notes-import
        # step. The default path (_rebuild_from_notes) reconstructs SQLite from git notes
        # FIRST, which reverts any direct-SQL/bulk change not yet persisted to notes
        # (e.g. an epistemic-garden bulk resolve). This flag is the safe resync path after
        # such changes: it touches Qdrant only, never SQLite.
        if qdrant_only:
            from empirica.core.qdrant.vector_store import rebuild_qdrant_from_db

            qdrant_result = rebuild_qdrant_from_db()
            ok = bool(qdrant_result.get("ok"))
            result = {
                "ok": ok,
                "qdrant": qdrant_result,
                "message": "Rebuilt Qdrant from current SQLite (notes-import skipped)",
            }
            if output_format == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                print(
                    "✅ Rebuilt Qdrant from current SQLite (notes-import skipped)"
                    if ok
                    else f"❌ Qdrant rebuild failed: {qdrant_result.get('error', 'unknown')}"
                )
            return 0 if ok else 1

        if not from_notes:
            result = {"ok": False, "error": "Only --from-notes rebuild is currently supported"}
            print(json.dumps(result, indent=2))
            return 1

        # Run rebuild
        rebuild_result = _rebuild_from_notes()

        total_rebuilt = sum(v for k, v in rebuild_result.items() if k != "error" and isinstance(v, int))

        result = {
            "ok": "error" not in rebuild_result,
            "rebuilt": rebuild_result,
            "total": total_rebuilt,
            "message": f"Rebuilt {total_rebuilt} records from git notes",
        }

        # Optionally rebuild Qdrant
        if qdrant:
            try:
                from empirica.core.qdrant.vector_store import rebuild_qdrant_from_db

                qdrant_result = rebuild_qdrant_from_db()
                result["qdrant"] = qdrant_result
            except Exception as e:
                result["qdrant_error"] = str(e)

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            if result["ok"]:
                print(f"✅ Rebuilt {total_rebuilt} records from git notes")
                for type_name, count in rebuild_result.items():
                    if type_name != "error" and count > 0:
                        print(f"   {type_name}: {count}")
                if qdrant and "qdrant" in result:
                    print("   🔍 Qdrant: rebuilt")
            else:
                print(f"❌ Rebuild failed: {rebuild_result.get('error', 'Unknown error')}")

        return 0 if result["ok"] else 1

    except Exception as e:
        handle_cli_error(e, "Rebuild", getattr(args, "verbose", False))
        return 1
