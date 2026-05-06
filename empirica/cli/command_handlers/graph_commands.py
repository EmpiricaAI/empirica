"""
Graph Artifact Commands — batch logging and resolution of connected artifacts.

Implements the Artifact Graph API (spec: empirica-cortex/.empirica/plans/artifact-graph-api.md).
Nodes are typed artifacts, edges are relationships between them.
"""

import json
import logging
import sys

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)

# Node types and their required fields
NODE_REQUIRED_FIELDS = {
    'finding': ['finding'],
    'unknown': ['unknown'],
    'dead_end': ['approach', 'why_failed'],
    'mistake': ['mistake', 'why_wrong'],
    'assumption': ['assumption', 'confidence'],
    'decision': ['choice', 'rationale'],
    'source': ['title'],
}

# Valid edge relation types
VALID_RELATIONS = {
    'evidence', 'raised_by', 'grounded_by', 'resolves',
    'invalidates', 'sourced_from', 'caused_by', 'prevents', 'attached_to',
}

# Creation order — dependencies resolved top-down
CREATION_ORDER = ['source', 'finding', 'unknown', 'dead_end', 'mistake', 'assumption', 'decision']


# ─── schemas (printed by --schema, used in error messages) ────────────────

LOG_ARTIFACTS_SCHEMA = {
    "nodes": [
        {
            "ref": "<local-id like 'f1' — referenced from edges>",
            "type": "finding | unknown | dead_end | mistake | assumption | decision | source",
            "data": {
                "finding | unknown | choice | etc.": "<type-specific required fields>",
                "impact": "<float 0-1, optional>",
                "subject": "<optional>",
                "visibility": "<public | shared | local — optional, default 'shared'>",
            },
        },
    ],
    "edges": [
        {
            "from": "<ref or UUID>",
            "to": "<ref or UUID>",
            "relation": "evidence | raised_by | grounded_by | resolves | "
                        "invalidates | sourced_from | caused_by | prevents | attached_to",
        },
    ],
    "session_id": "<optional, auto-resolved from active context>",
    "project_id": "<optional, auto-resolved from active context>",
}

RESOLVE_ARTIFACTS_SCHEMA = {
    "resolutions": [
        {
            "type": "unknown | assumption | goal",
            "id": "<UUID of the artifact to resolve>",
            "resolution": "<resolution text or status — semantics depend on type>",
            "verified": "<true/false, optional, for assumption→finding>",
        },
    ],
}

DELETE_ARTIFACTS_SCHEMA = {
    "deletions": [
        {
            "type": "finding | unknown | dead_end | mistake | assumption | decision",
            "id": "<UUID of the artifact to delete>",
        },
    ],
    "reason": "<optional human-readable reason — logged as decision>",
}


def _print_schema_and_exit(schema: dict, command: str) -> int:
    """Print the input schema for a batch artifact verb and exit cleanly.

    Mirrors the noetic-batch --schema pattern. Used so AIs hitting these
    verbs can self-discover the input shape without trial-and-error.
    """
    payload = {
        "command": command,
        "schema": schema,
        "valid_node_types": sorted(NODE_REQUIRED_FIELDS.keys()),
        "valid_relations": sorted(VALID_RELATIONS),
        "node_required_fields_by_type": NODE_REQUIRED_FIELDS,
    }
    print(json.dumps(payload, indent=2))
    return 0


# ─── input normalization (forgiving aliases) ──────────────────────────────

# Field aliases AIs commonly use that we accept as drop-in replacements.
# 'id' → 'ref' on nodes is the most common miss because resolve-artifacts
# and delete-artifacts both use 'id' in their input shapes.
# 'type' → 'relation' on edges similarly: AIs reach for 'type' as a
# generic kind-field.
_NODE_REF_ALIASES = ('ref', 'id', 'node_id')
_EDGE_RELATION_ALIASES = ('relation', 'type', 'kind')


