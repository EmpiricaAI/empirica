"""
Domain registry CLI command handlers (A1 — SPEC 1 Part 1).

Commands:
  domain-validate   Validate all YAML domain files

The inspection verbs (domain-list / domain-show / domain-resolve) were
pruned in the CLI-surface cut — the DomainRegistry itself remains
load-bearing (calibration tuple resolution); validate is kept as the
YAML guard-rail for that config.
"""

from __future__ import annotations

import json
from pathlib import Path

from empirica.config.domain_registry import DomainRegistry


def _get_registry(args) -> DomainRegistry:
    """Build registry from project context."""
    project_path = getattr(args, "project_path", None)
    if project_path is None:
        try:
            from empirica.utils.session_resolver import InstanceResolver

            project_path = InstanceResolver.project_path()
        except Exception:
            pass
    return DomainRegistry(
        project_path=Path(project_path) if project_path else None,
    )


def handle_domain_validate_command(args):
    """Validate all YAML domain files."""
    reg = _get_registry(args)
    domains = reg.list_domains()
    output = getattr(args, "output", "text")
    errors = []

    for name in domains:
        entry = reg.get_domain_entry(name)
        if entry is None:
            errors.append(f"{name}: entry is None after loading")
            continue
        if not entry.criticalities:
            errors.append(f"{name}: no criticality levels defined")

    if output == "json":
        print(
            json.dumps(
                {
                    "ok": len(errors) == 0,
                    "domains_validated": len(domains),
                    "errors": errors,
                },
                indent=2,
            )
        )
    else:
        if errors:
            print(f"❌ Validation found {len(errors)} error(s):")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"✓ All {len(domains)} domain(s) valid.")

    return {"ok": len(errors) == 0}
