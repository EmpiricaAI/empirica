"""`empirica sources-sanctify` — classify the source corpus + recommend hygiene actions.

Report-only by default (deletions are a judgment — ARTIFACT_HYGIENE). Gathers the
active sources, their ``sourced_from`` references, content-hash groups, and
canonical-path existence; classifies each dead / duplicate / zombie / valid; and
prints the roll-up + recommendations. Human + JSON. The DB/FS gathering is
fail-soft — a read error degrades a signal to empty rather than raising.
"""

from __future__ import annotations

import json
import os


def _gather(db) -> tuple[list[dict], set, dict, set]:
    """Read active sources + references + hash groups + missing paths. Fail-soft."""
    sources: list[dict] = []
    referenced: set = set()
    hash_counts: dict = {}
    missing: set = set()
    try:
        cur = db.conn.execute(
            "SELECT id, title, content_hash, canonical_path FROM epistemic_sources "
            "WHERE archived IS NULL OR archived = 0"
        )
        for sid, title, chash, cpath in cur.fetchall():
            sources.append({"id": sid, "title": title, "content_hash": chash, "canonical_path": cpath})
            if chash:
                hash_counts[chash] = hash_counts.get(chash, 0) + 1
            if cpath and not os.path.exists(cpath):
                missing.add(cpath)
    except Exception:
        pass
    try:
        cur = db.conn.execute("SELECT DISTINCT to_id FROM artifact_edges WHERE relation = 'sourced_from'")
        referenced = {row[0] for row in cur.fetchall()}
    except Exception:
        pass
    return sources, referenced, hash_counts, missing


def handle_sources_sanctify_command(args) -> None:
    """Render the sanctification report (human or JSON)."""
    from empirica.core.sources import classify_sources, summarize
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    try:
        sources, referenced, hash_counts, missing = _gather(db)
    finally:
        db.close()

    classifications = classify_sources(sources, referenced, hash_counts, missing)
    summary = summarize(classifications)
    flagged = [c for c in classifications if c["verdict"] != "valid"]

    if getattr(args, "output", "human") == "json":
        print(json.dumps({"summary": summary, "flagged": flagged}, indent=2))
        return

    print("\n🧹 Source Sanctification (report — no deletions)")
    print("━" * 60)
    if summary["total"] == 0:
        print("  (no active sources)")
        print("━" * 60)
        return
    order = ", ".join(f"{k}={v}" for k, v in sorted(summary["by_verdict"].items()))
    print(f"Active sources:  {summary['total']}   ({order})")
    print(f"Auto-safe (dead/duplicate archival): {summary['auto_safe']}")
    if flagged:
        print("\nFlagged:")
        for c in flagged:
            print(f"  [{c['verdict']:<9}] {c['title'][:52]}")
            print(f"              → {c['recommendation']}")
    else:
        print("\n  ✅ corpus clean — every active source is referenced, present, and unique")
    print("━" * 60)
    print("Report only. Retire dead/duplicate via `source-archive`; review zombies before retiring.")
    print("━" * 60)