def _normalize_graph(graph: dict) -> tuple[dict, list[str]]:
    """Apply forgiving aliasing to a graph payload.

    Returns (normalized_graph, deprecation_warnings). The graph is a copy
    with canonical field names so downstream code can rely on 'ref' /
    'relation'. Warnings are surfaced in the response so AIs learn the
    canonical names over time.
    """
    if not isinstance(graph, dict):
        return graph, []

    out = dict(graph)
    warnings: list[str] = []

    nodes = out.get('nodes')
    if isinstance(nodes, list):
        new_nodes = []
        for node in nodes:
            if not isinstance(node, dict):
                new_nodes.append(node)
                continue
            n = dict(node)
            if 'ref' not in n:
                for alias in _NODE_REF_ALIASES[1:]:
                    if alias in n:
                        n['ref'] = n[alias]
                        warnings.append(
                            f"node uses '{alias}' (accepted as alias for 'ref' — prefer 'ref')"
                        )
                        break
            new_nodes.append(n)
        out['nodes'] = new_nodes

    edges = out.get('edges')
    if isinstance(edges, list):
        new_edges = []
        for edge in edges:
            if not isinstance(edge, dict):
                new_edges.append(edge)
                continue
            e = dict(edge)
            if 'relation' not in e:
                for alias in _EDGE_RELATION_ALIASES[1:]:
                    if alias in e:
                        e['relation'] = e[alias]
                        warnings.append(
                            f"edge uses '{alias}' (accepted as alias for 'relation' — prefer 'relation')"
                        )
                        break
            new_edges.append(e)
        out['edges'] = new_edges

    # Deduplicate warnings (one entry per alias rather than per node).
    return out, sorted(set(warnings))


def _validate_graph(graph: dict) -> list[str]:
    """Validate graph structure. Returns list of errors (empty = valid)."""
    errors = []
    nodes = graph.get('nodes', [])
    edges = graph.get('edges', [])

    if not nodes:
        errors.append("No nodes provided")
        return errors

    refs = set()
    for i, node in enumerate(nodes):
        ref = node.get('ref')
        ntype = node.get('type')
        data = node.get('data', {})

        if not ref:
            errors.append(f"Node {i}: missing 'ref'")
            continue
        if ref in refs:
            errors.append(f"Node {i}: duplicate ref '{ref}'")
        refs.add(ref)

        if ntype not in NODE_REQUIRED_FIELDS:
            errors.append(f"Node '{ref}': unknown type '{ntype}' (valid: {', '.join(NODE_REQUIRED_FIELDS)})")
            continue

        for field in NODE_REQUIRED_FIELDS[ntype]:
            if field not in data:
                errors.append(f"Node '{ref}' ({ntype}): missing required field '{field}'")

    for i, edge in enumerate(edges):
        from_ref = edge.get('from')
        to_ref = edge.get('to')
        relation = edge.get('relation')

        if not from_ref or not to_ref:
            errors.append(f"Edge {i}: missing 'from' or 'to'")
            continue
        if relation not in VALID_RELATIONS:
            errors.append(f"Edge {i}: unknown relation '{relation}' (valid: {', '.join(sorted(VALID_RELATIONS))})")

        # Refs must exist in nodes (or be UUIDs for existing artifacts)
        if from_ref not in refs and not _is_uuid(from_ref):
            errors.append(f"Edge {i}: 'from' ref '{from_ref}' not found in nodes")
        if to_ref not in refs and not _is_uuid(to_ref):
            errors.append(f"Edge {i}: 'to' ref '{to_ref}' not found in nodes")

    return errors


def _is_uuid(s: str) -> bool:
    """Check if string looks like a UUID."""
    import re
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', s, re.I))


