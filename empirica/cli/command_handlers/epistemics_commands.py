"""
CLI command handlers for epistemic trajectory queries.
"""

import json
import sys

from empirica.data.session_database import SessionDatabase


def _fmt(value: float | None) -> str:
    """A vector for display; NULL means "not assessed", which prints as n/a rather than failing the whole trajectory."""
    return "n/a" if value is None else f"{value:.2f}"


def handle_epistemics_stats_command(args):
    """
    Show detailed epistemic trajectory for a session (epistemics-show).

    Usage:
        empirica epistemics-show --session-id <UUID> --output json
        empirica epistemics-show --session-id <UUID> --phase PREFLIGHT
    """
    try:
        session_id = getattr(args, "session_id", None)
        phase_filter = getattr(args, "phase", None)
        output_format = getattr(args, "output", "json")

        # UNIFIED: Auto-derive session_id if not provided
        if not session_id:
            from empirica.utils.session_resolver import InstanceResolver as R

            session_id = R.session_id()

        if not session_id:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No active transaction and --session-id not provided",
                        "hint": "Either run PREFLIGHT first, or provide --session-id explicitly",
                    }
                )
            )
            sys.exit(1)

        # Get reflexes from database
        db = SessionDatabase()
        cursor = db.conn.cursor()

        # First get session info
        cursor.execute("SELECT project_id, ai_id FROM sessions WHERE session_id = ?", (session_id,))
        session_row = cursor.fetchone()

        if not session_row:
            print(json.dumps({"ok": False, "error": f"Session {session_id} not found"}))
            db.close()
            sys.exit(1)

        project_id = session_row["project_id"]
        ai_id = session_row["ai_id"]

        # Get reflexes with optional phase filter
        if phase_filter:
            cursor.execute(
                """
                SELECT phase, engagement, know, do, context, clarity, coherence,
                       signal, density, state, change, completion, impact, uncertainty,
                       reasoning, timestamp
                FROM reflexes
                WHERE session_id = ? AND phase = ?
                ORDER BY timestamp ASC
            """,
                (session_id, phase_filter),
            )
        else:
            cursor.execute(
                """
                SELECT phase, engagement, know, do, context, clarity, coherence,
                       signal, density, state, change, completion, impact, uncertainty,
                       reasoning, timestamp
                FROM reflexes
                WHERE session_id = ?
                ORDER BY timestamp ASC
            """,
                (session_id,),
            )

        reflexes = []
        for row in cursor.fetchall():
            reflexes.append(
                {
                    "phase": row["phase"],
                    "vectors": {
                        "engagement": row["engagement"],
                        "know": row["know"],
                        "do": row["do"],
                        "context": row["context"],
                        "clarity": row["clarity"],
                        "coherence": row["coherence"],
                        "signal": row["signal"],
                        "density": row["density"],
                        "state": row["state"],
                        "change": row["change"],
                        "completion": row["completion"],
                        "impact": row["impact"],
                        "uncertainty": row["uncertainty"],
                    },
                    "reasoning": row["reasoning"],
                    "timestamp": row["timestamp"],
                }
            )

        db.close()

        if output_format == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "project_id": project_id,
                        "ai_id": ai_id,
                        "count": len(reflexes),
                        "phase_filter": phase_filter,
                        "trajectories": reflexes,
                    },
                    indent=2,
                )
            )
        else:
            print(f"\n📊 Epistemic Trajectory for Session: {session_id}")
            print(f"{'=' * 70}")
            print(f"Project: {project_id}")
            print(f"AI: {ai_id}")
            if phase_filter:
                print(f"Phase Filter: {phase_filter}")
            print(f"\nTotal Reflexes: {len(reflexes)}\n")

            for i, reflex in enumerate(reflexes, 1):
                print(f"{i}. Phase: {reflex['phase']}")
                print(f"   Time: {reflex['timestamp']}")
                vectors = reflex["vectors"]
                print(f"   Know: {_fmt(vectors['know'])}, Uncertainty: {_fmt(vectors['uncertainty'])}")
                print(f"   Context: {_fmt(vectors['context'])}, Completion: {_fmt(vectors['completion'])}")
                if reflex["reasoning"]:
                    print(f"   Reasoning: {reflex['reasoning'][:80]}...")
                print()

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


def handle_epistemics_list_command(args):
    """
    List epistemic trajectories for a session.

    Usage:
        empirica epistemics-list --session-id <UUID> --output json
    """
    try:
        from empirica.cli.cli_utils import handle_cli_error
        from empirica.data.session_database import SessionDatabase

        session_id = getattr(args, "session_id", None)
        output_format = getattr(args, "output", "json")

        # UNIFIED: Auto-derive session_id if not provided
        if not session_id:
            from empirica.utils.session_resolver import InstanceResolver as R

            session_id = R.session_id()

        if not session_id:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No active transaction and --session-id not provided",
                        "hint": "Either run PREFLIGHT first, or provide --session-id explicitly",
                    }
                )
            )
            sys.exit(1)

        # Get trajectories directly from database
        db = SessionDatabase()
        cursor = db.conn.cursor()

        # First get session info
        cursor.execute("SELECT project_id, ai_id FROM sessions WHERE session_id = ?", (session_id,))
        session_row = cursor.fetchone()

        if not session_row:
            print(json.dumps({"ok": False, "error": f"Session {session_id} not found"}))
            db.close()
            sys.exit(1)

        project_id = session_row["project_id"]
        ai_id = session_row["ai_id"]

        # Get all reflexes for this session
        cursor.execute(
            """
            SELECT phase, engagement, know, do, context, clarity, coherence,
                   signal, density, state, change, completion, impact, uncertainty,
                   reasoning, timestamp
            FROM reflexes
            WHERE session_id = ?
            ORDER BY timestamp ASC
        """,
            (session_id,),
        )

        reflexes = []
        for row in cursor.fetchall():
            reflexes.append(
                {
                    "phase": row["phase"],
                    "vectors": {
                        "engagement": row["engagement"],
                        "know": row["know"],
                        "do": row["do"],
                        "context": row["context"],
                        "clarity": row["clarity"],
                        "coherence": row["coherence"],
                        "signal": row["signal"],
                        "density": row["density"],
                        "state": row["state"],
                        "change": row["change"],
                        "completion": row["completion"],
                        "impact": row["impact"],
                        "uncertainty": row["uncertainty"],
                    },
                    "reasoning": row["reasoning"],
                    "timestamp": row["timestamp"],
                }
            )

        db.close()

        if output_format == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "project_id": project_id,
                        "ai_id": ai_id,
                        "count": len(reflexes),
                        "trajectories": reflexes,
                    },
                    indent=2,
                )
            )
        else:
            print(f"📊 Epistemic Trajectories for Session: {session_id}")
            print(f"   Project: {project_id}")
            print(f"   AI: {ai_id}")
            print(f"   Count: {len(reflexes)}\n")
            for t in reflexes:
                print(f"   Phase: {t['phase']}")
                print(f"   Time: {t['timestamp']}")
                vectors = t.get("vectors", {})
                if vectors:
                    print(f"   Know: {vectors.get('know', 'N/A')}, Uncertainty: {vectors.get('uncertainty', 'N/A')}")
                print()

    except Exception as e:
        from empirica.cli.cli_utils import handle_cli_error

        handle_cli_error(e, "List epistemics", getattr(args, "verbose", False))
