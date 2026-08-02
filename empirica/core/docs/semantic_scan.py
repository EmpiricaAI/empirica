"""Live filesystem scan for the semantic index.

Walks docs + core modules and produces the same per-file metadata shape
the cached SEMANTIC_INDEX.yaml uses (tags, doc_type, description, concepts).

This is the load-bearing scanner; scripts/generate_semantic_index.py is a
thin wrapper around it for explicit YAML regeneration. Also called from
empirica/config/semantic_index_loader.py when the cached YAML is missing
or stale, so consumers (project-embed, doc_planner) always see live state
without the operator having to remember to regenerate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScanRule:
    """One file-category rule. The first rule that matches a path wins."""

    glob: str
    doc_type: str
    base_tags: tuple[str, ...]
    tags_from_path: bool = True
    extract_docstring: bool = False


SCAN_RULES: tuple[ScanRule, ...] = (
    ScanRule(glob="docs/architecture/**/*.md", doc_type="architecture", base_tags=("architecture",)),
    ScanRule(glob="docs/reference/**/*.md", doc_type="reference", base_tags=("reference",)),
    ScanRule(glob="docs/guides/**/*.md", doc_type="guide", base_tags=("guide",)),
    ScanRule(glob="docs/human/**/*.md", doc_type="user-docs", base_tags=("documentation",)),
    ScanRule(glob="docs/*.md", doc_type="documentation", base_tags=("documentation",)),
    # Catch-all for docs/ — LAST among the docs rules, so the typed ones above
    # still win (first match wins). Without it, a doc in any subdirectory the
    # rules do not name is silently invisible to semantic search: no error, no
    # staleness signal, it simply never answers a query. Measured 2026-08-02:
    # docs/examples/ and docs/foundation/ matched nothing, and MESSAGING_LAYERS
    # and UPGRADE_TO_1.13 were unreachable the day they were written.
    #
    # A rule per directory would fix today and re-break the next time someone
    # adds a folder. Coverage-by-default makes omission impossible instead of
    # merely remembered — the same reason the cron gate keys on `kind` rather
    # than a per-entry flag.
    ScanRule(glob="docs/**/*.md", doc_type="documentation", base_tags=("documentation",)),
    ScanRule(
        glob="empirica/core/**/*.py", doc_type="core-module", base_tags=("core", "python"), extract_docstring=True
    ),
    ScanRule(
        glob="empirica/cli/command_handlers/*.py",
        doc_type="cli-handler",
        base_tags=("cli", "commands"),
        extract_docstring=True,
    ),
    ScanRule(
        glob="empirica/data/**/*.py", doc_type="data-layer", base_tags=("data", "database"), extract_docstring=True
    ),
    ScanRule(glob="empirica/utils/*.py", doc_type="utility", base_tags=("utils",), extract_docstring=True),
    ScanRule(glob="empirica/config/*.py", doc_type="config", base_tags=("config",), extract_docstring=True),
    ScanRule(glob="*.md", doc_type="project-root", base_tags=("project",), tags_from_path=False),
)

SKIP_PATTERNS = (
    "__pycache__",
    ".pyc",
    "__init__.py",
    "build/",
    ".venv",
    ".egg-info",
    "node_modules",
)

MIN_FILE_SIZE = 100  # bytes


NOINDEX_MARKER = ".noindex"


def _should_skip(relpath: str) -> bool:
    return any(pat in relpath for pat in SKIP_PATTERNS)


def classify_audience(relpath: str) -> str:
    """Who reads this doc: "user", "developer", or "ai".

    AI-first: a practice's doc store is read by practitioners, so "ai" is the
    default and only an explicitly human-prefixed path is anything else.

    Canonical here rather than in the CLI handler because both the index
    builder and the renderer need it, and two hand-maintained copies of a
    classifier drift. `docs_commands._classify_audience` delegates to this.
    """
    path_str = relpath.lower().replace("\\", "/")
    if "human/end-users" in path_str or "human/end_users" in path_str:
        return "user"
    if "human/developers" in path_str or "human/developer" in path_str:
        return "developer"
    return "ai"


def _is_marked_internal(filepath: Path, project_root: Path) -> bool:
    """True when any ancestor directory carries a `.noindex` marker.

    The catch-all `docs/**/*.md` rule made coverage the default, which is right —
    but coverage-by-default and internal-by-choice are complementary, not
    competing. Without an opt-OUT, the catch-all sweeps in material that exists
    to be written rather than retrieved.

    Measured 2026-08-02: it pulled in `docs/research/acat_pilot_runs/**` — raw
    session transcripts — and those transcripts were the top hit for a NONSENSE
    query (score 0.25), because long diffuse prose matches everything weakly.
    Internal material does not merely waste index space; it becomes the thing
    semantic search falls back on when nothing genuinely matches.

    A marker rather than a directory list, for the same reason the rule is a
    catch-all rather than a list: a new internal folder opts out by carrying the
    file, not by someone remembering to edit a constant.
    """
    try:
        current = filepath.parent.resolve()
        root = project_root.resolve()
    except OSError:
        return False
    while True:
        if (current / NOINDEX_MARKER).exists():
            return True
        if current == root or current.parent == current:
            return False
        current = current.parent


def _extract_module_docstring(filepath: Path) -> str | None:
    """First line of the module-level triple-quoted docstring, if any."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    match = re.search(
        r'^(?:\s*#[^\n]*\n)*\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')',
        content,
        re.DOTALL,
    )
    if not match:
        return None
    doc = (match.group(1) or match.group(2) or "").strip()
    return doc.split("\n")[0].strip() or None


