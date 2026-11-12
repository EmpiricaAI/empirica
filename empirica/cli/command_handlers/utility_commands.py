"""
Utility Commands - Token savings, efficiency reports, and Qdrant management.

Note: Session list/show/export commands live in session_commands.py.
"""

import json

from ..cli_utils import handle_cli_error


def handle_log_token_saving(args):
    """Log a token saving event"""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()

    saving_id = db.log_token_saving(
        session_id=args.session_id, saving_type=args.type, tokens_saved=args.tokens, evidence=args.evidence
    )

    db.close()

    if args.output == "json":
        print(json.dumps({"ok": True, "saving_id": saving_id, "tokens_saved": args.tokens, "type": args.type}))
    else:
        print(f"✅ Token saving logged: {args.tokens} tokens saved ({args.type})")


def handle_efficiency_report(args):
    """Show token efficiency report for session"""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    savings = db.get_session_token_savings(args.session_id)

    if args.output == "json":
        print(json.dumps(savings, indent=2))
    else:
        print("\n📊 Token Efficiency Report")
        print("━" * 60)
        print(f"✅ Tokens Saved This Session:     {savings['total_tokens_saved']:,} tokens")
        print(f"💰 Cost Saved:                    ${savings['cost_saved_usd']:.4f} USD")

        if savings["breakdown"]:
            print("\nBreakdown:")
            for saving_type, data in savings["breakdown"].items():
                type_label = saving_type.replace("_", " ").title()
                print(f"  {type_label:.<30} {data['tokens']:,} tokens ({data['count']}x)")
        else:
            print("\n  (No token savings logged yet)")

        print("━" * 60)

    db.close()


def handle_qdrant_cleanup_command(args):
    """Remove empty Qdrant collections to reduce resource usage (#49)."""
    try:
        from empirica.core.qdrant.collections import cleanup_empty_collections

        dry_run = not getattr(args, "execute", False)
        result = cleanup_empty_collections(dry_run=dry_run)

        if "error" in result:
            if getattr(args, "output", "human") == "json":
                print(json.dumps({"ok": False, "error": result["error"]}))
            else:
                print(f"Error: {result['error']}")
            return 1

        if getattr(args, "output", "human") == "json":
            print(json.dumps({"ok": True, **result}, indent=2))
        else:
            action = "Deleted" if not dry_run else "Would delete"
            items = result.get("deleted" if not dry_run else "would_delete", [])

            print(f"\nQdrant Collection Cleanup {'(DRY RUN)' if dry_run else ''}")
            print("=" * 60)
            print(f"Total collections:     {result['total']}")
            print(f"Empty (0 points):      {result['empty_count']}")
            print(f"Non-empty (with data): {result['non_empty_count']}")
            print()

            if items:
                print(f"{action} {len(items)} empty collection(s):")
                for name in sorted(items):
                    print(f"  - {name}")
            else:
                print("No empty collections found.")

            if result.get("kept"):
                print(f"\nKept {len(result['kept'])} collection(s) with data:")
                for c in sorted(result["kept"], key=lambda x: x["name"]):
                    print(f"  - {c['name']} ({c['points']} points)")

            if dry_run and items:
                print("\nTo actually delete, run: empirica qdrant-cleanup --execute")

    except Exception as e:
        handle_cli_error(e, "Qdrant cleanup", getattr(args, "verbose", False))


def _group_collections_by_project(info):
    """Group Qdrant collections by project. Returns (projects_dict, globals_list)."""
    projects = {}
    globals_list = []
    for c in info:
        name = c["name"]
        if name.startswith("project_"):
            parts = name.split("_")
            if len(parts) >= 3:
                coll_type = parts[-1]
                project_id = "_".join(parts[1:-1])
                if project_id not in projects:
                    projects[project_id] = []
                projects[project_id].append({"type": coll_type, "points": c["points"], "dimensions": c["dimensions"]})
            else:
                globals_list.append(c)
        else:
            globals_list.append(c)
    return projects, globals_list


def _print_qdrant_status_human(info, projects, globals_list, total_points, empty_count):
    """Print Qdrant status in human-readable format."""
    print("\nQdrant Collection Inventory")
    print("=" * 60)
    print(f"Total collections: {len(info)}")
    print(f"Total points:      {total_points:,}")
    print(f"Empty collections: {empty_count}")
    print(f"Projects:          {len(projects)}")

    for pid in sorted(projects.keys()):
        colls = projects[pid]
        proj_points = sum(c["points"] or 0 for c in colls)
        proj_empty = sum(1 for c in colls if (c["points"] or 0) == 0)
        print(f"\n  Project: {pid[:12]}...")
        print(f"    Collections: {len(colls)} ({proj_empty} empty)")
        print(f"    Points: {proj_points:,}")
        for c in sorted(colls, key=lambda x: x["type"]):
            marker = " " if (c["points"] or 0) > 0 else "x"
            print(f"      [{marker}] {c['type']}: {c['points'] or 0} points")

    if globals_list:
        print("\n  Global collections:")
        for c in sorted(globals_list, key=lambda x: x["name"]):
            print(f"    - {c['name']}: {c['points'] or 0} points")

    if empty_count > 0:
        print(f"\nTip: Run 'empirica qdrant-cleanup' to remove {empty_count} empty collections")


def handle_qdrant_status_command(args):
    """Show Qdrant collection inventory and stats."""
    try:
        from empirica.core.qdrant.collections import get_collection_info

        info = get_collection_info()

        if info is None or (isinstance(info, list) and len(info) == 0):
            from empirica.core.qdrant.connection import _check_qdrant_available

            if not _check_qdrant_available():
                if getattr(args, "output", "human") == "json":
                    print(json.dumps({"ok": False, "error": "Qdrant not available"}))
                else:
                    print("Qdrant is not available. Set EMPIRICA_QDRANT_URL or start Qdrant.")
                return 1

        projects, globals_list = _group_collections_by_project(info)
        total_points = sum(c["points"] or 0 for c in info)
        empty_count = sum(1 for c in info if (c["points"] or 0) == 0)

        if getattr(args, "output", "human") == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "total_collections": len(info),
                        "total_points": total_points,
                        "empty_collections": empty_count,
                        "projects": dict(projects.items()),
                        "global_collections": globals_list,
                    },
                    indent=2,
                )
            )
        else:
            _print_qdrant_status_human(info, projects, globals_list, total_points, empty_count)

    except Exception as e:
        handle_cli_error(e, "Qdrant status", getattr(args, "verbose", False))
