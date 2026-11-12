"""Gardening must reach git notes, and reach them the way sqlite does.

Notes are the canonical log — `rebuild --qdrant` force-imports them back INTO
sqlite — so a note that disagrees with sqlite is not a stale copy, it is a
pending revert. Gardening operated on sqlite alone, so gardened noise re-surfaced
on any notes-reading path.

**The two halves needed different fixes, and the difference is the whole design.**
Measured 2026-09-06:

    delete-artifacts   DID reach notes, via `git update-ref -d` — but DESTROYED
                       the ref. The row leaves the active graph, correct, and the
                       journey leaves with it.
    *-resolve          reached notes NOT AT ALL. Zero references in the single or
                       batch resolve handlers.

So delete changes from destroy to ARCHIVE, and resolve gains a stamp it never had.
Mirroring sqlite exactly, which is the acceptance invariant: sqlite DELETES noise
rows and KEEPS resolved ones with their resolution fields. Archiving a resolved
note would have been the obvious-looking mistake — it would drop resolved rows
out of the active graph while sqlite kept them, a second divergence in the
opposite direction.

Layout, parallel namespaces so the journey is kept beside the active graph (LOCAL ONLY: no sync verb pushes or pulls
`empirica-archive`, deliberately, and sync counts exclude it, so an archived note does not survive on a remote):

    refs/notes/empirica/<type>/<id>           active — mirrors sqlite
    refs/notes/empirica-archive/<type>/<id>   the journey

Nine store modules each hand-roll `git notes --ref=… add -f`, which is why the
gap existed in all of them simultaneously: there was nowhere central to put this.
It lives here once, and both callers import it.
"""

from __future__ import annotations

import json
import logging
import subprocess

logger = logging.getLogger(__name__)

ACTIVE_PREFIX = "refs/notes/empirica"
ARCHIVE_PREFIX = "refs/notes/empirica-archive"


def _git(args: list[str], project_path: str | None, input: str | None = None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=project_path or None,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            input=input,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug(f"note_lifecycle: git {args[:2]} failed: {e}")
        return None


def note_target(note_ref: str, workspace_root: str | None) -> str:
    """The commit to write ``note_ref``'s note on: the commit its current note is attached to, else HEAD.

    ``git notes add -f <commit>`` replaces only the note on that commit. A rewrite at HEAD after HEAD has moved left a second
    note under the ref, and readers take whichever ``git notes list`` prints first, so a rebuild could restore the old state
    (1.16.0 sweep; goal_store._note_commit fixed it for goals). Raises CalledProcessError when there is no HEAD, as the
    callers' own ``rev-parse HEAD`` did."""
    listed = subprocess.run(
        ["git", "notes", f"--ref={note_ref}", "list"], cwd=workspace_root, capture_output=True, text=True, timeout=10
    )
    if listed.returncode == 0 and listed.stdout.strip():
        parts = listed.stdout.strip().splitlines()[0].split()
        if len(parts) >= 2:
            return parts[1]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace_root, capture_output=True, text=True, check=True, timeout=10
    )
    return head.stdout.strip()


def active_ref(artifact_type: str, artifact_id: str) -> str:
    return f"{ACTIVE_PREFIX}/{artifact_type}/{artifact_id}"


def archive_ref(artifact_type: str, artifact_id: str) -> str:
    return f"{ARCHIVE_PREFIX}/{artifact_type}/{artifact_id}"


def archive_note(artifact_type: str, artifact_id: str, project_path: str | None = None) -> dict:
    """MOVE the note out of the active namespace, keeping it in the archive.

    Copy-then-delete, in that order and checked between: a delete that ran first
    (or a copy whose failure went unnoticed) would destroy the journey while
    reporting the archive succeeded — the shape this whole change exists to
    remove. If the copy fails, the active ref is LEFT ALONE and the caller is
    told, because a note still in the active namespace is a visible problem while
    a vanished one is not.

    Returns {archived, reason} — never raises. Absent is reported as
    `not_present`, not as success: a delete of something that was never there is
    a different fact from a delete that worked.
    """
    src, dst = active_ref(artifact_type, artifact_id), archive_ref(artifact_type, artifact_id)

    rev = _git(["rev-parse", "--verify", "--quiet", src], project_path)
    if rev is None:
        return {"archived": False, "reason": "git unavailable"}
    if rev.returncode != 0 or not rev.stdout.strip():
        return {"archived": False, "reason": "not_present"}
    sha = rev.stdout.strip()

    # Never overwrite an archive that already holds something else: a sync-pull can resurrect an active note that was
    # archived, and deleting it again would replace the earlier archive with the same-named newer one.
    existing = _git(["rev-parse", "--verify", "--quiet", dst], project_path)
    if existing is not None and existing.returncode == 0 and existing.stdout.strip() not in ("", sha):
        return {"archived": False, "reason": "archive ref already holds different content; active ref left intact"}
    wrote = _git(["update-ref", dst, sha], project_path)
    if wrote is None or wrote.returncode != 0:
        detail = (wrote.stderr.strip()[:120] if wrote else "git unavailable") or "unknown"
        # Deliberately NOT deleting the active ref here.
        return {"archived": False, "reason": f"archive write failed, active ref left intact: {detail}"}

    removed = _git(["update-ref", "-d", src], project_path)
    if removed is None or removed.returncode != 0:
        return {"archived": False, "reason": "copied to archive but active ref could not be removed — note is in BOTH"}
    return {"archived": True, "reason": "moved to archive", "sha": sha}


