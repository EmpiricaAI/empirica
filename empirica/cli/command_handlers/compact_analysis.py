"""
Compact Analysis - Retroactive measurement of epistemic loss during memory compaction

This module analyzes pre-compact snapshots vs post-compact PREFLIGHT assessments
to measure what knowledge is lost and recovered during Claude Code memory compaction.

Data Quality Filtering:
- Excludes test sessions (ai_id patterns: test*, *-test, storage-*)
- Requires complete CASCADE loop (has POSTFLIGHT)
- Requires actual work evidence (findings or unknowns logged)
- Filters rapid-fire sessions (< 5 min duration)
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

# AI IDs that indicate test/non-production sessions
TEST_AI_PATTERNS = [
    "test%",
    "%test%",
    "storage%",
    "mcp-%test",
    "cli-e2e%",
    "cli-tester",
]

# Minimum session duration to be considered "real" (seconds)
MIN_SESSION_DURATION = 300  # 5 minutes


def get_ref_docs_path() -> Path:
    """Get the ref-docs directory for pre-compact snapshots via unified context resolver."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        context = R.context()
        project_path = context.get("project_path")
        if project_path:
            ref_docs = Path(project_path) / ".empirica" / "ref-docs"
            if ref_docs.exists():
                return ref_docs
    except Exception:
        pass

    # Fallback to CWD-based
    local_path = Path.cwd() / ".empirica" / "ref-docs"
    if local_path.exists():
        return local_path

    global_path = Path.home() / ".empirica" / "ref-docs"
    if global_path.exists():
        return global_path

    raise FileNotFoundError("No ref-docs directory found")


def is_test_session(ai_id: str) -> bool:
    """Check if AI ID indicates a test session."""
    ai_lower = ai_id.lower()
    return (
        ai_lower.startswith("test")
        or "-test" in ai_lower
        or "test-" in ai_lower
        or ai_lower.startswith("storage")
        or ai_lower in ("cli-tester", "empirica-tester", "mcp-full-test", "mcp-quick-test")
    )


def load_pre_compact_snapshots(ref_docs_path: Path) -> list[dict]:
    """Load all pre-compact snapshots."""
    snapshots = []

    for snapshot_file in ref_docs_path.glob("pre_summary_*.json"):
        try:
            with open(snapshot_file) as f:
                data = json.load(f)
                data["_file"] = str(snapshot_file)
                snapshots.append(data)
        except (OSError, json.JSONDecodeError):
            continue

    # Sort by timestamp
    snapshots.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return snapshots


