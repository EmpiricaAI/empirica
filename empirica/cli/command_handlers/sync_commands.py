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

from empirica.core.auto_push import SUPPORTED_TRIGGERS
from empirica.core.sync_remotes import CODE, NOTES
from empirica.core.sync_remotes import refusal as _remote_refusal
from empirica.core.sync_remotes import render_refusal as _render_remote_refusal
from empirica.core.sync_remotes import resolve as _resolve_remote

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


# Default sync configuration
#
# NO REMOTE HAS A DEFAULT. `remote`, `notes_remote` and `code_remote` are all None
# until a human sets them, and every verb REFUSES rather than guessing.
#
# The defaults used to be `forgejo` for notes and `origin` for code, and both were
# guesses that read as safe. Measured consequence: on one box `origin` was a PUBLIC
# GitHub repo, so the code default pointed at publication; on another there was no
# `origin` at all, so notes silently synced nowhere for weeks. Same default, opposite
# invisible failures, and in neither case did anything say which remote was in use.
#
# **Guessing wrong here is publishing.** A default that is usually right is the worst
# shape for that, because it works until the seat where it does not and never
# announces which case you are in. David's ruling, 2026-09-02: no guessing anywhere —
# earned confidence, not most-likely prediction.
DEFAULT_SYNC_CONFIG = {
    "enabled": True,
    "remote": None,
    "visibility": "private",  # 'private' or 'public' - determines warnings
    "provider": "forgejo",  # 'github', 'gitlab', 'forgejo', 'bitbucket', 'auto'
    # Empty = off. Set via `empirica sync-config auto_push_on postflight`, which
    # validates; a hand-edit of an unsupported value is rejected at read time rather
    # than silently ignored. `session_end` is deliberately unimplemented.
    "auto_push_on": [],
    "code_remote": None,  # where CODE goes. Unset = refuse; see empirica.core.sync_remotes
    "notes_remote": None,  # where NOTES go. Unset = fall through to `remote`, then refuse
}

