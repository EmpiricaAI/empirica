"""
Concept graph command handlers (experimental).

Part of Phase 2: Epistemic Prediction System - Concept Co-occurrence Graphs.

NOTE: Implementation moved to empirica-prediction package.
Install with: pip install empirica-prediction
"""

import json
from pathlib import Path


def _get_concept_graph():
    """Import ConceptGraph from empirica-prediction package."""
    try:
        from empirica_prediction.concepts.graph import ConceptGraph  # pyright: ignore[reportMissingImports]
        return ConceptGraph
    except ImportError:
        print("Error: empirica-prediction package not installed.")
        print("Install with: pip install empirica-prediction")
        print("Or from source: pip install -e /path/to/empirica-prediction")
        return None


def _get_project_id(args):
    """Get project ID from args or via the canonical local resolver.

    project.yaml is authoritative, sessions.db is the fallback (see
    `_get_project_id_from_local_db`). Previously this read a non-existent
    `.empirica/project.json` at both the resolved-path and CWD sites.
    """
    if hasattr(args, 'project_id') and args.project_id:
        return args.project_id

    from empirica.utils.session_resolver import _get_project_id_from_local_db

    # Prefer the unified context resolver's project path; else CWD.
    project_path = None
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        project_path = R.context().get('project_path')
    except Exception:
        pass

    try:
        return _get_project_id_from_local_db(Path(project_path) if project_path else Path.cwd())
    except Exception:
        return None


def _get_db_path():
    """Get the database path via unified context resolver."""
    from empirica.config.path_resolver import get_session_db_path
    return get_session_db_path()


def handle_concept_build(args):
    """Build concept graph from findings/unknowns."""
    ConceptGraph = _get_concept_graph()
    if not ConceptGraph:
        return

    project_id = _get_project_id(args)
    if not project_id:
        print("Error: Could not determine project ID. Use --project-id or run from a project directory.")
        return

    db_path = _get_db_path()
    if not db_path.exists():
        print("Error: No database found. Run some sessions first.")
        return

    print(f"Building concept graph for project {project_id[:12]}...")
    graph = ConceptGraph(project_id, db_path)
    result = graph.build_from_sources(overwrite=args.overwrite)

    if args.output == 'json':
        print(json.dumps(result, indent=2))
    else:
        if result.get("ok"):
            print("\nConcept Graph Built:")
            print(f"  Sources processed: {result.get('sources_processed', 0)}")
            print(f"  Concepts extracted: {result.get('concepts_extracted', 0)}")
            print(f"  Nodes stored: {result.get('nodes_stored', 0)}")
            print(f"  Edges stored: {result.get('edges_stored', 0)}")
        else:
            print(f"Error: {result.get('note', 'Unknown error')}")


def handle_concept_stats(args):
    """Show concept graph statistics."""
    ConceptGraph = _get_concept_graph()
    if not ConceptGraph:
        return

    project_id = _get_project_id(args)
    if not project_id:
        print("Error: Could not determine project ID. Use --project-id or run from a project directory.")
        return

    db_path = _get_db_path()
    if not db_path.exists():
        print("Error: No database found. Run 'empirica concept-build' first.")
        return

    graph = ConceptGraph(project_id, db_path)
    stats = graph.get_stats()

    if args.output == 'json':
        print(json.dumps(stats, indent=2))
    else:
        print(f"\n{'='*50}")
        print("CONCEPT GRAPH STATISTICS")
        print(f"{'='*50}\n")

        nodes = stats.get("nodes", {})
        edges = stats.get("edges", {})

        print("Concept Nodes:")
        print(f"  Total concepts: {nodes.get('count', 0)}")
        print(f"  Total mentions: {nodes.get('total_mentions', 0)}")
        print(f"  Avg frequency: {nodes.get('avg_frequency', 0):.1f}")
        print(f"  Avg impact: {nodes.get('avg_impact', 0):.2f}")

        print("\nCo-occurrence Edges:")
        print(f"  Total edges: {edges.get('count', 0)}")
        print(f"  Total co-occurrences: {edges.get('total_co_occurrences', 0)}")
        print(f"  Avg weight: {edges.get('avg_weight', 0):.2f}")


def handle_concept_top(args):
    """Show top concepts by frequency."""
    ConceptGraph = _get_concept_graph()
    if not ConceptGraph:
        return

    project_id = _get_project_id(args)
    if not project_id:
        print("Error: Could not determine project ID. Use --project-id or run from a project directory.")
        return

    db_path = _get_db_path()
    if not db_path.exists():
        print("Error: No database found. Run 'empirica concept-build' first.")
        return

    graph = ConceptGraph(project_id, db_path)
    concepts = graph.get_top_concepts(limit=args.limit)

    if args.output == 'json':
        results = []
        for c in concepts:
            results.append({
                "concept_id": c.concept_id,
                "text": c.concept_text,
                "normalized": c.normalized_text,
                "frequency": c.frequency,
                "avg_impact": c.avg_impact,
                "source_types": c.source_type,
                "session_count": len(c.session_ids),
            })
        print(json.dumps(results, indent=2))
    else:
        if not concepts:
            print("No concepts found. Run 'empirica concept-build' first.")
            return

        print(f"\n{'='*60}")
        print(f"TOP CONCEPTS ({len(concepts)} shown)")
        print(f"{'='*60}\n")

        for i, c in enumerate(concepts, 1):
            print(f"{i:2}. {c.concept_text}")
            print(f"    Frequency: {c.frequency} | Impact: {c.avg_impact:.2f} | Sessions: {len(c.session_ids)}")
            print(f"    Sources: {c.source_type}")
            print()


def handle_concept_related(args):
    """Find concepts related to a search term."""
    ConceptGraph = _get_concept_graph()
    if not ConceptGraph:
        return

    project_id = _get_project_id(args)
    if not project_id:
        print("Error: Could not determine project ID. Use --project-id or run from a project directory.")
        return

    db_path = _get_db_path()
    if not db_path.exists():
        print("Error: No database found. Run 'empirica concept-build' first.")
        return

    graph = ConceptGraph(project_id, db_path)
    related = graph.find_related_concepts(args.search_term, limit=args.limit)

    if args.output == 'json':
        results = []
        for node, weight in related:
            results.append({
                "concept_id": node.concept_id,
                "text": node.concept_text,
                "normalized": node.normalized_text,
                "weight": weight,
                "frequency": node.frequency,
                "avg_impact": node.avg_impact,
            })
        print(json.dumps(results, indent=2))
    else:
        if not related:
            print(f"No concepts found related to '{args.search_term}'.")
            print("Try running 'empirica concept-build' first.")
            return

        print(f"\n{'='*60}")
        print(f"CONCEPTS RELATED TO: {args.search_term}")
        print(f"{'='*60}\n")

        for node, weight in related:
            weight_bar = '█' * int(weight * 10)
            print(f"  {node.concept_text}")
            print(f"    Weight: {weight:.2f} {weight_bar}")
            print(f"    Frequency: {node.frequency} | Impact: {node.avg_impact:.2f}")
            print()
