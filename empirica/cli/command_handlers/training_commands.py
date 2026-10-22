"""
Training Commands - Export epistemic transaction data as JSONL for model fine-tuning.

Exports matched (preflight, postflight, grounded_calibration, noetic_artifacts) tuples
from the sessions database. Each JSONL line is one epistemic transaction — a complete
belief-update cycle suitable for supervised fine-tuning on epistemic self-awareness.
"""

import json
import logging
import sqlite3
from pathlib import Path

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def _export_from_db(
    db_path, project_filter, ai_filter, min_vectors, include_artifacts, include_grounded, project_name=None
):
    """Export training records from a single sessions.db. Returns (records, skipped)."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except Exception as e:
        logger.debug(f"Cannot open {db_path}: {e}")
        return [], 0

    pairs = _find_transaction_pairs(conn, project_filter, ai_filter)
    records = []
    skipped = 0
    for pair in pairs:
        record = _build_training_record(
            conn,
            pair,
            include_artifacts=include_artifacts,
            include_grounded=include_grounded,
            min_vectors=min_vectors,
        )
        if record:
            if project_name:
                record["_source_project"] = project_name
            records.append(record)
        else:
            skipped += 1

    conn.close()
    return records, skipped


def _find_workspace_dbs():
    """Find all project sessions.db files via workspace.db global_projects table."""
    workspace_db_path = Path.home() / ".empirica" / "workspace" / "workspace.db"
    if not workspace_db_path.exists():
        return []

    conn = sqlite3.connect(str(workspace_db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, trajectory_path FROM global_projects WHERE status != 'archived'"
        ).fetchall()
    except Exception:
        conn.close()
        return []
    conn.close()

    dbs = []
    for row in rows:
        tpath = row["trajectory_path"]
        if not tpath:
            continue
        # trajectory_path may be /path/to/project/.empirica or /path/to/project
        tpath = Path(tpath)
        candidates = [
            tpath / "sessions" / "sessions.db",
            tpath / ".empirica" / "sessions" / "sessions.db",
        ]
        # Also check if tpath itself ends with .empirica
        if tpath.name == ".empirica":
            candidates.insert(0, tpath / "sessions" / "sessions.db")

        for candidate in candidates:
            if candidate.exists():
                dbs.append(
                    {
                        "project_id": row["id"],
                        "project_name": row["name"],
                        "db_path": candidate,
                    }
                )
                break

    return dbs


def _training_collect_records(
    args, project_filter, ai_filter, min_vectors, include_artifacts, include_grounded, workspace_mode
):
    """Collect training records from workspace or single project.

    Returns (all_records, total_skipped, db_sources).
    """
    all_records = []
    total_skipped = 0
    db_sources = []

    if workspace_mode:
        project_dbs = _find_workspace_dbs()
        if not project_dbs:
            return [], 0, []

        seen_dbs = set()
        for pdb in project_dbs:
            db_key = str(pdb["db_path"])
            if db_key in seen_dbs:
                continue
            seen_dbs.add(db_key)

            records, skipped = _export_from_db(
                pdb["db_path"],
                project_filter,
                ai_filter,
                min_vectors,
                include_artifacts,
                include_grounded,
                project_name=pdb["project_name"],
            )
            if records:
                all_records.extend(records)
                total_skipped += skipped
                db_sources.append(
                    {
                        "project": pdb["project_name"],
                        "db_path": str(pdb["db_path"]),
                        "exported": len(records),
                    }
                )
    else:
        from empirica.config.path_resolver import get_session_db_path

        db_path = get_session_db_path()
        records, skipped = _export_from_db(
            db_path,
            project_filter,
            ai_filter,
            min_vectors,
            include_artifacts,
            include_grounded,
        )
        all_records = records
        total_skipped = skipped
        db_sources = [{"db_path": str(db_path), "exported": len(records)}]

    return all_records, total_skipped, db_sources


def handle_training_export_command(args):
    """Handle training-export command — export epistemic transactions as JSONL."""
    try:
        output_path = getattr(args, "output_path", None)
        project_filter = getattr(args, "project_id", None)
        ai_filter = getattr(args, "ai_id", None)
        min_vectors = getattr(args, "min_vectors", 3)
        include_artifacts = not getattr(args, "no_artifacts", False)
        include_grounded = not getattr(args, "no_grounded", False)
        workspace_mode = getattr(args, "workspace", False)
        output_format = getattr(args, "output", "human")

        all_records, total_skipped, db_sources = _training_collect_records(
            args, project_filter, ai_filter, min_vectors, include_artifacts, include_grounded, workspace_mode
        )

        if not all_records:
            if output_format == "json":
                msg = (
                    "No project databases found in workspace"
                    if workspace_mode and not db_sources
                    else "No paired transactions found"
                )
                print(json.dumps({"ok": True, "exported": 0, "message": msg}))
            else:
                msg = (
                    "No project databases found in workspace."
                    if workspace_mode and not db_sources
                    else "No paired PREFLIGHT/POSTFLIGHT transactions found."
                )
                print(msg)
            return None

        if output_path:
            out_path = Path(output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w") as f:
                for rec in all_records:
                    f.write(json.dumps(rec, default=str) + "\n")
            if output_format == "json":
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "exported": len(all_records),
                            "skipped": total_skipped,
                            "output_path": str(out_path),
                            "sources": db_sources,
                        }
                    )
                )
            else:
                print(f"Exported {len(all_records)} transactions to {out_path}")
                if total_skipped:
                    print(f"   Skipped {total_skipped} (insufficient vector data)")
                if workspace_mode:
                    print(f"   Sources: {len(db_sources)} project databases")
                    for src in db_sources:
                        print(f"     {src.get('project', 'unknown')}: {src['exported']} transactions")
                else:
                    print(f"   Source: {db_sources[0]['db_path']}")
        else:
            for rec in all_records:
                print(json.dumps(rec, default=str))

        return None

    except Exception as e:
        handle_cli_error(e, "Training export", getattr(args, "verbose", False))
        return None


def _get_reflexes_columns(conn):
    """Check which columns exist in the reflexes table. Returns set of column names."""
    try:
        cursor = conn.execute("PRAGMA table_info(reflexes)")
        return {row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        return set()


def _build_select_columns(has_transaction_id, has_cascade_id, has_project_id, has_reflex_data, has_reasoning):
    """Build SQL SELECT column fragments with NULL fallbacks for missing columns."""
    return {
        "tx": "pf.transaction_id," if has_transaction_id else "NULL as transaction_id,",
        "cascade": "pf.cascade_id," if has_cascade_id else "NULL as cascade_id,",
        "project_id": "pf.project_id," if has_project_id else "NULL as project_id,",
        "pf_reflex": "pf.reflex_data as pf_reflex_data," if has_reflex_data else "NULL as pf_reflex_data,",
        "po_reflex": "po.reflex_data as po_reflex_data," if has_reflex_data else "NULL as po_reflex_data,",
        "po_reasoning": "po.reasoning as po_reasoning," if has_reasoning else "NULL as po_reasoning,",
    }


def _build_join_condition(has_transaction_id, has_cascade_id):
    """Build SQL JOIN condition with cascading fallback for matching pairs."""
    join_parts = []
    if has_transaction_id:
        join_parts.append("(pf.transaction_id IS NOT NULL AND pf.transaction_id = po.transaction_id)")
    if has_cascade_id:
        prefix = "(pf.transaction_id IS NULL AND " if has_transaction_id else "("
        join_parts.append(f"{prefix}pf.cascade_id IS NOT NULL AND pf.cascade_id = po.cascade_id)")

    # Session_id fallback
    null_checks = []
    if has_transaction_id:
        null_checks.append("pf.transaction_id IS NULL")
    if has_cascade_id:
        null_checks.append("pf.cascade_id IS NULL")
    prefix = f"({' AND '.join(null_checks)} AND " if null_checks else "("
    join_parts.append(f"{prefix}pf.session_id = po.session_id)")

    return " OR ".join(join_parts)


def _find_transaction_pairs(conn, project_filter=None, ai_filter=None):
    """Find matched PREFLIGHT/POSTFLIGHT pairs from reflexes table.

    Handles schema variations in older DBs:
    - Missing transaction_id column -> fall back to cascade_id or session_id matching
    - Missing cascade_id column -> fall back to session_id matching
    - Missing reflexes table entirely -> return empty list
    """
    columns = _get_reflexes_columns(conn)
    if not columns:
        logger.debug("No reflexes table found, skipping")
        return []

    has_transaction_id = "transaction_id" in columns
    has_cascade_id = "cascade_id" in columns
    has_project_id = "project_id" in columns
    has_reasoning = "reasoning" in columns
    has_reflex_data = "reflex_data" in columns

    try:
        sess_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    except sqlite3.OperationalError:
        sess_cols = set()
    has_ai_id = "ai_id" in sess_cols

    sel = _build_select_columns(has_transaction_id, has_cascade_id, has_project_id, has_reflex_data, has_reasoning)
    join_condition = _build_join_condition(has_transaction_id, has_cascade_id)

    query = f"""
        SELECT
            pf.id as preflight_id, pf.session_id,
            {sel["tx"]} {sel["cascade"]} pf.timestamp as preflight_ts, {sel["project_id"]}
            pf.engagement as pf_engagement, pf.know as pf_know, pf.do as pf_do,
            pf.context as pf_context, pf.clarity as pf_clarity, pf.coherence as pf_coherence,
            pf.signal as pf_signal, pf.density as pf_density, pf.state as pf_state,
            pf.change as pf_change, pf.completion as pf_completion, pf.impact as pf_impact,
            pf.uncertainty as pf_uncertainty, {sel["pf_reflex"]}
            po.id as postflight_id, po.timestamp as postflight_ts,
            po.engagement as po_engagement, po.know as po_know, po.do as po_do,
            po.context as po_context, po.clarity as po_clarity, po.coherence as po_coherence,
            po.signal as po_signal, po.density as po_density, po.state as po_state,
            po.change as po_change, po.completion as po_completion, po.impact as po_impact,
            po.uncertainty as po_uncertainty,
            {sel["po_reflex"]} {sel["po_reasoning"]}
            {"s.ai_id" if has_ai_id else "NULL as ai_id"}
        FROM reflexes pf
        JOIN reflexes po ON ({join_condition})
        {"JOIN sessions s ON pf.session_id = s.session_id" if has_ai_id else "LEFT JOIN sessions s ON pf.session_id = s.session_id"}
        WHERE pf.phase = 'PREFLIGHT' AND po.phase = 'POSTFLIGHT' AND po.timestamp > pf.timestamp
    """
    params = []

    if project_filter and has_project_id:
        query += " AND pf.project_id LIKE ?"
        params.append(f"{project_filter}%")
    if ai_filter and has_ai_id:
        query += " AND s.ai_id = ?"
        params.append(ai_filter)

    query += " ORDER BY pf.timestamp ASC"

    try:
        rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning(f"Query failed: {e}")
        rows = []

    seen_preflight = set()
    pairs = []
    for row in rows:
        pf_id = row["preflight_id"]
        if pf_id not in seen_preflight:
            seen_preflight.add(pf_id)
            pairs.append(dict(row))

    return pairs


def _has_column(conn, table: str, column: str) -> bool:
    try:
        return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())
    except sqlite3.OperationalError:
        return False


def _transaction_scope(conn, table: str, transaction_id, time_column: str, start_ts, end_ts):
    """``(sql, params)`` selecting a transaction's own rows.

    A row stamped with this transaction belongs to it whatever the clock says; a row stamped with ANOTHER transaction never
    does, even inside the window (two transactions of one session overlap, or follow each other within the old five-minute
    tail, and exchanged artifacts). A row with no transaction id (written before ids existed) belongs to no other
    transaction, so the window still places it. Without a transaction id, or a table that has no such column, the window
    decides, as it always did.
    """
    window = f"{time_column} >= ? AND {time_column} <= ?"
    if transaction_id and _has_column(conn, table, "transaction_id"):
        return f"(transaction_id = ? OR (transaction_id IS NULL AND {window}))", [transaction_id, start_ts, end_ts]
    return window, [start_ts, end_ts]


def _collect_transaction_artifacts(conn, session_id: str, start_ts, end_ts, transaction_id=None) -> dict:
    """Collect noetic artifacts for a transaction: by its transaction_id where rows carry one, else by its time window."""
    ARTIFACT_QUERIES = [
        (
            "findings",
            "project_findings",
            "finding, impact, subject",
            "ORDER BY impact DESC",
            lambda r: {"finding": r["finding"], "impact": r["impact"], "subject": r["subject"]},
        ),
        (
            "unknowns",
            "project_unknowns",
            "unknown, is_resolved, impact",
            "",
            lambda r: {"unknown": r["unknown"], "resolved": bool(r["is_resolved"]), "impact": r["impact"]},
        ),
        (
            "dead_ends",
            "project_dead_ends",
            "approach, why_failed, impact",
            "",
            lambda r: {"approach": r["approach"], "why_failed": r["why_failed"], "impact": r["impact"]},
        ),
        (
            "mistakes",
            "mistakes_made",
            "mistake, why_wrong, prevention, root_cause_vector",
            "",
            lambda r: {
                "mistake": r["mistake"],
                "why_wrong": r["why_wrong"],
                "prevention": r["prevention"],
                "root_cause_vector": r["root_cause_vector"],
            },
        ),
        (
            "decisions",
            "decisions",
            "choice, rationale, reversibility",
            "",
            lambda r: {"choice": r["choice"], "rationale": r["rationale"], "reversibility": r["reversibility"]},
        ),
    ]
    artifacts = {}
    for key, table, columns, order, mapper in ARTIFACT_QUERIES:
        scope, scope_params = _transaction_scope(conn, table, transaction_id, "created_timestamp", start_ts, end_ts)
        try:
            rows = conn.execute(
                f"""
                SELECT {columns} FROM {table}
                WHERE session_id = ? AND {scope}
                {order} LIMIT 10
            """,
                [session_id, *scope_params],
            ).fetchall()
            if rows:
                artifacts[key] = [mapper(r) for r in rows]
        except sqlite3.OperationalError as e:
            # An older database without the table is expected and quiet. Anything else (a table or column the query names and
            # the schema lacks) is a bug in this exporter: swallowed, it read as "no decisions this transaction" for months.
            if "no such table" not in str(e):
                logger.warning(f"training export: {key} artifacts query on {table} failed: {e}")
    return artifacts


def _extract_vectors_and_delta(pair, min_vectors):
    """Extract preflight/postflight vectors and compute delta from a pair.

    Returns (preflight, postflight, delta) or (None, None, None) if insufficient data.
    """
    VECTORS = [
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
        "engagement",
        "uncertainty",
    ]

    preflight = {}
    postflight = {}
    for v in VECTORS:
        pf_val = pair.get(f"pf_{v}")
        po_val = pair.get(f"po_{v}")
        if pf_val is not None:
            preflight[v] = pf_val
        if po_val is not None:
            postflight[v] = po_val

    if len(preflight) < min_vectors or len(postflight) < min_vectors:
        return None, None, None

    delta = {}
    for v in VECTORS:
        if v in preflight and v in postflight:
            delta[v] = round(postflight[v] - preflight[v], 4)

    return preflight, postflight, delta


def _collect_check_decisions(conn, session_id, preflight_ts, postflight_ts, transaction_id=None):
    """Collect a transaction's CHECK decisions (by transaction_id where stamped, else its window). Returns list or None."""
    scope, scope_params = _transaction_scope(conn, "reflexes", transaction_id, "timestamp", preflight_ts, postflight_ts)
    check_query = f"""
        SELECT timestamp, reflex_data, reasoning,
               know, uncertainty, completion, clarity
        FROM reflexes
        WHERE session_id = ? AND phase = 'CHECK'
          AND {scope}
        ORDER BY timestamp
    """
    checks = conn.execute(check_query, [session_id, *scope_params]).fetchall()
    if not checks:
        return None

    check_decisions = []
    for c in checks:
        check_rec = {
            "timestamp": c["timestamp"],
            "vectors": {
                "know": c["know"],
                "uncertainty": c["uncertainty"],
                "completion": c["completion"],
                "clarity": c["clarity"],
            },
        }
        if c["reflex_data"]:
            try:
                rd = json.loads(c["reflex_data"])
                check_rec["decision"] = rd.get("decision") or rd.get("computed_decision")
                check_rec["gate_passed"] = rd.get("gate_passed")
            except (json.JSONDecodeError, TypeError):
                pass
        check_decisions.append(check_rec)
    return check_decisions


