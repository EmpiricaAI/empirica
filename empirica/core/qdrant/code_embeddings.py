"""
Code embeddings: AST-extracted API surfaces stored in Qdrant for semantic search.

Extracts functions, classes, and module structure from Python code and embeds them
so agents can search "how do I ingest a voice sample" and get the actual function
signature, parameters, and module path.

Uses AST parsing (no runtime imports needed) — works across projects safely.
"""

from __future__ import annotations

import ast
import hashlib
import time
from pathlib import Path

from empirica.core.qdrant.collections import _eidetic_collection
from empirica.core.qdrant.connection import (
    _check_qdrant_available,
    _get_embedding_safe,
    _get_qdrant_client,
    _get_qdrant_imports,
    _get_vector_size,
    logger,
)
from empirica.core.qdrant.text_preview import preview_fields

# Code embeddings go into the eidetic collection with fact_type="code_api"
# This avoids collection bloat while keeping code searchable alongside facts.


def _extract_function_signature(node) -> dict:
    """Extract function signature from AST node."""
    params = []
    for arg in node.args.args:
        param = {"name": arg.arg}
        if arg.annotation:
            try:
                param["type"] = ast.unparse(arg.annotation)
            except Exception:
                pass
        params.append(param)

    # Defaults (aligned from the right)
    defaults = node.args.defaults
    if defaults:
        offset = len(params) - len(defaults)
        for i, default in enumerate(defaults):
            try:
                params[offset + i]["default"] = ast.unparse(default)
            except Exception:
                pass

    returns = ""
    if node.returns:
        try:
            returns = ast.unparse(node.returns)
        except Exception:
            pass

    docstring = ast.get_docstring(node) or ""

    decorators = []
    for dec in node.decorator_list:
        try:
            decorators.append(ast.unparse(dec))
        except Exception:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)

    return {
        "name": node.name,
        "params": params,
        "returns": returns,
        "docstring": docstring[:500],
        "decorators": decorators,
        "line": node.lineno,
    }


def _extract_class_info(node: ast.ClassDef) -> dict:
    """Extract class info from AST node."""
    methods = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = _extract_function_signature(item)
            methods.append(sig)

    bases = []
    for base in node.bases:
        try:
            bases.append(ast.unparse(base))
        except Exception:
            if isinstance(base, ast.Name):
                bases.append(base.id)

    docstring = ast.get_docstring(node) or ""

    return {
        "name": node.name,
        "bases": bases,
        "methods": methods,
        "docstring": docstring[:500],
        "line": node.lineno,
    }