#: What `sync-config` accepts — and what its own help prints. These were two lists,
#: and the printed one was four keys short of the validated one, so `code_remote`,
#: `notes_remote` and `auto_push_on` were settable and undocumented at the point of
#: use. A key list is exactly the thing that must be derived, not restated.
VALID_CONFIG_KEYS: tuple[str, ...] = tuple(DEFAULT_SYNC_CONFIG)


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
        if key not in VALID_CONFIG_KEYS:
            result = {"ok": False, "error": f"Unknown config key: {key}", "valid_keys": list(VALID_CONFIG_KEYS)}
            print(json.dumps(result, indent=2))
            return 1

        # `value` arrives as a string and each branch below narrows it to that key's
        # own type. Keep the raw string so later branches read it rather than
        # whatever an earlier branch coerced — the branches are exclusive, but
        # reassigning the shared name makes that an assumption rather than a fact.
        raw_value = value

        # Parse boolean values
        if key == "enabled":
            value = raw_value.lower() in ("true", "1", "yes", "on")

        # auto_push_on is the one key that makes empirica push CODE, so enabling it
        # goes through a validating path rather than a hand-edit of config.yaml.
        # It was absent from valid_keys before, which sounds like a safety property
        # and is the opposite: the CLI refused it while a hand-edit set a key nothing
        # read, so the deliberate act produced silence and the accidental one produced
        # a value with no effect.
        #
        # `session_end` is rejected BY NAME rather than accepted-and-ignored. A trigger
        # that fires when a session crashes fires when state is least trustworthy, and
        # silently dropping it would be the advertised-no-op shape again.
        if key == "auto_push_on":
            requested = (
                [v.strip() for v in raw_value.split(",") if v.strip()] if raw_value.lower() not in ("", "none") else []
            )
            unsupported = [t for t in requested if t not in SUPPORTED_TRIGGERS]
            if unsupported:
                result = {
                    "ok": False,
                    "error": (
                        f"Unsupported trigger(s): {', '.join(unsupported)}. "
                        f"Supported: {', '.join(sorted(SUPPORTED_TRIGGERS))}. "
                        "`session_end` is deliberately not implemented — sessions end by crashing too, "
                        "so a trigger that fires on abnormal termination fires when state is least trustworthy."
                    ),
                    "supported": sorted(SUPPORTED_TRIGGERS),
                }
                print(json.dumps(result, indent=2))
                return 1
            value = requested

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
            # The single-key path never computed the remote locals, and the human
            # renderer below read them unconditionally — so `sync-config <key> <value>
            # --output human` raised UnboundLocalError on every invocation. Invisible
            # because the default output is json, and `--output human` is what a person
            # reaches for exactly when they are unsure whether the write landed.
            if output_format == "json":
                print(json.dumps(result, indent=2))
            else:
                shown = result.get("value")
                print(f"   {key}: {shown if shown not in (None, []) else '(not set)'}")
            return 0 if result["ok"] else 1
        else:
            # Get remote info for context
            current_remote = _resolve_remote(NOTES, sync_config)
            remote_url = _get_remote_url(current_remote) if current_remote else None
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
            print(f"   remote: {sync_config.get('remote') or '(not set)'}")
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

            # Both destinations, ALWAYS — including when unset.
            #
            # `Code: <remote> (public)` used to sit beside the notes line as though both
            # were synced while NOTHING IN EMPIRICA PUSHED CODE, and a practitioner
            # auditing their config saw code listed as configured and stopped looking
            # (~765 commits lived on one laptop). auto_push now makes that label real
            # when it is on, so the line has to say WHICH of the two states it is in.
            #
            # And the block used to be gated on `notes_remote != code_remote`, which
            # hid it in exactly the case that matters most: with both unset they are
            # equal, so a seat that had chosen no destination at all was told nothing.
            code_remote = _resolve_remote(CODE, sync_config)
            _auto = sync_config.get("auto_push_on") or []
            print("\n   Destinations:")
            if current_remote:
                print(f"      Notes: {current_remote} — synced by `empirica sync-push`")
            else:
                print("      Notes: NOT SET — sync-push refuses rather than guessing")
            if code_remote and "postflight" in _auto:
                print(f"      Code:  {code_remote} — auto-pushed on postflight")
            elif code_remote:
                print(
                    f"      Code:  {code_remote} — configured only; auto-push is OFF "
                    f"(enable: empirica sync-config auto_push_on postflight)"
                )
            else:
                print("      Code:  NOT SET — empirica pushes no code")

            # Show private sync hint if notes remote is a public provider
            if detected_provider in ("github", "gitlab", "bitbucket"):
                print("\n   WARNING: Notes remote points to a public provider!")
                print("      Epistemic notes contain private data (findings, mistakes, messages).")
                print("      Switch to a private remote:")
                print("      empirica sync-config notes_remote <private-remote>")

            print("\n   Set with: empirica sync-config <key> <value>")
            print(f"   Keys: {', '.join(VALID_CONFIG_KEYS)}")

        return 0 if result["ok"] else 1

    except Exception as e:
        handle_cli_error(e, "Sync config", getattr(args, "verbose", False))
        return 1


def _resolve_or_refuse(kind: str, sync_config: dict[str, Any], explicit: str | None, output_format: str) -> str | None:
    """The remote for `kind`, or None having already PRINTED the refusal.

    Shared by push and pull so the two cannot drift into refusing differently — the
    original defect in this file was two verbs resolving the same destination by two
    different rules.
    """
    remote = _resolve_remote(kind, sync_config, explicit)
    if remote:
        return remote
    payload = _remote_refusal(kind)
    print(json.dumps(payload, indent=2) if output_format == "json" else _render_remote_refusal(payload))
    return None


