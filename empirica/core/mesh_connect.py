"""`empirica mesh connect`: move a practice that has run without Cortex onto Cortex (goal 7377658d).

Three steps, each one Cortex's contract, none of them new on the server:

1. **Sign in.** The Cortex credential `auth login` stores (OAuth first, the api_key as a fallback). Cortex is team-first, with no
   personal orgs: a user whose ``GET /v1/users/me`` names no org is told to create a team or ask a team admin, and connect stops.
2. **Register** the practice under its own project id (``POST /v1/projects/register``, the same call `projects-register` makes).
   A 409 ``slug_taken`` is reported with the id Cortex already holds and its suggestion.
3. **Import** its artifacts (``POST /v1/artifacts/import``, import mode): findings, unknowns, dead ends, mistakes, assumptions,
   decisions and sources, each with the id and date the practice gave it and whether it is resolved, plus the links between them.
   At most 500 nodes a request. Cortex embeds what it receives with its own model, so nothing is re-embedded here; what it
   already holds is skipped, never overwritten; a node carrying a credential is refused and the rest still go.

Visibility maps local -> project, shared -> org, public -> org (Cortex has no community org; the report says how many). Raw
source files stay on this machine: a source goes as its title, type and address, never its body. The seam was agreed with cortex
(prop_mua4sgwzrzfglagatztlcr6n6i): core builds this client, cortex owns the endpoints and the conformance suite.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import time
from collections.abc import Callable
from typing import Any

#: Cortex's import cap per request.
BATCH_SIZE = 500
#: Cortex's accepted link relations; others stay local and are counted.
EDGE_RELATIONS = frozenset(
    {
        "evidence",
        "raised_by",
        "grounded_by",
        "resolves",
        "invalidates",
        "sourced_from",
        "caused_by",
        "prevents",
        "attached_to",
    }
)
VISIBILITY = {"local": "project", "shared": "org", "public": "org"}
NO_TEAM = (
    "Cortex is for working with others, and this account belongs to no team yet. Create a team, or ask a team admin to "
    "add you, then run `empirica mesh connect` again."
)

# (type, table, the columns that become data, the column that says it is closed, the test on it). Column names are core's own.
_TYPES: tuple[tuple[str, str, tuple[str, ...], str | None, str], ...] = (
    ("finding", "project_findings", ("finding", "impact", "subject"), "is_resolved", "COALESCE(is_resolved, 0)"),
    ("unknown", "project_unknowns", ("unknown", "impact", "subject"), "is_resolved", "COALESCE(is_resolved, 0)"),
    (
        "dead_end",
        "project_dead_ends",
        ("approach", "why_failed", "impact", "subject"),
        "is_invalidated",
        "COALESCE(is_invalidated, 0)",
    ),
    (
        "mistake",
        "mistakes_made",
        ("mistake", "why_wrong", "prevention", "impact"),
        "is_invalidated",
        "COALESCE(is_invalidated, 0)",
    ),
    (
        "assumption",
        "assumptions",
        ("assumption", "confidence", "description"),
        "status",
        "status IN ('verified', 'falsified', 'expired')",
    ),
    ("decision", "decisions", ("choice", "rationale", "reversibility", "description"), None, "0"),
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def export_nodes(conn: sqlite3.Connection, project_id: str) -> tuple[list[dict], dict[str, int]]:
    """This practice's artifacts as import nodes, oldest first, and how many went to ``org`` from ``public``.

    A node's ref is its own id, so a link can name a node sent in an earlier request (Cortex resolves an id it already holds).
    A table or column this store lacks is skipped, never guessed.
    """
    nodes: list[dict] = []
    public = 0
    for node_type, table, fields, closed_column, closed_sql in _TYPES:
        have = _columns(conn, table)
        if not have or "id" not in have:
            continue
        wanted = [c for c in fields if c in have]
        extra = [c for c in ("created_timestamp", "visibility") if c in have]
        where = "WHERE project_id = ?" if "project_id" in have else ""
        resolved = closed_sql if closed_column is None or closed_column in have else "0"
        try:
            rows = conn.execute(
                f"SELECT id, {', '.join(wanted + extra)}, {resolved} FROM {table} {where} ORDER BY rowid",
                (project_id,) if where else (),
            ).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            values = dict(zip(["id", *wanted, *extra, "_resolved"], row, strict=False))
            data = {k: values[k] for k in wanted if values.get(k) not in (None, "")}
            raw_visibility = (values.get("visibility") or "local").lower()
            data["visibility"] = VISIBILITY.get(raw_visibility, "project")
            public += raw_visibility == "public"
            node = {"ref": values["id"], "type": node_type, "id": values["id"], "data": data}
            if values.get("created_timestamp") is not None:
                node["created_at"] = values["created_timestamp"]
            if values.get("_resolved"):
                node["is_resolved"] = True
            nodes.append(node)
    nodes.extend(_source_nodes(conn, project_id))
    to_org = sum(1 for n in nodes if n["data"].get("visibility") == "org")
    # Said before anything moves: what the practice marked shared or public becomes visible to the whole team.
    return nodes, {"public_as_org": public, "to_team": to_org, "to_project": len(nodes) - to_org}


def _source_nodes(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    """Sources go as title, type and address; their raw files stay on this machine."""
    have = _columns(conn, "epistemic_sources")
    if not {"id", "title", "source_type"} <= have:
        return []
    keep = [c for c in ("title", "source_type", "source_url", "description", "confidence") if c in have]
    archived = "AND COALESCE(archived, 0) = 0" if "archived" in have else ""
    rows = conn.execute(
        f"SELECT id, {', '.join(keep)}, {'visibility' if 'visibility' in have else 'NULL'} FROM epistemic_sources "
        f"WHERE project_id = ? {archived} ORDER BY rowid",
        (project_id,),
    ).fetchall()
    out = []
    for row in rows:
        values = dict(zip(["id", *keep, "visibility"], row, strict=False))
        data = {k: values[k] for k in keep if values.get(k) not in (None, "")}
        data["visibility"] = VISIBILITY.get((values.get("visibility") or "local").lower(), "project")
        out.append({"ref": values["id"], "type": "source", "id": values["id"], "data": data})
    return out


def export_edges(conn: sqlite3.Connection, node_ids: set[str]) -> tuple[list[dict], int]:
    """Links between exported nodes in a relation Cortex accepts, and how many stayed local (another relation, or an end
    that is not exported, such as a goal)."""
    if not {"from_id", "to_id", "relation"} <= _columns(conn, "artifact_edges"):
        return [], 0
    edges, local = [], 0
    for from_id, to_id, relation in conn.execute("SELECT from_id, to_id, relation FROM artifact_edges ORDER BY rowid"):
        if from_id not in node_ids and to_id not in node_ids:
            continue  # another practice's link, or two things this does not export
        if relation == "addresses_goal":
            relation = "attached_to"
        if relation in EDGE_RELATIONS and from_id in node_ids and to_id in node_ids:
            edges.append({"from": from_id, "to": to_id, "relation": relation})
        else:
            local += 1
    return edges, local


def batches(nodes: list[dict], edges: list[dict], size: int = BATCH_SIZE) -> list[tuple[list[dict], list[dict]]]:
    """Requests of at most ``size`` nodes. Each link goes with the request that holds its later end, so both ends exist
    in Cortex when it is wired."""
    chunks = [nodes[i : i + size] for i in range(0, len(nodes), size)] or [[]]
    where = {n["id"]: i for i, chunk in enumerate(chunks) for n in chunk}
    links: list[list[dict]] = [[] for _ in chunks]
    for e in edges:
        links[max(where[e["from"]], where[e["to"]])].append(e)
    return list(zip(chunks, links, strict=True))


# ── the steps ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def _http_json(method: str, url: str, bearer: str, body: dict | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    import requests

    resp = requests.request(method, url, json=body, headers={"Authorization": f"Bearer {bearer}"}, timeout=timeout)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": resp.text[:300]}
    return resp.status_code, payload if isinstance(payload, dict) else {"body": payload}


def connect(
    *,
    project: dict[str, Any],
    conn: sqlite3.Connection,
    dry_run: bool = False,
    credential: Callable[[], dict] | None = None,
    http: Callable[..., tuple[int, dict]] = _http_json,
    register: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Run sign-in, register and import for one practice. Returns a report; ``ok`` is False with ``step`` naming where it
    stopped. ``project`` is {name, project_id, path, repo_url?}."""
    report: dict[str, Any] = {"ok": False, "dry_run": dry_run, "practice": project.get("name")}
    if credential is None:
        from empirica.core.auth.cortex_oauth import cortex_bearer

        credential = cortex_bearer
    cred = credential() or {}
    url, bearer = (cred.get("url") or "").rstrip("/"), cred.get("bearer")
    if not url or not bearer:
        return {**report, "step": "sign-in", "error": "not signed in to Cortex: run `empirica auth login` first"}

    status, me = http("GET", f"{url}/v1/users/me", bearer)
    if status in (401, 403):
        return {
            **report,
            "step": "sign-in",
            "error": f"Cortex rejected the credential ({status}): run `empirica auth login`",
        }
    if status >= 300:
        return {**report, "step": "sign-in", "error": f"could not read the account ({status})"}
    if not me.get("org_id"):
        return {**report, "step": "team", "error": NO_TEAM}
    report["org"] = me.get("org_name") or me.get("org_id")

    nodes, counts = export_nodes(conn, project["project_id"])
    edges, local_edges = export_edges(conn, {n["id"] for n in nodes})
    report.update(nodes=len(nodes), edges=len(edges), links_kept_local=local_edges, **counts)

    if not dry_run:
        if register is None:
            from empirica.cli.command_handlers.projects_commands import _register_one_project

            register = _register_one_project
        reg = register(project, url, bearer, 30.0)
        report["register"] = reg
        if reg.get("outcome") not in ("registered", "skipped"):
            return {
                **report,
                "step": "register",
                "error": reg.get("reason") or f"register answered {reg.get('status')}",
            }

    totals = {"created": 0, "skipped_existing": 0, "refused": 0, "would_create": 0, "edges_wired": 0}
    errors: list = []
    source = {"machine": socket.gethostname(), "practice": project.get("name"), "exported_at": time.time()}
    for i, (chunk, links) in enumerate(batches(nodes, edges)):
        if not chunk and not links:
            continue
        body = {
            "project_id": project["project_id"],
            "nodes": chunk,
            "edges": links,
            "source": source,
            "dry_run": dry_run,
        }
        status, out = http("POST", f"{url}/v1/artifacts/import", bearer, body, 120.0)
        if status >= 300 or not out.get("ok"):
            return {
                **report, **totals, "step": "import", "batch": i,
                "error": f"import answered {status}: {(out.get('errors') or [out.get('error')])[:3]}",
            }  # fmt: skip
        for key in totals:
            value = out.get(key)
            totals[key] += len(value) if isinstance(value, list) else int(value or 0)
        errors.extend(out.get("errors") or [])
    report.update(totals, errors=errors[:20], ok=True, step="done")
    report["note"] = "Raw source files stay on this machine; Cortex holds each source's title, type and address."
    return report


def practice_of(root: str) -> dict[str, Any] | None:
    """{name, project_id, path, repo_url?} for the practice at ``root`` from its project.yaml, or None."""
    import yaml

    path = os.path.join(root, ".empirica", "project.yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return None
    project_id = data.get("project_id") or data.get("id")
    name = data.get("ai_id") or data.get("name") or os.path.basename(root)
    if not project_id:
        return None
    return {
        "name": name,
        "project_id": project_id,
        "path": root,
        **({"repo_url": data["repo_url"]} if data.get("repo_url") else {}),
    }
