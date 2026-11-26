"""`empirica history-import --from <old practice>`: copy your local history from an old practice into this one (goal f357ecf4).

The engine and its rules are empirica.core.history_import. This handler finds both practices' stores and identities from
their .empirica/project.yaml, previews by default, and says what is left to do afterwards (search indexing; git notes are
not copied).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml


def _practice(path: Path) -> dict[str, Any]:
    """The practice at or above ``path``: its root, project_id, ai_id and live store."""
    for root in (path, *path.parents):
        project_yaml = root / ".empirica" / "project.yaml"
        if project_yaml.exists():
            data = yaml.safe_load(project_yaml.read_text(encoding="utf-8")) or {}
            pid = data.get("project_id")
            if not pid:
                raise ValueError(f"{project_yaml} has no project_id")
            return {
                "root": root,
                "project_id": str(pid),
                "ai_id": str(data.get("ai_id") or root.name),
                "store": root / ".empirica" / "sessions" / "sessions.db",
            }
    raise ValueError(f"no Empirica practice at or above {path} (no .empirica/project.yaml)")


def handle_history_import_command(args) -> int:
    from empirica.core.history_import import copy_history

    output = getattr(args, "output", "human")
    apply = bool(getattr(args, "apply", False))
    try:
        source = _practice(Path(args.from_path).expanduser().resolve())
        target = _practice(Path(getattr(args, "into", None) or Path.cwd()).expanduser().resolve())
        if source["root"] == target["root"]:
            raise ValueError("--from names this practice; run it from the practice you are copying INTO")
        result = copy_history(
            source["store"],
            target["store"],
            old_project_id=source["project_id"],
            new_project_id=target["project_id"],
            old_ai_id=source["ai_id"],
            new_ai_id=target["ai_id"],
            apply=apply,
        )
    except (ValueError, FileNotFoundError) as e:
        if output == "json":
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"❌ {e}", file=sys.stderr)
        return 1

    result.update(
        {
            "ok": True,
            "from": {k: str(source[k]) for k in ("root", "project_id", "ai_id")},
            "into": {k: str(target[k]) for k in ("root", "project_id", "ai_id")},
            "next": (
                ["empirica project-embed  (index the copied artifacts for project-search)"]
                if apply
                else ["re-run with --apply to copy"]
            )
            + ["git notes were not copied: the old repo keeps them"],
        }
    )
    if output == "json":
        print(json.dumps(result, indent=2, default=str))
        return 0
    total = sum(result["tables"].values())
    verb = "Copied" if apply else "Would copy"
    print(f"{verb} {total} rows from {source['ai_id']} ({source['root']}) into {target['ai_id']} ({target['root']})")
    for name, n in sorted(result["tables"].items(), key=lambda kv: -kv[1]):
        if n:
            print(f"   {name}: {n}")
    if result["kept_in_target"]:
        print(f"   kept (already in this practice, not overwritten): {sum(result['kept_in_target'].values())} rows")
    if result["missing_in_target"]:
        print(f"   ⚠️  not copied, this practice has no such table: {result['missing_in_target']}")
    if result.get("backup"):
        print(f"   backup of this practice's store: {result['backup']}")
    for step in result["next"]:
        print(f"→ {step}")
    return 0