def _refuse_public_notes_remote(remote: str, force: bool, output_format: str) -> bool:
    """True (having printed) when `remote` is a public host and `--force` was not given.

    Notes carry findings, mistakes and mesh messages. `--force` is the override — the
    point is that publishing them has to be an explicit act, not the result of a
    remote whose provider nobody looked at.
    """
    remote_url = _get_remote_url(remote)
    detected = _detect_provider(remote_url) if remote_url else "unknown"
    if detected not in ("github", "gitlab", "bitbucket") or force:
        return False

    configured = ", ".join(_list_remotes()) or "none"
    result = {
        "ok": False,
        "error": f"Refusing to push epistemic notes to public provider ({detected})",
        "remote": remote,
        "remote_url": remote_url,
        "hint": (
            "Notes contain private epistemic data (findings, mistakes, messages). "
            "Point notes at a private remote: 'empirica sync-config notes_remote <remote>'. "
            f"Configured here: {configured}. Use --force to override."
        ),
    }
    if output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"BLOCKED: Won't push notes to {detected} ({remote_url})")
        print("   Notes contain private epistemic data.")
        print("   Set a private remote: empirica sync-config notes_remote <remote>")
        print(f"   Configured here: {configured}")
        print("   Or use --force to override.")
    return True


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


#: The note namespaces `sync-push` replicates, with their timeouts. This IS the
#: replication contract — a namespace absent here never leaves the machine no
#: matter how the remote is configured, which is why `session/*` questions get
#: answered by reading this tuple rather than from memory (they live under
#: `refs/notes/empirica/session/*`, so the wildcard already carries them).
_PUSH_REFSPECS: tuple[tuple[str, str, int], ...] = (
    ("empirica/*", "refs/notes/empirica/*:refs/notes/empirica/*", 60),
    ("breadcrumbs", "refs/notes/breadcrumbs:refs/notes/breadcrumbs", 30),
    ("empirica-precompact", "refs/notes/empirica-precompact:refs/notes/empirica-precompact", 30),
)


