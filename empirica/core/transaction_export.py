"""Per-transaction history with structure only: ids, timestamps, numbers and enum-shaped tokens, never text.

`grounding-export --transactions` is built on this. The consumer is a dashboard or graph explorer that draws each transaction
as PREFLIGHT, CHECK(s), POSTFLIGHT vector states with self versus grounded values and the goals and artifacts linked to it
(cowork, 2026-10-04). The point of the contract is that the output can leave a practice without a content review, because the
free-text fields (reasoning, objectives, artifact titles, retrospectives) carry people's and clients' names.

How that is held: nothing is read from a text column. Reflex JSON is read through a whitelist of keys, and every string that
survives is checked against a narrow token pattern, so prose arriving under a whitelisted key is dropped, not passed through.
`dropped_unsafe_values` counts those, so a field that stopped being an enum is visible rather than silently missing.
"""

from __future__ import annotations

import calendar
import json
import re
import time
from typing import Any

SCHEMA = "empirica.transaction_export.v1"

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

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000

#: Identifiers and enum-shaped tokens: one word, no spaces, bounded. A sentence cannot match.
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")
_SHA = re.compile(r"[0-9a-f]{7,64}")
#: A git notes ref is a slash-separated path of ids (`empirica/session/<id>/postflight/<id>`): no spaces, so not prose.
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")

#: reflex_data keys that may be read, by phase, and the kind each must be.
_REFLEX_FIELDS: dict[str, dict[str, str]] = {
    "PREFLIGHT": {"git_commit_sha": "sha", "git_notes_ref": "ref"},
    "CHECK": {
        "decision": "token",
        "confidence": "number",
        "cycle": "number",
        "auto_checkpoint": "bool",
        "git_commit_sha": "sha",
        "git_notes_ref": "ref",
    },
    "POSTFLIGHT": {
        "work_type": "token",
        "internal_consistency": "token",
        "tool_call_count": "number",
        "postflight_confidence": "number",
        "auto_closed": "bool",
        "git_commit_sha": "sha",
        "git_notes_ref": "ref",
    },
}

#: (table, type name). Every one carries transaction_id; goal_id where the column exists.
_ARTIFACT_TABLES = (
    ("project_findings", "finding"),
    ("project_unknowns", "unknown"),
    ("project_dead_ends", "dead_end"),
    ("mistakes_made", "mistake"),
    ("assumptions", "assumption"),
    ("decisions", "decision"),
)

_CHUNK = 400