def _create_node(db, node: dict, context: dict) -> str | None:
    """Create a single artifact node. Returns the UUID or None on failure."""
    ntype = node['type']
    data = node['data']
    session_id = context['session_id']
    project_id = context['project_id']
    goal_id = data.get('goal_id') or context.get('goal_id')
    transaction_id = context.get('transaction_id')
    visibility = data.get('visibility')
    epistemic_source = data.get('epistemic_source')

    try:
        if ntype == 'finding':
            return db.log_finding(
                project_id=project_id, session_id=session_id,
                finding=data['finding'], impact=data.get('impact', 0.5),
                goal_id=goal_id, subject=data.get('subject'),
                transaction_id=transaction_id,
                visibility=visibility,
                epistemic_source=epistemic_source,
            )
        elif ntype == 'unknown':
            return db.log_unknown(
                project_id=project_id, session_id=session_id,
                unknown=data['unknown'],
                goal_id=goal_id, subject=data.get('subject'),
                transaction_id=transaction_id,
                visibility=visibility,
                epistemic_source=epistemic_source,
            )
        elif ntype == 'dead_end':
            return db.log_dead_end(
                project_id=project_id, session_id=session_id,
                approach=data['approach'], why_failed=data['why_failed'],
                impact=data.get('impact', 0.5),
                goal_id=goal_id, subject=data.get('subject'),
                transaction_id=transaction_id,
                visibility=visibility,
                epistemic_source=epistemic_source,
            )
        elif ntype == 'mistake':
            return db.log_mistake(
                session_id=session_id,
                mistake=data['mistake'], why_wrong=data['why_wrong'],
                cost_estimate=data.get('cost_estimate'),
                root_cause_vector=data.get('root_cause_vector'),
                prevention=data.get('prevention'),
                goal_id=goal_id,
                project_id=project_id,
                transaction_id=transaction_id,
                visibility=visibility,
                epistemic_source=epistemic_source,
            )
        elif ntype == 'assumption':
            return db.log_assumption(
                project_id=project_id, session_id=session_id,
                assumption=data['assumption'],
                confidence=data.get('confidence', 0.5),
                domain=data.get('domain'),
                goal_id=goal_id,
                transaction_id=transaction_id,
                visibility=visibility,
                epistemic_source=epistemic_source,
            )
        elif ntype == 'decision':
            return db.log_decision(
                project_id=project_id, session_id=session_id,
                choice=data['choice'], rationale=data['rationale'],
                alternatives=data.get('alternatives'),
                reversibility=data.get('reversibility', 'exploratory'),
                confidence=data.get('confidence', 0.7),
                goal_id=goal_id,
                transaction_id=transaction_id,
                visibility=visibility,
                epistemic_source=epistemic_source,
            )
        elif ntype == 'source':
            return db.add_reference_doc(
                project_id=project_id,
                doc_path=data.get('title', ''),
                doc_type=data.get('source_type'),
                description=data.get('description'),
            )
    except Exception as e:
        logger.warning(f"Failed to create {ntype} node '{node.get('ref')}': {e}")
    return None


def _wire_edges(db, edges: list[dict], ref_map: dict[str, str]) -> int:
    """Wire edges between created artifacts. Returns count of edges wired."""
    wired = 0
    for edge in edges:
        from_id = ref_map.get(edge['from'], edge['from'])
        to_id = ref_map.get(edge['to'], edge['to'])
        relation = edge['relation']

        # Store edge in the 'from' artifact's data column as JSON
        try:
            _store_edge(db, from_id, to_id, relation)
            wired += 1
        except Exception as e:
            logger.debug(f"Failed to wire edge {edge}: {e}")

    return wired


def _store_edge(db, from_id: str, to_id: str, relation: str, metadata: dict | None = None):
    """Store an edge relationship.

    Writes to the canonical `artifact_edges` table (post-migration 041) AND
    keeps the legacy data.edges JSON in the artifact's data column populated
    where one exists. Dual-write is a transitional compat layer — readers
    that haven't migrated to the edge table yet keep working. Once all
    readers use the edge table, the data.edges JSON arm can be removed.

    Edges from `assumptions` and `decisions` (which have no data column)
    used to silently drop here; now they're recorded in the edge table.
    """
    if not db.conn:
        return

    cursor = db.conn.cursor()

    # Canonical write: artifact_edges table (works for ALL artifact types,
    # including assumptions and decisions which previously dropped edges).
    try:
        meta_json = json.dumps(metadata) if metadata else None
        cursor.execute(
            "INSERT OR IGNORE INTO artifact_edges (from_id, to_id, relation, metadata) "
            "VALUES (?, ?, ?, ?)",
            (from_id, to_id, relation, meta_json),
        )
    except Exception as e:
        logger.debug(f"_store_edge: artifact_edges write failed (non-fatal): {e}")

    # Legacy compat: also update data.edges JSON for tables that have a data column,
    # so existing readers (e.g. UIs reading directly from finding_data) keep seeing
    # the edge until they migrate to the edge table.
    for _atype, (table, id_col, data_col) in _ARTIFACT_TABLES.items():
        if not data_col:
            continue
        cursor.execute(f"SELECT {data_col} FROM {table} WHERE {id_col} = ?", (from_id,))
        row = cursor.fetchone()
        if row is not None:
            existing_data = {}
            if row[0]:
                try:
                    existing_data = json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    pass

            edges_list = existing_data.get('edges', [])
            # Dedupe — don't append the same edge twice
            already_present = any(
                e.get('to') == to_id and e.get('relation') == relation
                for e in edges_list
            )
            if not already_present:
                edges_list.append({'to': to_id, 'relation': relation})
                existing_data['edges'] = edges_list
                cursor.execute(
                    f"UPDATE {table} SET {data_col} = ? WHERE {id_col} = ?",
                    (json.dumps(existing_data), from_id),
                )
            db.conn.commit()
            return

    db.conn.commit()


