"""
Phase Boundary Detection

Detects the CHECK proceed boundary in a transaction to split calibration
into noetic (PREFLIGHT→CHECK) and praxic (CHECK→POSTFLIGHT) phases.

The CHECK gate is the natural boundary — it already separates investigation
from action. Phase-aware calibration respects this boundary instead of
treating the entire transaction as one undifferentiated block.
"""

import json
import logging

logger = logging.getLogger(__name__)


def _scope(session_id: str, transaction_id: str | None) -> tuple:
    """Parameters for the predicate that bounds a lookup to one transaction.

    A session is a compaction boundary, not a unit of work: scoped by session,
    one CHECK made every later POSTFLIGHT in that session phase-aware, and its
    noetic row graded THIS transaction's evidence against the vectors of an
    earlier, unrelated CHECK. Session scope survives only for callers that have
    no transaction id to give.
    """
    tx = transaction_id or None
    return (tx, tx, tx, session_id)


def detect_phase_boundary(session_id: str, db, transaction_id: str | None = None) -> dict:
    """Find the CHECK proceed boundary in a transaction.

    Queries the reflexes table for the transaction's PREFLIGHT and CHECK entries.
    The last CHECK with decision="proceed" marks the noetic→praxic boundary.

    Returns:
        {
            "has_check": bool,
            "proceed_check_timestamp": float | None,
            "proceed_check_vectors": dict | None,
            "preflight_vectors": dict | None,
            "preflight_timestamp": float | None,
            "noetic_only": bool,  # True if no proceed CHECK
            "check_count": int,
            "investigate_count": int,
        }
    """
    result = {
        "has_check": False,
        "proceed_check_timestamp": None,
        "proceed_check_vectors": None,
        "preflight_vectors": None,
        "preflight_timestamp": None,
        "noetic_only": False,
        "check_count": 0,
        "investigate_count": 0,
    }

    try:
        cursor = db.conn.cursor()
        params = _scope(session_id, transaction_id)

        # Get PREFLIGHT vectors and timestamp
        cursor.execute(
            """
            SELECT timestamp, know, uncertainty, completion, context,
                   do, signal, coherence, engagement
            FROM reflexes
            WHERE ((? IS NOT NULL AND transaction_id = ?) OR (? IS NULL AND session_id = ?))
              AND phase = 'PREFLIGHT'
            ORDER BY timestamp DESC LIMIT 1
        """,
            params,
        )
        preflight_row = cursor.fetchone()

        if preflight_row:
            result["preflight_timestamp"] = preflight_row[0]
            result["preflight_vectors"] = {
                "know": preflight_row[1],
                "uncertainty": preflight_row[2],
                "completion": preflight_row[3],
                "context": preflight_row[4],
                "do": preflight_row[5],
                "signal": preflight_row[6],
                "coherence": preflight_row[7],
                "engagement": preflight_row[8],
            }

        # Get all CHECK entries
        cursor.execute(
            """
            SELECT timestamp, reflex_data, know, uncertainty, completion,
                   context, do, signal, coherence, engagement
            FROM reflexes
            WHERE ((? IS NOT NULL AND transaction_id = ?) OR (? IS NULL AND session_id = ?))
              AND phase = 'CHECK'
            ORDER BY timestamp ASC
        """,
            params,
        )
        check_rows = cursor.fetchall()

        if not check_rows:
            return result

        result["has_check"] = True
        result["check_count"] = len(check_rows)

        # Find the last proceed CHECK
        proceed_row = None
        investigate_count = 0
        for row in check_rows:
            try:
                data = json.loads(row[1]) if row[1] else {}
                decision = data.get("decision", "")
                if decision == "proceed":
                    proceed_row = row
                elif decision == "investigate":
                    investigate_count += 1
            except (json.JSONDecodeError, TypeError):
                pass

        result["investigate_count"] = investigate_count

        if proceed_row:
            result["proceed_check_timestamp"] = proceed_row[0]
            result["proceed_check_vectors"] = {
                "know": proceed_row[2],
                "uncertainty": proceed_row[3],
                "completion": proceed_row[4],
                "context": proceed_row[5],
                "do": proceed_row[6],
                "signal": proceed_row[7],
                "coherence": proceed_row[8],
                "engagement": proceed_row[9],
            }
        else:
            # All CHECKs were investigate — noetic-only session
            result["noetic_only"] = True
            # Use the last CHECK vectors as the noetic endpoint
            last_check = check_rows[-1]
            result["proceed_check_timestamp"] = last_check[0]
            result["proceed_check_vectors"] = {
                "know": last_check[2],
                "uncertainty": last_check[3],
                "completion": last_check[4],
                "context": last_check[5],
                "do": last_check[6],
                "signal": last_check[7],
                "coherence": last_check[8],
                "engagement": last_check[9],
            }

    except Exception as e:
        logger.debug(f"Phase boundary detection failed (non-fatal): {e}")

    return result
