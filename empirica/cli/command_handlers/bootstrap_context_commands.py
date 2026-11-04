"""Handler for `empirica bootstrap-context` — emits the v2 wire shape.

Thin CLI wrapper over `empirica.core.bootstrap.build_bootstrap_payload`.
Available directly for users who want to inspect the payload (`empirica
bootstrap-context --output json | jq`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def handle_bootstrap_context_command(args) -> int:
    """Emit the bootstrap context payload (schema v2) as JSON."""
    from empirica.core.bootstrap import build_bootstrap_payload

    project_path = getattr(args, "project_path", None)
    if not project_path:
        # Resolve via canonical chain (instance_projects → active_work)
        try:
            from empirica.utils.session_resolver import InstanceResolver as R

            project_path = R.project_path()
        except Exception:
            project_path = None

    if not project_path:
        print(
            "Error: no project bound. Pass --project-path or run from inside a project tree.",
            file=sys.stderr,
        )
        return 2

    similarity_threshold = float(getattr(args, "similarity_threshold", 0.65))
    session_id = getattr(args, "session_id", None)

    payload = build_bootstrap_payload(
        project_path=Path(project_path),
        session_id=session_id,
        similarity_threshold=similarity_threshold,
    )

    output = getattr(args, "output", "json")
    if output == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        # Human format — concise summary
        print(f"Project: {payload.get('project_name')} ({payload.get('project_id')})")
        print(f"Schema: v{payload.get('schema_version')}")
        topic = payload.get("active_topic", {})
        print(f"Active topic: {topic.get('source', 'none')} (threshold={topic.get('similarity_threshold')})")
        for circle_key, circle in [
            ("active_state", payload.get("active_state", {})),
            ("persistent_reference", payload.get("persistent_reference", {})),
            ("topic_relevant_backlog", payload.get("topic_relevant_backlog", {})),
        ]:
            print(f"\n{circle_key}:")
            for sub_key, items in circle.items():
                print(f"  {sub_key}: {len(items)}")
    return 0
