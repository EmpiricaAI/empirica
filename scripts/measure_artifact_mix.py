#!/usr/bin/env python3
"""Artifact type mix per practice, read-only: the numbers behind docs/reference/ARTIFACT_GRAPH.md section 8.

For every active practice in the workspace registry, opens its .empirica/sessions/sessions.db read-only and counts each
artifact type, the closed share of each lifecycle, and the sourced_from links. The practice's type comes from its own
.empirica/project.yaml, not the registry (registry rows may still hold legacy values).

    python scripts/measure_artifact_mix.py                    # JSON, one object per practice
    python scripts/measure_artifact_mix.py --min-findings 100 # only practices with that many findings

First run 2026-11-13 (33 practices with a store): assumptions 0 to 5 per 100 findings in every practice, decision outcomes recorded
for at most about 5% of decisions, 70 sourced_from links across 15,462 findings in the 14 practices with 100 or more (27 of them
in one practice). Lifetime counts: compare rates over a window,
not totals, when measuring a change.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from empirica.core.project_types import project_file_labels

TABLES = {
    "finding": "project_findings",
    "unknown": "project_unknowns",
    "assumption": "assumptions",
    "decision": "decisions",
    "dead_end": "project_dead_ends",
    "mistake": "mistakes_made",
    "lesson": "lessons",
    "falsifier": "falsifiers",
    "source": "epistemic_sources",
    "goal": "goals",
    "edge": "artifact_edges",
}
CLOSED = {
    "findings_resolved": "SELECT COUNT(*) FROM project_findings WHERE is_resolved = 1",
    "findings_retracted": "SELECT COUNT(*) FROM project_findings WHERE resolution_kind = 'retracted'",
    "unknowns_resolved": "SELECT COUNT(*) FROM project_unknowns WHERE is_resolved = 1",
    "assumptions_closed": "SELECT COUNT(*) FROM assumptions WHERE status IN ('verified', 'falsified', 'expired')",
    "decisions_assessed": "SELECT COUNT(*) FROM decisions WHERE outcome IS NOT NULL",
    "sourced_from_links": "SELECT COUNT(*) FROM artifact_edges WHERE relation = 'sourced_from'",
}


def _count(con: sqlite3.Connection, query: str) -> int | None:
    try:
        return con.execute(query).fetchone()[0]
    except sqlite3.Error:
        return None  # a table this store does not have


def measure(workspace_db: Path) -> list[dict]:
    ws = sqlite3.connect(f"file:{workspace_db}?mode=ro", uri=True)
    rows = ws.execute(
        "SELECT name, project_type, trajectory_path FROM global_projects WHERE status = 'active'"
    ).fetchall()
    out, seen = [], set()
    for name, registry_type, trajectory in rows:
        root = Path(trajectory).parent if str(trajectory).endswith(".empirica") else Path(trajectory)
        db = root / ".empirica" / "sessions" / "sessions.db"
        if not db.is_file() or str(db) in seen:
            continue
        seen.add(str(db))
        project_type, domain = project_file_labels(root)
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        counts = {t: _count(con, f"SELECT COUNT(*) FROM {table}") for t, table in TABLES.items()}
        closed = {k: _count(con, q) for k, q in CLOSED.items()}
        out.append(
            {
                "name": name,
                "type": project_type or f"({registry_type or 'unset'})",
                "domain": domain,
                **counts,
                **closed,
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace-db", type=Path, default=Path.home() / ".empirica" / "workspace" / "workspace.db")
    parser.add_argument("--min-findings", type=int, default=0)
    args = parser.parse_args()
    rows = [r for r in measure(args.workspace_db) if (r["finding"] or 0) >= args.min_findings]
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
