"""Memory directory swap for cross-CWD project work.

When the harness CWD doesn't match the active transaction's project (e.g.
the user `cd`'d into one repo but the open transaction lives in another),
Claude Code's auto-memory loader still reads `~/.claude/projects/-{cwd}/memory/`
— which loads the WRONG project's memory.

This module swaps the wrong-project's memory dir contents with the right
project's contents at compaction / project-switch boundaries, then restores
them when the work returns to the original CWD project (or the session ends).

Approach:
1. Backup the harness-CWD project's memory dir to a sibling backup directory
2. Copy the active-transaction project's memory dir contents into the
   harness-CWD project's memory dir
3. Write a manifest file recording the swap so we can restore it later
4. On restore: move the backup contents back, delete the manifest

The swap is idempotent — calling swap_memory() twice is a no-op if the swap
is already active and points at the same source. Calling restore_memory()
on an unswapped dir is a no-op.

Why a swap and not the resolver: the project resolver gives us correctness
for all internal Empirica code paths (CLI, hooks, sentinel) but cannot reach
Claude Code's auto-memory loader, which is wired to the harness CWD at
session start. The swap closes that visibility gap.

Related: KNOWN_ISSUES.md 11.28
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Manifest filename written into the swapped (wrong-CWD) memory dir
MANIFEST_NAME = ".memory-swap-manifest.json"
# Backup subdirectory name (lives inside the harness-CWD project memory dir)
BACKUP_SUBDIR = ".memory-swap-backup"


def _claude_memory_dir(project_path: Path) -> Path:
    """Compute the Claude Code auto-memory directory for a project path.

    Claude Code maps absolute paths to memory dirs by replacing `/` with `-`:
        /path/to/repo  →  ~/.claude/projects/-path-to-repo/memory/
    """
    project_key = str(project_path.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / project_key / "memory"


def is_swap_active(harness_cwd_project: Path) -> bool:
    """Check whether a memory swap is currently active for this CWD project."""
    memory_dir = _claude_memory_dir(harness_cwd_project)
    return (memory_dir / MANIFEST_NAME).exists()


def read_manifest(harness_cwd_project: Path) -> dict | None:
    """Read the swap manifest if a swap is active. Returns None otherwise."""
    memory_dir = _claude_memory_dir(harness_cwd_project)
    manifest_file = memory_dir / MANIFEST_NAME
    if not manifest_file.exists():
        return None
    try:
        with open(manifest_file) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read memory swap manifest: {e}")
        return None


def _copy_dir_entries(source: Path, dest: Path, label: str) -> list[str]:
    """Copy directory entries from source to dest, skipping swap-internal files."""
    copied = []
    for entry in source.iterdir():
        if entry.name in (BACKUP_SUBDIR, MANIFEST_NAME):
            continue
        target = dest / entry.name
        try:
            if entry.is_dir():
                shutil.copytree(entry, target, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, target)
            copied.append(entry.name)
        except Exception as e:
            logger.warning(f"{label} failed for {entry}: {e}")
    return copied


def _sha256(path: Path) -> str | None:
    """Content hash of a file; None for a directory or anything unreadable."""
    try:
        if not path.is_file():
            return None
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _memory_files(directory: Path) -> dict[str, Path]:
    """Every file under a memory dir by relative posix path, skipping the swap's own backup dir and manifest at the top level."""
    found: dict[str, Path] = {}
    for path in sorted(directory.rglob("*")):
        rel = path.relative_to(directory)
        if rel.parts[0] in (BACKUP_SUBDIR, MANIFEST_NAME) or not path.is_file():
            continue
        found[rel.as_posix()] = path
    return found


def _conflict_copy(dest: Path) -> Path:
    """A free sibling name for an edit that cannot be written over `dest`: `note.md` -> `note.swap-conflict.md`."""
    candidate = dest.with_name(f"{dest.stem}.swap-conflict{dest.suffix}")
    n = 2
    while candidate.exists():
        candidate = dest.with_name(f"{dest.stem}.swap-conflict-{n}{dest.suffix}")
        n += 1
    return candidate