def _auto_embed_node(node: dict, artifact_id: str, context: dict):
    """Auto-embed a created node to Qdrant (non-fatal)."""
    try:
        from empirica.core.qdrant.memory import embed_single_memory_item
        ntype = node['type']
        data = node['data']

        # Build text from type-specific fields
        if ntype == 'finding':
            text = data['finding']
        elif ntype == 'unknown':
            text = data['unknown']
        elif ntype == 'dead_end':
            text = f"{data['approach']}: {data['why_failed']}"
        elif ntype == 'mistake':
            text = f"{data['mistake']}: {data['why_wrong']}"
        elif ntype == 'assumption':
            text = data['assumption']
        elif ntype == 'decision':
            text = f"{data['choice']}: {data['rationale']}"
        else:
            return

        embed_single_memory_item(
            project_id=context['project_id'],
            item_id=artifact_id,
            text=text,
            item_type=ntype,
            session_id=context['session_id'],
        )
    except Exception:
        pass  # Qdrant embedding is non-critical


def _read_graph_input(args) -> dict | None:
    """Read, normalize, and validate graph JSON from stdin or file.

    Tolerates `id`/`node_id` as aliases for `ref` on nodes and
    `type`/`kind` as aliases for `relation` on edges. Surfaces a hint
    to `--schema` on validation failure so AIs can self-correct.
    """
    from empirica.cli.cli_utils import parse_json_safely

    if hasattr(args, 'config') and args.config:
        if args.config == '-':
            raw = sys.stdin.read()
        else:
            with open(args.config) as f:
                raw = f.read()
    else:
        raw = sys.stdin.read()

    graph = parse_json_safely(raw)
    if not graph:
        print(json.dumps({
            "ok": False, "error": "Invalid JSON input",
            "hint": "Run with --schema to see the expected input shape.",
        }))
        return None

    graph, alias_warnings = _normalize_graph(graph)
    errors = _validate_graph(graph)
    if errors:
        print(json.dumps({
            "ok": False,
            "errors": errors,
            "hint": "Run `empirica log-artifacts --schema` for the full input shape. "
                    "Common pitfalls: nodes need 'ref' (not 'id'), edges need 'relation' "
                    "(not 'type').",
        }))
        return None

    if alias_warnings:
        # Stash warnings on the graph so the handler can include them in
        # its success response.
        graph['_alias_warnings'] = alias_warnings

    return graph


def _resolve_graph_context(graph: dict, args, db) -> dict | None:
    """Resolve session/project/transaction context for graph operations."""
    from empirica.utils.session_resolver import InstanceResolver as R

    session_id = graph.get('session_id') or getattr(args, 'session_id', None)
    if not session_id:
        try:
            ctx = R.context()
            session_id = ctx.get('empirica_session_id')
        except Exception:
            pass

    project_id = graph.get('project_id') or getattr(args, 'project_id', None)
    if not project_id and session_id:
        session = db.get_session(session_id)
        if session:
            project_id = session.get('project_id')

    if not session_id or not project_id:
        print(json.dumps({"ok": False, "error": "Could not resolve session_id or project_id"}))
        return None

    transaction_id = graph.get('transaction_id')
    if not transaction_id:
        try:
            ctx = R.context()
            transaction_id = ctx.get('transaction_id')
        except Exception:
            pass

    return {
        'session_id': session_id,
        'project_id': project_id,
        'goal_id': graph.get('goal_id'),
        'transaction_id': transaction_id,
    }


