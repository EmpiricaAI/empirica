"""
Project Search Commands — semantic search over a project's docs & memory (Qdrant-backed).

Local single-project search by default. With ``--global`` it ALSO pulls the
``global_learnings`` pool + every other LOCAL project's memory/eidetic/episodic
collections (semantic top-k, this-machine Qdrant only) — a local cross-project
fallback, NOT a cross-practice/mesh search. True cross-practice retrieval across
the tenant's practices is Cortex's ``investigate`` / ``search_knowledge`` (the
Qdrant + glue layer is Cortex/mesh-support's lane).
"""

from __future__ import annotations

import json

from ..cli_utils import handle_cli_error


def _print_search_results_human(task, results, use_global):
    """Print search results in human-readable format."""
    print(f"🔎 Semantic search for: {task}")
    if "docs" in results:
        print("\n📄 Docs:")
        for i, d in enumerate(results["docs"], 1):
            print(f"  {i}. {d.get('doc_path')}  (score: {d.get('score'):.3f})")
    if "memory" in results:
        print("\n🧠 Memory:")
        for i, m in enumerate(results["memory"], 1):
            text = (m.get("text") or "")[:60]
            print(f"  {i}. [{m.get('type')}] {text}... (score: {m.get('score'):.3f})")
    if results.get("eidetic"):
        print("\n💎 Eidetic (facts):")
        for i, e in enumerate(results["eidetic"], 1):
            content = (e.get("content") or "")[:60]
            conf = e.get("confidence", 0)
            print(f"  {i}. [{e.get('type')}] {content}... (conf: {conf:.2f}, score: {e.get('score'):.3f})")
    if results.get("episodic"):
        print("\n📖 Episodic (session arcs):")
        for i, ep in enumerate(results["episodic"], 1):
            narr = (ep.get("narrative") or "")[:60]
            outcome = ep.get("outcome", "unknown")
            print(f"  {i}. [{outcome}] {narr}... (score: {ep.get('score'):.3f})")
    if use_global and results.get("global"):
        print("\n🌐 Global (cross-project learnings):")
        for i, g in enumerate(results["global"], 1):
            proj = g.get("project_id", "unknown")[:8]
            print(f"  {i}. [{g.get('type')}] {g.get('text', '')[:50]}... (proj: {proj}, score: {g.get('score'):.3f})")
    if use_global and results.get("cross_project"):
        print("\n🔗 Cross-project (other projects' knowledge):")
        for i, cp in enumerate(results["cross_project"], 1):
            proj = cp.get("project_id", "unknown")[:8]
            coll = cp.get("collection_type", "?")
            text = cp.get("text") or cp.get("content") or cp.get("narrative") or ""
            text = text[:60]
            score = cp.get("score", 0)
            print(f"  {i}. [{coll}] {text}... (proj: {proj}, score: {score:.3f})")


def handle_project_search_command(args):
    """Handle project-search command for semantic search over docs and memory."""
    try:
        from empirica.cli.utils.project_resolver import resolve_project_id
        from empirica.core.qdrant.vector_store import (
            init_collections,
            search,
            search_cross_project,
            search_global,
        )

        project_id = resolve_project_id(args.project_id)
        task = args.task
        kind = getattr(args, "type", "all")
        limit = getattr(args, "limit", 5)
        use_global = getattr(args, "global_search", False)

        init_collections(project_id)
        results = search(project_id, task, kind=kind, limit=limit)

        if use_global:
            results["global"] = search_global(task, limit=limit)
            cross_results = search_cross_project(
                task,
                exclude_project_id=project_id,
                limit=limit,
            )
            if cross_results:
                results["cross_project"] = cross_results

        if getattr(args, "output", "default") == "json":
            print(json.dumps({"ok": True, "results": results}, indent=2))
        else:
            _print_search_results_human(task, results, use_global)

        return None
    except Exception as e:
        handle_cli_error(e, "Project search", getattr(args, "verbose", False))
        return None
