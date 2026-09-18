"""
Doc Planner - computes documentation completeness and suggests updates
based on project epistemic memory (findings/unknowns/mistakes) and
semantic index (docs/SEMANTIC_INDEX.yaml).
"""

from __future__ import annotations

import os


def _load_semantic_index(root: str) -> dict[str, dict]:
    """Load semantic index (per-project, with graceful fallback)"""
    from empirica.config.semantic_index_loader import load_semantic_index

    index = load_semantic_index(root)
    if not index:
        return {}
    return index.get("index", {}) or {}


def _find_cli_reference(root: str) -> str | None:
    ref_dir = os.path.join(root, "docs", "reference")
    if not os.path.isdir(ref_dir):
        return None
    for name in os.listdir(ref_dir):
        if name.lower().startswith("cli_commands") or "cli" in name.lower():
            return os.path.join("docs", "reference", name)
    return None


def _find_doc_by_tags(index: dict, target_tags: list[str]) -> str | None:
    """Find the first doc in the index matching any of the target tags."""
    for rel, meta in index.items():
        tags = [t.lower() for t in meta.get("tags", [])]
        if any(t in tags for t in target_tags):
            return rel
    return None


def _suggest_by_tag(index, suggest_fn, num_mistakes, num_unknowns, num_findings):
    """Suggest doc updates based on memory state and semantic index tags."""
    if num_mistakes:
        rel = _find_doc_by_tags(index, ["troubleshooting"])
        if rel:
            suggest_fn(rel, f"{num_mistakes} mistakes logged → add prevention guidance")
    if num_unknowns:
        rel = _find_doc_by_tags(index, ["investigation", "unknowns"])
        if rel:
            suggest_fn(rel, f"{num_unknowns} unresolved unknowns → add resolution patterns or notes")
    if num_findings:
        rel = _find_doc_by_tags(index, ["project", "bootstrap", "breadcrumbs"])
        if rel:
            suggest_fn(rel, f"{num_findings} findings → update knowledge sections")


def compute_doc_plan(project_id: str, session_id: str | None = None, goal_id: str | None = None) -> dict:
    """
    Heuristic planner that:
    - Loads semantic index
    - Loads project memory (findings/unknowns/mistakes)
    - Computes a rough completeness score
    - Suggests doc updates (paths + reasons)
    """
    from empirica.data.session_database import SessionDatabase

    root = os.getcwd()
    index = _load_semantic_index(root)

    db = SessionDatabase()
    # Memory
    findings = db.get_project_findings(project_id)
    unknowns = db.get_project_unknowns(project_id)
    # mistakes via join
    cur = db.conn.cursor()
    cur.execute(
        """
        SELECT m.id, m.mistake, m.prevention
        FROM mistakes_made m
        JOIN sessions s ON m.session_id = s.session_id
        WHERE s.project_id = ?
        ORDER BY m.created_timestamp DESC
        """,
        (project_id,),
    )
    mistakes = [dict(row) for row in cur.fetchall()]

    # Basic metrics
    num_findings = len(findings)
    num_unknowns = len(unknowns)
    num_mistakes = len(mistakes)

    # Very simple scoring: encourage mapping memory to docs
    # Start from 0.6, penalize if lots of items likely need docs
    score = 0.6
    if num_findings > 5:
        score -= 0.1
    if num_unknowns > 3:
        score -= 0.1
    if num_mistakes > 2:
        score -= 0.1
    score = max(0.0, min(1.0, score))

    suggestions: list[dict] = []

    # Helpers to add suggestions if indexed doc exists
    def _suggest_if_present(rel: str, reason: str) -> None:
        if rel in index:
            suggestions.append(
                {
                    "doc_path": rel,
                    "reason": reason,
                    "tags": index[rel].get("tags", []),
                }
            )

    # Suggest core docs based on memory state
    _suggest_by_tag(index, _suggest_if_present, num_mistakes, num_unknowns, num_findings)

    # Suggest CLI reference if we detect new CLI (project-search/embed exist in codebase)
    cli_ref = _find_cli_reference(root)
    if cli_ref and not any(s["doc_path"] == cli_ref for s in suggestions):
        suggestions.append(
            {
                "doc_path": cli_ref,
                "reason": "New CLI (project-embed, project-search) → add usage examples",
                "tags": ["cli", "reference"],
            }
        )

    # Also include any reference docs explicitly added to project. They live in
    # epistemic_sources as source_type='pointer' since migration 046; this read
    # stayed on project_reference_docs, which 047 drops, so on a migrated store
    # the planner raised "no such table" (and on a store an older binary had
    # recreated it in, it silently returned none).
    cur.execute(
        """
        SELECT source_url AS doc_path,
               json_extract(source_metadata, '$.doc_type') AS doc_type,
               description
        FROM epistemic_sources
        WHERE project_id = ? AND source_type = 'pointer' AND COALESCE(archived, 0) = 0
        ORDER BY discovered_at DESC
        """,
        (project_id,),
    )
    refdocs = [dict(row) for row in cur.fetchall()]
    db.close()

    plan = {
        "doc_completeness_score": round(score, 2),
        "suggested_updates": suggestions,
        "unmapped_findings": [f.get("finding") for f in findings[:10]],
        "resolved_unknowns_missing_docs": [u.get("unknown") for u in unknowns if u.get("is_resolved")][:10],
        "mistakes_missing_prevention_docs": [m.get("mistake") for m in mistakes if not m.get("prevention")][:10],
        "reference_docs": refdocs,
    }
    return plan