def _collect_grounded_calibration(conn, session_id, postflight_ts, transaction_id=None):
    """Collect the transaction's grounded calibration. Returns dict or None.

    By ``transaction_id`` where the verification carries one. The five minutes after POSTFLIGHT is only the fallback for a
    row with no id: as the primary rule it handed a transaction the calibration of whichever one finished next.
    """
    scope, scope_params = _transaction_scope(
        conn, "grounded_verifications", transaction_id, "created_at", postflight_ts, postflight_ts + 300
    )
    gv_query = f"""
        SELECT self_assessed_vectors, grounded_vectors, calibration_gaps,
               overall_calibration_score, grounded_coverage,
               evidence_count, sources_available
        FROM grounded_verifications
        WHERE session_id = ?
          AND {scope}
        ORDER BY (transaction_id IS NULL), created_at DESC LIMIT 1
    """
    try:
        gv = conn.execute(gv_query, [session_id, *scope_params]).fetchone()
        if not gv:
            return None
        grounded = {
            "calibration_score": gv["overall_calibration_score"],
            "grounded_coverage": gv["grounded_coverage"],
            "evidence_count": gv["evidence_count"],
        }
        for field in ["calibration_gaps", "sources_available"]:
            raw = gv[field]
            if raw:
                try:
                    grounded[field] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass
        return grounded
    except sqlite3.OperationalError:
        return None


