"""Broken-link checker for tech docs.

General-purpose markdown link integrity check. Walks a project tree, finds
every `.md` file outside SKIP_DIRS, extracts markdown links via regex,
classifies (external URL / anchor-only / relative path), verifies relative
paths exist on disk.

Tier-prioritised output: top-level README → per-folder READMEs → all other
markdown. Exit code 0 if clean, 1 if any broken links found — fits cleanly
into CI gates and the compliance pipeline (`tech_docs` check group).

Used by:
- `empirica docs-link-check` (this CLI verb)
- Compliance pipeline (optional opt-in via `--include-link-check`)
- Release pre-publish gate (when wired)
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    "htmlcov", ".empirica", ".empirica-project",
    # Deprecated by design — broken links there are accepted noise
    "_archive",
})

# `[text](target)` and `![alt](target)` (image variant)
LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "ftp://", "tel:"))


def _is_anchor_only(target: str) -> bool:
    return target.startswith("#")


def _resolve_relative(source: Path, target: str) -> Path:
    """Resolve a relative-path link against the source file's directory.

    Strips trailing `#anchor` and `?query` from the target before resolution.
    """
    bare = target.split("#", 1)[0].split("?", 1)[0]
    if not bare:
        return source  # pure anchor → links to self
    return (source.parent / bare).resolve()


def _find_md_files(root: Path, skip_dirs: frozenset[str]) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*.md"):
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in skip_dirs for part in rel_parts):
            continue
        found.append(path)
    return sorted(found)


def _check_one_file(source: Path) -> list[dict[str, Any]]:
    """Return list of {line, text, target, reason} for broken links."""
    broken: list[dict[str, Any]] = []
    try:
        content = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [{"line": 0, "text": "", "target": "", "reason": f"read error: {e}"}]

    for line_no, line in enumerate(content.splitlines(), start=1):
        for match in LINK_RE.finditer(line):
            text = match.group(1)
            target = match.group(2).strip()

            if _is_external(target) or _is_anchor_only(target):
                continue

            # Skip Jinja/Markdown placeholders (e.g. {{ var }})
            if "{{" in target or "}}" in target:
                continue

            try:
                resolved = _resolve_relative(source, target)
            except (ValueError, OSError):
                broken.append({"line": line_no, "text": text, "target": target,
                               "reason": "could not resolve path"})
                continue

            if not resolved.exists():
                broken.append({"line": line_no, "text": text, "target": target,
                               "reason": "target not found"})

    return broken


def _scan(root: Path, skip_dirs: frozenset[str]) -> dict[str, Any]:
    """Tier-prioritised scan: top-level README → per-folder READMEs → others."""
    md_files = _find_md_files(root, skip_dirs)

    top_readme = root / "README.md"
    folder_readmes = [f for f in md_files if f.name == "README.md" and f != top_readme]
    other_md = [f for f in md_files if f.name != "README.md" and f != top_readme]

    tiers = [
        ("tier_1_top_readme", [top_readme] if top_readme.exists() else []),
        ("tier_2_folder_readmes", folder_readmes),
        ("tier_3_other_md", other_md),
    ]

    result_tiers: dict[str, dict[str, Any]] = {}
    grand_total = 0
    for tier_id, files in tiers:
        per_file: list[dict[str, Any]] = []
        tier_total = 0
        for f in files:
            broken = _check_one_file(f)
            if broken:
                per_file.append({
                    "file": str(f.relative_to(root)),
                    "broken_count": len(broken),
                    "broken": broken,
                })
                tier_total += len(broken)
        result_tiers[tier_id] = {
            "broken_total": tier_total,
            "files_with_breaks": len(per_file),
            "files": per_file,
        }
        grand_total += tier_total

    return {
        "scanned_files": len(md_files),
        "broken_total": grand_total,
        "passed": grand_total == 0,
        "tiers": result_tiers,
    }


def _format_human(report: dict[str, Any]) -> str:
    lines = [f"Scanned {report['scanned_files']} markdown files"]
    if report["broken_total"] == 0:
        lines.append("")
        lines.append("✅ All links valid — 0 broken")
        return "\n".join(lines)

    tier_labels = {
        "tier_1_top_readme": "Tier 1: Top-level README.md",
        "tier_2_folder_readmes": "Tier 2: Per-folder README.md files",
        "tier_3_other_md": "Tier 3: All other markdown files",
    }
    for tier_id, label in tier_labels.items():
        tier = report["tiers"][tier_id]
        if tier["broken_total"] == 0:
            continue
        lines.append("")
        lines.append(f"# {label}")
        lines.append(f"({tier['broken_total']} broken across {tier['files_with_breaks']} files)")
        for f in tier["files"]:
            lines.append("")
            lines.append(f"## {f['file']} ({f['broken_count']} broken)")
            for entry in f["broken"]:
                lines.append(
                    f"  line {entry['line']}: [{entry['text']}]({entry['target']}) — {entry['reason']}"
                )
    lines.append("")
    lines.append(f"❌ {report['broken_total']} broken links")
    return "\n".join(lines)


def handle_docs_link_check_command(args) -> int:
    """Handle `empirica docs-link-check`."""
    root_arg = getattr(args, "root", None)
    root = Path(root_arg).expanduser().resolve() if root_arg else Path.cwd().resolve()
    if not root.is_dir():
        print(f"Error: --root '{root}' is not a directory", file=sys.stderr)
        return 2

    extra_excludes = getattr(args, "exclude", None) or []
    skip_dirs = frozenset(DEFAULT_SKIP_DIRS | set(extra_excludes))

    report = _scan(root, skip_dirs)
    output_format = getattr(args, "output", "human")
    if output_format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(_format_human(report))

    return 0 if report["passed"] else 1