def get_post_compact_sessions(db_path: Path, after_timestamp: float) -> list[dict]:
    """
    Find sessions with PREFLIGHT shortly after a compact (within 30 minutes).
    Uses reflex timestamp since session start_time is ISO string.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Find PREFLIGHT reflexes within 30 minutes after the snapshot
    # (compact can take time, and session creation may be delayed)
    window_end = after_timestamp + 1800  # 30 minutes

    cursor.execute(
        """
        SELECT
            s.session_id,
            s.ai_id,
            s.start_time,
            r.phase,
            r.know,
            r.uncertainty,
            r.context,
            r.timestamp as reflex_timestamp
        FROM sessions s
        JOIN reflexes r ON s.session_id = r.session_id
        WHERE r.timestamp > ? AND r.timestamp < ?
        AND r.phase = 'PREFLIGHT'
        AND r.round = 1
        ORDER BY r.timestamp ASC
    """,
        (after_timestamp, window_end),
    )

    sessions = []
    for row in cursor.fetchall():
        sessions.append(
            {
                "session_id": row[0],
                "ai_id": row[1],
                "start_time": row[2],
                "phase": row[3],
                "know": row[4],
                "uncertainty": row[5],
                "context": row[6],
                "reflex_timestamp": row[7],
            }
        )

    conn.close()
    return sessions


def get_post_check_vectors(db_path: Path, session_id: str) -> dict | None:
    """Get the first CHECK vectors for a session (post-context-load state)."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT know, uncertainty, context, timestamp
        FROM reflexes
        WHERE session_id = ? AND phase = 'CHECK'
        ORDER BY timestamp ASC
        LIMIT 1
    """,
        (session_id,),
    )

    row = cursor.fetchone()
    conn.close()

    if row:
        return {"know": row[0], "uncertainty": row[1], "context": row[2], "timestamp": row[3]}
    return None


def get_session_quality_metrics(db_path: Path, session_id: str) -> dict:
    """Get quality metrics for a session to filter out tests."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check for findings
    cursor.execute(
        """
        SELECT COUNT(*) FROM project_findings WHERE session_id = ?
    """,
        (session_id,),
    )
    findings_count = cursor.fetchone()[0]

    # Check for unknowns
    cursor.execute(
        """
        SELECT COUNT(*) FROM project_unknowns WHERE session_id = ?
    """,
        (session_id,),
    )
    unknowns_count = cursor.fetchone()[0]

    # Check for complete loop (POSTFLIGHT)
    cursor.execute(
        """
        SELECT COUNT(*) FROM reflexes WHERE session_id = ? AND phase = 'POSTFLIGHT'
    """,
        (session_id,),
    )
    has_postflight = cursor.fetchone()[0] > 0

    # Session duration
    cursor.execute(
        """
        SELECT MIN(timestamp), MAX(timestamp) FROM reflexes WHERE session_id = ?
    """,
        (session_id,),
    )
    row = cursor.fetchone()
    duration = (row[1] - row[0]) if row[0] and row[1] else 0

    conn.close()

    return {
        "findings_count": findings_count,
        "unknowns_count": unknowns_count,
        "has_postflight": has_postflight,
        "duration_seconds": duration,
        "has_work_evidence": findings_count > 0 or unknowns_count > 0,
    }


def _delta(after: float | None, before: float | None) -> float | None:
    """after - before, or None when either was not assessed (NULL): a delta against an invented 0 is not a delta."""
    return round(after - before, 3) if after is not None and before is not None else None