def log_artifacts_graph(
    graph: dict,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    transaction_id: str | None = None,
    goal_id: str | None = None,
) -> dict:
    """Pure function: log a graph batch (nodes + edges) and return the result dict.

    Daemon's POST /api/v1/artifacts/log calls this directly; CLI's
    handle_log_artifacts_command wraps it to print JSON + return exit code.

    Resolution priority for context: explicit args > graph["session_id"|...] > R.context().
    Returns: {"ok": bool, "created": {ref: id}, "nodes_created": int,
              "edges_wired": int, "errors": [str], "alias_warnings"?: [str]}
    """
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    try:
        # Build a synthetic args-like object for _resolve_graph_context (reuses
        # the existing R.context() chain). Explicit args win over graph fields.
        # _resolve_graph_context reads graph["session_id"|"project_id"|"goal_id"|"transaction_id"]
        # then args fields; merge our overrides into a graph copy so it picks them up.
        from types import SimpleNamespace
        ctx_args = SimpleNamespace(session_id=session_id, project_id=project_id)
        graph_for_ctx = dict(graph)
        if session_id:
            graph_for_ctx["session_id"] = session_id
        if project_id:
            graph_for_ctx["project_id"] = project_id
        if transaction_id:
            graph_for_ctx["transaction_id"] = transaction_id
        if goal_id:
            graph_for_ctx["goal_id"] = goal_id

        context = _resolve_graph_context(graph_for_ctx, ctx_args, db)
        if not context:
            return {"ok": False, "error": "Could not resolve session_id or project_id"}

        nodes = graph.get('nodes', [])
        sorted_nodes = sorted(
            nodes,
            key=lambda n: CREATION_ORDER.index(n.get('type', 'finding'))
            if n.get('type') in CREATION_ORDER else 99,
        )

        ref_map: dict[str, str] = {}
        created_errors: list[str] = []
        for node in sorted_nodes:
            artifact_id = _create_node(db, node, context)
            if artifact_id:
                ref_map[node['ref']] = artifact_id
                _auto_embed_node(node, artifact_id, context)
            else:
                created_errors.append(f"Failed to create {node['type']} '{node['ref']}'")

        edges = graph.get('edges', [])
        edges_wired = _wire_edges(db, edges, ref_map) if edges else 0

        # Git notes (non-fatal)
        try:
            import subprocess
            subprocess.run(
                ['git', 'notes', '--ref=breadcrumbs', 'append', '-m',
                 json.dumps({"batch_log": len(ref_map), "edges": edges_wired})],
                capture_output=True, timeout=5, check=False,
            )
        except Exception:
            pass

        result = {
            "ok": True,
            "created": ref_map,
            "nodes_created": len(ref_map),
            "edges_wired": edges_wired,
            "errors": created_errors,
        }
        warnings = graph.get('_alias_warnings')
        if warnings:
            result['alias_warnings'] = warnings
        return result
    finally:
        db.close()


