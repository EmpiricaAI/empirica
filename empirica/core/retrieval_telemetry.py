"""Stamp WHEN an artifact was surfaced — from EVERY path that surfaces one.

Migration 063 added ``last_retrieved_at`` + ``retrieval_count`` to
``project_findings`` so relevance would have a signal that is not age. It then
acquired exactly ONE writer: ``bootstrap.circles``, on the narrowest query in
the system — findings inside an active goal, within a 7-day window. Every other
surfacing path wrote nothing:

    project-bootstrap circle 1 ......... stamped
    PREFLIGHT/CHECK context injection .. NOT stamped  (the highest-volume path)
    project-search ..................... NOT stamped
    investigate ........................ NOT stamped

So the column read ``0`` for artifacts that had in fact been surfaced dozens of
times, and ``0`` is also what "genuinely never surfaced" looks like. **The two
states a reader must tell apart rendered identically, and the broken one looked
healthy** — which is why it survived a month and was found by a peer counting
from the outside (empirica-paper, prop_qzzhmmfyejarlgoo2il2h46cc4) rather than
by anything here.

This module is the single home for the write, so "which paths stamp?" has one
answer that can be read off the call sites instead of being reconstructed.

**It returns a COUNT, deliberately.** A stamping function that returns ``None``
makes "updated 12 rows" and "matched nothing at all" indistinguishable to its
caller and to its tests — the same defect one level up. Two real ways this
silently matches nothing, both live in this codebase:

* the mistake id is ``mistake_<uuid>`` in the Qdrant payload and a bare
  ``<uuid>`` in ``mistakes_made.id`` (see ``_ID_PREFIXES``);
* the table is ``mistakes_made``, not ``mistakes``, and ``project_decisions``
  does not exist at all.

Either one produces a clean, exception-free, zero-row UPDATE. The count is what
makes that a test failure rather than a green suite.

Kept to the standard library on purpose: this is imported on the PREFLIGHT hot
path, where a module-scope import of the Qdrant/numpy stack has already cost a
CI regression once.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

#: Artifact ``type`` (as it appears in a Qdrant memory payload, and as the
#: artifact-log verbs name it) -> the SQLite table that actually holds the row.
#:
#: ``mistakes_made`` and ``decisions`` are the two that do not follow from the
#: type name. A query against the plausible-looking ``mistakes`` or
#: ``project_decisions`` returns zero rows forever and reports success.
_TABLE_BY_TYPE: dict[str, str] = {
    "finding": "project_findings",
    "unknown": "project_unknowns",
    "dead_end": "project_dead_ends",
    "deadend": "project_dead_ends",
    "mistake": "mistakes_made",
    "decision": "decisions",
    "assumption": "assumptions",
}

#: Types whose Qdrant point id is NAMESPACED but whose SQLite id is bare.
#: Stripping is not cosmetic — without it the UPDATE matches nothing, quietly.
_ID_PREFIXES: dict[str, str] = {
    "mistake": "mistake_",
}

#: Types deliberately NOT stamped. ``lesson`` and ``episodic`` are surfaced from
#: stores with their own identity scheme and no retrieval columns; listing them
#: here means an unknown type is a real signal rather than one of these.
_NOT_STAMPED = frozenset({"lesson", "episodic", "eidetic", "doc", "goal"})


def normalize_artifact_id(artifact_id: str, artifact_type: str) -> str:
    """Strip a namespacing prefix so the id matches the SQLite primary key."""
    prefix = _ID_PREFIXES.get(artifact_type)
    if prefix and artifact_id.startswith(prefix):
        return artifact_id[len(prefix) :]
    return artifact_id


def _default_db_path() -> Path | None:
    try:
        from empirica.data.session_database import _resolve_canonical_project_root

        root = _resolve_canonical_project_root()
        if not root:
            return None
        path = Path(root) / ".empirica" / "sessions" / "sessions.db"
        return path if path.is_file() else None
    except Exception as e:
        logger.debug(f"retrieval telemetry: could not resolve db path: {e}")
        return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def collect_ids(items: Iterable[dict], default_type: str | None = None) -> list[tuple[str, str]]:
    """Pull ``(artifact_id, type)`` pairs out of a retrieval result list.

    Tolerates the two payload shapes in circulation — ``artifact_id`` (Qdrant
    memory points) and ``id`` — and skips anything with neither rather than
    guessing, because a stamp written against a guessed id is worse than no
    stamp: it is wrong data that looks like measurement.
    """
    out: list[tuple[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        aid = item.get("artifact_id") or item.get("id")
        atype = item.get("type") or default_type
        if not aid or not atype:
            continue
        out.append((str(aid), str(atype)))
    return out


def _group_stampable(pairs: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    """Bucket ``(id, type)`` pairs by type, dropping what has nowhere to go.

    Normalizes the id here rather than at the UPDATE, so there is exactly one
    place where a namespaced Qdrant id becomes a SQLite primary key.
    """
    by_type: dict[str, list[str]] = {}
    for aid, atype in pairs:
        if not aid or not atype:
            continue
        atype = atype.lower()
        if atype in _NOT_STAMPED:
            continue
        if atype not in _TABLE_BY_TYPE:
            logger.debug(f"retrieval telemetry: no table for artifact type {atype!r}")
            continue
        by_type.setdefault(atype, []).append(normalize_artifact_id(str(aid), atype))
    return by_type


def stamp_retrieval(
    pairs: Iterable[tuple[str, str]],
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
    commit: bool | None = None,
) -> int:
    """Record that these artifacts were actually SURFACED into context.

    Returns the number of rows updated. ``0`` from a non-empty input is a real
    signal — it means the ids did not match any row — and callers under test
    should assert on it rather than on "did not raise".

    Best-effort by design: a bookkeeping write must never break a read path, so
    every failure is swallowed. It is swallowed LOUDLY at debug and, for the one
    case that indicates a genuine wiring bug (rows requested, none matched), at
    ``warning`` — a persistently silent stamp is precisely the failure that let
    this sit broken for a month.

    ``commit`` is explicit rather than inferred, and that is the whole lesson of
    the predecessor. ``bootstrap.circles`` handed its private version a cursor
    borrowed from a READ path — a path that closes without committing, because
    reads have nothing to commit. The UPDATE ran, reported rows, and was
    discarded on close; measured across a real bootstrap, stamped rows were 0
    before and 0 after. Nothing raised.

    So: when this function opens its own connection it commits (there is no
    other owner). When a caller passes ``conn``, it must SAY whether to commit,
    because "borrowed from a read path" and "borrowed from a write path" are
    indistinguishable from in here, and defaulting either way silently does the
    wrong thing for the other.
    """
    by_type = _group_stampable(pairs)
    if not by_type:
        return 0

    owns_conn = conn is None
    if owns_conn:
        path = Path(db_path) if db_path else _default_db_path()
        if not path:
            return 0
        try:
            conn = sqlite3.connect(str(path), timeout=2.0)
        except sqlite3.Error as e:
            logger.debug(f"retrieval telemetry: connect failed: {e}")
            return 0

    total = 0
    requested = 0
    try:
        now = time.time()
        for atype, ids in by_type.items():
            table = _TABLE_BY_TYPE[atype]
            cols = _columns(conn, table)
            # Pre-migration DB, or a table that never got the columns. Genuinely
            # nothing to do — distinct from "matched no rows", so it must not
            # count toward `requested` or the warning below fires on every call
            # against an old database.
            if "retrieval_count" not in cols or "last_retrieved_at" not in cols:
                logger.debug(f"retrieval telemetry: {table} has no retrieval columns; skipped")
                continue
            requested += len(ids)
            placeholders = ",".join("?" * len(ids))
            cur = conn.execute(
                f"UPDATE {table} SET last_retrieved_at = ?, "
                f"retrieval_count = COALESCE(retrieval_count, 0) + 1 "
                f"WHERE id IN ({placeholders})",
                (now, *ids),
            )
            total += cur.rowcount or 0
        if commit or (commit is None and owns_conn):
            conn.commit()
    except sqlite3.Error as e:
        logger.debug(f"retrieval telemetry: stamp skipped: {e}")
        return 0
    finally:
        if owns_conn:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    if requested and not total:
        # Every id was well-formed, the columns exist, and nothing matched. That
        # is a wiring bug (wrong table, unstripped prefix, foreign project), not
        # a quiet no-op — say so where an operator can see it.
        logger.warning(
            f"retrieval telemetry: {requested} artifact(s) surfaced but 0 rows matched "
            f"— ids may be namespaced or from another project; counters will read as never-retrieved"
        )
    return total


def stamp_items(items: Iterable[dict], default_type: str | None = None, **kwargs) -> int:
    """``collect_ids`` + ``stamp_retrieval`` — the shape every call site wants."""
    return stamp_retrieval(collect_ids(items, default_type), **kwargs)
