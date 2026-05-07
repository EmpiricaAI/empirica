#!/usr/bin/env python3
"""Markdown link checker — walks the repo, finds broken relative-path links.

For each `.md` file outside vendored/build trees:
  - Extract `[text](target)` patterns.
  - Classify: external URL, anchor-only, or relative path.
  - For relative paths: resolve against the source file, check existence.
  - Emit a report grouped by source file.

Excludes external URLs (http/https/mailto) and pure anchors (#section).
Strips anchor fragments from relative paths before existence check.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    "htmlcov", ".empirica", ".empirica-project",
    # Deprecated/archived docs — broken links there are by-design, low-priority
    "_archive",
}

# Markdown link pattern: [text](url) — also matches images ![alt](url)
LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)]+)\)")


def is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "ftp://", "tel:"))


def is_anchor_only(target: str) -> bool:
    return target.startswith("#")


def resolve_target(source: Path, target: str) -> Path:
    """Resolve a relative-path link against the source file's directory.

    Strips trailing #anchor from the target before resolution.
    """
    bare = target.split("#", 1)[0].split("?", 1)[0]
    if not bare:
        return source  # pure anchor → links to self
    return (source.parent / bare).resolve()


def find_md_files(root: Path) -> list[Path]:
    """Walk root, return all .md files outside SKIP_DIRS."""
    found: list[Path] = []
    for path in root.rglob("*.md"):
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        found.append(path)
    return sorted(found)


def check_file(source: Path) -> list[tuple[int, str, str, str]]:
    """Return list of (line_no, link_text, target, reason) for broken links."""
    broken: list[tuple[int, str, str, str]] = []
    try:
        content = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [(0, "", "", f"read error: {e}")]

    for line_no, line in enumerate(content.splitlines(), start=1):
        for match in LINK_RE.finditer(line):
            text = match.group(1)
            target = match.group(2).strip()

            if is_external(target) or is_anchor_only(target):
                continue

            # Skip Jinja/Markdown placeholders (e.g. {{ var }})
            if "{{" in target or "}}" in target:
                continue

            try:
                resolved = resolve_target(source, target)
            except (ValueError, OSError):
                broken.append((line_no, text, target, "could not resolve path"))
                continue

            if not resolved.exists():
                broken.append((line_no, text, target, "target not found"))

    return broken


def main():
    md_files = find_md_files(REPO_ROOT)
    print(f"Scanned {len(md_files)} markdown files\n", file=sys.stderr)

    # Tier sources by priority
    readme = REPO_ROOT / "README.md"
    folder_readmes = [f for f in md_files if f.name == "README.md" and f != readme]
    other_md = [f for f in md_files if f.name != "README.md" and f != readme]

    tier_label_pairs = [
        ("# Tier 1: Top-level README.md", [readme] if readme.exists() else []),
        ("# Tier 2: Per-folder README.md files", folder_readmes),
        ("# Tier 3: All other markdown files", other_md),
    ]

    grand_total = 0
    for label, files in tier_label_pairs:
        tier_total = 0
        section: list[str] = []
        for f in files:
            broken = check_file(f)
            if broken:
                rel = f.relative_to(REPO_ROOT)
                section.append(f"\n## {rel} ({len(broken)} broken)")
                for line_no, text, target, reason in broken:
                    section.append(f"  line {line_no}: [{text}]({target}) — {reason}")
                tier_total += len(broken)
        if tier_total > 0:
            print(label)
            print(f"({tier_total} broken links across {sum(1 for f in files if check_file(f))} files)")
            print("\n".join(section))
            print()
        grand_total += tier_total

    print(f"\n=== TOTAL BROKEN LINKS: {grand_total} ===", file=sys.stderr)
    sys.exit(0 if grand_total == 0 else 1)


if __name__ == "__main__":
    main()