def _extract_md_title(filepath: Path) -> str | None:
    """First H1 or H2 heading from a markdown file."""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
                if line.startswith("## "):
                    return line[3:].strip()
    except Exception:
        return None
    return None


def _tags_from_filepath(relpath: str) -> list[str]:
    """Lowercase, dash-normalized path components as tags."""
    parts = Path(relpath).parts
    tags: list[str] = []
    for part in parts[:-1]:
        cleaned = part.replace("_", "-").replace(".", "-").lower()
        if cleaned and cleaned not in {"docs", "empirica", "src"}:
            tags.append(cleaned)
    stem = Path(relpath).stem.replace("_", "-").lower()
    if stem and len(stem) > 2:
        tags.append(stem)
    return tags


def _concepts_from_content(filepath: Path, max_concepts: int = 5) -> list[str]:
    """Top-N class/function names (.py) or headings (.md) — used for search."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")[:3000]
    except Exception:
        return []

    concepts: set[str] = set()
    if filepath.suffix == ".py":
        for match in re.finditer(r"^class\s+(\w+)", content, re.MULTILINE):
            concepts.add(match.group(1))
        for match in re.finditer(r"^def\s+(\w+)", content, re.MULTILINE):
            name = match.group(1)
            if not name.startswith("_"):
                concepts.add(name)
    elif filepath.suffix == ".md":
        for match in re.finditer(r"^#{1,3}\s+(.+)", content, re.MULTILINE):
            heading = match.group(1).strip()
            if len(heading) < 60:
                concepts.add(heading)

    return sorted(concepts)[:max_concepts]


def scan_project(project_root: Path) -> dict[str, dict[str, Any]]:
    """Scan a project tree and return the per-file metadata index.

    Same shape as the cached SEMANTIC_INDEX.yaml's `index` dict — keys are
    relative paths, values are {tags, doc_type, description?, concepts?}.
    First matching rule wins per path.
    """
    entries: dict[str, dict[str, Any]] = {}

    for rule in SCAN_RULES:
        for filepath in sorted(project_root.glob(rule.glob)):
            if not filepath.is_file():
                continue
            try:
                if filepath.stat().st_size < MIN_FILE_SIZE:
                    continue
            except OSError:
                continue

            relpath = str(filepath.relative_to(project_root))
            if _should_skip(relpath) or relpath in entries:
                continue
            if _is_marked_internal(filepath, project_root):
                continue

            tags = list(rule.base_tags)
            if rule.tags_from_path:
                tags.extend(_tags_from_filepath(relpath))
            tags = sorted(set(tags))

            description: str | None = None
            if rule.extract_docstring and filepath.suffix == ".py":
                description = _extract_module_docstring(filepath)
            elif filepath.suffix == ".md":
                description = _extract_md_title(filepath)

            entry: dict[str, Any] = {
                "tags": tags,
                "doc_type": rule.doc_type,
                "audience": classify_audience(relpath),
            }
            if description:
                entry["description"] = description
            concepts = _concepts_from_content(filepath)
            if concepts:
                entry["concepts"] = concepts

            entries[relpath] = entry

    return entries


def newest_source_mtime(project_root: Path) -> float:
    """Most recent mtime across all files matched by SCAN_RULES.

    Used by the loader to decide whether the cached YAML is stale —
    if any source file is newer than the cache, the cache is invalid.
    Walks the same globs scan_project does so the staleness signal
    matches the scan exactly.
    """
    newest = 0.0
    for rule in SCAN_RULES:
        for filepath in project_root.glob(rule.glob):
            if not filepath.is_file():
                continue
            relpath = str(filepath.relative_to(project_root))
            if _should_skip(relpath):
                continue
            try:
                mtime = filepath.stat().st_mtime
                if mtime > newest:
                    newest = mtime
            except OSError:
                continue
    return newest