def analyze_compact_events(include_tests: bool = False, min_findings: int = 0, limit: int = 20) -> list[dict]:
    """
    Analyze compact events by matching pre-compact snapshots to post-compact sessions.

    Returns list of compact events with:
    - pre_vectors: Epistemic state before compact
    - post_preflight_vectors: State immediately after compact (before context load)
    - post_check_vectors: State after CHECK (context loaded)
    - loss: Delta from pre to post-preflight
    - recovery: Delta from post-preflight to post-check
    - net_loss: Delta from pre to post-check (permanent loss)
    """
    from empirica.config.path_resolver import get_session_db_path

    db_path = get_session_db_path()
    ref_docs_path = get_ref_docs_path()

    snapshots = load_pre_compact_snapshots(ref_docs_path)
    events = []

    for snapshot in snapshots[: limit * 2]:  # Check more than limit to allow for filtering
        # Parse snapshot timestamp
        ts_str = snapshot.get("timestamp", "")
        try:
            # Format: 2026-01-21T17-34-30
            dt = datetime.strptime(ts_str, "%Y-%m-%dT%H-%M-%S")
            snapshot_timestamp = dt.timestamp()
        except ValueError:
            continue

        # Get pre-compact vectors
        pre_vectors = snapshot.get("vectors_canonical") or snapshot.get("checkpoint", {})
        if not pre_vectors:
            live_state = snapshot.get("live_state", {})
            pre_vectors = live_state.get("vectors", {})

        if not pre_vectors.get("know"):
            continue

        # Find post-compact session
        post_sessions = get_post_compact_sessions(db_path, snapshot_timestamp)

        for post_session in post_sessions:
            ai_id = post_session["ai_id"]

            # Filter test sessions unless explicitly included
            if not include_tests and is_test_session(ai_id):
                continue

            # Get quality metrics
            quality = get_session_quality_metrics(db_path, post_session["session_id"])

            # Filter by quality
            if not include_tests:
                if quality["duration_seconds"] < MIN_SESSION_DURATION:
                    continue
                if min_findings > 0 and quality["findings_count"] < min_findings:
                    continue

            # Get post-CHECK vectors
            post_check = get_post_check_vectors(db_path, post_session["session_id"])

            # Calculate deltas
            pre_know = pre_vectors.get("know", 0)
            pre_unc = pre_vectors.get("uncertainty", 0)
            pre_ctx = pre_vectors.get("context", 0)

            post_pf_know = post_session.get("know", 0)
            post_pf_unc = post_session.get("uncertainty", 0)
            post_pf_ctx = post_session.get("context", 0)

            event = {
                "timestamp": ts_str,
                "pre_session_id": snapshot.get("session_id"),
                "post_session_id": post_session["session_id"],
                "ai_id": ai_id,
                "pre_vectors": {"know": pre_know, "uncertainty": pre_unc, "context": pre_ctx},
                "post_preflight_vectors": {"know": post_pf_know, "uncertainty": post_pf_unc, "context": post_pf_ctx},
                "immediate_loss": {
                    "know": round(post_pf_know - pre_know, 3),
                    "uncertainty": round(post_pf_unc - pre_unc, 3),
                    "context": round(post_pf_ctx - pre_ctx, 3) if post_pf_ctx and pre_ctx else None,
                },
                "quality": quality,
            }

            if post_check:
                # No default: a key present with a NULL value is "not assessed", and .get(k, 0) would not have replaced it anyway.
                post_ck_know = post_check.get("know")
                post_ck_unc = post_check.get("uncertainty")
                post_ck_ctx = post_check.get("context")

                event["post_check_vectors"] = {"know": post_ck_know, "uncertainty": post_ck_unc, "context": post_ck_ctx}
                event["recovery"] = {
                    "know": _delta(post_ck_know, post_pf_know),
                    "uncertainty": _delta(post_ck_unc, post_pf_unc),
                    "context": round(post_ck_ctx - post_pf_ctx, 3) if post_ck_ctx and post_pf_ctx else None,
                }
                event["net_loss"] = {
                    "know": _delta(post_ck_know, pre_know),
                    "uncertainty": _delta(post_ck_unc, pre_unc),
                    "context": round(post_ck_ctx - pre_ctx, 3) if post_ck_ctx and pre_ctx else None,
                }

            events.append(event)
            break  # Only take first matching post-compact session

        if len(events) >= limit:
            break

    return events


def calculate_aggregate_stats(events: list[dict]) -> dict:
    """Calculate aggregate statistics across all compact events."""
    if not events:
        return {"error": "No events to analyze"}

    # Collect deltas
    immediate_know_loss = []
    immediate_unc_gain = []
    recovery_know = []
    recovery_unc = []
    net_know_loss = []
    net_unc_gain = []

    for event in events:
        il = event.get("immediate_loss", {})
        if il.get("know") is not None:
            immediate_know_loss.append(il["know"])
        if il.get("uncertainty") is not None:
            immediate_unc_gain.append(il["uncertainty"])

        rec = event.get("recovery", {})
        if rec.get("know") is not None:
            recovery_know.append(rec["know"])
        if rec.get("uncertainty") is not None:
            recovery_unc.append(rec["uncertainty"])

        net = event.get("net_loss", {})
        if net.get("know") is not None:
            net_know_loss.append(net["know"])
        if net.get("uncertainty") is not None:
            net_unc_gain.append(net["uncertainty"])

    def stats(values):
        """Calculate mean, min, max, count for a list of numeric values."""
        if not values:
            return None
        return {
            "mean": round(sum(values) / len(values), 3),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "count": len(values),
        }

    return {
        "events_analyzed": len(events),
        "immediate_loss": {"know": stats(immediate_know_loss), "uncertainty": stats(immediate_unc_gain)},
        "recovery": {"know": stats(recovery_know), "uncertainty": stats(recovery_unc)},
        "net_loss": {"know": stats(net_know_loss), "uncertainty": stats(net_unc_gain)},
        "interpretation": {
            "avg_knowledge_lost": f"{abs(immediate_stats['mean']) * 100:.1f}%"
            if (immediate_stats := stats(immediate_know_loss))
            else "N/A",
            "avg_knowledge_recovered": f"{abs(recovery_stats['mean']) * 100:.1f}%"
            if (recovery_stats := stats(recovery_know))
            else "N/A",
            "avg_permanent_loss": f"{abs(net_stats['mean']) * 100:.1f}%"
            if (net_stats := stats(net_know_loss))
            else "N/A",
        },
    }