def handle_log_artifacts_command(args):
    """Handle log-artifacts command: batch artifact logging with graph format.

    Thin wrapper around log_artifacts_graph() — handles arg parsing, JSON I/O,
    and exit code. Pure logic lives in log_artifacts_graph() so the daemon can
    call it without subprocess overhead.
    """
    if getattr(args, 'schema', False):
        return _print_schema_and_exit(LOG_ARTIFACTS_SCHEMA, 'log-artifacts')
    try:
        graph = _read_graph_input(args)
        if not graph:
            return 1

        result = log_artifacts_graph(
            graph,
            session_id=getattr(args, 'session_id', None),
            project_id=getattr(args, 'project_id', None),
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get('ok') else 1

    except Exception as e:
        handle_cli_error(e, "Log artifacts", getattr(args, 'verbose', False))
        return 1


def handle_resolve_artifacts_command(args):  # noqa: C901 — batch dispatcher fan-out
    """Handle resolve-artifacts command: batch resolution of open artifacts."""
    if getattr(args, 'schema', False):
        return _print_schema_and_exit(RESOLVE_ARTIFACTS_SCHEMA, 'resolve-artifacts')
    try:
        from empirica.cli.cli_utils import parse_json_safely
        from empirica.data.session_database import SessionDatabase

        # Parse input
        if hasattr(args, 'config') and args.config:
            if args.config == '-':
                raw = sys.stdin.read()
            else:
                with open(args.config) as f:
                    raw = f.read()
        else:
            raw = sys.stdin.read()

        resolutions = parse_json_safely(raw)
        if not resolutions:
            print(json.dumps({"ok": False, "error": "Invalid JSON input"}))
            return 1

        db = SessionDatabase()
        if not db.conn:
            print(json.dumps({"ok": False, "error": "No database connection"}))
            return 1

        resolved_count = 0
        resolution_errors: list[str] = []
        items = resolutions.get('resolutions', resolutions.get('items', []))

        for item in items:
            artifact_type = item.get('type')
            artifact_id = item.get('id')
            resolution = item.get('resolution', item.get('resolved_by', ''))

            if not artifact_id or not artifact_type:
                resolution_errors.append("Missing 'id' or 'type' in resolution item")
                continue

            try:
                if artifact_type == 'unknown':
                    cursor = db.conn.cursor()
                    cursor.execute(
                        "UPDATE project_unknowns SET is_resolved = 1, resolved_by = ?, "
                        "resolved_timestamp = datetime('now') WHERE unknown_id LIKE ?",
                        (resolution, f"{artifact_id}%"),
                    )
                    if cursor.rowcount > 0:
                        resolved_count += 1
                    else:
                        resolution_errors.append(f"Unknown '{artifact_id}' not found")

                elif artifact_type == 'assumption':
                    cursor = db.conn.cursor()
                    cursor.execute(
                        "UPDATE project_assumptions SET is_verified = 1, "
                        "verified_by = ? WHERE assumption_id LIKE ?",
                        (resolution, f"{artifact_id}%"),
                    )
                    if cursor.rowcount > 0:
                        resolved_count += 1
                    else:
                        resolution_errors.append(f"Assumption '{artifact_id}' not found")

                elif artifact_type == 'goal':
                    reason = item.get('reason', resolution)
                    cursor = db.conn.cursor()
                    # Goals table: 'goals' with primary key 'id'.
                    # Set both is_completed (canonical) and status (text), and
                    # record completed_timestamp + completion reason in goal_data.
                    import time as _time
                    cursor.execute(
                        "SELECT goal_data FROM goals WHERE id LIKE ?",
                        (f"{artifact_id}%",),
                    )
                    row = cursor.fetchone()
                    if not row:
                        resolution_errors.append(f"Goal '{artifact_id}' not found")
                    else:
                        try:
                            gd = json.loads(row[0]) if row[0] else {}
                        except (json.JSONDecodeError, TypeError):
                            gd = {}
                        gd['completed_reason'] = reason
                        cursor.execute(
                            "UPDATE goals SET is_completed = 1, status = 'completed', "
                            "completed_timestamp = ?, goal_data = ? WHERE id LIKE ?",
                            (_time.time(), json.dumps(gd), f"{artifact_id}%"),
                        )
                        if cursor.rowcount > 0:
                            resolved_count += 1
                        else:
                            resolution_errors.append(f"Goal '{artifact_id}' update failed")

                else:
                    resolution_errors.append(f"Unsupported resolution type: '{artifact_type}'")

            except Exception as e:
                resolution_errors.append(f"Error resolving {artifact_type} '{artifact_id}': {e}")

        db.conn.commit()
        db.close()

        result = {
            "ok": True,
            "resolved": resolved_count,
            "errors": resolution_errors,
        }
        print(json.dumps(result, indent=2))
        return 0

    except Exception as e:
        handle_cli_error(e, "Resolve artifacts", getattr(args, 'verbose', False))
        return 1


# Table → ID column mapping for deletion
# Table → (table_name, id_column, data_column_for_edges)
_ARTIFACT_TABLES = {
    'finding': ('project_findings', 'id', 'finding_data'),
    'unknown': ('project_unknowns', 'id', 'unknown_data'),
    'dead_end': ('project_dead_ends', 'id', 'dead_end_data'),
    'mistake': ('mistakes_made', 'id', 'mistake_data'),
    'assumption': ('assumptions', 'id', None),
    'decision': ('decisions', 'id', None),
    'goal': ('goals', 'id', 'goal_data'),
}


def _delete_from_qdrant(artifact_id: str, project_id: str):
    """Remove an artifact from Qdrant memory collections (non-fatal)."""
    try:
        from empirica.core.qdrant.collections import _memory_collection
        from empirica.core.qdrant.connection import _get_qdrant_client

        client = _get_qdrant_client()
        if not client:
            return

        import hashlib

        collection = _memory_collection(project_id)
        # Try to delete by point ID (md5 hash of artifact UUID, matching embed scheme)
        point_id = int(hashlib.md5(artifact_id.encode()).hexdigest()[:16], 16) % (2**63)
        try:
            client.delete(
                collection_name=collection,
                points_selector=[point_id],
            )
        except Exception:
            pass  # Collection may not exist or point not found
    except ImportError:
        pass


def _read_deletion_input(args) -> dict | None:
    """Read and validate deletion JSON from stdin or file."""
    from empirica.cli.cli_utils import parse_json_safely

    if hasattr(args, 'config') and args.config:
        if args.config == '-':
            raw = sys.stdin.read()
        else:
            with open(args.config) as f:
                raw = f.read()
    else:
        raw = sys.stdin.read()

    data = parse_json_safely(raw)
    if not data:
        print(json.dumps({"ok": False, "error": "Invalid JSON input"}))
        return None

    items = data.get('deletions', data.get('items', []))
    if not items:
        print(json.dumps({"ok": False, "error": "No deletions specified"}))
        return None

    return data


def _delete_single_artifact(cursor, item: dict, project_id: str | None, dry_run: bool) -> dict | None:
    """Delete a single artifact. Returns result dict or None on error."""
    artifact_type = item.get('type')
    artifact_id = item.get('id')

    if not artifact_id or not artifact_type:
        return {"error": "Missing 'id' or 'type' in deletion item"}

    if artifact_type not in _ARTIFACT_TABLES:
        return {"error": f"Unknown artifact type: '{artifact_type}'"}

    table, id_col, _data_col = _ARTIFACT_TABLES[artifact_type]

    cursor.execute(f"SELECT {id_col} FROM {table} WHERE {id_col} LIKE ?", (f"{artifact_id}%",))
    row = cursor.fetchone()
    if not row:
        return {"error": f"{artifact_type} '{artifact_id}' not found"}

    full_id = row[0]

    if dry_run:
        return {"type": artifact_type, "id": full_id, "action": "would_delete"}

    cursor.execute(f"DELETE FROM {table} WHERE {id_col} = ?", (full_id,))
    if project_id:
        _delete_from_qdrant(full_id, project_id)

    return {"type": artifact_type, "id": full_id, "action": "deleted"}


def handle_delete_artifacts_command(args):  # noqa: C901 — batch dispatcher fan-out
    """Handle delete-artifacts command: batch deletion of stale/non-pertinent artifacts."""
    if getattr(args, 'schema', False):
        return _print_schema_and_exit(DELETE_ARTIFACTS_SCHEMA, 'delete-artifacts')
    try:
        from empirica.data.session_database import SessionDatabase

        data = _read_deletion_input(args)
        if not data:
            return 1

        items = data.get('deletions', data.get('items', []))
        reason = data.get('reason', 'Batch deletion — non-pertinent')
        dry_run = data.get('dry_run', getattr(args, 'dry_run', False))

        db = SessionDatabase()
        if not db.conn:
            print(json.dumps({"ok": False, "error": "No database connection"}))
            return 1

        cursor = db.conn.cursor()
        deleted_count = 0
        delete_errors: list[str] = []
        deleted_items: list[dict] = []

        # Resolve project_id for Qdrant cleanup
        project_id = data.get('project_id')
        if not project_id:
            try:
                from empirica.utils.session_resolver import InstanceResolver as R
                ctx = R.context()
                sid = ctx.get('empirica_session_id')
                if sid:
                    session = db.get_session(sid)
                    if session:
                        project_id = session.get('project_id')
            except Exception:
                pass

        for item in items:
            result_item = _delete_single_artifact(cursor, item, project_id, dry_run)
            if not result_item:
                continue
            if 'error' in result_item:
                delete_errors.append(result_item['error'])
            else:
                deleted_items.append(result_item)
                deleted_count += 1

        if not dry_run:
            db.conn.commit()

            # Log the deletion as a decision (audit trail)
            if deleted_count > 0:
                try:
                    from empirica.utils.session_resolver import InstanceResolver as R
                    ctx = R.context()
                    sid = ctx.get('empirica_session_id')
                    if sid and project_id:
                        cursor.execute(
                            "INSERT INTO project_decisions "
                            "(decision_id, project_id, session_id, choice, rationale, reversibility, created_timestamp) "
                            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                            (str(__import__('uuid').uuid4()), project_id, sid,
                             f"Deleted {deleted_count} artifact(s)", reason, 'committal'),
                        )
                        db.conn.commit()
                except Exception:
                    pass

        db.close()

        result = {
            "ok": True,
            "deleted": deleted_count,
            "dry_run": dry_run,
            "items": deleted_items,
            "errors": delete_errors,
        }
        print(json.dumps(result, indent=2))
        return 0

    except Exception as e:
        handle_cli_error(e, "Delete artifacts", getattr(args, 'verbose', False))
        return 1
