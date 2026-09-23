"""Falsifiers: the observation that would refute a belief, registered before the evidence.

Autonomy's FALSIFIER_SPEC (source f486a5dc), Phase 1, approved by David on
2026-09-22. The failure it exists for is a TRUE measurement asserted past the
population it was taken over: such a claim adjudicates `held`, so no confidence
gate sees it, and the refuting case usually arrives after the transaction that
made the belief has closed.

So a falsifier is a node, not a field. It is registered at PREFLIGHT or CHECK,
beside `claims`, and names its parent belief. It stays `registered`, surfaced at
every later PREFLIGHT, until a POSTFLIGHT adjudicates it:

- ``tripped``  the named observation happened. Record what fired it.
- ``survived`` someone looked at the population and it did not happen. This
  needs evidence of the looking.
- ``expired``  nobody looked, or the belief no longer matters. The honest
  verdict for silence. A `survived` with no evidence is recorded as `expired`
  and the response says so, because "nobody looked" read as "it held" is false
  confirmation (spec section 6; `untested` does the same job for claims).

Deliberately NOT here: mesh matching (spec section 8, deferred, no owner), an
in-window hook interrupt (section 9 Phase 3, deferred on evidence), and section
7's pricing of promotion. Every existing artifact has no falsifier, so gating
promotion on one would drop the whole graph out of the steering band on day one.
That needs its own ruling.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

STATES = ("registered", "tripped", "survived", "expired")
ADJUDICATED = ("tripped", "survived", "expired")

#: A falsifier tests something that could be OBSERVED false. Per type, the table
#: and the column its visibility lives in (None where the table has no such column).
#:
#: `dead_end` is here for the reason it is the best case in the set: "this approach
#: does not work" is a claim true over one version, one config, one input, and then
#: asserted as a permanent constraint on the option space. Dead-ends are never
#: resolved by policy, because they are meant to resurface, so a falsifier is the
#: only thing that can ever retire a stale one — as a flag on `is_invalidated` for a
#: human, never automatically (spec section 8).
#:
#: `mistake` is here for its `prevention`, which is the live half: the mistake itself
#: is history and cannot be refuted, but "this prevention stops recurrence" is
#: refuted by the same mistake being logged again after it was in place.
#:
#: `unknown` is deliberately absent. An unknown asserts nothing, so nothing can make
#: it false; it gets answered. An unknown whose QUESTION presupposes something untrue
#: is two artifacts: log the presupposition as an assumption and falsify that. David
#: ruled this widening on 2026-09-23.
PARENT_TABLES = {
    "finding": ("project_findings", "visibility"),
    "assumption": ("assumptions", "visibility"),
    "decision": ("decisions", "visibility"),
    "dead_end": ("project_dead_ends", "visibility"),
    "mistake": ("mistakes_made", "visibility"),
    "lesson": ("lessons", "sharing_policy"),
}

#: How many open falsifiers PREFLIGHT prints. The total is always reported beside
#: the page, so a capped list reads as capped.
SURFACE_LIMIT = 10


class FalsifierRefused(ValueError):
    """A falsifier that cannot be written: no parent, an unknown parent, no statement."""


def _resolve_parent(conn, ref: Any) -> tuple[str, str, str | None]:
    """(parent_type, full parent id, parent visibility) for what the practitioner typed.

    Exact id, or an 8+ character prefix naming exactly one belief. Anything else
    is refused: a falsifier attached to nothing tests nothing.
    """
    text = str(ref or "").strip()
    if not text:
        raise FalsifierRefused("a falsifier needs `falsifies`: the id of the " + ", ".join(PARENT_TABLES) + " it tests")
    for ptype, (table, viscol) in PARENT_TABLES.items():
        try:
            rows = conn.execute(f"SELECT id, {viscol} FROM {table} WHERE id = ?", (text,)).fetchall()
        except Exception as exc:
            logger.debug("falsifier parent lookup skipped for %s: %s", table, exc)
            continue
        if rows:
            return ptype, rows[0][0], rows[0][1]
    if len(text) < 8:
        raise FalsifierRefused(f"falsifies {text!r} is too short to identify an artifact (8+ characters)")
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    hits: list[tuple[str, str, str | None]] = []
    for ptype, (table, viscol) in PARENT_TABLES.items():
        try:
            rows = conn.execute(
                f"SELECT id, {viscol} FROM {table} WHERE id LIKE ? ESCAPE '\\' LIMIT 3", (escaped + "%",)
            ).fetchall()
        except Exception as exc:
            logger.debug("falsifier parent lookup skipped for %s: %s", table, exc)
            continue
        hits.extend((ptype, r[0], r[1]) for r in rows)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise FalsifierRefused(
            f"falsifies {text!r} matches no {', '.join(PARENT_TABLES)}; a falsifier must test an artifact that "
            "asserts something. An unknown asserts nothing — log its presupposition as an assumption and test that."
        )
    raise FalsifierRefused(f"falsifies {text!r} matches more than one artifact; give the full id")


def _project_of(conn, session_id: str | None) -> str | None:
    if not session_id:
        return None
    try:
        row = conn.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    except Exception:
        return None
    return row[0] if row else None


def register(
    db,
    *,
    session_id: str | None,
    transaction_id: str | None,
    phase: str,
    items: list[Any],
) -> dict[str, Any]:
    """Write each falsifier that names a real belief; refuse the rest, by name.

    Returns ``{registered: [...], refused: [...]}``. A refused item is not
    written. The transaction it arrived with still opens: a malformed optional
    record must not block the measurement window, but the refusal is reported
    beside the registered ones, never folded into them.
    """
    conn = db.conn
    project_id = _project_of(conn, session_id)
    registered: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            refused.append({"item": item, "reason": "each falsifier is an object with statement and falsifies"})
            continue
        statement = str(item.get("statement") or "").strip()
        query = str(item.get("query") or "").strip() or None
        try:
            if not statement:
                raise FalsifierRefused("a falsifier needs `statement`: the observation that would refute the belief")
            ptype, pid, pvis = _resolve_parent(conn, item.get("falsifies") or item.get("parent"))
        except FalsifierRefused as exc:
            refused.append({"statement": statement or None, "falsifies": item.get("falsifies"), "reason": str(exc)})
            continue
        fid = str(uuid.uuid4())
        # The spec's invariant: a falsifier is at least as visible as its belief,
        # or a shared claim ships without its test. Inheriting satisfies it. A
        # lesson's column is `sharing_policy`, whose vocabulary is its own, so
        # anything outside the visibility vocabulary falls to the closed value
        # rather than being written through as if it meant the same thing.
        visibility = pvis if pvis in ("local", "shared", "public") else "local"
        conn.execute(
            "INSERT INTO falsifiers (id, project_id, session_id, transaction_id, registered_phase, parent_type,"
            " parent_id, statement, query, state, visibility, registered_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,'registered',?,?)",
            (fid, project_id, session_id, transaction_id, phase, ptype, pid, statement, query, visibility, time.time()),
        )
        registered.append(
            {"id": fid, "falsifies": f"{ptype}:{pid}", "statement": statement, "executable": query is not None}
        )
    conn.commit()
    out: dict[str, Any] = {"registered": registered, "refused": refused}
    if registered and not any(r["executable"] for r in registered):
        out["note"] = (
            "No falsifier carries a `query`. An executable form can be re-run by someone who does not know "
            "the belief, which is the property that made execution the only working correction."
        )
    return out


def open_falsifiers(db, project_id: str | None, limit: int = SURFACE_LIMIT) -> dict[str, Any] | None:
    """The page of open falsifiers PREFLIGHT prints, with the total beside it."""
    if not project_id:
        return None
    conn = db.conn
    try:
        total = conn.execute(
            "SELECT count(*) FROM falsifiers WHERE project_id = ? AND state = 'registered'", (project_id,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id, parent_type, parent_id, statement, query, registered_at FROM falsifiers"
            " WHERE project_id = ? AND state = 'registered' ORDER BY registered_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    except Exception as exc:
        logger.debug("open falsifiers unavailable: %s", exc)
        return None
    if not total:
        return None
    now = time.time()
    return {
        "open_total": total,
        "shown": len(rows),
        "truncated": total > len(rows),
        "items": [
            {
                "id": r[0],
                "falsifies": f"{r[1]}:{r[2]}",
                "statement": r[3],
                "query": r[4],
                "age_days": round((now - r[5]) / 86400, 1),
            }
            for r in rows
        ],
        "instruction": (
            "Each is the observation you said would refute a belief you acted on. If this work touches "
            "that population, look. Adjudicate at POSTFLIGHT under `falsifiers`: "
            "[{id, state: tripped|survived|expired, evidence, tripped_by}]."
        ),
    }


def _one_open(conn, ref: str) -> str:
    text = str(ref or "").strip()
    if not text:
        raise FalsifierRefused("no falsifier id given")
    row = conn.execute("SELECT id, state FROM falsifiers WHERE id = ?", (text,)).fetchone()
    if row is None:
        if len(text) < 8:
            raise FalsifierRefused(f"{text!r} is too short to identify a falsifier (8+ characters)")
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = conn.execute(
            "SELECT id, state FROM falsifiers WHERE id LIKE ? ESCAPE '\\' LIMIT 3", (escaped + "%",)
        ).fetchall()
        if not rows:
            raise FalsifierRefused(f"no falsifier matches {text!r}")
        if len(rows) > 1:
            raise FalsifierRefused(f"{text!r} matches more than one falsifier; give the full id")
        row = rows[0]
    if row[1] != "registered":
        raise FalsifierRefused(f"falsifier {row[0][:8]} is already {row[1]}")
    return row[0]


def adjudicate(db, *, transaction_id: str | None, items: list[Any]) -> dict[str, Any] | None:
    """Apply POSTFLIGHT verdicts to open falsifiers, from this transaction or any earlier one."""
    if not items:
        return None
    conn = db.conn
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    downgraded: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            rejected.append({"item": item, "reason": "each verdict is an object with id and state"})
            continue
        state = str(item.get("state") or item.get("verdict") or "").strip().lower()
        evidence = str(item.get("evidence") or "").strip() or None
        tripped_by = str(item.get("tripped_by") or "").strip() or None
        try:
            if state not in ADJUDICATED:
                raise FalsifierRefused(f"state must be one of {', '.join(ADJUDICATED)}, got {state!r}")
            fid = _one_open(conn, item.get("id"))
        except FalsifierRefused as exc:
            rejected.append({"id": item.get("id"), "reason": str(exc)})
            continue
        if state == "survived" and not evidence:
            # Silence is not survival (spec section 6).
            state = "expired"
            downgraded.append(fid)
        if state == "tripped" and not (tripped_by or evidence):
            rejected.append({"id": fid, "reason": "tripped needs tripped_by or evidence: what fired it"})
            continue
        conn.execute(
            "UPDATE falsifiers SET state = ?, evidence = ?, tripped_by = ?, adjudicated_at = ?,"
            " adjudicated_transaction_id = ? WHERE id = ?",
            (state, evidence, tripped_by, time.time(), transaction_id, fid),
        )
        applied.append({"id": fid, "state": state})
    conn.commit()
    out: dict[str, Any] = {"adjudicated": applied}
    if rejected:
        out["rejected"] = rejected
    if downgraded:
        out["survived_without_evidence"] = (
            f"{len(downgraded)} verdict(s) said survived with no evidence and were recorded as EXPIRED. "
            "Survived means the population was observed and the refutation did not appear; say what you looked at."
        )
    return out


def counts(db, project_id: str | None = None) -> dict[str, int]:
    """Falsifiers by state, for the spec's own check: tripped against registered after 30 days."""
    sql = "SELECT state, count(*) FROM falsifiers"
    params: tuple = ()
    if project_id:
        sql += " WHERE project_id = ?"
        params = (project_id,)
    try:
        found = dict(db.conn.execute(sql + " GROUP BY state", params).fetchall())
    except Exception as exc:
        logger.debug("falsifier counts unavailable: %s", exc)
        return {}
    return {s: int(found.get(s, 0)) for s in STATES}