def _push_note_namespaces(remote: str) -> tuple[dict[str, bool], list[str]]:
    """Push each note namespace. Returns (per-namespace exit-code result, errors).

    Exit codes only — whether anything REPLICATED is `_verify_push_landed`'s
    question, deliberately kept separate so the two are not confused again.

    Errors from the secondary namespaces used to be swallowed entirely
    (`except Exception: push_results[...] = False`), so a breadcrumbs push that
    failed for a nameable reason reported the same as one that was never tried.
    """
    results: dict[str, bool] = {}
    errors: list[str] = []
    for name, refspec, timeout in _PUSH_REFSPECS:
        try:
            proc = subprocess.run(
                ["git", "push", remote, refspec],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            results[name] = proc.returncode == 0
            if proc.returncode != 0 and proc.stderr:
                errors.append(f"{name}: {proc.stderr.strip()}")
        except subprocess.TimeoutExpired:
            results[name] = False
            errors.append(f"{name}: push timed out after {timeout}s")
        except Exception as e:
            results[name] = False
            errors.append(f"{name}: {e}")
    return results, errors


def _verify_push_landed(remote: str, remote_before: int | None) -> tuple[dict[str, Any], bool]:
    """Did the remote actually MOVE? Returns (verification, push_refuted).

    `git push <remote> 'refs/notes/empirica/*:refs/notes/empirica/*'` with a
    refspec matching nothing exits **0** and pushes **nothing** — from the exit
    code alone, indistinguishable from replicating the whole graph. Two
    practices carried five-figure local-only counts while every push had
    "succeeded" (5,622 here, 3,617 on a peer's box — 69% of their graph).

    So the verdict comes from counting the remote, not from git's mood.
    `push_refuted` is True only for the case the exit code cannot express:
    it returned success and the remote did not move.
    """
    local_total = _count_all_local_note_refs()
    remote_after, after_err = _count_remote_notes(remote)
    verification: dict[str, Any] = {
        "local_refs": local_total,
        "remote_before": remote_before,
        "remote_after": remote_after,
    }

    if remote_after is None:
        # Unreachable AFTER a push that reported success: unknown, carrying the
        # reason. Degrading to zero would report a healthy seat as catastrophic;
        # degrading to silence is the original defect.
        verification["verdict"] = "unknown"
        verification["reason"] = after_err or "remote unreachable after push"
        return verification, False

    verification["pushed"] = remote_after - remote_before if remote_before is not None else None

    if remote_after >= local_total:
        verification["verdict"] = "replicated"
        return verification, False

    if remote_before is not None and remote_after > remote_before:
        verification["verdict"] = "partial"
        verification["missing"] = local_total - remote_after
        return verification, False

    verification["verdict"] = "not_replicating"
    verification["missing"] = local_total - remote_after
    verification["error"] = (
        f"git push reported success but the remote did not move: "
        f"{local_total} local refs, {remote_after} on {remote}. "
        f"A refspec matching nothing exits 0 — check that local refs exist under "
        f"refs/notes/empirica/* and that {remote} accepts note refs."
    )
    return verification, True


def handle_sync_push_command(args):
    """Handle sync push command - push all epistemic notes to remote"""
    try:
        # Load config
        sync_config = _load_sync_config()

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

        # Explicit flag, else config, else REFUSE. `--force` overrides the private-provider
        # block below; it does NOT invent a destination, because there is nothing to
        # override here — an unset remote is an absent answer, not a cautious one.
        remote = _resolve_or_refuse(NOTES, sync_config, getattr(args, "remote", None), output_format)
        if not remote:
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
        if _refuse_public_notes_remote(remote, force, output_format):
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

        # Count the remote BEFORE, so success can be judged by whether the remote
        # MOVED rather than by git's exit code. `git push <remote> 'refs/notes/
        # empirica/*:refs/notes/empirica/*'` with a refspec that matches nothing
        # local exits 0 and pushes nothing — indistinguishable, from the exit
        # code alone, from a push that replicated the whole graph. Two practices
        # measured five-figure local-only ref counts while every push had
        # "succeeded": 5,622 here, 3,617 on a peer's box.
        remote_before, _before_err = _count_remote_notes(str(remote))

        push_results, errors = _push_note_namespaces(str(remote))
        success = push_results.get("empirica/*", False)

        # VERIFY THROUGH THE REMOTE, not through the exit code. git having
        # returned 0 says the command ran, not that anything replicated — so
        # re-count and report what actually landed. `local` is every local note
        # ref (the same counter sync-status uses); comparing against a
        # namespace-scoped count is how an earlier version of this reporting
        # printed REPLICATED while 5,622 refs were missing.
        verification, push_refuted = _verify_push_landed(str(remote), remote_before)
        if push_refuted and success:
            errors.append(verification["error"])
            success = False

        result = {
            "ok": success,
            "remote": remote,
            "push_results": push_results,
            "verification": verification,
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

        # Explicit flag, else config, else REFUSE. Pulling from the wrong remote is
        # cheaper than pushing to it, but it is the same guess and it imports a
        # stranger's graph into yours.
        remote = _resolve_or_refuse(NOTES, sync_config, getattr(args, "remote", None), output_format)
        if not remote:
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


def _count_all_local_note_refs() -> int:
    """EVERY ref under ``refs/notes/``, not just the enumerated namespaces.

    ``_count_local_notes()`` walks ``EMPIRICA_NOTES_REFS`` and is the right function
    for the per-namespace breakdown. It is the WRONG one to compare against a remote,
    because the remote side counts ``refs/notes/*`` wholesale — and on this repo those
    differ by 11,232 refs (``breadcrumbs`` and ``empirica-precompact`` live outside the
    enumerated list).

    Caught by running it: the first version of the replication check reported
    ``REPLICATED — all 6405 local note refs are on the remote`` while 5,620 refs were
    missing. **Two counts over two different sets is not a comparison**, and the
    direction of the error was the reassuring one.
    """
    try:
        proc = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/notes/"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:
        return 0
    if proc.returncode != 0:
        return 0
    return len([ln for ln in proc.stdout.splitlines() if ln.strip()])


def _count_remote_notes(remote: str, timeout: int = 20) -> tuple[int | None, str | None]:
    """Note refs present on `remote` — ``(count, unreachable_reason)``.

    Exactly one of the two is None. **An unreachable remote must not read as zero and
    must not read as fine**: a network failure and an empty remote are opposite facts
    and only one of them is a sync problem, so the reason is carried rather than
    swallowed into a number.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-remote", remote, "refs/notes/*"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s reaching {remote}"
    except Exception as e:  # broad by design — the reason is REPORTED, never swallowed
        return None, f"{type(e).__name__}: {e}"
    if proc.returncode != 0:
        return None, (proc.stderr.strip().splitlines() or ["git ls-remote failed"])[-1][:200]
    return len([ln for ln in proc.stdout.splitlines() if ln.strip()]), None


def _replication_verdict(local: int, remote_count: int | None, unreachable: str | None) -> dict[str, Any]:
    """Are local notes actually reaching the remote? Say it in words.

    THE GAP THIS CLOSES. Across four practices measured 2026-09-02, ~9,500 epistemic
    artifact refs had never left the machine they were written on — and `sync-status`
    reported healthy for every one, because "a remote is configured" was the only
    question it could answer. Configuration is not replication. A verb consulted to
    decide whether a thing is working must be able to say *it is not*.

    `local > remote` is the detectable signature and it is deliberately cheap: it
    needs no history walk, only two counts.
    """
    if unreachable or remote_count is None:
        reason = unreachable or "remote ref count unavailable"
        return {"state": "unknown", "reason": f"remote not reachable — {reason}", "behind": None}
    behind = local - remote_count
    if local == 0:
        return {"state": "nothing_to_replicate", "reason": "no local note refs", "behind": 0}
    if behind > 0:
        # `<1%` rather than a rounded `0%`. A real one-ref gap rendering as
        # "1 of 970 (0%) are NOT on the remote" puts the prose in contradiction with
        # the categorical state beside it, and the predictable repair is to soften the
        # STATE to match the number — fixing the honest half. The integer `behind` is
        # what consumers should threshold on; this only stops the string arguing
        # against it.
        raw = 100 * behind / local
        pct = "<1" if raw < 0.5 else str(round(raw))
        return {
            "state": "not_replicating" if remote_count == 0 else "behind",
            "reason": (
                f"{behind} of {local} local note refs ({pct}%) are NOT on the remote"
                + (" — nothing has ever been pushed" if remote_count == 0 else "")
            ),
            "behind": behind,
        }
    return {"state": "replicated", "reason": f"all {local} local note refs are on the remote", "behind": 0}


def _replication_block(remote: str | None, remote_configured: bool, local_only: bool) -> dict[str, Any]:
    """The `replication` fields for `sync-status`. **Always present, never omitted.**

    Costs one ``git ls-remote``; `--local` opts out. Default-on deliberately — a signal
    nobody turns on is a signal nobody sees, and every seat this was built for would
    have had to know to ask.

    The key is emitted even when nothing could be computed. It used to be omitted when
    no remote was configured — which is the DEFAULT state of every seat after the
    no-default change, so it was the common case at rollout, not an edge. An absent key
    cannot be told apart from *computed and fine* or *this build predates the field*:
    absence defaults to whatever the reader assumes. That is the same collapse as
    PASS-vs-SKIP and unset-vs-misconfigured, one layer down and applied to a JSON key.

    ``sync_available`` is deliberately NOT touched here. "The remote is configured" and
    "the notes are there" are different facts, and the whole defect was one field
    answering both.
    """
    if not remote:
        skipped = "no notes remote configured — nothing to compare against"
    elif not remote_configured:
        skipped = f"'{remote}' is configured but is not a git remote here"
    elif local_only:
        skipped = "skipped by --local (no network call was made)"
    else:
        skipped = ""

    if skipped:
        return {"remote_notes": None, "replication": {"state": "unknown", "reason": skipped, "behind": None}}

    remote_count, unreachable = _count_remote_notes(str(remote))
    # BOTH SIDES OVER THE SAME REF SET. `total_notes` covers the enumerated namespaces
    # only, and comparing it here reported REPLICATED while 5,622 refs were missing.
    all_local = _count_all_local_note_refs()
    return {
        "local_note_refs": all_local,
        "remote_notes": remote_count,
        "replication": _replication_verdict(all_local, remote_count, unreachable),
    }


def handle_sync_status_command(args):
    """Handle sync status command - show sync status"""
    try:
        # RESOLVE THROUGH THE SHARED RESOLVER, like every other sync verb.
        #
        # This verb used to take its remote from args with a literal default and never
        # read sync_config at all, so on any seat whose remote was not named origin it
        # reported on a remote nothing else used. A practitioner set the remote
        # correctly, ran this, was told the remote was unconfigured, and concluded the
        # write had failed. It had not.
        #
        # That is the expensive direction of this defect: the STATUS verb is what
        # someone consults to decide whether a thing is working, so when it is the one
        # that is wrong it does not merely fail to inform, it sends the reader to fix
        # something that was not broken — or to trust something that is.
        # STATUS REPORTS, it does not refuse — but it must distinguish the two ways a
        # remote can be absent, because they used to render identically. `remote: null`
        # means nobody chose one; `remote: forgejo, remote_configured: false` means
        # someone chose a remote this repo does not have. The first is a decision not
        # taken, the second is a decision that no longer matches the repo, and the fix
        # differs.
        sync_config = _load_sync_config()
        remote = _resolve_remote(NOTES, sync_config, getattr(args, "remote", None))
        code_remote = _resolve_remote(CODE, sync_config)
        output_format = getattr(args, "output", "json")

        # Check remote exists
        remote_configured = bool(remote) and _check_remote(remote)
        auto_push_on = sync_config.get("auto_push_on") or []

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
            # CODE is reported separately and never folded into `sync_available` —
            # `sync-push` moves notes only, so a configured code remote says nothing
            # about whether notes reach anywhere.
            "code_remote": code_remote,
            "code_auto_push_on": auto_push_on,
            "available_remotes": _list_remotes(),
        }
        if not remote:
            result["hint"] = _remote_refusal(NOTES)["hint"]

        result.update(_replication_block(remote, remote_configured, getattr(args, "local", False)))

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print("📊 Empirica Sync Status")
            if remote:
                print(f"   Notes remote: {remote} ({'configured' if remote_configured else 'NOT a git remote here'})")
            else:
                print("   Notes remote: NOT SET — nothing pushes notes anywhere")
            if code_remote:
                fires = "auto-pushed on postflight" if "postflight" in auto_push_on else "auto-push OFF"
                print(f"   Code remote:  {code_remote} ({fires})")
            else:
                print("   Code remote:  NOT SET — empirica pushes no code")
            print(f"   Local refs with data: {refs_with_data}")
            print(f"   Total notes: {total_notes}")

            # The replication verdict, in words, ABOVE the per-ref counts. It is the
            # thing the reader came for; four seats read a healthy-looking status while
            # thousands of refs had never left the machine.
            rep = result.get("replication")
            if rep:
                state = str(rep["state"])
                icon = {"replicated": "✅", "behind": "⚠️", "not_replicating": "❌", "unknown": "❓"}.get(state, "•")
                if result.get("remote_notes") is not None:
                    print(
                        f"   All note refs: {result.get('local_note_refs')} local / {result.get('remote_notes')} remote"
                    )
                print(f"   {icon} Replication: {state.upper()} — {rep['reason']}")
                if state in ("behind", "not_replicating"):
                    print("      Fix: empirica sync-push")

            if local_counts:
                print("\n   Note counts:")
                for ref, count in sorted(local_counts.items()):
                    if count > 0:
                        print(f"      refs/notes/{ref}: {count}")

            if not remote:
                print(f"\n   ⚠️ {_remote_refusal(NOTES)['hint']}")
            elif not remote_configured:
                print(
                    f"\n   ⚠️ '{remote}' is configured in .empirica but is not a git remote here. "
                    f"Present: {', '.join(_list_remotes()) or 'none'}"
                )

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