def parse_since(value: Any) -> float | None:
    """Epoch seconds from None, a number, `YYYY-MM-DD`, or `YYYY-MM-DDTHH:MM:SS[Z]` (UTC). ValueError otherwise."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return float(calendar.timegm(time.strptime(text, fmt)))
        except ValueError:
            continue
    raise ValueError(f"--since {value!r} is not an epoch, YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ")


class _Safe:
    """Value checks that count what they drop."""

    def __init__(self) -> None:
        self.dropped = 0

    def token(self, value: Any) -> str | None:
        if isinstance(value, str) and _TOKEN.fullmatch(value):
            return value
        if value not in (None, ""):
            self.dropped += 1
        return None

    def number(self, value: Any) -> float | int | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
            return round(value, 4) if isinstance(value, float) else value
        if value not in (None, ""):
            self.dropped += 1
        return None

    def kind(self, kind: str, value: Any) -> Any:
        if kind == "bool":
            return value if isinstance(value, bool) else None
        if kind == "number":
            return self.number(value)
        pattern = {"sha": _SHA, "ref": _REF}.get(kind)
        if pattern is None:
            return self.token(value)
        if isinstance(value, str) and pattern.fullmatch(value):
            return value
        if value not in (None, ""):
            self.dropped += 1
        return None


def _chunks(items: list[str]):
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _read(conn, unavailable: list[str], name: str, sql: str, params: list[str]) -> list:
    """Rows, or [] with `name` recorded as unavailable when the store predates the table or column."""
    import sqlite3

    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        if name not in unavailable:
            unavailable.append(name)
        return []


def _json(raw: Any) -> dict:
    try:
        value = json.loads(raw) if isinstance(raw, str) else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _reflex(row, safe: _Safe) -> dict:
    phase = row["phase"]
    out: dict[str, Any] = {"timestamp": safe.number(row["timestamp"])}
    out["vectors"] = {v: round(row[v], 4) for v in VECTORS if row[v] is not None and isinstance(row[v], (int, float))}
    data = _json(row["reflex_data"])
    for key, kind in _REFLEX_FIELDS.get(phase, {}).items():
        if key in data:
            value = safe.kind(kind, data[key])
            if value is not None and not (kind == "bool" and value is False):
                out[key] = value
    return out


def _grounded(row, safe: _Safe) -> dict:
    selfv, grounded, gaps = (
        _json(row["self_assessed_vectors"]),
        _json(row["grounded_vectors"]),
        _json(row["calibration_gaps"]),
    )
    vectors: dict[str, dict] = {}
    for vec in VECTORS:
        s = selfv.get(vec)
        g = grounded.get(vec)
        entry: dict[str, Any] = {}
        if safe.number(s) is not None:
            entry["self"] = safe.number(s)
        if isinstance(g, dict) and safe.number(g.get("value")) is not None:
            entry["grounded"] = safe.number(g["value"])
            for key in ("confidence", "evidence_count"):
                if safe.number(g.get(key)) is not None:
                    entry[key] = safe.number(g[key])
            if safe.token(g.get("source")) is not None:
                entry["source"] = g["source"]
        if "self" in entry and "grounded" in entry:
            gap = gaps.get(vec) if safe.number(gaps.get(vec)) is not None else entry["self"] - entry["grounded"]
            entry["gap"] = round(float(gap), 4)
        if entry:
            vectors[vec] = entry
    return {
        "phase": safe.token(row["phase"]),
        "created_at": safe.number(row["created_at"]),
        "grounded_coverage": safe.number(row["grounded_coverage"]),
        "overall_calibration_score": safe.number(row["overall_calibration_score"]),
        "evidence_count": safe.number(row["evidence_count"]),
        "practitioner_model": safe.token(row["practitioner_model"]),
        "compliance_status": safe.token(row["compliance_status"]),
        "vectors": vectors,
    }


def export_transactions(conn, ai_id: str, since: float | None = None, limit: int | None = None) -> dict:
    """Transactions of `ai_id` newest first, structure only. `conn` is a sqlite connection with row access by name."""
    import sqlite3

    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return _export(conn, ai_id, since, DEFAULT_LIMIT if limit is None else max(0, min(int(limit), MAX_LIMIT)))
    finally:
        conn.row_factory = previous_factory


def _export(conn, ai_id: str, since: float | None, limit: int) -> dict:
    safe = _Safe()
    unavailable: list[str] = []
    lower = since if since is not None else 0.0

    starts = conn.execute(
        """
        SELECT r.transaction_id AS tx,
               COALESCE(MIN(CASE WHEN r.phase = 'PREFLIGHT' THEN r.timestamp END), MIN(r.timestamp)) AS start
        FROM reflexes r JOIN sessions s ON s.session_id = r.session_id
        WHERE s.ai_id = ? AND r.transaction_id IS NOT NULL
        GROUP BY r.transaction_id
        HAVING start >= ?
        ORDER BY start DESC
        """,
        (ai_id, lower),
    ).fetchall()
    skipped = conn.execute(
        "SELECT COUNT(*) FROM reflexes r JOIN sessions s ON s.session_id = r.session_id "
        "WHERE s.ai_id = ? AND r.transaction_id IS NULL AND r.timestamp >= ?",
        (ai_id, lower),
    ).fetchone()[0]

    chosen = [row["tx"] for row in starts[:limit]]
    records: dict[str, dict] = {
        tx: {
            "transaction_id": safe.token(tx),
            "session_id": None,
            "ai_id": safe.token(ai_id),
            "preflight": None,
            "checks": [],
            "postflight": None,
            "grounded": [],
            "goals": [],
            "artifacts": [],
        }
        for tx in chosen
    }

    for part in _chunks(chosen):
        for row in conn.execute(
            f"SELECT * FROM reflexes WHERE transaction_id IN ({_marks(len(part))}) ORDER BY timestamp, id", part
        ):
            rec = records[row["transaction_id"]]
            rec["session_id"] = rec["session_id"] or safe.token(row["session_id"])
            phase = row["phase"]
            if phase == "PREFLIGHT" and rec["preflight"] is None:
                rec["preflight"] = _reflex(row, safe)
            elif phase == "CHECK":
                rec["checks"].append(_reflex(row, safe))
            elif phase == "POSTFLIGHT":
                rec["postflight"] = _reflex(row, safe)  # the last one wins, as the calibration pass reads it

        rows = _read(
            conn,
            unavailable,
            "grounded_verifications",
            f"SELECT * FROM grounded_verifications WHERE transaction_id IN ({_marks(len(part))}) ORDER BY created_at",
            part,
        )
        for row in rows:
            records[row["transaction_id"]]["grounded"].append(_grounded(row, safe))

        rows = _read(
            conn,
            unavailable,
            "goals",
            f"SELECT id, status, created_timestamp, completed_timestamp, transaction_id FROM goals "
            f"WHERE transaction_id IN ({_marks(len(part))}) ORDER BY created_timestamp",
            part,
        )
        for row in rows:
            records[row["transaction_id"]]["goals"].append(
                {
                    "id": safe.token(row["id"]),
                    "status": safe.token(row["status"]),
                    "created_timestamp": safe.number(row["created_timestamp"]),
                    "completed_timestamp": safe.number(row["completed_timestamp"]),
                }
            )

        for table, kind in _ARTIFACT_TABLES:
            rows = _read(
                conn,
                unavailable,
                table,
                f"SELECT id, goal_id, transaction_id FROM {table} WHERE transaction_id IN ({_marks(len(part))}) ORDER BY rowid",
                part,
            )
            for row in rows:
                item: dict[str, Any] = {"id": safe.token(row["id"]), "type": kind}
                goal_id = safe.token(row["goal_id"])
                if goal_id is not None:
                    item["goal_id"] = goal_id
                records[row["transaction_id"]]["artifacts"].append(item)

    transactions = [records[tx] for tx in chosen]
    return {
        "ok": True,
        "schema": SCHEMA,
        "ai_id": safe.token(ai_id),
        "since": since,
        "limit": limit,
        "returned": len(transactions),
        "total_matching": len(starts),
        "truncated": len(starts) > len(transactions),
        "skipped_without_transaction_id": skipped,
        "dropped_unsafe_values": safe.dropped,
        "unavailable": unavailable,
        "transactions": transactions,
    }