def format_human_readable(events: list[dict], stats: dict) -> str:
    """Format analysis results for human reading."""
    lines = []
    lines.append("=" * 60)
    lines.append("COMPACT ANALYSIS - Epistemic Loss Measurement")
    lines.append("=" * 60)
    lines.append("")

    # Summary stats
    lines.append("AGGREGATE STATISTICS")
    lines.append("-" * 40)
    interp = stats.get("interpretation", {})
    lines.append(f"Events analyzed: {stats.get('events_analyzed', 0)}")
    lines.append(f"Avg immediate knowledge loss: {interp.get('avg_knowledge_lost', 'N/A')}")
    lines.append(f"Avg knowledge recovered (via CHECK): {interp.get('avg_knowledge_recovered', 'N/A')}")
    lines.append(f"Avg permanent loss after recovery: {interp.get('avg_permanent_loss', 'N/A')}")
    lines.append("")

    # Recent events
    lines.append("RECENT COMPACT EVENTS")
    lines.append("-" * 40)

    for i, event in enumerate(events[:10], 1):
        lines.append(f"\n{i}. {event['timestamp']}")
        lines.append(f"   AI: {event['ai_id']}")

        pre = event.get("pre_vectors", {})
        post_pf = event.get("post_preflight_vectors", {})
        post_ck = event.get("post_check_vectors", {})

        lines.append(f"   Pre-compact:     know={pre.get('know', 'N/A'):.2f}, unc={pre.get('uncertainty', 'N/A'):.2f}")
        lines.append(
            f"   Post-PREFLIGHT:  know={post_pf.get('know', 'N/A'):.2f}, unc={post_pf.get('uncertainty', 'N/A'):.2f}"
        )

        if post_ck:
            lines.append(
                f"   Post-CHECK:      know={post_ck.get('know', 'N/A'):.2f}, unc={post_ck.get('uncertainty', 'N/A'):.2f}"
            )

        il = event.get("immediate_loss", {})
        lines.append(
            f"   Immediate loss:  Δknow={il.get('know', 'N/A'):+.2f}, Δunc={il.get('uncertainty', 'N/A'):+.2f}"
        )

        if event.get("net_loss"):
            nl = event["net_loss"]
            lines.append(
                f"   Net loss:        Δknow={nl.get('know', 'N/A'):+.2f}, Δunc={nl.get('uncertainty', 'N/A'):+.2f}"
            )

        q = event.get("quality", {})
        lines.append(
            f"   Quality: {q.get('findings_count', 0)} findings, {q.get('unknowns_count', 0)} unknowns, {'complete' if q.get('has_postflight') else 'incomplete'} loop"
        )

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


def handle_compact_analysis(args: argparse.Namespace) -> dict:
    """CLI handler for compact-analysis command."""
    try:
        events = analyze_compact_events(
            include_tests=getattr(args, "include_tests", False),
            min_findings=getattr(args, "min_findings", 0),
            limit=getattr(args, "limit", 20),
        )

        stats = calculate_aggregate_stats(events)

        output_format = getattr(args, "output", "human")

        if output_format == "json":
            return {"ok": True, "events": events, "stats": stats}
        else:
            print(format_human_readable(events, stats))
            return {"ok": True}

    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Analysis failed: {e}"}