def _write_back(target_memory: Path, manifest: dict) -> tuple[list[str], list[str]]:
    """Carry what was written to the swapped memory dir back to the project it belongs to.

    While a swap is active the harness's auto-memory writes into the harness project's dir, which holds the ACTIVE project's files.
    Restoring without this threw those edits away. Works per file, recursively, against the hash recorded for each file at swap time.
    Returns (written_back, conflicts), both as relative paths.

    * A swapped-in file that changed is written back when the source still holds what was copied (or no longer has the file).
    * A new file is written back when the source has no file of that name.
    * Anything else is a CONFLICT (another session wrote there meanwhile): the source is never overwritten, and the edit is kept as
      `<name>.swap-conflict<ext>` beside it, because the caller removes the swapped-in files afterwards and a conflict that is only
      counted would be destroyed with them.
    * A file the source already holds with identical content counts as written (a retry after an interrupted restore).

    A manifest from before hashes were recorded cannot tell an edit from the original, so it writes nothing back.
    """
    hashes = manifest.get("copied_hashes")
    source_project = manifest.get("source_project")
    if not isinstance(hashes, dict) or not source_project:
        return [], []
    source_memory = _claude_memory_dir(Path(source_project))
    written: list[str] = []
    conflicts: list[str] = []
    for rel, path in _memory_files(target_memory).items():
        now = _sha256(path)
        recorded = hashes.get(rel)
        if recorded is not None and now == recorded:
            continue  # nobody touched it
        if recorded is None and _sha256(target_memory / BACKUP_SUBDIR / rel) == now:
            continue  # the harness project's own original, put back by a restore that was interrupted: never the active project's
        dest = source_memory / rel
        if not dest.is_symlink() and dest.is_file() and _sha256(dest) == now:
            written.append(rel)
            continue
        # A symlink in the source is never written through: its target is a different file than the one that was edited.
        free = not dest.is_symlink() and (
            not dest.exists() or (recorded is not None and dest.is_file() and _sha256(dest) == recorded)
        )
        target = dest if free else _conflict_copy(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        (written if free else conflicts).append(rel)
    return written, conflicts


def _swapped_in_names(target_memory: Path, manifest: dict) -> list[str]:
    """Top-level entries a restore removes. With hashes recorded, every write-back outcome (copied over, kept as a conflict copy, or
    already there) has been secured in the source project, so everything but the swap's own backup and manifest goes: a new file left
    behind would load as this project's memory. A manifest without hashes wrote nothing back, so only what the swap itself copied in
    is removed and anything else stays where it was written."""
    copied = set(manifest.get("copied_files", []))
    if not isinstance(manifest.get("copied_hashes"), dict):
        return sorted(copied)
    return sorted(copied | {e.name for e in target_memory.iterdir() if e.name not in (BACKUP_SUBDIR, MANIFEST_NAME)})


def _guarded_write_back(target_memory: Path, manifest: dict, force: bool) -> tuple[list[str], list[str], dict | None]:
    """_write_back, with a failure turned into a refusal to restore: removing the swapped-in files would destroy the only copy of an
    edit. A forced replacement (swapping to a different project) proceeds anyway, as every other restore error does there."""
    try:
        written, conflicts = _write_back(target_memory, manifest)
        return written, conflicts, None
    except Exception as e:
        logger.warning(f"Write-back failed for {manifest.get('harness_cwd_project')}: {e}")
        if force:
            return [], [], None
        return [], [], {"ok": False, "action": "write_back_failed", "message": str(e)}


def _remove_entries(directory: Path, names: list[str]) -> None:
    """Remove named entries from a directory."""
    for name in names:
        original = directory / name
        try:
            if original.is_dir():
                shutil.rmtree(original)
            else:
                original.unlink()
        except Exception as e:
            logger.warning(f"Failed to remove original {original}: {e}")


def swap_memory(
    harness_cwd_project: Path,
    active_tx_project: Path,
    *,
    claude_session_id: str | None = None,
    transaction_id: str | None = None,
) -> dict:
    """Swap the harness-CWD project's memory dir with the active-tx project's.

    Args:
        harness_cwd_project: Path the harness CWD points at (visible to Claude
            Code's auto-memory loader)
        active_tx_project: Path of the project that owns the open transaction
            (the work the user is actually doing)
        claude_session_id: Optional session UUID for the manifest
        transaction_id: Optional transaction UUID for the manifest

    Returns:
        Result dict with keys: ok, action, message, manifest_path
    """
    harness_cwd_project = Path(harness_cwd_project).resolve()
    active_tx_project = Path(active_tx_project).resolve()

    # Same project: nothing to swap in. But if an earlier swap put ANOTHER project's files
    # into this memory dir, the work has come back to its own project, so give it its own
    # memory back instead of leaving the stale copy in place.
    if harness_cwd_project == active_tx_project:
        if read_manifest(harness_cwd_project):
            return restore_memory(harness_cwd_project)
        return {
            "ok": True,
            "action": "noop",
            "message": "Harness CWD matches active transaction project — no swap needed",
        }

    target_memory = _claude_memory_dir(harness_cwd_project)
    source_memory = _claude_memory_dir(active_tx_project)

    if not source_memory.exists():
        return {
            "ok": False,
            "action": "skip",
            "message": f"Source memory dir does not exist: {source_memory}",
        }

    # Idempotency: if swap is already active and points at the same source,
    # don't re-swap (would clobber any in-flight memory writes)
    existing = read_manifest(harness_cwd_project)
    if existing and existing.get("source_project") == str(active_tx_project):
        return {
            "ok": True,
            "action": "already_active",
            "message": f"Swap already active for {active_tx_project} → {harness_cwd_project}",
            "manifest_path": str(target_memory / MANIFEST_NAME),
        }

    # If a different swap is already active, restore it first to avoid layering
    if existing:
        logger.info(f"Replacing existing swap (was: {existing.get('source_project')})")
        replaced = restore_memory(harness_cwd_project, _force_replace=True)
        if replaced.get("action") == "broken":
            # Its backup is gone, so the files in this dir are the OLD project's, not the harness project's. Swapping now would back
            # them up as this project's originals and restore them as such for good.
            return {
                "ok": False,
                "action": "restore_before_swap_failed",
                "message": (
                    f"An earlier swap ({existing.get('source_project')}) has no backup left to restore from; not layering a new swap "
                    f"over {target_memory}. Clear that directory by hand, then retry."
                ),
            }

    target_memory.mkdir(parents=True, exist_ok=True)
    backup_dir = target_memory / BACKUP_SUBDIR

    # A backup with no manifest is what a swap leaves when its manifest write failed: the originals are in the backup, the swapped-in
    # files are in this dir, and nothing records the swap. Backing the directory up again would put the swapped-in files over the
    # originals, so refuse and say where they are.
    if existing is None and backup_dir.is_dir() and any(backup_dir.iterdir()):
        return {
            "ok": False,
            "action": "orphan_backup",
            "message": (
                f"{backup_dir} holds a backup but no swap manifest exists (an earlier swap did not finish). Not swapping over it: the "
                f"original memory is in {BACKUP_SUBDIR}. Move those files back into {target_memory}, delete {BACKUP_SUBDIR}, then retry."
            ),
        }

    # Backup target memory dir contents (excluding the backup dir itself if it exists)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backed_up = _copy_dir_entries(target_memory, backup_dir, "Backup")
    unsaved = sorted(
        e.name
        for e in target_memory.iterdir()
        if e.name not in (BACKUP_SUBDIR, MANIFEST_NAME) and e.name not in backed_up
    )
    if unsaved:
        # Removing an original that was not backed up destroys it, and a manifest-less half swap cannot be restored.
        return {
            "ok": False,
            "action": "backup_incomplete",
            "message": f"Could not back up {', '.join(unsaved)} in {target_memory}; nothing was removed or swapped.",
        }

    # Remove originals (now safely backed up)
    _remove_entries(target_memory, backed_up)

    # Copy source memory dir contents into target (skipping its own backup dirs)
    copied = _copy_dir_entries(source_memory, target_memory, "Copy")

    # Write the manifest
    manifest = {
        "version": 1,
        "swapped_at": datetime.now().isoformat(),
        "swapped_at_epoch": time.time(),
        "harness_cwd_project": str(harness_cwd_project),
        "source_project": str(active_tx_project),
        "claude_session_id": claude_session_id,
        "transaction_id": transaction_id,
        "backed_up_files": backed_up,
        "copied_files": copied,
        # What each swapped-in file (by relative path, directories walked) held at swap time, so a restore can tell an edit made
        # during the swap from the original.
        "copied_hashes": {rel: _sha256(path) for rel, path in _memory_files(target_memory).items()},
    }
    manifest_file = target_memory / MANIFEST_NAME
    try:
        with open(manifest_file, "w") as f:
            json.dump(manifest, f, indent=2)
    except Exception as e:
        return {
            "ok": False,
            "action": "manifest_write_failed",
            "message": str(e),
        }

    return {
        "ok": True,
        "action": "swapped",
        "message": (
            f"Swapped memory: {active_tx_project.name} → {harness_cwd_project.name} "
            f"({len(copied)} files copied, {len(backed_up)} backed up)"
        ),
        "manifest_path": str(manifest_file),
    }


def restore_memory(harness_cwd_project: Path, *, _force_replace: bool = False) -> dict:
    """Restore the original memory dir contents and remove the swap.

    Args:
        harness_cwd_project: Same path passed to swap_memory()
        _force_replace: Internal flag — when True, swallow restore errors so a
            replacement swap can proceed

    Returns:
        Result dict with keys: ok, action, message
    """
    harness_cwd_project = Path(harness_cwd_project).resolve()
    target_memory = _claude_memory_dir(harness_cwd_project)
    manifest = read_manifest(harness_cwd_project)

    if not manifest:
        return {
            "ok": True,
            "action": "noop",
            "message": "No active swap to restore",
        }

    backup_dir = target_memory / BACKUP_SUBDIR
    if not backup_dir.exists():
        # Manifest exists but backup is missing — broken state, just clear the manifest
        try:
            (target_memory / MANIFEST_NAME).unlink(missing_ok=True)
        except Exception:
            pass
        return {
            "ok": bool(_force_replace),
            "action": "broken",
            "message": f"Backup dir missing for {harness_cwd_project} — manifest cleared",
        }

    # Keep what was written while swapped before the swapped-in files are removed.
    written_back, write_back_conflicts, failure = _guarded_write_back(target_memory, manifest, _force_replace)
    if failure:
        return failure

    swapped_in = _swapped_in_names(target_memory, manifest)
    for name in swapped_in:
        path = target_memory / name
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Failed to remove swapped-in {path}: {e}")
            if not _force_replace:
                return {
                    "ok": False,
                    "action": "remove_failed",
                    "message": str(e),
                }

    # Restore the originals from backup
    restored = []
    for entry in backup_dir.iterdir():
        dest = target_memory / entry.name
        try:
            if entry.is_dir():
                shutil.copytree(entry, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, dest)
            restored.append(entry.name)
        except Exception as e:
            logger.warning(f"Restore failed for {entry}: {e}")
            if not _force_replace:
                return {
                    "ok": False,
                    "action": "restore_failed",
                    "message": str(e),
                }

    # Remove backup dir + manifest
    try:
        shutil.rmtree(backup_dir)
    except Exception as e:
        logger.warning(f"Failed to remove backup dir: {e}")
    try:
        (target_memory / MANIFEST_NAME).unlink(missing_ok=True)
    except Exception:
        pass

    return {
        "ok": True,
        "action": "restored",
        "message": (
            f"Restored {len(restored)} files for {harness_cwd_project.name}"
            + (f"; wrote back {len(written_back)} changed to the active project" if written_back else "")
            + (
                f"; {len(write_back_conflicts)} not written back (changed in both places)"
                if write_back_conflicts
                else ""
            )
        ),
        "written_back": written_back,
        "write_back_conflicts": write_back_conflicts,
    }


def harness_project_for(cwd: Path) -> Path:
    """The project a harness cwd belongs to: its git root, else the directory itself."""
    try:
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except Exception:
        pass
    return Path(cwd)


def restore_if_swapped(harness_cwd: Path) -> dict | None:
    """Restore the harness project's memory if a swap is active; None when there is nothing to restore.

    Never raises: a failure comes back as ``{"ok": False, "action": "restore_failed", ...}`` so the
    caller can say so. A swallowed failure leaves another project's memory under this project's name."""
    try:
        project = harness_project_for(harness_cwd).resolve()
        if not read_manifest(project):
            return None
        return restore_memory(project)
    except Exception as e:
        return {"ok": False, "action": "restore_failed", "message": str(e)}


def maybe_swap_for_active_transaction(
    claude_session_id: str | None = None,
    harness_cwd: Path | None = None,
) -> dict:
    """Detect harness-CWD vs active-transaction mismatch and swap if needed.

    Convenience entry point for hooks. Reads the active transaction via the
    InstanceResolver, compares against the harness CWD's git root, and swaps the
    memory dir if they differ.

    Args:
        claude_session_id: Optional session UUID from hook input
        harness_cwd: The directory the harness launched in. A hook that has
            chdir'd (post-compact moves to the resolved practice) MUST capture
            this before moving and pass it: the process cwd is no longer the
            harness's, and comparing it against the active project always matches.

    Returns:
        Result dict from swap_memory() or a noop indicator
    """
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        ctx = R.context(claude_session_id)
        active_tx_project = ctx.get("project_path")
        transaction_id = ctx.get("transaction_id")
    except Exception as e:
        return {
            "ok": False,
            "action": "context_lookup_failed",
            "message": str(e),
        }

    if not active_tx_project:
        return {
            "ok": True,
            "action": "noop",
            "message": "No active transaction project — nothing to swap",
        }

    return swap_memory(
        harness_cwd_project=harness_project_for(harness_cwd if harness_cwd is not None else Path.cwd()),
        active_tx_project=Path(active_tx_project),
        claude_session_id=claude_session_id,
        transaction_id=transaction_id,
    )
