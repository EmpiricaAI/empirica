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

Layout, parallel namespaces so the journey survives remotely too:

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
