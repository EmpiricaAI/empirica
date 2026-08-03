#!/usr/bin/env python3
"""Evaluate a prompt change against RECORDED BEHAVIOUR, not against its own words.

The trim programme kept stalling because every participant was grepping — three
practitioners produced three different counts of which obligations were exposed
from the same files. Greps answer "did the words survive". The question that
matters is "did the behaviour survive", and no amount of string matching reaches
it.

Empirica already records the behaviour. This reads it:

    reflexes                every PREFLIGHT/CHECK/POSTFLIGHT, phase-tagged
    calibration_trajectory  self_assessed vs grounded vs GAP, per vector
    artifact tables         findings / unknowns / dead-ends / mistakes / ...

So a prompt change is an intervention, and evaluation is a before/after
comparison on real work rather than a synthetic scenario suite.

**The metric no grep can reach** is the calibration gap. A prompt that stops
keeping beliefs honest widens `self_assessed - grounded` while every phrase it
was audited for survives intact.

Usage:
    prompt_eval.py --window 2026-08-01..2026-08-03
    prompt_eval.py --before 2026-07-25..2026-08-01 --after 2026-08-01..2026-08-03

Observational, not causal — see the caveat it prints. It answers "did behaviour
shift", never "the prompt caused it".
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

ARTIFACT_TABLES = (
    "project_findings",
    "project_unknowns",
    "project_dead_ends",
    "mistakes_made",
    "decisions",
    "assumptions",
)

# Below this many transactions a window cannot support a claim, and the tool
# says so rather than printing a ratio that looks like evidence.
MIN_TRANSACTIONS = 5


def _parse_window(spec: str) -> tuple[float, float]:
    try:
        start, end = spec.split("..", 1)
        s = dt.datetime.strptime(start.strip(), "%Y-%m-%d")
        e = dt.datetime.strptime(end.strip(), "%Y-%m-%d") + dt.timedelta(days=1)
    except ValueError as exc:
        raise SystemExit(f"bad window {spec!r} — expected YYYY-MM-DD..YYYY-MM-DD ({exc})") from exc
    return s.timestamp(), e.timestamp()


def assert_timestamps_are_comparable(conn: sqlite3.Connection) -> None:
    """Refuse to run on mixed-type timestamps. This is not defensive padding.

    SQLite sorts TEXT after every number, so a single text row in a numeric
    column wins every `ORDER BY ts DESC` and falls outside every numeric
    `BETWEEN`. Found live in this store: 13 legacy rows dated 2025-12-31 sat at
    the top of every recency query, so the "most recent findings" injected into
    each session were eight months stale — and nothing errored.

    A windowing tool that ran anyway would silently exclude those rows from
    every window and report the result as measurement.
    """
    dirty = []
    for table in ARTIFACT_TABLES:
        try:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE typeof(created_timestamp)='text'"  # noqa: S608
            ).fetchone()[0]
        except sqlite3.OperationalError:
            continue
        if n:
            dirty.append(f"{table}: {n}")
    if dirty:
        raise SystemExit(
            "refusing to measure — these tables hold TEXT timestamps in a numeric "
            "column, which sort above every number and fall outside every window:\n  "
            + "\n  ".join(dirty)
            + "\nNormalise them first; a window computed over these is wrong, not approximate."
        )


def _count(conn: sqlite3.Connection, table: str, lo: float, hi: float) -> int:
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE created_timestamp >= ? AND created_timestamp < ?",  # noqa: S608
            (lo, hi),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def measure(conn: sqlite3.Connection, lo: float, hi: float) -> dict:
    """Behavioural metrics for one window."""
    phases = dict(
        conn.execute(
            "SELECT phase, COUNT(*) FROM reflexes WHERE timestamp >= ? AND timestamp < ? GROUP BY phase",
            (lo, hi),
        ).fetchall()
    )
    preflights = phases.get("PREFLIGHT", 0)
    artifacts = {t: _count(conn, t, lo, hi) for t in ARTIFACT_TABLES}
    total_artifacts = sum(artifacts.values())

    gaps = conn.execute(
        "SELECT AVG(ABS(gap)), COUNT(*) FROM calibration_trajectory "
        "WHERE timestamp >= ? AND timestamp < ? AND gap IS NOT NULL",
        (lo, hi),
    ).fetchone()

    # Type breadth: how many artifact KINDS were used. Collapse toward
    # findings-only is a documented degradation the raw count cannot see.
    kinds_used = sum(1 for v in artifacts.values() if v)

    return {
        "preflights": preflights,
        "checks": phases.get("CHECK", 0),
        "postflights": phases.get("POSTFLIGHT", 0),
        "artifacts": artifacts,
        "total_artifacts": total_artifacts,
        "artifacts_per_transaction": (total_artifacts / preflights) if preflights else None,
        "check_rate": (phases.get("CHECK", 0) / preflights) if preflights else None,
        "postflight_rate": (phases.get("POSTFLIGHT", 0) / preflights) if preflights else None,
        "kinds_used": kinds_used,
        "mean_abs_calibration_gap": gaps[0],
        "calibration_points": gaps[1],
    }


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def _report_window(name: str, m: dict) -> None:
    print(f"\n── {name} " + "─" * max(0, 56 - len(name)))
    print(f"  transactions (PREFLIGHT)      {m['preflights']}")
    if m["preflights"] < MIN_TRANSACTIONS:
        print(f"  ⚠ below {MIN_TRANSACTIONS} transactions — too few to support a claim")
    print(f"  CHECK rate                    {_fmt(m['check_rate'])}   ({m['checks']} checks)")
    print(f"  POSTFLIGHT rate               {_fmt(m['postflight_rate'])}   ({m['postflights']})")
    print(f"  artifacts / transaction       {_fmt(m['artifacts_per_transaction'])}   ({m['total_artifacts']} total)")
    print(f"  artifact kinds used           {m['kinds_used']} of {len(ARTIFACT_TABLES)}")
    print(f"  mean |calibration gap|        {_fmt(m['mean_abs_calibration_gap'])}   ({m['calibration_points']} pts)")
    for t, n in sorted(m["artifacts"].items(), key=lambda kv: -kv[1]):
        if n:
            print(f"      {t:24} {n}")


def _delta(before: dict, after: dict) -> None:
    print("\n── delta " + "─" * 48)
    rows = [
        ("artifacts / transaction", "artifacts_per_transaction", "higher is richer logging"),
        ("CHECK rate", "check_rate", "gate discipline"),
        ("POSTFLIGHT rate", "postflight_rate", "transactions closed"),
        ("mean |calibration gap|", "mean_abs_calibration_gap", "LOWER is better — belief vs evidence"),
    ]
    for label, key, note in rows:
        b, a = before[key], after[key]
        if b is None or a is None:
            print(f"  {label:26} n/a")
            continue
        arrow = "→"
        change = a - b
        sign = "+" if change >= 0 else ""
        print(f"  {label:26} {b:.2f} {arrow} {a:.2f}  ({sign}{change:.2f})   {note}")
    print(f"  artifact kinds used        {before['kinds_used']} {'→'} {after['kinds_used']}")

    thin = [n for n, m in (("before", before), ("after", after)) if m["preflights"] < MIN_TRANSACTIONS]
    if thin:
        print(f"\n  ⚠ {', '.join(thin)} window has fewer than {MIN_TRANSACTIONS} transactions.")
        print("    Read the deltas as anecdote, not measurement.")

    print("\n  OBSERVATIONAL, NOT CAUSAL. Other things changed between these windows —")
    print("  the work itself, the codebase, the operator. This says whether behaviour")
    print("  shifted. It does not say the prompt shifted it.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate a prompt change against recorded behaviour")
    ap.add_argument("--window", help="Single window: YYYY-MM-DD..YYYY-MM-DD")
    ap.add_argument("--before", help="Baseline window")
    ap.add_argument("--after", help="Comparison window")
    ap.add_argument("--db", help="Session DB path (default: the active project's)")
    args = ap.parse_args()

    if not args.window and not (args.before and args.after):
        ap.error("give --window, or both --before and --after")

    if args.db:
        db_path = Path(args.db)
    else:
        from empirica.config.path_resolver import get_session_db_path

        db_path = Path(get_session_db_path())
    if not db_path.exists():
        raise SystemExit(f"no session DB at {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    assert_timestamps_are_comparable(conn)

    print(f"prompt-eval · {db_path}")
    if args.window:
        lo, hi = _parse_window(args.window)
        _report_window(args.window, measure(conn, lo, hi))
        print("\n  Baseline only. Re-run with --before/--after once a change has landed.")
    else:
        b_lo, b_hi = _parse_window(args.before)
        a_lo, a_hi = _parse_window(args.after)
        before, after = measure(conn, b_lo, b_hi), measure(conn, a_lo, a_hi)
        _report_window(f"before  {args.before}", before)
        _report_window(f"after   {args.after}", after)
        _delta(before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
