"""
Project Embed Command - Build Qdrant indices from docs + project memory.
"""
from __future__ import annotations

import json
import logging
import os

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def _load_semantic_index(root: str) -> dict:
    """Load semantic index (per-project, with graceful fallback)"""
    from empirica.config.semantic_index_loader import load_semantic_index
    index = load_semantic_index(root)
    return index or {}


def _read_file(path: str) -> str:
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ""


def _compact_text(text: str, max_chars: int = 900) -> str:
    normalized = " ".join((text or "").replace("\x00", " ").split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rsplit(" ", 1)[0] + " ..."


def _resolve_doc_path(root: str, relpath: str) -> str:
    """Resolve semantic index entries against the project root first."""
    if os.path.isabs(relpath):
        return relpath

    candidates = [os.path.join(root, relpath)]

    if relpath.startswith('docs/'):
        candidates.append(os.path.join(root, relpath.split('docs/', 1)[-1]))
    else:
        candidates.append(os.path.join(root, 'docs', relpath))

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    return candidates[0]


def _build_embedding_text(relpath: str, meta: dict, text: str) -> str:
    """Build compact embedding text from semantic-index metadata plus an excerpt."""
    parts = [f"File: {relpath}"]

    tags = meta.get("tags") or []
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")

    concepts = meta.get("concepts") or []
    if concepts:
        parts.append(f"Concepts: {', '.join(concepts)}")

    questions = meta.get("questions") or []
    if questions:
        parts.append(f"Questions: {' | '.join(questions)}")

    use_cases = meta.get("use_cases") or []
    if use_cases:
        parts.append(f"Use cases: {', '.join(use_cases)}")

    description = meta.get("description")
    if description:
        parts.append(f"Description: {_compact_text(description, max_chars=240)}")

    doc_type = meta.get("doc_type")
    if doc_type:
        parts.append(f"Doc type: {doc_type}")

    excerpt = _compact_text(text, max_chars=240)
    if excerpt:
        parts.append(f"Excerpt: {excerpt}")

    return "\n".join(parts)


def _has_indexed_python_files(docs_cfg: dict) -> bool:
    return any(str(path).endswith(".py") for path in docs_cfg)


def _build_memory_items(findings, unknowns, mistakes, dead_ends, lessons, snapshots) -> list:
    """Build Qdrant memory items from all artifact types. Uses actual artifact IDs."""
    items = []
    for f in findings:
        fid = f.get('finding_id') or str(f.get('id', ''))
        if fid:
            items.append({'id': fid, 'text': f.get('finding', ''), 'type': 'finding',
                          'goal_id': f.get('goal_id'), 'subtask_id': f.get('subtask_id'),
                          'session_id': f.get('session_id'), 'timestamp': f.get('created_timestamp'),
                          'subject': f.get('subject')})
    for u in unknowns:
        uid = u.get('unknown_id') or str(u.get('id', ''))
        if uid:
            items.append({'id': uid, 'text': u.get('unknown', ''), 'type': 'unknown',
                          'goal_id': u.get('goal_id'), 'subtask_id': u.get('subtask_id'),
                          'session_id': u.get('session_id'), 'timestamp': u.get('created_timestamp'),
                          'subject': u.get('subject'), 'is_resolved': u.get('is_resolved', False)})
    for m in mistakes:
        mid = str(m.get('id', ''))
        if mid:
            items.append({'id': f"mistake_{mid}",
                          'text': f"{m.get('mistake','')} Prevention: {m.get('prevention','')}",
                          'type': 'mistake', 'session_id': m.get('session_id'),
                          'goal_id': m.get('goal_id'), 'timestamp': m.get('created_timestamp')})
    for d in dead_ends:
        did = d.get('dead_end_id') or str(d.get('id', ''))
        if did:
            items.append({'id': did,
                          'text': f"DEAD END: {d.get('approach', '')} Why failed: {d.get('why_failed', '')}",
                          'type': 'dead_end', 'session_id': d.get('session_id'),
                          'goal_id': d.get('goal_id'), 'subtask_id': d.get('subtask_id'),
                          'timestamp': d.get('created_timestamp')})
    for lesson in lessons:
        lid = str(lesson.get('id', ''))
        if lid:
            items.append({'id': f"lesson_{lid}",
                          'text': f"LESSON: {lesson.get('name', '')} - {lesson.get('description', '')} Domain: {lesson.get('domain', '')}",
                          'type': 'lesson', 'lesson_id': lesson.get('id'),
                          'domain': lesson.get('domain'), 'tags': lesson.get('tags'),
                          'timestamp': lesson.get('created_timestamp')})
    for snap in snapshots:
        context = snap.get('context_summary', '')
        sid = snap.get('snapshot_id') or str(snap.get('id', ''))
        if context and sid:
            items.append({'id': f"snap_{sid}", 'text': f"SESSION NARRATIVE: {context}",
                          'type': 'episodic', 'session_id': snap.get('session_id'),
                          'snapshot_id': snap.get('snapshot_id'), 'timestamp': snap.get('timestamp')})
    return items


def _resolve_project_root_and_db(project_id, root):
    """Resolve correct sessions.db path and project root from workspace.

    Returns (db_path, root) tuple.
    """
    from empirica.utils.session_resolver import InstanceResolver as R

    db_path = None
    try:
        project_info = R.resolve_workspace_project(project_id)
        if project_info and project_info.get('project_path'):
            project_root = project_info['project_path']
            candidate = os.path.join(project_root, '.empirica', 'sessions', 'sessions.db')
            if os.path.exists(candidate):
                db_path = candidate
                root = project_root
    except Exception:
        pass
    return db_path, root


def _prepare_docs_from_semantic_index(root, docs_cfg):
    """Build docs list from semantic index entries. Returns (docs_to_upsert, next_id)."""
    docs_to_upsert = []
    did = 1
    for relpath, meta in docs_cfg.items():
        doc_path = _resolve_doc_path(root, relpath)
        file_text = _read_file(doc_path)
        text = _build_embedding_text(relpath, meta, file_text)
        docs_to_upsert.append({
            'id': did, 'text': text,
            'metadata': {
                'doc_path': relpath, 'tags': meta.get('tags', []),
                'concepts': meta.get('concepts', []),
                'questions': meta.get('questions', []),
                'use_cases': meta.get('use_cases', []),
            }
        })
        did += 1
    return docs_to_upsert, did


def _append_reference_docs(db, project_id, docs_to_upsert, start_id):
    """Append reference docs from DB to docs list. Returns updated next_id."""
    did = start_id
    try:
        refdocs = db.get_project_reference_docs(project_id)
        for rdoc in refdocs:
            doc_path = rdoc.get('doc_path', '')
            file_text = _read_file(doc_path) if doc_path else ''
            if not file_text:
                file_text = rdoc.get('description', '') or f"Reference: {doc_path}"

            description = rdoc.get('description', '') or ''
            doc_type = rdoc.get('doc_type', '') or ''
            keywords = [w.lower() for w in (description + ' ' + doc_type).split() if len(w) > 3]
            meta = {
                'doc_path': doc_path, 'doc_type': doc_type,
                'description': description, 'tags': keywords, 'source': 'refdoc',
            }
            text = _build_embedding_text(doc_path, meta, file_text)
            docs_to_upsert.append({'id': did, 'text': text, 'metadata': meta})
            did += 1
        logger.debug(f"Added {len(refdocs)} reference docs to embedding queue")
    except Exception as e:
        logger.debug(f"Could not load reference docs: {e}")
    return did


def _query_memory_artifacts(db, project_id):
    """Query all memory artifact types from the database.

    Returns (findings, unknowns, mistakes, dead_ends, lessons, snapshots).
    """
    findings = db.get_project_findings(project_id)
    unknowns = db.get_project_unknowns(project_id)

    cur = db.conn.cursor()
    cur.execute("""
        SELECT m.id, m.mistake, m.prevention
        FROM mistakes_made m JOIN sessions s ON m.session_id = s.session_id
        WHERE s.project_id = ? ORDER BY m.created_timestamp DESC
    """, (project_id,))
    mistakes = [dict(row) for row in cur.fetchall()]

    cur.execute("""
        SELECT id, approach, why_failed, session_id, goal_id, subtask_id, created_timestamp
        FROM project_dead_ends WHERE project_id = ? ORDER BY created_timestamp DESC
    """, (project_id,))
    dead_ends = [dict(row) for row in cur.fetchall()]

    cur.execute("""
        SELECT id, name, description, domain, tags, lesson_data, created_timestamp
        FROM lessons ORDER BY created_timestamp DESC
    """)
    lessons = [dict(row) for row in cur.fetchall()]

    cur.execute("""
        SELECT snapshot_id, session_id, context_summary, timestamp
        FROM epistemic_snapshots
        WHERE session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)
        ORDER BY timestamp DESC
    """, (project_id,))
    snapshots = [dict(row) for row in cur.fetchall()]

    return findings, unknowns, mistakes, dead_ends, lessons, snapshots


def _rehydrate_eidetic(project_id, findings, embed_eidetic_fn, check_fn):
    """Rehydrate eidetic collection from findings. Returns count embedded.

    Uses batch embedding to avoid sequential per-finding round-trips
    (which on a slow local embedder costs ~Nfindings × 4-6s).
    Falls back to the per-finding ``embed_eidetic_fn`` if batch embedding
    is unavailable (provider import error, Qdrant down, etc.).
    """
    import hashlib
    import time

    eidetic_count = 0
    if not (check_fn() and findings):
        return eidetic_count

    # Pre-filter empty-text findings and pair each with its content hash.
    valid = []
    for f in findings:
        finding_text = f.get('finding', '')
        if not finding_text:
            continue
        valid.append((f, finding_text, hashlib.md5(finding_text.encode()).hexdigest()))
    if not valid:
        return 0

    # Try batched embed path first.
    try:
        from empirica.core.qdrant.collections import _eidetic_collection
        from empirica.core.qdrant.connection import (
            _get_embeddings_batch_for_collection,
            _get_qdrant_client,
            _get_qdrant_imports,
        )
        _, _, _, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            raise RuntimeError("Qdrant client unavailable")
        coll = _eidetic_collection(project_id)

        embed_batch_size = int(os.environ.get("EMPIRICA_EMBED_BATCH_SIZE", "50"))
        texts = [t for (_, t, _) in valid]
        vectors: list = []
        create_if_missing = True
        for i in range(0, len(texts), embed_batch_size):
            batch_vectors = _get_embeddings_batch_for_collection(
                client, coll, texts[i:i + embed_batch_size],
                create_if_missing=create_if_missing,
            )
            vectors.extend(batch_vectors)
            create_if_missing = False

        points = []
        now = time.time()
        for (f, finding_text, content_hash), vector in zip(valid, vectors):
            if vector is None:
                continue
            impact = f.get('impact')
            base_confidence = float(impact) if impact else 0.6
            fact_id = f.get('id', content_hash)
            point_id = int(hashlib.md5(fact_id.encode()).hexdigest()[:15], 16)
            payload = {
                "type": "fact",
                "content": finding_text[:500] if finding_text else None,
                "content_full": finding_text if len(finding_text) <= 500 else None,
                "content_hash": content_hash,
                "domain": f.get('subject'),
                "confidence": base_confidence,
                "confirmation_count": 1,
                "first_seen": now,
                "last_confirmed": now,
                "source_sessions": [f.get('session_id')] if f.get('session_id') else [],
                "source_findings": [f.get('id')] if f.get('id') else [],
                "tags": [f.get('subject')] if f.get('subject') else [],
            }
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        if points:
            # Upsert in chunks so we don't blow past Qdrant's request body limit
            # when finding counts run into the thousands.
            upsert_chunk = 256
            for i in range(0, len(points), upsert_chunk):
                client.upsert(collection_name=coll, points=points[i:i + upsert_chunk])
        return len(points)
    except Exception as e:
        logger.debug(f"Eidetic batch path unavailable ({e}); falling back to per-finding embed")

    # Fallback: sequential per-finding via the injected embed_eidetic_fn.
    for f, finding_text, content_hash in valid:
        impact = f.get('impact')
        base_confidence = float(impact) if impact else 0.6
        try:
            success = embed_eidetic_fn(
                project_id=project_id,
                fact_id=f.get('id', content_hash),
                content=finding_text, fact_type="fact",
                domain=f.get('subject'),
                source_sessions=[f.get('session_id')] if f.get('session_id') else None,
                source_findings=[f.get('id')] if f.get('id') else None,
                confidence=base_confidence,
                tags=[f.get('subject')] if f.get('subject') else None,
            )
            if success:
                eidetic_count += 1
        except Exception as e:
            logger.debug(f"Eidetic embed failed for finding {f.get('id', 'unknown')}: {e}")
    return eidetic_count


def handle_project_embed_command(args):
    """Handle project-embed command to sync docs and memory to Qdrant."""
    try:
        from empirica.core.qdrant.vector_store import (
            _check_qdrant_available,
            embed_eidetic,
            init_collections,
            init_global_collection,
            sync_high_impact_to_global,
            upsert_docs,
            upsert_memory,
        )
        from empirica.data.session_database import SessionDatabase
        from empirica.utils.session_resolver import InstanceResolver as R

        project_id = args.project_id
        context_project = R.project_path()
        root = context_project if context_project else os.getcwd()
        sync_global = getattr(args, 'global_sync', False)

        init_collections(project_id)
        if sync_global:
            init_global_collection()

        db_path, root = _resolve_project_root_and_db(project_id, root)
        db = SessionDatabase(db_path=db_path)

        idx = _load_semantic_index(root)
        docs_cfg = idx.get('index', {})
        docs_to_upsert, did = _prepare_docs_from_semantic_index(root, docs_cfg)
        _append_reference_docs(db, project_id, docs_to_upsert, did)
        upsert_docs(project_id, docs_to_upsert)

        findings, unknowns, mistakes, dead_ends, lessons, snapshots = _query_memory_artifacts(db, project_id)
        db.close()

        mem_items = _build_memory_items(findings, unknowns, mistakes, dead_ends, lessons, snapshots)
        upsert_memory(project_id, mem_items)

        eidetic_count = _rehydrate_eidetic(project_id, findings, embed_eidetic, _check_qdrant_available)

        code_embedded = 0
        try:
            from pathlib import Path

            from empirica.core.qdrant.code_embeddings import embed_project_code
            code_root = Path(root)
            if _has_indexed_python_files(docs_cfg) and code_root.is_dir():
                code_result = embed_project_code(project_id, code_root)
                code_embedded = code_result.get('modules_embedded', 0)
        except Exception as e:
            logger.debug(f"Code embedding skipped: {e}")

        global_synced = 0
        if sync_global:
            min_impact = getattr(args, 'min_impact', 0.7)
            global_synced = sync_high_impact_to_global(project_id, min_impact)

        result = {
            'ok': True, 'docs': len(docs_to_upsert), 'memory': len(mem_items),
            'eidetic': eidetic_count, 'code_api': code_embedded,
            'breakdown': {
                'findings': len(findings), 'unknowns': len(unknowns),
                'mistakes': len(mistakes), 'dead_ends': len(dead_ends),
                'lessons': len(lessons), 'snapshots': len(snapshots)
            },
            'global_synced': global_synced if sync_global else None
        }

        if getattr(args, 'output', 'default') == 'json':
            print(json.dumps(result, indent=2))
        else:
            msg = f"✅ Embedded docs: {len(docs_to_upsert)} | memory: {len(mem_items)}"
            msg += f" (findings: {len(findings)}, unknowns: {len(unknowns)}, dead_ends: {len(dead_ends)}, lessons: {len(lessons)}, snapshots: {len(snapshots)})"
            if sync_global:
                msg += f" | global: {global_synced}"
            print(msg)

        return result
    except Exception as e:
        handle_cli_error(e, "Project embed", getattr(args, 'verbose', False))
        return None
