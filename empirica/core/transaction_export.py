"""Per-transaction history with structure only: ids, timestamps, numbers and enum-shaped tokens, never text.

`grounding-export --transactions` is built on this. The consumer is a dashboard or graph explorer that draws each transaction
as PREFLIGHT, CHECK(s), POSTFLIGHT vector states with self versus grounded values and the goals and artifacts linked to it
(cowork, 2026-10-04). The point of the contract is that the output can leave a practice without a content review, because the
free-text fields (reasoning, objectives, artifact titles, retrospectives) carry people's and clients' names.

How that is held: nothing is read from a text column. Reflex JSON is read through a whitelist of keys, and every string that
survives is checked against what that field can legitimately be: identifiers must be UUID- or hex-shaped, enum fields must
be a member of a closed vocabulary, a notes ref must have the exact shape the writer produces, numbers must be finite and
bounded. A single word is not enough (a name, a client, a lowercased slug all fit a token pattern), so the pattern alone is
not the contract. Anything else is dropped, never passed through, and `dropped_unsafe_values` counts it, so a field that
stopped being an enum, or a vocabulary that grew, is visible rather than silently missing. The one field that is only
shape-checked is `practitioner_model` (a model id from a known family prefix, lowercase, no spaces).
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

#: Free-form tokens are used only for the practice name echoed back. Everything else below is stricter.
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX_ID = re.compile(r"[0-9a-f]{8,64}")
_SHA = re.compile(r"[0-9a-f]{7,64}")
#: The one notes ref a session-phase checkpoint is written under: no free text can be a segment of it.
_REF = re.compile(r"empirica/session/[0-9a-f-]{36}/(PREFLIGHT|CHECK|POSTFLIGHT)/[0-9]{1,6}")
_MODEL = re.compile(r"(claude|gpt|codex|gemini|o[0-9]|qwen|llama|mistral|deepseek|grok)[a-z0-9.-]{0,60}")
_NUMBER_LIMIT = 1e12  # epoch seconds are ~1.8e9; this only stops absurd values


def _compliance_values() -> frozenset[str]:
    try:
        from empirica.core.post_test.compliance_status import ComplianceStatus

        return frozenset(m.value for m in ComplianceStatus)
    except Exception:  # an old install: refuse every value rather than guess
        return frozenset()


#: Closed vocabularies. A value outside its set is dropped and counted, which is the visible failure: when a vocabulary
#: grows the export says so, instead of passing whatever the new value is.
_VOCAB: dict[str, frozenset[str]] = {
    "goal_status": frozenset({"planned", "in_progress", "completed", "abandoned", "blocked", "paused", "archived"}),
    "grounded_phase": frozenset({"combined", "noetic", "praxic"}),
    "decision": frozenset({"proceed", "proceed_with_caution", "investigate", "investigate_more"}),
    "work_type": frozenset(
        {"code", "research", "docs", "debug", "infra", "release", "remote-ops", "config", "data", "comms", "design", "audit"}
    ),
    "consistency": frozenset({"good", "moderate", "poor"}),
    "compliance": _compliance_values(),
    "source": frozenset(
        {
            "artifacts", "sentinel", "goals", "issues", "noetic", "git", "code_quality", "triage", "codebase_model",
            "non_git_files", "meta", "prose_quality", "prose_stylometry", "document_metrics", "source_quality",
            "action_verification", "pytest", "web",
        }
    ),
}  # fmt: skip

#: reflex_data keys that may be read, by phase, and the kind each must be.
_REFLEX_FIELDS: dict[str, dict[str, str]] = {
    "PREFLIGHT": {"git_commit_sha": "sha", "git_notes_ref": "ref"},
    "CHECK": {
        "decision": "vocab:decision",
        "confidence": "number",
        "cycle": "number",
        "auto_checkpoint": "bool",
        "git_commit_sha": "sha",
        "git_notes_ref": "ref",
    },
    "POSTFLIGHT": {
        "work_type": "vocab:work_type",
        "internal_consistency": "vocab:consistency",
        "tool_call_count": "number",
        "postflight_confidence": "number",
        "auto_closed": "bool",
        "git_commit_sha": "sha",
        "git_notes_ref": "ref",
    },
}

#: (table, type name). Every one carries transaction_id; goal_id where the column exists.
_ARTIFACT_TABLES = (
    ("project_findings", "finding", "finding"),
    ("project_unknowns", "unknown", "unknown"),
    ("project_dead_ends", "dead_end", "approach"),
    ("mistakes_made", "mistake", "mistake"),
    ("assumptions", "assumption", "assumption"),
    ("decisions", "decision", "choice"),
)
#: With content=True each goal objective and artifact text is cut to one line of at most this many characters.
_CONTENT_MAX = 300

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
        self.redacted = 0

    def _drop(self, value: Any) -> None:
        if value not in (None, ""):
            self.dropped += 1

    def token(self, value: Any) -> str | None:
        if isinstance(value, str) and _TOKEN.fullmatch(value):
            return value
        self._drop(value)
        return None

    def ident(self, value: Any) -> str | None:
        """An id: UUID- or hex-shaped. A word, however innocent, is not an id."""
        if isinstance(value, str) and (_UUID.fullmatch(value) or _HEX_ID.fullmatch(value)):
            return value
        self._drop(value)
        return None

    def vocab(self, name: str, value: Any) -> str | None:
        if isinstance(value, str) and value in _VOCAB[name]:
            return value
        self._drop(value)
        return None

    def model(self, value: Any) -> str | None:
        if isinstance(value, str) and _MODEL.fullmatch(value):
            return value
        self._drop(value)
        return None

    def line(self, value: Any) -> str | None:
        """Content only (--content): one bounded line, with credential-shaped text redacted and counted."""
        if not isinstance(value, str) or not value.strip():
            return None
        from empirica.core.redaction import redact_secrets

        text = " ".join(value.split())
        clean = redact_secrets(text)
        if clean != text:
            self.redacted += 1
        return clean if len(clean) <= _CONTENT_MAX else clean[: _CONTENT_MAX - 1] + "…"

    def number(self, value: Any) -> float | int | None:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value == value
            and abs(value) <= _NUMBER_LIMIT
        ):
            return round(value, 4) if isinstance(value, float) else value
        self._drop(value)
        return None

    def kind(self, kind: str, value: Any) -> Any:
        if kind == "bool":
            return value if isinstance(value, bool) else None
        if kind == "number":
            return self.number(value)
        if kind.startswith("vocab:"):
            return self.vocab(kind.split(":", 1)[1], value)
        pattern = {"sha": _SHA, "ref": _REF}.get(kind)
        if pattern is not None and isinstance(value, str) and pattern.fullmatch(value):
            return value
        self._drop(value)
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
            if safe.vocab("source", g.get("source")) is not None:
                entry["source"] = g["source"]
        if "self" in entry and "grounded" in entry:
            gap = gaps.get(vec) if safe.number(gaps.get(vec)) is not None else entry["self"] - entry["grounded"]
            entry["gap"] = round(float(gap), 4)
        if entry:
            vectors[vec] = entry
    return {
        "phase": safe.vocab("grounded_phase", row["phase"]),
        "created_at": safe.number(row["created_at"]),
        "grounded_coverage": safe.number(row["grounded_coverage"]),
        "overall_calibration_score": safe.number(row["overall_calibration_score"]),
        "evidence_count": safe.number(row["evidence_count"]),
        "practitioner_model": safe.model(row["practitioner_model"]),
        "compliance_status": safe.vocab("compliance", row["compliance_status"]),
        "vectors": vectors,
    }


def own_ai_id() -> str | None:
    """This store's own practice id: `ai_id` in the git root's `.empirica/project.yaml`, else the root's directory name."""
    try:
        from pathlib import Path

        from empirica.config.path_resolver import get_git_root

        root = get_git_root()
        if not root:
            return None
        cfg = Path(root) / ".empirica" / "project.yaml"
        if cfg.is_file():
            import yaml

            value = (yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}).get("ai_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return Path(root).name
    except Exception:
        return None


def export_transactions(
    conn, ai_id: str, since: float | None = None, limit: int | None = None, content: bool = False
) -> dict:
    """Transactions of `ai_id` newest first. `conn` is a sqlite connection with row access by name.

    Structure only unless `content` is true. `content` adds goal objectives and one text line per artifact for a view inside
    the owner's own tenant: the envelope says `content_scope: tenant` and `do_not_share: true`, credential-shaped text is
    redacted and counted, and the caller (the verb) refuses it for any practice but this store's own.
    """
    import sqlite3

    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return _export(
            conn, ai_id, since, DEFAULT_LIMIT if limit is None else max(0, min(int(limit), MAX_LIMIT)), content
        )
    finally:
        conn.row_factory = previous_factory


def _select_transactions(conn, ai_id: str, lower: float) -> tuple[list[str], int, int, int]:
    """(usable transaction ids newest first, total usable, unsafe-id count, reflex rows without a transaction id)."""
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
    # A transaction whose id is not UUID- or hex-shaped cannot be linked by a consumer and its id could be anything:
    # it is left out and counted, rather than emitted with a null id.
    usable = [row["tx"] for row in starts if _UUID.fullmatch(row["tx"]) or _HEX_ID.fullmatch(row["tx"])]
    return usable, len(usable), len(starts) - len(usable), skipped


def _new_record(tx: str, ai_id: str, safe: _Safe) -> dict:
    return {
        "transaction_id": safe.ident(tx),
        "session_id": None,
        "ai_id": safe.token(ai_id),
        "preflight": None,
        "checks": [],
        "postflight": None,
        "grounded": [],
        "goals": [],
        "goals_touched": [],
        "artifacts": [],
    }


def _fill_reflexes(conn, part: list[str], records: dict, ai_id: str, safe: _Safe) -> None:
    for row in conn.execute(
        f"SELECT * FROM reflexes WHERE transaction_id IN ({_marks(len(part))}) "
        "AND session_id IN (SELECT session_id FROM sessions WHERE ai_id = ?) ORDER BY timestamp, id",
        [*part, ai_id],
    ):
        rec = records[row["transaction_id"]]
        rec["session_id"] = rec["session_id"] or safe.ident(row["session_id"])
        phase = row["phase"]
        if phase == "PREFLIGHT" and rec["preflight"] is None:
            rec["preflight"] = _reflex(row, safe)
        elif phase == "CHECK":
            rec["checks"].append(_reflex(row, safe))
        elif phase == "POSTFLIGHT":
            rec["postflight"] = _reflex(row, safe)  # the last one wins, as the calibration pass reads it


def _fill_links(
    conn, part: list[str], records: dict, ai_id: str, safe: _Safe, unavailable: list[str], content: bool
) -> None:
    rows = _read(
        conn,
        unavailable,
        "grounded_verifications",
        f"SELECT * FROM grounded_verifications WHERE transaction_id IN ({_marks(len(part))}) "
        "AND ai_id = ? ORDER BY created_at",
        [*part, ai_id],
    )
    for row in rows:
        records[row["transaction_id"]]["grounded"].append(_grounded(row, safe))

    objective = ", objective" if content else ""
    rows = _read(
        conn,
        unavailable,
        "goals",
        f"SELECT id, status, created_timestamp, completed_timestamp, transaction_id{objective} FROM goals "
        f"WHERE transaction_id IN ({_marks(len(part))}) ORDER BY created_timestamp",
        part,
    )
    for row in rows:
        goal = {
            "id": safe.ident(row["id"]),
            "status": safe.vocab("goal_status", row["status"]),
            "created_timestamp": safe.number(row["created_timestamp"]),
            "completed_timestamp": safe.number(row["completed_timestamp"]),
        }
        if content:
            goal["objective"] = safe.line(row["objective"])
        records[row["transaction_id"]]["goals"].append(goal)

    for table, kind, text_col in _ARTIFACT_TABLES:
        text = f", {text_col} AS text" if content else ""
        rows = _read(
            conn,
            unavailable,
            table,
            f"SELECT id, goal_id, transaction_id{text} FROM {table} "
            f"WHERE transaction_id IN ({_marks(len(part))}) ORDER BY rowid",
            part,
        )
        for row in rows:
            item: dict[str, Any] = {"id": safe.ident(row["id"]), "type": kind}
            goal_id = safe.ident(row["goal_id"])
            if goal_id is not None:
                item["goal_id"] = goal_id
            if content:
                item["text"] = safe.line(row["text"])
            records[row["transaction_id"]]["artifacts"].append(item)


def _goals_touched(conn, rec: dict, ai_id: str, safe: _Safe, unavailable: list[str]) -> list[dict]:
    """The goals in play in one transaction, by the Sentinel's own definition (sentinel-gate.py _check_goalless_work).

    A goal is touched when it was created there (`goals.transaction_id`), when an artifact logged there carries its
    goal_id, or when one of its tasks was created or completed between PREFLIGHT and POSTFLIGHT. `via` lists every way.
    `goals.transaction_id` is a single column, so created/activated alone gave a goal worked across N transactions one.
    """
    via: dict[str, set[str]] = {}
    for goal in rec["goals"]:
        if goal["id"]:
            via.setdefault(goal["id"], set()).add("created")
    for item in rec["artifacts"]:
        if item.get("goal_id"):
            via.setdefault(item["goal_id"], set()).add("artifact")

    timestamps = [t["timestamp"] for t in [rec["preflight"], *rec["checks"]] if t and t.get("timestamp") is not None]
    if timestamps:
        start = min(timestamps)
        end = rec["postflight"]["timestamp"] if rec["postflight"] and rec["postflight"].get("timestamp") else 1e13
        # typeof guard: legacy rows hold TEXT timestamps and SQLite ranks any TEXT above any number, so a bare
        # comparison would put them inside every window.
        rows = _read(
            conn,
            unavailable,
            "subtasks",
            "SELECT DISTINCT s.goal_id AS goal_id FROM subtasks s JOIN goals g ON g.id = s.goal_id "
            "WHERE g.session_id IN (SELECT session_id FROM sessions WHERE ai_id = ?) AND ("
            "(typeof(s.created_timestamp) IN ('real','integer') AND s.created_timestamp BETWEEN ? AND ?) OR "
            "(typeof(s.completed_timestamp) IN ('real','integer') AND s.completed_timestamp BETWEEN ? AND ?))",
            [ai_id, start, end, start, end],
        )
        for row in rows:
            goal_id = safe.ident(row["goal_id"])
            if goal_id:
                via.setdefault(goal_id, set()).add("task")
    return [{"id": gid, "via": sorted(ways)} for gid, ways in sorted(via.items())]


def _export(conn, ai_id: str, since: float | None, limit: int, content: bool = False) -> dict:
    safe = _Safe()
    unavailable: list[str] = []
    usable, total, unsafe_ids, skipped = _select_transactions(conn, ai_id, since if since is not None else 0.0)
    chosen = usable[:limit]
    records = {tx: _new_record(tx, ai_id, safe) for tx in chosen}

    for part in _chunks(chosen):
        _fill_reflexes(conn, part, records, ai_id, safe)
        _fill_links(conn, part, records, ai_id, safe, unavailable, content)
    for rec in records.values():
        rec["goals_touched"] = _goals_touched(conn, rec, ai_id, safe, unavailable)

    transactions = [records[tx] for tx in chosen]
    out: dict[str, Any] = {
        "ok": True,
        "schema": SCHEMA,
        "ai_id": safe.token(ai_id),
        "since": since,
        "limit": limit,
        "returned": len(transactions),
        "total_matching": total,
        "truncated": total > len(transactions),
        "skipped_without_transaction_id": skipped,
        "skipped_unsafe_transaction_ids": unsafe_ids,
        "dropped_unsafe_values": safe.dropped,
        "unavailable": unavailable,
        "content_scope": "tenant" if content else "none",
    }
    if content:
        out["do_not_share"] = True
        out["content_redactions"] = safe.redacted
    out["transactions"] = transactions
    return out
