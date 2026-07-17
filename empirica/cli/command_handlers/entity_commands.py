"""
Entity Commands — CLI surface for workspace.db entity_registry.

Backs the Practice Model concept (see /empirica-constitution Section XIII):
entities (project, contact, organization, engagement, user) and their
membership edges are queryable without dropping into raw SQL.

Verbs:
- entity-list:   list entities, optionally filtered by type / status
- entity-show:   one entity + its membership edges
- entity-walk:   BFS the membership graph from a starting node
- entity-search: text-search display_name + description
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ...data.repositories.workspace_db import WorkspaceDBRepository
from ..cli_utils import handle_cli_error


def _embed_entity_row(
    entity_type: str,
    entity_id: str,
    display_name: str,
    description: str | None = None,
    metadata: dict | None = None,
    status: str = "active",
    domain: str | None = None,
    stage: str | None = None,
    emoji_state: str | None = None,
) -> None:
    """ERM §6.2 hook: embed an entity row as its own searchable workspace_index
    point after a registry write. Best-effort — a Qdrant hiccup must NEVER fail
    the mint, so all errors are swallowed (the writer itself also never raises)."""
    try:
        from empirica.core.qdrant.workspace_index import embed_entity_to_workspace_index

        embed_entity_to_workspace_index(
            entity_type=entity_type,
            entity_id=entity_id,
            display_name=display_name,
            description=description or "",
            status=status,
            metadata=metadata,
            domain=domain,
            stage=stage,
            emoji_state=emoji_state,
        )
    except Exception:
        pass


def handle_entity_reindex_command(args):
    """entity-reindex — backfill every entity_registry ROW as a §6.2 searchable
    workspace_index point (point_kind='entity'). The ERM §6.2 spec's
    ``workspace-index reindex --entities``, in core's flat-verb convention.

    Idempotent by construction (stable point ids → upsert). ``--dry-run`` counts
    the rows without embedding; ``--type`` scopes to one entity_type.
    """
    import json as _json

    try:
        from empirica.core.qdrant.workspace_index import embed_entity_to_workspace_index

        output = getattr(args, "output", "human")
        dry_run = getattr(args, "dry_run", False)
        type_filter = getattr(args, "type", None)

        # Materialize the rows, then close the repo before the (network) embed loop.
        with WorkspaceDBRepository.open() as repo:
            rows = repo.list_entities(entity_type=type_filter, status="all", limit=1_000_000)

        embedded = 0
        skipped = 0
        for r in rows:
            et, eid = r.get("entity_type"), r.get("entity_id")
            if not et or not eid:
                skipped += 1
                continue
            if dry_run:
                embedded += 1
                continue
            try:
                meta = _json.loads(r.get("metadata") or "{}")
            except (ValueError, TypeError):
                meta = {}
            ok = embed_entity_to_workspace_index(
                entity_type=et,
                entity_id=eid,
                display_name=r.get("display_name") or eid,
                description=r.get("description") or "",
                status=r.get("status") or "active",
                metadata=meta if isinstance(meta, dict) else {},
                emoji_state=r.get("emoji_state"),
            )
            embedded += 1 if ok else 0
            skipped += 0 if ok else 1

        result = {"ok": True, "embedded": embedded, "skipped": skipped, "dry_run": dry_run, "total": len(rows)}
        if output == "json":
            print(_json.dumps(result, indent=2))
        else:
            verb = "would embed" if dry_run else "embedded"
            print(
                f"🔎 entity-reindex: {verb} {embedded}/{len(rows)} entity rows into workspace_index ({skipped} skipped)"
            )
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        handle_cli_error(e, "entity-reindex", getattr(args, "verbose", False))


def _parse_entity_arg(args) -> tuple[str | None, str | None]:
    """Resolve the entity reference from positional 'type:id' or --type/--id.

    Returns (entity_type, entity_id). Either may be None if absent — the
    caller errors with a useful message.
    """
    et = getattr(args, "entity_type", None)
    eid = getattr(args, "entity_id", None)
    positional = getattr(args, "entity", None)
    if positional and ":" in positional:
        p_type, p_id = positional.split(":", 1)
        et = et or p_type.strip() or None
        eid = eid or p_id.strip() or None
    elif positional and not et and not eid:
        # Treat as id when type/id flags absent — caller error
        eid = positional
    return et, eid


def _format_entity_line(e: dict[str, Any], indent: str = "") -> str:
    short_id = e["entity_id"][:8] if len(e["entity_id"]) >= 8 else e["entity_id"]
    name = e.get("display_name", "") or "(no name)"
    status = e.get("status", "active")
    emoji = e.get("emoji_state") or ""
    suffix = f"  [{status}]" if status != "active" else ""
    return f"{indent}{emoji}{' ' if emoji else ''}{e['entity_type']:13} {short_id}  {name}{suffix}"


def handle_entity_list_command(args):
    """Handle entity-list command."""
    try:
        entity_type = getattr(args, "type", None)
        status = getattr(args, "status", "active")
        limit = getattr(args, "limit", 100)
        output = getattr(args, "output", "human")
        with WorkspaceDBRepository.open() as repo:
            entities = repo.list_entities(entity_type=entity_type, status=status, limit=limit)
        if output == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "count": len(entities),
                        "entities": entities,
                    },
                    indent=2,
                    default=str,
                )
            )
            return
        if not entities:
            print(f"No entities found (type={entity_type or 'any'}, status={status})")
            return
        print(
            f"# {len(entities)} entit{'y' if len(entities) == 1 else 'ies'} "
            f"(type={entity_type or 'any'}, status={status})"
        )
        for e in entities:
            print(_format_entity_line(e))
    except Exception as e:
        handle_cli_error(e, "entity-list", getattr(args, "verbose", False))


def _slugify(text: str) -> str:
    """Lowercase, non-alphanumerics → '-', collapsed and trimmed."""
    out = []
    prev_dash = False
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def mint_contact(
    name: str,
    email: str | None = None,
    phone: str | None = None,
    role: str | None = None,
    company_name: str | None = None,
    description: str | None = None,
    extra_metadata: dict | None = None,
    repo: WorkspaceDBRepository | None = None,
) -> dict[str, Any]:
    """Idempotent contact mint into workspace.db entity_registry.

    The directly-callable Python API behind `empirica entity-create` —
    consumers on the same box (e.g. a CRM MCP server) can import and call
    this instead of shelling out.

    Identity resolution, in order:
      1. **Email match** — an existing active contact whose metadata.email
         equals the normalized email wins. Email is the strongest identity
         key; the existing row is returned untouched (verified no-op).
      2. **Deterministic slug** — ``c-<name-slug>[-<company-slug>]``,
         matching the registry's existing human-readable convention
         (NOT an opaque hash; the live rows + the EKG traversal +
         extension rendering all use readable ids). Re-minting the same
         identity returns the existing row.
      3. **Collision disambiguation** — same slug but a DIFFERENT email
         on the existing row means a different person: the new id gains
         a deterministic 6-hex suffix derived from the new email/phone.

    Re-calling with the same identity always returns the same entity_id
    with created=False — double-execute is a verified no-op (the mesh
    idempotent-ask convention applied to the mint write).
    """
    import hashlib
    import time

    if not name or not name.strip():
        return {"ok": False, "error": "name is required"}
    name = name.strip()
    norm_email = email.strip().lower() if email else None

    def _resolve(repo: WorkspaceDBRepository) -> dict[str, Any]:
        contacts = repo.list_entities(entity_type="contact", status="active", limit=100000)

        if norm_email:
            for c in contacts:
                try:
                    meta = json.loads(c.get("metadata") or "{}")
                except json.JSONDecodeError:
                    continue
                if (meta.get("email") or "").strip().lower() == norm_email:
                    return {"ok": True, "entity_id": c["entity_id"], "created": False, "matched_by": "email"}

        slug = f"c-{_slugify(name)}"
        if company_name:
            slug += f"-{_slugify(company_name)}"

        entity_id = slug
        existing = next((c for c in contacts if c["entity_id"] == slug), None)
        if existing:
            try:
                existing_email = (json.loads(existing.get("metadata") or "{}").get("email") or "").strip().lower()
            except json.JSONDecodeError:
                existing_email = ""
            if norm_email and existing_email and existing_email != norm_email:
                # Same name/company, different person — disambiguate
                # deterministically off the new identity's strongest key.
                key = norm_email or (phone or "").strip()
                suffix = hashlib.sha256(key.encode()).hexdigest()[:6]
                entity_id = f"{slug}-{suffix}"
                if any(c["entity_id"] == entity_id for c in contacts):
                    return {"ok": True, "entity_id": entity_id, "created": False, "matched_by": "slug+suffix"}
            else:
                return {"ok": True, "entity_id": slug, "created": False, "matched_by": "slug"}

        metadata = {
            k: v
            for k, v in {
                "email": norm_email,
                "phone": phone,
                "role": role,
                "company_name": company_name,
                "minted_at": time.time(),
                "minted_by": "entity-create",
            }.items()
            if v is not None
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        repo.upsert_entity(
            entity_type="contact",
            entity_id=entity_id,
            display_name=name,
            source_db="workspace",
            source_table="contacts",
            description=description,
            metadata=json.dumps(metadata),
        )
        _embed_entity_row("contact", entity_id, name, description, metadata)  # §6.2 searchable point
        return {"ok": True, "entity_id": entity_id, "created": True, "matched_by": None}

    if repo is not None:
        return _resolve(repo)
    with WorkspaceDBRepository.open() as opened:
        return _resolve(opened)


# Deterministic human-readable id prefixes for the entity types mint_entity
# handles, matching the registry convention (contacts use 'c-' but mint via
# the specialized mint_contact path, so they're intentionally absent here).
# Used by mint_entity when no explicit --id is supplied.
_ENTITY_ID_PREFIX: dict[str, str] = {
    "engagement": "e",
    "organization": "o",
}


def mint_entity(
    entity_type: str,
    name: str,
    entity_id: str | None = None,
    description: str | None = None,
    extra_metadata: dict | None = None,
    repo: WorkspaceDBRepository | None = None,
) -> dict[str, Any]:
    """Idempotent mint for non-contact entities (engagement, organization).

    The general sibling of ``mint_contact`` — contacts keep their specialized
    email-dedupe path; engagement/organization dedupe by their slug id
    (no email key). Identity:

      - explicit ``entity_id`` if given (mesh callers that already own a
        canonical id pass it verbatim — e.g. an org id of ``o-nle``);
      - otherwise the deterministic slug ``<prefix>-<name-slug>`` where prefix
        is ``e`` (engagement) / ``o`` (organization), matching the registry's
        human-readable convention.

    Re-minting the same (entity_type, entity_id) returns the existing row with
    created=False — a verified no-op, the same idempotent-ask convention the
    contact mint follows. ``upsert_entity`` itself refreshes metadata/desc on
    re-mint, so callers can also use it to update an existing row.
    """
    import time

    if entity_type not in _ENTITY_ID_PREFIX:
        return {"ok": False, "error": f"mint_entity does not handle entity_type={entity_type!r}"}
    if not name or not name.strip():
        return {"ok": False, "error": "name is required"}
    name = name.strip()

    eid = entity_id.strip() if entity_id else f"{_ENTITY_ID_PREFIX[entity_type]}-{_slugify(name)}"

    def _resolve(repo: WorkspaceDBRepository) -> dict[str, Any]:
        existing = repo.get_entity(entity_type, eid)
        created = existing is None
        metadata = {"minted_at": time.time(), "minted_by": "entity-create"}
        if extra_metadata:
            metadata.update(extra_metadata)
        repo.upsert_entity(
            entity_type=entity_type,
            entity_id=eid,
            display_name=name,
            source_db="workspace",
            source_table=entity_type,
            description=description,
            metadata=json.dumps(metadata),
        )
        # §6.2 searchable point. Engagements re-embed with domain+stage in
        # engagement-create (written to the sidecar after this mint); the stable
        # point id makes that a clean idempotent enrichment.
        _embed_entity_row(entity_type, eid, name, description, metadata)
        # Self-heal: write the organizations-table detail row alongside the
        # registry entry (CI-safe guard, no-op if the table doesn't exist).
        # Closes the registry-without-detail orphan gap (core prop_zajvxjuk).
        if entity_type == "organization":
            meta = extra_metadata or {}
            repo.upsert_organization_detail(
                org_id=eid,
                name=name,
                domain=meta.get("domain"),
                industry=meta.get("industry"),
                org_type=meta.get("org_type", "prospect"),
                description=description,
                tags=json.dumps(meta["tags"]) if isinstance(meta.get("tags"), list) else None,
            )
        return {"ok": True, "entity_id": eid, "created": created, "matched_by": None if created else "id"}

    if repo is not None:
        return _resolve(repo)
    with WorkspaceDBRepository.open() as opened:
        return _resolve(opened)


_ENTITY_CREATE_TYPES = ("contact", "engagement", "organization")
_ENTITY_CREATE_EMOJI = {"contact": "👤", "engagement": "🤝", "organization": "🏢"}


def handle_entity_create_command(args):
    """Handle entity-create command — idempotent mint of contacts,
    engagements, and organizations into the workspace entity registry."""
    try:
        output = getattr(args, "output", "human")
        entity_type = getattr(args, "type", "contact")
        if entity_type not in _ENTITY_CREATE_TYPES:
            err = {
                "ok": False,
                "error": "unsupported_entity_type",
                "message": f"entity-create mints {', '.join(_ENTITY_CREATE_TYPES)} (got {entity_type!r}). "
                "Other entity types (project, user) are written by their owning pipelines.",
            }
            print(
                json.dumps(err, indent=2) if output == "json" else f"❌ {err['message']}",
                file=sys.stderr if output != "json" else sys.stdout,
            )
            sys.exit(1)

        extra = None
        raw_meta = getattr(args, "metadata", None)
        if raw_meta:
            extra = json.loads(raw_meta)

        if entity_type == "contact":
            # Contacts keep the specialized email-dedupe identity path.
            result = mint_contact(
                name=getattr(args, "name", None),
                email=getattr(args, "email", None),
                phone=getattr(args, "phone", None),
                role=getattr(args, "role", None),
                company_name=getattr(args, "company", None),
                description=getattr(args, "description", None),
                extra_metadata=extra,
            )
        else:
            # engagement / organization dedupe by slug id (no email key).
            result = mint_entity(
                entity_type=entity_type,
                name=getattr(args, "name", None),
                entity_id=getattr(args, "id", None),
                description=getattr(args, "description", None),
                extra_metadata=extra,
            )

        if output == "json":
            print(json.dumps(result, indent=2, default=str))
        elif result.get("ok"):
            verb = "created" if result["created"] else f"exists (matched by {result['matched_by']})"
            emoji = _ENTITY_CREATE_EMOJI.get(entity_type, "•")
            print(f"{emoji} {entity_type.capitalize()} {verb}: {result['entity_id']}")
        else:
            print(f"❌ {result.get('error')}", file=sys.stderr)
        sys.exit(0 if result.get("ok") else 1)
    except SystemExit:
        raise
    except Exception as e:
        handle_cli_error(e, "entity-create", getattr(args, "verbose", False))


def handle_entity_show_command(args):
    """Handle entity-show command."""
    try:
        et, eid = _parse_entity_arg(args)
        output = getattr(args, "output", "human")
        if not et or not eid:
            err = {
                "ok": False,
                "error": "entity_reference_required",
                "message": "Pass entity as 'type:id' (e.g. 'project:f73f3708') or use --type + --id",
                "hint": "List available entities: empirica entity-list --type <type> --limit 5",
            }
            if output == "json":
                print(json.dumps(err, indent=2))
            else:
                print(f"❌ {err['message']}", file=sys.stderr)
                print(f"   {err['hint']}", file=sys.stderr)
            sys.exit(1)
        with WorkspaceDBRepository.open() as repo:
            entity = repo.get_entity(et, eid)
            if not entity:
                err = {
                    "ok": False,
                    "error": "entity_not_found",
                    "message": f"No entity matches {et}:{eid} (full id or unambiguous prefix required)",
                    "hint": f"List candidates: empirica entity-list --type {et}",
                }
                if output == "json":
                    print(json.dumps(err, indent=2))
                else:
                    print(f"❌ {err['message']}", file=sys.stderr)
                    print(f"   {err['hint']}", file=sys.stderr)
                sys.exit(1)
            memberships = repo.get_entity_memberships(entity["entity_type"], entity["entity_id"])
        if output == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "entity": entity,
                        "memberships": memberships,
                    },
                    indent=2,
                    default=str,
                )
            )
            return
        print(f"# {entity['entity_type']}:{entity['entity_id']}")
        print(f"  display_name: {entity.get('display_name', '')}")
        if entity.get("description"):
            print(f"  description:  {entity['description']}")
        print(f"  status:       {entity.get('status', 'active')}")
        print(f"  source:       {entity['source_db']}.{entity['source_table']}")
        if memberships["member_of"]:
            print(f"\n## member_of ({len(memberships['member_of'])})")
            for m in memberships["member_of"]:
                role = f" ({m['role']})" if m.get("role") else ""
                print(f"  → {m['group_type']}:{m['group_id'][:8]}{role}")
        if memberships["members"]:
            print(f"\n## members ({len(memberships['members'])})")
            for m in memberships["members"]:
                role = f" ({m['role']})" if m.get("role") else ""
                print(f"  ← {m['entity_type']}:{m['entity_id'][:8]}{role}")
        if not memberships["member_of"] and not memberships["members"]:
            print("\n  (no membership edges)")
    except Exception as e:
        handle_cli_error(e, "entity-show", getattr(args, "verbose", False))


def handle_entity_walk_command(args):
    """Handle entity-walk command."""
    try:
        et, eid = _parse_entity_arg(args)
        depth = getattr(args, "depth", 2)
        output = getattr(args, "output", "human")
        if not et or not eid:
            err = {
                "ok": False,
                "error": "entity_reference_required",
                "message": "Pass start entity as 'type:id' or use --type + --id",
                "hint": "List available entities: empirica entity-list --type <type> --limit 5",
            }
            if output == "json":
                print(json.dumps(err, indent=2))
            else:
                print(f"❌ {err['message']}", file=sys.stderr)
                print(f"   {err['hint']}", file=sys.stderr)
            sys.exit(1)
        with WorkspaceDBRepository.open() as repo:
            result = repo.walk_entity_graph(et, eid, max_depth=depth)
        if result["root"] is None:
            err = {
                "ok": False,
                "error": "entity_not_found",
                "message": f"No entity matches {et}:{eid}",
                "hint": f"List candidates: empirica entity-list --type {et}",
            }
            if output == "json":
                print(json.dumps(err, indent=2))
            else:
                print(f"❌ {err['message']}", file=sys.stderr)
                print(f"   {err['hint']}", file=sys.stderr)
            sys.exit(1)
        if output == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "root": result["root"],
                        "nodes": result["nodes"],
                        "edges": result["edges"],
                        "truncated": result["truncated"],
                        "max_depth": depth,
                    },
                    indent=2,
                    default=str,
                )
            )
            return
        root = result["root"]
        print(
            f"# walk from {root['entity_type']}:{root['entity_id'][:8]} "
            f"(depth ≤ {depth}, {len(result['nodes'])} node{'s' if len(result['nodes']) != 1 else ''}, "
            f"{len(result['edges'])} edge{'s' if len(result['edges']) != 1 else ''})"
        )
        # Group nodes by depth for tree-style output
        by_depth: dict[int, list[dict[str, Any]]] = {}
        for n in result["nodes"]:
            by_depth.setdefault(n["depth"], []).append(n)
        for d in sorted(by_depth.keys()):
            indent = "  " * d
            for n in by_depth[d]:
                print(_format_entity_line(n, indent=indent))
        if result["truncated"]:
            print(f"\n  ⚠ Walk truncated at depth {depth} (more edges beyond)")
    except Exception as e:
        handle_cli_error(e, "entity-walk", getattr(args, "verbose", False))


def handle_entity_search_command(args):
    """Handle entity-search command — SQL LIKE by default, or semantic (§6.2
    entity-row vector points) with --semantic."""
    try:
        query = args.query
        entity_type = getattr(args, "type", None)
        status = getattr(args, "status", "active")
        limit = getattr(args, "limit", 50)
        output = getattr(args, "output", "human")
        semantic = getattr(args, "semantic", False)

        if semantic:
            # Route to the §6.2 entity-row vector points. status=all → include archived.
            from empirica.core.qdrant.workspace_index import search_workspace_index

            results = search_workspace_index(
                query_text=query,
                entity_type=entity_type,
                point_kind="entity",
                status=None if status == "all" else status,
                limit=limit,
            )
        else:
            with WorkspaceDBRepository.open() as repo:
                results = repo.search_entities(query=query, entity_type=entity_type, status=status, limit=limit)

        if output == "json":
            print(
                json.dumps(
                    {"ok": True, "query": query, "semantic": semantic, "count": len(results), "entities": results},
                    indent=2,
                    default=str,
                )
            )
            return
        if not results:
            hint = " (run entity-reindex if the vector index is empty)" if semantic else ""
            print(f"No entities match '{query}' (type={entity_type or 'any'}, status={status}){hint}")
            return
        print(
            f"# {len(results)} {'semantic ' if semantic else ''}match{'es' if len(results) != 1 else ''} for '{query}'"
        )
        for e in results:
            line = _format_entity_line(e)
            if semantic and e.get("score") is not None:
                line += f"   [{e['score']:.2f}]"
            print(line)
    except Exception as e:
        handle_cli_error(e, "entity-search", getattr(args, "verbose", False))


def _parse_ref(ref: str | None) -> tuple[str | None, str | None]:
    """Parse a 'type:id' entity reference into (type, id). Either may be None."""
    if not ref or ":" not in ref:
        return None, None
    t, i = ref.split(":", 1)
    return (t.strip() or None), (i.strip() or None)


def handle_entity_delete_command(args):
    """Handle entity-delete — soft-archive (default) or hard-delete (--hard) an entity.

    Default (soft-archive): sets the entity_registry row's status='archived' and
    closes its active memberships — reversible and auditable. ``--hard`` does an
    irreversible dependent-order cascade (entity_artifacts → entity_memberships →
    engagements sidecar → registry row) and therefore requires ``--confirm``.
    ``--dry-run`` previews either path without mutating. Pass the entity as
    'type:id' (or --type + --id).
    """
    try:
        output = getattr(args, "output", "human")
        et, eid = _parse_entity_arg(args)
        if not (et and eid):
            msg = "Entity ref required as 'type:id' (e.g. engagement:e-foo) or --type/--id"
            print(
                json.dumps({"ok": False, "error": msg}, indent=2) if output == "json" else f"❌ {msg}",
                file=sys.stdout if output == "json" else sys.stderr,
            )
            sys.exit(1)

        hard = getattr(args, "hard", False)
        confirm = getattr(args, "confirm", False)
        dry_run = getattr(args, "dry_run", False)

        with WorkspaceDBRepository.open(ensure_schema=True) as repo:
            entity = repo.get_entity(et, eid)
            if not entity:
                msg = f"No entity found: {et}:{eid}"
                print(
                    json.dumps({"ok": False, "error": msg}, indent=2) if output == "json" else f"❌ {msg}",
                    file=sys.stdout if output == "json" else sys.stderr,
                )
                sys.exit(1)
            et, eid = entity["entity_type"], entity["entity_id"]  # resolve any prefix match

            try:
                mem = repo.get_entity_memberships(et, eid)
                mem_count = sum(len(v) for v in mem.values()) if isinstance(mem, dict) else 0
            except Exception:
                mem_count = 0
            try:
                art_count = len(repo.get_entity_artifacts_by_entity(et, eid))
            except Exception:
                art_count = 0

            if hard and not confirm and not dry_run:
                msg = (
                    f"--hard is irreversible (would remove {art_count} artifact link(s), "
                    f"{mem_count} membership(s), the engagements sidecar if present, and the "
                    "registry row). Re-run with --confirm, or drop --hard for a reversible soft-archive."
                )
                print(
                    json.dumps({"ok": False, "error": msg, "requires": "--confirm"}, indent=2)
                    if output == "json"
                    else f"❌ {msg}",
                    file=sys.stdout if output == "json" else sys.stderr,
                )
                sys.exit(1)

            if dry_run:
                action = "would_hard_delete" if hard else "would_archive"
                counts: dict[str, int] = {"entity_artifacts": art_count, "entity_memberships": mem_count}
            elif hard:
                action = "hard_deleted"
                counts = repo.delete_entity_hard(et, eid)
            else:
                changed = repo.archive_entity(et, eid)
                action = "archived" if changed else "already_archived"
                counts = {"memberships_closed": mem_count}

        result = {
            "ok": True,
            "action": action,
            "entity": f"{et}:{eid}",
            "display_name": entity.get("display_name"),
            "counts": counts,
        }
        if output == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"✅ {action}: {et}:{eid}  ({entity.get('display_name', '') or '(no name)'})")
            for k, v in counts.items():
                print(f"   {k}: {v}")
    except Exception as e:
        handle_cli_error(e, "entity-delete", getattr(args, "verbose", False))


def handle_entity_link_command(args):
    """Handle entity-link command — write (or soft-close) a typed membership
    edge between two entities: '<member> is <role> of <group>'.

    The write peer to entity-show/-walk's read path. Both refs are 'type:id'.
    Example: entity-link engagement:e-cowork-recovery organization:o-nle
             --role ticket_of
    """
    try:
        output = getattr(args, "output", "human")
        m_type, m_id = _parse_ref(getattr(args, "member", None))
        g_type, g_id = _parse_ref(getattr(args, "group", None))
        if not (m_type and m_id and g_type and g_id):
            err = "Both member and group must be 'type:id' (e.g. engagement:e-x organization:o-y)"
            print(
                json.dumps({"ok": False, "error": err}, indent=2) if output == "json" else f"❌ {err}",
                file=sys.stderr if output != "json" else sys.stdout,
            )
            sys.exit(1)

        role = getattr(args, "role", None)
        notes = getattr(args, "notes", None)
        closing = getattr(args, "close", False)
        primary = getattr(args, "primary", False)

        with WorkspaceDBRepository.open(ensure_schema=True) as repo:
            if closing:
                changed = repo.close_entity_membership(m_type, m_id, g_type, g_id)
                action = "closed" if changed else "no_active_edge"
            else:
                repo.upsert_entity_membership(
                    entity_type=m_type,
                    entity_id=m_id,
                    group_type=g_type,
                    group_id=g_id,
                    role=role,
                    notes=notes,
                )
                action = "linked"
                if primary:
                    repo.set_primary_membership(m_type, m_id, g_type, g_id)
                    action = "linked_primary"

        result = {
            "ok": True,
            "action": action,
            "member": f"{m_type}:{m_id}",
            "group": f"{g_type}:{g_id}",
            "role": role,
        }
        if output == "json":
            print(json.dumps(result, indent=2, default=str))
        elif action in ("linked", "linked_primary"):
            rel = f" ({role})" if role else ""
            star = " ⭐ primary" if action == "linked_primary" else ""
            print(f"🔗 {m_type}:{m_id} → {g_type}:{g_id}{rel}{star}")
        elif action == "closed":
            print(f"🔻 Closed edge {m_type}:{m_id} → {g_type}:{g_id}")
        else:
            print(f"• No active edge {m_type}:{m_id} → {g_type}:{g_id} to close")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        handle_cli_error(e, "entity-link", getattr(args, "verbose", False))