def stamp_resolution(
    artifact_type: str,
    artifact_id: str,
    resolution: dict,
    project_path: str | None = None,
) -> dict:
    """Stamp resolution fields into the note, which STAYS ACTIVE.

    sqlite keeps resolved rows, so the active notes ref must too — otherwise a
    rebuild from notes would drop every resolved artifact and the two stores
    would diverge in the opposite direction from the one being fixed.

    Merges into the existing payload rather than replacing it: the claim text is
    immutable by design, and a resolution records that a claim was closed, not
    that it was rewritten.
    """
    ref = active_ref(artifact_type, artifact_id)
    ref_short = ref[len("refs/notes/") :]

    # A notes ref points at a notes COMMIT whose tree maps target-commit -> blob;
    # it is not itself a blob. `<ref>^{blob}` therefore does not resolve, and
    # reading through it silently fails on every note. `notes list` gives the
    # (blob, target) pairs, which is also how the ORIGINAL TARGET is recovered —
    # re-adding against HEAD instead would move the note to a different commit
    # and orphan it from the work it annotates.
    listing = _git(["notes", f"--ref={ref_short}", "list"], project_path)
    if listing is None:
        return {"stamped": False, "reason": "git unavailable"}
    if listing.returncode != 0 or not listing.stdout.strip():
        return {"stamped": False, "reason": "not_present"}
    first = listing.stdout.strip().splitlines()[0].split()
    if len(first) != 2:
        return {"stamped": False, "reason": f"unexpected notes-list output: {listing.stdout.strip()[:80]}"}
    blob_sha, target_commit = first

    show = _git(["cat-file", "-p", blob_sha], project_path)
    if show is None or show.returncode != 0:
        # The note object exists but its blob is unreadable. Report it; do not
        # overwrite a payload we could not read.
        return {"stamped": False, "reason": "note payload unreadable — refusing to overwrite it"}

    try:
        payload = json.loads(show.stdout)
    except (ValueError, TypeError):
        return {"stamped": False, "reason": "note payload is not JSON — refusing to overwrite it"}

    payload.update({k: v for k, v in resolution.items() if v is not None})

    added = _git(
        ["notes", f"--ref={ref_short}", "add", "-f", "-F", "-", target_commit],
        project_path,
        input=json.dumps(payload, indent=2),
    )
    if added is None or added.returncode != 0:
        detail = (added.stderr.strip()[:120] if added else "git unavailable") or "unknown"
        return {"stamped": False, "reason": f"note rewrite failed: {detail}"}
    return {"stamped": True, "reason": "resolution stamped, note remains ACTIVE"}


#: Note kinds whose stores rewrote a note at HEAD before note_target, so a ref may hold two notes. Goals have their own repair
#: (goal_note_state._consolidate); findings, dead ends and mistakes are restamped on their own commit by stamp_resolution.
FORKABLE_KINDS = ("unknowns", "assumptions", "decisions", "sources")


def plan_note_forks(project_path: str | None) -> list[dict]:
    """Every unknown, assumption, decision or source note ref that holds more than one note: ``[{"ref", "notes"}]``."""
    out: list[dict] = []
    refs = _git(
        ["for-each-ref", "--format=%(refname)", *(f"{ACTIVE_PREFIX}/{k}/" for k in FORKABLE_KINDS)], project_path
    )
    for full in refs.stdout.split() if refs and refs.returncode == 0 else []:
        short = full.removeprefix("refs/notes/")
        listed = _git(["notes", f"--ref={short}", "list"], project_path)
        count = len([ln for ln in (listed.stdout.splitlines() if listed else []) if len(ln.split()) >= 2])
        if count > 1:
            out.append({"ref": short, "notes": count})
    return out


def _merged_note(ref: str, commits: list[str], project_path: str | None) -> dict | None:
    """The notes of one ref merged oldest commit first, a later non-null value winning: these artifacts' state only moves
    forward (resolved, verified, assessed, archived), so the newest write holds it, and earlier keys it dropped survive."""
    dated = []
    for commit in commits:
        # Ordered by how many commits precede it, then by commit time: two commits made in the same second tie on time, and
        # the old writer always wrote at the HEAD of the moment, so the deeper commit holds the later write.
        depth = _git(["rev-list", "--count", commit], project_path)
        when = _git(["show", "-s", "--format=%ct", commit], project_path)
        shown = _git(["notes", f"--ref={ref}", "show", commit], project_path)
        try:
            payload = json.loads(shown.stdout) if shown and shown.returncode == 0 else None
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            key = (
                int(depth.stdout.strip() or 0) if depth and depth.returncode == 0 else 0,
                int(when.stdout.strip() or 0) if when and when.returncode == 0 else 0,
            )
            dated.append((key, payload))
    if not dated:
        return None
    merged: dict = {}
    for _when, payload in sorted(dated, key=lambda d: d[0]):
        merged.update({k: v for k, v in payload.items() if v is not None or k not in merged})
    return merged


def apply_note_forks(project_path: str | None, plan: list[dict]) -> dict:
    """Merge each planned ref onto the commit readers look at first and remove its other notes. ``{"merged", "failed"}``.
    The removed notes stay in the notes ref's own history."""
    merged = failed = 0
    for item in plan:
        ref = item["ref"]
        listed = _git(["notes", f"--ref={ref}", "list"], project_path)
        commits = [ln.split()[1] for ln in (listed.stdout.splitlines() if listed else []) if len(ln.split()) >= 2]
        note = _merged_note(ref, commits, project_path) if len(commits) > 1 else None
        if note is None:
            failed += len(commits) > 1
            continue
        wrote = _git(
            ["notes", f"--ref={ref}", "add", "-f", "-F", "-", commits[0]],
            project_path,
            input=json.dumps(note, indent=2),
        )
        if not wrote or wrote.returncode != 0:
            failed += 1
            continue
        for commit in commits[1:]:
            _git(["notes", f"--ref={ref}", "remove", commit], project_path)
        merged += 1
    return {"merged": merged, "failed": failed}