def _build_training_record(conn, pair, include_artifacts=True, include_grounded=True, min_vectors=3):
    """Build a single JSONL training record from a PREFLIGHT/POSTFLIGHT pair."""
    preflight, postflight, delta = _extract_vectors_and_delta(pair, min_vectors)
    if preflight is None:
        return None

    record = {
        "session_id": pair["session_id"],
        "ai_id": pair["ai_id"],
        "project_id": pair.get("project_id"),
        "transaction_id": pair.get("transaction_id"),
        "preflight_ts": pair["preflight_ts"],
        "postflight_ts": pair["postflight_ts"],
        "preflight_vectors": preflight,
        "postflight_vectors": postflight,
        "delta": delta,
    }

    # Parse reflex_data for phase info and notes
    for prefix, key in [("pf_reflex_data", "preflight_meta"), ("po_reflex_data", "postflight_meta")]:
        raw = pair.get(prefix)
        if raw:
            try:
                data = json.loads(raw)
                record[key] = {k: data[k] for k in ["current_phase", "notes", "tool_call_count"] if k in data}
            except (json.JSONDecodeError, TypeError):
                pass

    if pair.get("po_reasoning"):
        record["postflight_reasoning"] = pair["po_reasoning"]

    session_id = pair["session_id"]

    tx = pair.get("transaction_id")
    check_decisions = _collect_check_decisions(conn, session_id, pair["preflight_ts"], pair["postflight_ts"], tx)
    if check_decisions:
        record["check_decisions"] = check_decisions

    if include_grounded:
        grounded = _collect_grounded_calibration(conn, session_id, pair["postflight_ts"], tx)
        if grounded:
            record["grounded_calibration"] = grounded

    if include_artifacts:
        artifacts = _collect_transaction_artifacts(conn, session_id, pair["preflight_ts"], pair["postflight_ts"], tx)
        if artifacts:
            record["noetic_artifacts"] = artifacts

    return record
