"""Engagement Commands — CLI surface for the engagement substrate.

engagement-create/list/show/walk over the engagements sidecar
(WorkspaceDBRepository). engagement-create rides the entities-mint path
(``mint_entity``) and then writes the sidecar row — no parallel writer. The
engagement is the OPERATIONAL projection (plain SQL, no confidence/epistemic
fields); diagnostic findings stay epistemic and link in via entity_artifacts.

Verbs:
- engagement-create: mint the engagement entity + create its sidecar row
                     (+ optional --org link with role='ticket_of')
- engagement-list:   list engagements, filtered by domain / lifecycle / org
- engagement-show:   one engagement + its membership edges
- engagement-walk:   BFS the membership graph from an engagement
"""

from __future__ import annotations

import json
import sys

from ...data.repositories.workspace_db import WorkspaceDBRepository
from ..cli_utils import handle_cli_error
from .entity_commands import mint_entity


def _emit_user_error(output: str, message: str, error: str = "invalid_argument") -> None:
    """Print a clean user-facing error (no stack trace) and exit 1."""
    payload = {"ok": False, "error": error, "message": message}
    if output == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(f"❌ {message}", file=sys.stderr)
    sys.exit(1)


def handle_engagement_create_command(args):
    """engagement-create — mint the engagement entity, then create the sidecar row.

    Idempotent end-to-end: re-running returns the existing engagement (the
    mint dedupes by slug; the sidecar create is skipped if the row exists).
    """
    try:
        output = getattr(args, "output", "human")
        title = args.title
        result = mint_entity(
            entity_type="engagement",
            name=title,
            entity_id=getattr(args, "id", None),
            description=getattr(args, "description", None),
        )
        if not result.get("ok"):
            print(
                json.dumps(result, indent=2, default=str) if output == "json" else f"❌ {result.get('error')}",
                file=sys.stderr if output != "json" else sys.stdout,
            )
            sys.exit(1)
        eid = result["entity_id"]
        org = getattr(args, "org", None)
        with WorkspaceDBRepository.open() as repo:
            engagement = repo.get_engagement(eid)
            sidecar_created = engagement is None
            if engagement is None:
                try:
                    engagement = repo.create_engagement(
                        eid,
                        title,
                        domain=getattr(args, "domain", None),
                        stage=getattr(args, "stage", None),
                        engagement_type=getattr(args, "engagement_type", "outreach"),
                        description=getattr(args, "description", None),
                    )
                except ValueError as ve:
                    _emit_user_error(output, str(ve))
            if org:
                repo.upsert_entity_membership("engagement", eid, "organization", org, role="ticket_of")
        if output == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "entity_id": eid,
                        "entity_created": result["created"],
                        "sidecar_created": sidecar_created,
                        "org": org,
                        "engagement": engagement,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            verb = "created" if (result["created"] or sidecar_created) else "exists"
            print(f"🤝 Engagement {verb}: {eid}")
            if org:
                print(f"   linked to organization:{org} (ticket_of)")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        handle_cli_error(e, "engagement-create", getattr(args, "verbose", False))


def handle_engagement_list_command(args):
    """engagement-list — list engagements with optional domain/lifecycle/org filters."""
    try:
        output = getattr(args, "output", "human")
        try:
            with WorkspaceDBRepository.open() as repo:
                rows = repo.list_engagements(
                    domain=getattr(args, "domain", None),
                    lifecycle_state=getattr(args, "lifecycle", None),
                    org_id=getattr(args, "org", None),
                    limit=getattr(args, "limit", 100),
                )
        except ValueError as ve:
            _emit_user_error(output, str(ve))
        if output == "json":
            print(json.dumps({"ok": True, "count": len(rows), "engagements": rows}, indent=2, default=str))
            return
        if not rows:
            print("(no engagements)")
            return
        for e in rows:
            dom = e.get("domain") or "-"
            stage = e.get("stage") or "-"
            print(
                f"🤝 {e['engagement_id']:24} {e.get('lifecycle_state', 'open'):12} "
                f"{dom:10} {stage:22} {e.get('title', '')}"
            )
    except Exception as e:
        handle_cli_error(e, "engagement-list", getattr(args, "verbose", False))


def _resolve_engagement(repo, eid: str) -> dict | None:
    """Resolve an engagement by sidecar id, falling back to entity-registry
    prefix resolution (so a short id works the same as for entities)."""
    engagement = repo.get_engagement(eid)
    if engagement is None:
        ent = repo.get_entity("engagement", eid)
        if ent:
            engagement = repo.get_engagement(ent["entity_id"])
    return engagement


def handle_engagement_show_command(args):
    """engagement-show — one engagement's record + its membership edges."""
    try:
        output = getattr(args, "output", "human")
        eid = args.engagement_id
        with WorkspaceDBRepository.open() as repo:
            engagement = _resolve_engagement(repo, eid)
            if engagement is None:
                _emit_user_error(
                    output,
                    f"No engagement matches {eid!r} (full id or unambiguous prefix required)",
                    error="engagement_not_found",
                )
            real_id = engagement["engagement_id"]
            memberships = repo.get_entity_memberships("engagement", real_id)
        if output == "json":
            print(json.dumps({"ok": True, "engagement": engagement, "memberships": memberships}, indent=2, default=str))
            return
        print(f"# engagement:{real_id}")
        print(f"  title:           {engagement.get('title', '')}")
        print(f"  lifecycle_state: {engagement.get('lifecycle_state', 'open')}")
        print(f"  domain / stage:  {engagement.get('domain') or '-'} / {engagement.get('stage') or '-'}")
        if engagement.get("outcome"):
            print(f"  outcome:         {engagement['outcome']}")
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
    except Exception as e:
        handle_cli_error(e, "engagement-show", getattr(args, "verbose", False))


def handle_engagement_walk_command(args):
    """engagement-walk — BFS the membership graph from an engagement."""
    try:
        output = getattr(args, "output", "human")
        eid = args.engagement_id
        depth = getattr(args, "depth", 2)
        with WorkspaceDBRepository.open() as repo:
            engagement = _resolve_engagement(repo, eid)
            if engagement is None:
                _emit_user_error(
                    output,
                    f"No engagement matches {eid!r}",
                    error="engagement_not_found",
                )
            result = repo.walk_entity_graph("engagement", engagement["engagement_id"], max_depth=depth)
        if output == "json":
            print(json.dumps({"ok": True, **result}, indent=2, default=str))
            return
        for n in result["nodes"]:
            print(f"{'  ' * n['depth']}{n['entity_type']}:{n['entity_id'][:8]}  {n.get('display_name', '')}")
        if result["truncated"]:
            print(f"\n  (truncated at depth {depth} — increase with --depth)")
    except Exception as e:
        handle_cli_error(e, "engagement-walk", getattr(args, "verbose", False))
