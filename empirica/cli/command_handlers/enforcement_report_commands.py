"""`empirica enforcement-report` — artifact-graph enforce telemetry.

Reads ``weave_enforce_events`` (migration 052) and reports how the enforce-by-default
gate is behaving in practice: how often CHECK blocked the noetic→praxic transition,
and — the health metric — how often a blocked transaction **self-resolved** (the
practitioner wove an edge and reached a proceed) vs stalled.

self-resolve-rate is the empirical answer to "how well is enforce going": high =
the gate nudges and the system recovers on its own (working as designed); low =
it's over-blocking (dial the floor down). Pure aggregation in
``_aggregate_weave_events`` so the metric is unit-tested without a DB.
"""

from __future__ import annotations

import json


def _aggregate_weave_events(rows: list[dict]) -> dict:
    """Aggregate weave_enforce_events rows into enforce telemetry.

    ``rows`` — dicts with ``transaction_id``, ``created_timestamp``,
    ``connectivity_ratio``, ``response_band``, ``enforced`` (0/1), ``decision_out``.

    A transaction is **blocked** if any of its verdicts enforced. A blocked
    transaction **self-resolved** if, at or after its first block, a later verdict
    reached ``decision_out == 'proceed'`` (the practitioner wove an edge and the
    re-CHECK passed). self-resolve-rate = self-resolved / blocked.
    """
    total_verdicts = len(rows)
    bands: dict[str, int] = {}
    conn_vals: list[float] = []
    for r in rows:
        band = r.get("response_band") or "unknown"
        bands[band] = bands.get(band, 0) + 1
        cv = r.get("connectivity_ratio")
        if isinstance(cv, (int, float)):
            conn_vals.append(float(cv))

    # Group by transaction, ordered by time, to compute block + self-resolve.
    by_txn: dict[str, list[dict]] = {}
    for r in rows:
        by_txn.setdefault(r.get("transaction_id"), []).append(r)

    blocked_txns = 0
    self_resolved_txns = 0
    for verdicts in by_txn.values():
        ordered = sorted(verdicts, key=lambda x: x.get("created_timestamp") or 0)
        first_block_ts = next((v.get("created_timestamp") or 0 for v in ordered if v.get("enforced")), None)
        if first_block_ts is None:
            continue
        blocked_txns += 1
        if any(
            (v.get("created_timestamp") or 0) >= first_block_ts and v.get("decision_out") == "proceed" for v in ordered
        ):
            self_resolved_txns += 1

    total_txns = len(by_txn)
    return {
        "total_verdicts": total_verdicts,
        "total_transactions": total_txns,
        "blocked_transactions": blocked_txns,
        "block_rate": round(blocked_txns / total_txns, 3) if total_txns else 0.0,
        "self_resolved_transactions": self_resolved_txns,
        "self_resolve_rate": round(self_resolved_txns / blocked_txns, 3) if blocked_txns else None,
        "avg_connectivity_ratio": round(sum(conn_vals) / len(conn_vals), 3) if conn_vals else None,
        "band_distribution": bands,
    }


def _read_weave_events(db, session_id: str | None) -> list[dict]:
    """Read weave_enforce_events from the session DB. Returns [] if the table is
    absent (un-migrated) or on any read error — the report degrades to 'no data'."""
    cols = (
        "transaction_id",
        "created_timestamp",
        "connectivity_ratio",
        "connectivity_floor",
        "strictness",
        "response_band",
        "enforced",
        "decision_in",
        "decision_out",
    )
    try:
        sql = f"SELECT {', '.join(cols)} FROM weave_enforce_events"
        params: tuple = ()
        if session_id:
            sql += " WHERE session_id = ?"
            params = (session_id,)
        cur = db.conn.execute(sql, params)
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def handle_enforcement_report_command(args) -> None:
    """Render the enforce telemetry report (human or JSON)."""
    from empirica.data.session_database import SessionDatabase

    session_id = getattr(args, "session_id", None)
    db = SessionDatabase()
    try:
        rows = _read_weave_events(db, session_id)
    finally:
        db.close()

    summary = _aggregate_weave_events(rows)

    if getattr(args, "output", "human") == "json":
        print(json.dumps(summary, indent=2))
        return

    print("\n🛡️  Artifact-Graph Enforcement Report")
    print("━" * 60)
    if summary["total_verdicts"] == 0:
        print("  (no weave verdicts recorded yet — enforce telemetry is empty)")
        print("━" * 60)
        return
    srr = summary["self_resolve_rate"]
    print(f"Weave verdicts:            {summary['total_verdicts']}")
    print(f"Transactions:              {summary['total_transactions']}")
    print(
        f"Blocked (enforced):        {summary['blocked_transactions']} "
        f"({summary['block_rate'] * 100:.0f}% of transactions)"
    )
    if srr is None:
        print("Self-resolve rate:         — (nothing blocked)")
    else:
        print(
            f"Self-resolve rate:         {srr * 100:.0f}% "
            f"({summary['self_resolved_transactions']}/{summary['blocked_transactions']} blocked → wove → proceeded)"
        )
    if summary["avg_connectivity_ratio"] is not None:
        print(f"Avg connectivity:          {summary['avg_connectivity_ratio'] * 100:.0f}%")
    if summary["band_distribution"]:
        bands = ", ".join(f"{k}={v}" for k, v in sorted(summary["band_distribution"].items()))
        print(f"Response bands:            {bands}")
    print("━" * 60)
    if srr is not None and srr < 0.5:
        print("⚠️  Low self-resolve rate — the floor may be over-blocking. Consider")
        print("   EMPIRICA_ARTIFACT_GRAPH_FLOOR=0.25 or investigate stalled transactions.")
        print("━" * 60)
