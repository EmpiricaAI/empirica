"""Restore `reflexes` rows from the session-phase git notes, idempotently.

A store that lost its reflex rows (a reset database, a fresh checkout, a path that resolved somewhere new) still has the
notes: `refs/notes/empirica/session/<session_id>/<PHASE>/<round>`, one per PREFLIGHT, CHECK and POSTFLIGHT, each carrying the
vectors, phase, round, session_id, an ISO timestamp and `meta` (transaction_id, reasoning and the phase's own fields).
`rebuild --from-notes` restores artifacts and stub sessions but no reflex rows, and `store_vectors` is unusable as an
importer: it stamps the current time and fills every missing vector with 0.5, which would invent measurements (a CHECK note
often carries 7 of the 13).

This importer keeps what the note says and nothing else: the original timestamp, only the vectors present (the rest stay
NULL), the transaction id, the reasoning, and the meta fields in `reflex_data` as the original writer shaped it.

Identity is (session_id, phase, round), which is exactly what the ref name encodes, so the decision to skip a row never needs
the note's content: a ref whose identity already has a row is left alone. One consequence is stated rather than hidden: the
writer reuses `<PHASE>/<round>` across transactions, so a ref can hold several notes, and when its identity is already
present the other notes under it are not looked at. For a ref that is NOT present, every note under it is restored. A session with no `sessions` row is skipped and
named, because a reflex without its session breaks every join that reads it. The default is a preview; nothing is written
without `apply`.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from typing import Any

PREFIX = "refs/notes/empirica/session/"
VECTORS = (
    "engagement",
    "know",
    "do",
    "context",
    "clarity",
    "coherence",
    "signal",
    "density",
    "state",
    "change",
    "completion",
    "impact",
    "uncertainty",
)
_PHASES = ("PREFLIGHT", "CHECK", "POSTFLIGHT")
_LISTED_SESSIONS_CAP = 20


def _git(repo: str, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, input=input_text, timeout=60, check=False
    )


def _parse_ref(refname: str) -> tuple[str, str, int] | None:
    """(session_id, phase, round) from `refs/notes/empirica/session/<sid>/<PHASE>/<round>`, or None."""
    if not refname.startswith(PREFIX):
        return None
    parts = refname[len(PREFIX) :].split("/")
    if len(parts) != 3 or parts[1] not in _PHASES or not parts[2].isdigit() or not parts[0]:
        return None
    return parts[0], parts[1], int(parts[2])


def _epoch(stamp: Any) -> float | None:
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        return float(stamp)
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _read_notes(repo: str, refname: str) -> list[dict | None]:
    """Every note body on `refname`, in listing order; None for one that cannot be read or is not an object.

    A ref can hold several notes, one per annotated commit: the writer reuses the name `<PHASE>/<round>` across
    transactions (20 of 1500 sampled refs in core's store hold more than one, for different transactions), so reading only
    the first would restore one transaction and silently lose the rest.
    """
    listed = _git(repo, "notes", f"--ref={refname[len('refs/notes/') :]}", "list")
    lines = [ln.split() for ln in listed.stdout.splitlines() if ln.strip()]
    if listed.returncode != 0 or not lines:
        return []
    bodies: list[dict | None] = []
    for parts in lines:
        blob = _git(repo, "cat-file", "blob", parts[0])
        if blob.returncode != 0:
            bodies.append(None)
            continue
        try:
            body = json.loads(blob.stdout)
        except ValueError:
            bodies.append(None)
            continue
        bodies.append(body if isinstance(body, dict) else None)
    return bodies


def _row(note: dict, ident: tuple[str, str, int], project_id: str | None) -> dict | None:
    """The row to insert, `{"skip": reason}` for a note that is deliberately not restored, or None if unreadable."""
    session_id, phase, rnd = ident
    if note.get("session_id") != session_id or note.get("phase") != phase or note.get("round") != rnd:
        return None  # the note disagrees with its own ref name: not ours to guess
    ts = _epoch(note.get("timestamp"))
    vectors = note.get("vectors")
    if ts is None or not isinstance(vectors, dict):
        return None
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    present = {
        k: float(v)
        for k, v in vectors.items()
        if k in VECTORS and isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    tx = meta.get("transaction_id") if isinstance(meta.get("transaction_id"), str) else None
    if not present:
        return {"skip": "no_vectors"}  # nothing to restore: a row of NULLs would be noise, not a measurement
    if meta.get("auto_checkpoint") is True and all(v == 0.5 for v in present.values()):
        return {"skip": "phantom"}  # the old auto-checkpoint's all-0.5 CHECK; delete-artifacts purged these on purpose
    # reflex_data as store_vectors shapes it: identity fields, then the writer's own metadata merged in.
    reflex_data: dict[str, Any] = {
        "session_id": session_id,
        "phase": phase,
        "round": rnd,
        "vectors": present,
        "timestamp": ts,
        "project_id": project_id,
        "transaction_id": tx,
    }
    reflex_data.update({k: v for k, v in meta.items() if k not in reflex_data})
    reasoning = meta.get("reasoning") if isinstance(meta.get("reasoning"), str) else None
    return {
        "session_id": session_id,
        "phase": phase,
        "round": rnd,
        "timestamp": ts,
        "vectors": present,
        "reflex_data": json.dumps(reflex_data, default=str),
        "reasoning": reasoning,
        "project_id": project_id,
        "transaction_id": tx,
    }


def _collect_rows(repo: str, candidates: list, sessions: dict) -> tuple[list[dict], dict]:
    """The rows to insert from the candidate refs, and the counts of what was left out and why."""
    rows: list[dict] = []
    counts = {"unreadable": 0, "multi_note_refs": 0, "no_vectors": 0, "phantom": 0}
    seen: set[tuple[str, str, int, str | None]] = set()
    for refname, ident in candidates:
        notes = _read_notes(repo, refname)
        if not notes:
            counts["unreadable"] += 1
            continue
        if len(notes) > 1:
            counts["multi_note_refs"] += 1
        for note in notes:
            row = _row(note, ident, sessions[ident[0]]) if note is not None else None
            if row is None:
                counts["unreadable"] += 1
            elif "skip" in row:
                counts[row["skip"]] += 1
            else:
                key = (*ident, row["transaction_id"])
                if key not in seen:  # the same transaction twice under one ref is one event
                    seen.add(key)
                    rows.append(row)
    return rows, counts


def import_reflexes(conn, repo: str, apply: bool = False) -> dict:
    """Preview or apply the restore of reflex rows from `repo`'s session-phase notes into `conn`'s `reflexes`."""
    listed = _git(repo, "for-each-ref", "--format=%(refname)", PREFIX)
    if listed.returncode != 0:
        return {"ok": False, "error": f"git for-each-ref failed in {repo}: {listed.stderr.strip()[:200]}"}

    idents: list[tuple[str, tuple[str, str, int]]] = []
    unparsed = 0
    for refname in listed.stdout.splitlines():
        parsed = _parse_ref(refname.strip())
        if parsed is None:
            unparsed += 1
        else:
            idents.append((refname.strip(), parsed))

    existing = {(r[0], r[1], r[2]) for r in conn.execute("SELECT session_id, phase, round FROM reflexes")}
    sessions = {r[0]: r[1] for r in conn.execute("SELECT session_id, project_id FROM sessions")}

    already = 0
    no_session: dict[str, int] = {}
    candidates: list[tuple[str, tuple[str, str, int]]] = []
    for refname, ident in idents:
        if ident in existing:
            already += 1
        elif ident[0] not in sessions:
            no_session[ident[0]] = no_session.get(ident[0], 0) + 1
        else:
            candidates.append((refname, ident))

    rows, counts = _collect_rows(repo, candidates, sessions)

    by_phase: dict[str, int] = {}
    for row in rows:
        by_phase[row["phase"]] = by_phase.get(row["phase"], 0) + 1

    imported = 0
    if apply and rows:
        cols = [
            "session_id",
            "phase",
            "round",
            "timestamp",
            *VECTORS,
            "reflex_data",
            "reasoning",
            "project_id",
            "transaction_id",
        ]
        sql = f"INSERT INTO reflexes ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        with conn:  # one transaction: all of it or none of it
            for row in rows:
                conn.execute(
                    sql,
                    [row["session_id"], row["phase"], row["round"], row["timestamp"]]
                    + [row["vectors"].get(v) for v in VECTORS]
                    + [row["reflex_data"], row["reasoning"], row["project_id"], row["transaction_id"]],
                )
                imported += 1

    return {
        "ok": True,
        "applied": bool(apply),
        "refs_found": len(idents),
        "unparsed_refs": unparsed,
        "already_present": already,
        "importable": len(rows),
        "imported": imported,
        "by_phase": by_phase,
        "skipped_no_session": sum(no_session.values()),
        "sessions_missing": sorted(no_session)[:_LISTED_SESSIONS_CAP],
        "unreadable_notes": counts["unreadable"],
        "multi_note_refs": counts["multi_note_refs"],
        "skipped_no_vectors": counts["no_vectors"],
        "skipped_phantom": counts["phantom"],
    }