def extract_module_api(file_path: Path, root_dir: Path | None = None) -> dict:
    """
    Extract the public API surface from a Python file using AST.

    Returns dict with module_path, functions, classes, and a searchable text summary.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError) as e:
        logger.debug(f"Cannot parse {file_path}: {e}")
        return {}

    # Compute module path
    if root_dir:
        try:
            rel = file_path.relative_to(root_dir)
            module_path = str(rel).replace("/", ".").replace(".py", "")
            if module_path.endswith(".__init__"):
                module_path = module_path[:-9]
        except ValueError:
            module_path = file_path.stem
    else:
        module_path = file_path.stem

    module_docstring = ast.get_docstring(tree) or ""

    functions = []
    classes = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):  # Public only
                functions.append(_extract_function_signature(node))
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                classes.append(_extract_class_info(node))

    if not functions and not classes:
        return {}

    search_text = _build_search_text(module_path, module_docstring, functions, classes)

    return {
        "module_path": module_path,
        "module_docstring": module_docstring[:500],
        "functions": functions,
        "classes": classes,
        "search_text": search_text,
        "file_path": str(file_path),
    }


def _build_search_text(module_path: str, module_docstring: str, functions: list, classes: list) -> str:
    """Build a searchable text summary from extracted module API."""
    summary_parts = [f"Module: {module_path}"]
    if module_docstring:
        summary_parts.append(module_docstring[:200])

    for f in functions:
        param_str = ", ".join(
            p["name"] + (f": {p['type']}" if "type" in p else "") + (f" = {p['default']}" if "default" in p else "")
            for p in f["params"]
            if p["name"] != "self"
        )
        ret_str = f" -> {f['returns']}" if f["returns"] else ""
        summary_parts.append(f"def {f['name']}({param_str}){ret_str}")
        if f["docstring"]:
            summary_parts.append(f"  {f['docstring'][:100]}")

    for c in classes:
        bases_str = f"({', '.join(c['bases'])})" if c["bases"] else ""
        summary_parts.append(f"class {c['name']}{bases_str}")
        if c["docstring"]:
            summary_parts.append(f"  {c['docstring'][:100]}")
        for m in c["methods"]:
            if not m["name"].startswith("_") or m["name"] in ("__init__", "__call__"):
                param_str = ", ".join(
                    p["name"] + (f": {p['type']}" if "type" in p else "") for p in m["params"] if p["name"] != "self"
                )
                summary_parts.append(f"  .{m['name']}({param_str})")

    return "\n".join(summary_parts)


def embed_code_api(
    project_id: str,
    module_info: dict,
) -> bool:
    """
    Embed a module's API surface into eidetic memory as fact_type='code_api'.

    Stored in the eidetic collection alongside regular facts, but filterable
    by type='code_api' and domain=module_path.
    """
    if not _check_qdrant_available():
        return False
    if not module_info or not module_info.get("search_text"):
        return False

    try:
        _, Distance, VectorParams, PointStruct = _get_qdrant_imports()
        client = _get_qdrant_client()
        if client is None:
            return False

        coll = _eidetic_collection(project_id)
        if not client.collection_exists(coll):
            vector_size = _get_vector_size()
            client.create_collection(coll, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))

        search_text = module_info["search_text"]
        vector = _get_embedding_safe(search_text)
        if vector is None:
            return False

        module_path = module_info["module_path"]
        content_hash = hashlib.md5(search_text.encode()).hexdigest()
        fact_id = f"code_api:{module_path}"

        # Structured payload for rich retrieval
        payload = {
            "type": "code_api",
            # Third variant of the same defect: `content_full` was ALWAYS set but
            # itself capped at 2000, so `_full or content` returned a cut that
            # looked whole — undetectable, unlike the null form. It bit ~8% of
            # points (119/1409 here) and used a different cap from eidetic.py,
            # the other writer of this same collection.
            **preview_fields("content", search_text),
            "content_hash": content_hash,
            "domain": module_path,
            "confidence": 0.9,  # Code structure is objective
            "confirmation_count": 1,
            "first_seen": time.time(),
            "last_confirmed": time.time(),
            "source_sessions": [],
            "source_findings": [],
            "tags": ["code_api", module_path.split(".")[0]],
            # Code-specific fields
            "module_path": module_path,
            "file_path": module_info.get("file_path", ""),
            "function_count": len(module_info.get("functions", [])),
            "class_count": len(module_info.get("classes", [])),
            "function_names": [f["name"] for f in module_info.get("functions", [])],
            "class_names": [c["name"] for c in module_info.get("classes", [])],
        }

        point_id = int(hashlib.md5(fact_id.encode()).hexdigest()[:15], 16)
        point = PointStruct(id=point_id, vector=vector, payload=payload)
        client.upsert(collection_name=coll, points=[point])
        return True
    except Exception as e:
        logger.warning(f"Failed to embed code API for {module_info.get('module_path', '?')}: {e}")
        return False


def search_code_api(
    project_id: str,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """
    Search for code API surfaces semantically.

    Filters eidetic collection to fact_type='code_api' only.
    """
    if not _check_qdrant_available():
        return []

    try:
        client = _get_qdrant_client()
        if client is None:
            return []
        coll = _eidetic_collection(project_id)
        if not client.collection_exists(coll):
            return []

        vector = _get_embedding_safe(query)
        if vector is None:
            return []

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        results = client.query_points(
            collection_name=coll,
            query=vector,
            query_filter=Filter(must=[FieldCondition(key="type", match=MatchValue(value="code_api"))]),
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "score": r.score,
                "module_path": r.payload.get("module_path", ""),
                "file_path": r.payload.get("file_path", ""),
                "content": r.payload.get("content_full") or r.payload.get("content", ""),
                "function_names": r.payload.get("function_names", []),
                "class_names": r.payload.get("class_names", []),
                "function_count": r.payload.get("function_count", 0),
                "class_count": r.payload.get("class_count", 0),
            }
            for r in results.points
        ]
    except Exception as e:
        logger.warning(f"Code API search failed: {e}")
        return []


def embed_project_code(
    project_id: str,
    root_dir: Path,
    glob_pattern: str = "**/*.py",
    exclude_patterns: list[str] | None = None,
) -> dict:
    """
    Extract and embed all Python module API surfaces for a project.

    Returns summary dict with counts.
    """
    exclude = exclude_patterns or ["__pycache__", ".git", "node_modules", ".venv", "venv"]

    py_files = list(root_dir.glob(glob_pattern))
    py_files = [f for f in py_files if not any(ex in str(f) for ex in exclude)]

    embedded = 0
    skipped = 0
    errors = 0

    for py_file in py_files:
        module_info = extract_module_api(py_file, root_dir)
        if not module_info:
            skipped += 1
            continue

        if embed_code_api(project_id, module_info):
            embedded += 1
        else:
            errors += 1

    return {
        "files_scanned": len(py_files),
        "modules_embedded": embedded,
        "skipped": skipped,
        "errors": errors,
    }
