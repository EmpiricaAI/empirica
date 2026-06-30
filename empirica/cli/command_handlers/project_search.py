"""
Project Search — semantic retrieval over a project's docs + epistemic memory (Qdrant).

WHAT IT DOES
  - Default: searches THIS project's docs + memory/eidetic/episodic collections.
  - ``--global``: ALSO pulls the ``global_learnings`` pool + every other LOCAL
    project's collections on this machine (semantic top-k, de-duplicated, ranked).

WHEN AN AI SHOULD REACH FOR IT  (vs the mesh)
  It is LOCAL + SEMANTIC-ONLY: this-machine Qdrant, cosine top-k, no
  ``artifact_edges`` traversal. It does NOT span the mesh — other practitioners'
  machines, or the tenant's practices retrieved server-side.

  - Cortex CONNECTED: prefer the mesh — ``cortex investigate`` /
    ``search_knowledge`` — for retrieval that should span practices across the
    tenant. Use project-search for THIS project's own docs/memory (and
    ``--global`` for quick local cross-project recall).
  - Cortex NOT connected (empirica-only install): there is no mesh.
    project-search (incl. ``--global``) IS the retrieval + cross-project tool —
    reach for it freely; here it's not the degraded choice, it's the only one.

  Rule of thumb: project-search = always-available LOCAL retrieval; the mesh =
  cross-PRACTICE retrieval WHEN Cortex is present. Don't assume the mesh exists;
  do prefer it for cross-practice scope when it does.
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
    """Run project-search: this project's docs + memory, plus (``--global``) a
    LOCAL cross-project pull. See the module docstring for when to use this vs
    the mesh (``cortex investigate``) — and note the mesh is absent in
    empirica-only installs, where this is the cross-project tool, not a fallback."""
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
            # --global stays LOCAL: global_learnings pool + other local projects'
            # collections (this machine, semantic). Cross-PRACTICE/mesh retrieval
            # is `cortex investigate` when Cortex is connected; this is the
            # cross-project tool in cortex-less installs. See module docstring.
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
