"""falsifier-list: the practice's falsifiers, with counts by state.

Falsifiers are registered at PREFLIGHT or CHECK and adjudicated at POSTFLIGHT
(`falsifiers` key on each), so this is the only verb they need: a read. The
counts are what FALSIFIER_SPEC section 11 checks after 30 days of use. If the
tripped count stays near zero against the registered count, the design is wrong,
not merely unadopted.
"""

from __future__ import annotations

import json
import os


def handle_falsifier_list_command(args):
    from empirica.core import falsifiers as fz
    from empirica.data.session_database import SessionDatabase

    project_id = getattr(args, "project_id", None)
    if not project_id:
        from empirica.utils.session_resolver import InstanceResolver as R

        project_id = R.project_id_from_db(R.project_path() or os.getcwd())
    state = getattr(args, "state", "registered")
    limit = getattr(args, "limit", 50)

    db = SessionDatabase()
    try:
        params: tuple = (project_id,)
        where = "project_id = ?"
        if state != "all":
            where += " AND state = ?"
            params = (project_id, state)
        total = db.conn.execute(f"SELECT count(*) FROM falsifiers WHERE {where}", params).fetchone()[0]
        rows = db.conn.execute(
            "SELECT id, state, parent_type, parent_id, statement, query, registered_phase, registered_at,"
            f" tripped_by, evidence FROM falsifiers WHERE {where} ORDER BY registered_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        by_state = fz.counts(db, project_id)
    finally:
        db.close()

    items = [
        {
            "id": r[0],
            "state": r[1],
            "falsifies": f"{r[2]}:{r[3]}",
            "statement": r[4],
            "query": r[5],
            "registered_phase": r[6],
            "registered_at": r[7],
            "tripped_by": r[8],
            "evidence": r[9],
        }
        for r in rows
    ]
    result = {
        "ok": True,
        "project_id": project_id,
        "state": state,
        "counts": by_state,
        "total_matching": total,
        "truncated": total > len(items),
        "falsifiers": items,
    }
    if getattr(args, "output", "human") == "json":
        print(json.dumps(result, indent=2))
        return None
    c = by_state
    print(
        f"Falsifiers: {c.get('registered', 0)} open · {c.get('tripped', 0)} tripped · "
        f"{c.get('survived', 0)} survived · {c.get('expired', 0)} expired"
    )
    if result["truncated"]:
        print(f"showing {len(items)} of {total} — raise --limit")
    for it in items:
        mark = "q" if it["query"] else " "
        print(f"  [{it['state']:<10}] {it['id'][:8]} {mark} {it['falsifies'][:20]}  {it['statement'][:80]}")
    if not items:
        print(f"  (none with state {state})")
    return None
