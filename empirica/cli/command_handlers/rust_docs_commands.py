#!/usr/bin/env python3
"""Rust-aware documentation coverage assessment.

Companion to docs_commands.handle_docs_assess (Python-focused) and the
external docpistemic CLI (framework-agnostic but Python-discovery-biased).

Both existing tools mishandle Rust forks like ecodex: they auto-discover
"features" from upstream-vendored Python tooling (e.g. codex's SDK build
scripts) and inflate the denominator with surface that doesn't need
user-facing docs. This Rust-aware check measures docstring coverage on
public Rust items in crates the project owns.

Discovery surface (v0):
    - Cargo.toml at project root → enumerate workspace members
    - For each member crate (filtered by includes/excludes), walk src/
      and count `pub fn|struct|enum|trait|mod|const|static|type|union`
      items. An item is "documented" if the line(s) immediately above
      include `///` doc comments OR `#[doc = "..."]` attributes.

Filtering:
    Read .empirica/rust_docs.toml (or the rust_docs key in
    .empirica/compliance.yaml when ported). Schema:
        [rust_docs]
        include = ["codex-rs/codex-empirica-plugin", ...]
        exclude = ["codex-rs/codex-cli", "codex-rs/vendor", ...]
    First-match-wins: include filters apply before exclude. When neither
    is set, the check walks all workspace members.

Output (JSON, --output json):
    {
        "tool": "rust-docs-assess",
        "project": <name>,
        "epistemic": {
            "overall_coverage": <0..100 float>,
            "documented_features": <int>,
            "total_features": <int>
        },
        "categories": [
            {"name": "<crate>", "documented": <int>, "total": <int>}
        ],
        "discovery": {
            "crates_walked": <int>,
            "files_walked": <int>
        }
    }

Compatible-enough with docpistemic's output shape that
compliance_report_commands._parse_docpistemic_result picks up the
coverage / documented / total fields directly. The "tool" string lets
the parser distinguish runners.

Usage:
    empirica rust-docs-assess
    empirica rust-docs-assess --project-root /path/to/repo
    empirica rust-docs-assess --output json --strict
    empirica rust-docs-assess --include codex-rs/codex-empirica-plugin

Strict mode (--strict): only `///` doc comments count as documentation.
Default mode also counts `#[doc = "..."]` attribute forms (less common
in idiomatic Rust but valid). The `//!` inner-module doc form counts
as module-level documentation for `mod` items.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Public Rust items that warrant documentation. We deliberately omit
# `pub use` (re-exports — the original item carries the docs) and
# `pub macro` (rare; rules unstable). Adding here is additive; reordering
# affects nothing since we match per-line.
PUB_ITEM_KINDS = (
    "fn",
    "struct",
    "enum",
    "trait",
    "mod",
    "const",
    "static",
    "type",
    "union",
)

# Match `pub fn ...`, `pub(crate) fn ...`, `pub(super) fn ...`, etc.
# The visibility prefix is captured and discarded; only the item kind
# and name participate in counting.
_PUB_ITEM_RE = re.compile(
    r"^\s*pub(?:\([^\)]+\))?\s+(?:async\s+|unsafe\s+|const\s+|extern\s+\"[^\"]+\"\s+)*"
    r"(?P<kind>" + "|".join(PUB_ITEM_KINDS) + r")\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
)

# Doc forms that count as "this item is documented":
# - `/// ...`        outer doc comment (most common)
# - `//! ...`        inner doc comment (module-level, applies to enclosing mod)
# - `#[doc = "..."]` attribute form (mostly proc-macros / generated code)
_OUTER_DOC_RE = re.compile(r"^\s*///")
_INNER_DOC_RE = re.compile(r"^\s*//!")
_ATTR_DOC_RE = re.compile(r"^\s*#\[\s*doc\s*=")


@dataclass
class CrateReport:
    """Per-crate documentation tally."""

    name: str
    documented: int = 0
    total: int = 0
    files_walked: int = 0


@dataclass
class AssessmentResult:
    """Aggregate output of a rust-docs-assess run."""

    project_name: str
    crates: list[CrateReport] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(c.total for c in self.crates)

    @property
    def documented(self) -> int:
        return sum(c.documented for c in self.crates)

    @property
    def coverage(self) -> float:
        if self.total == 0:
            return 100.0  # vacuously documented — empty surface
        return round(100 * self.documented / self.total, 1)

    @property
    def files_walked(self) -> int:
        return sum(c.files_walked for c in self.crates)


def _load_rust_docs_config(project_root: Path) -> dict[str, list[str]]:
    """Load include/exclude lists from .empirica/rust_docs.toml.

    Returns {"include": [...], "exclude": [...]}. Both default to empty
    (no filtering — walk every workspace member).
    """
    cfg_path = project_root / ".empirica" / "rust_docs.toml"
    if not cfg_path.exists():
        return {"include": [], "exclude": []}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        section = data.get("rust_docs", {})
        return {
            "include": list(section.get("include", [])),
            "exclude": list(section.get("exclude", [])),
        }
    except Exception:
        return {"include": [], "exclude": []}


def _enumerate_workspace_members(project_root: Path) -> list[Path]:
    """Walk Cargo.toml's [workspace] members glob to find member crate roots.

    Falls back to a single-crate result when Cargo.toml has [package]
    instead of [workspace]. Returns absolute paths to crate directories
    (containing Cargo.toml + src/).
    """
    cargo_root = project_root / "Cargo.toml"
    # Some projects keep Cargo.toml under a subdir (e.g. ecodex's
    # codex-rs/Cargo.toml). Accept either layout.
    candidates = [cargo_root]
    for nested in ("codex-rs", "rust"):
        candidates.append(project_root / nested / "Cargo.toml")
    cargo_path = next((c for c in candidates if c.exists()), None)
    if cargo_path is None:
        return []

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        with open(cargo_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []

    workspace = data.get("workspace")
    cargo_dir = cargo_path.parent
    members: list[Path] = []

    if workspace:
        # Workspace project — expand member globs.
        import glob as _glob

        excludes = set(workspace.get("exclude", []))
        for pattern in workspace.get("members", []):
            # Cargo glob support is limited but we honor the simple cases:
            # explicit dir, "*" suffix, "*/Cargo.toml" patterns.
            full = str(cargo_dir / pattern)
            for resolved in _glob.glob(full, recursive=True):
                p = Path(resolved)
                if p.name == "Cargo.toml":
                    p = p.parent
                if not p.is_dir():
                    continue
                rel = p.relative_to(cargo_dir).as_posix()
                if rel in excludes:
                    continue
                members.append(p)
    elif data.get("package"):
        # Single-crate project.
        members.append(cargo_dir)

    return sorted(members)


def _crate_matches_filters(
    crate_dir: Path, project_root: Path, includes: list[str], excludes: list[str]
) -> bool:
    """Return True when the crate should be walked given include/exclude rules.

    `includes` and `excludes` are interpreted as path prefixes relative to
    project_root (e.g. "codex-rs/codex-empirica-plugin"). Empty includes
    means "all crates allowed by exclude". Excludes always win over
    includes — the safety bias is to skip rather than over-walk.
    """
    try:
        rel = crate_dir.relative_to(project_root).as_posix()
    except ValueError:
        rel = str(crate_dir)

    for ex in excludes:
        if rel.startswith(ex):
            return False
    if not includes:
        return True
    return any(rel.startswith(inc) for inc in includes)


def _walk_rust_files(crate_dir: Path) -> list[Path]:
    """Yield .rs files under crate_dir/src/, excluding tests/benches dirs.

    Returns empty list when the crate has no src/ (e.g. a virtual
    workspace root).
    """
    src = crate_dir / "src"
    if not src.exists():
        return []
    files: list[Path] = []
    for path in src.rglob("*.rs"):
        # Skip generated / vendored / test-only files. Tests under
        # crate/tests/ are integration tests and have their own surface
        # contract — out of scope for this v0.
        rel_parts = path.relative_to(src).parts
        if any(part in ("tests", "benches", "examples") for part in rel_parts):
            continue
        files.append(path)
    return sorted(files)


def _count_documentation(file_path: Path, *, strict: bool) -> tuple[int, int]:
    """Count (documented_pub_items, total_pub_items) for a single .rs file.

    An item is "documented" iff the immediately preceding non-blank line
    is a `///` doc comment (always) OR `#[doc = "..."]` attribute (unless
    --strict). Stacked `///` lines count as one block — we don't double-count.
    Inner `//!` comments only count for the enclosing module declaration,
    which we approximate by attaching them to the next `pub mod` line.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0, 0

    lines = text.splitlines()
    documented = 0
    total = 0

    for idx, line in enumerate(lines):
        m = _PUB_ITEM_RE.match(line)
        if not m:
            continue
        total += 1

        # Walk backwards past blank lines + attribute lines (other than
        # #[doc=...]) to find the most recent doc comment.
        cursor = idx - 1
        has_doc = False
        while cursor >= 0:
            prev = lines[cursor].strip()
            if not prev:
                cursor -= 1
                continue
            # Skip non-doc attributes (e.g. #[derive(...)], #[cfg(...)])
            # so they don't sever the docstring → item association.
            if prev.startswith("#[") and not _ATTR_DOC_RE.match(lines[cursor]):
                cursor -= 1
                continue
            if _OUTER_DOC_RE.match(lines[cursor]):
                has_doc = True
            elif not strict and _ATTR_DOC_RE.match(lines[cursor]):
                has_doc = True
            elif _INNER_DOC_RE.match(lines[cursor]) and m.group("kind") == "mod":
                # //! at top of a file applies to the module.
                has_doc = True
            break

        if has_doc:
            documented += 1

    return documented, total


def assess_project(
    project_root: Path,
    *,
    strict: bool = False,
    cli_includes: list[str] | None = None,
    cli_excludes: list[str] | None = None,
) -> AssessmentResult:
    """Run a rust-docs assessment against project_root and return the result.

    `cli_includes` / `cli_excludes` override the .empirica/rust_docs.toml
    config when provided (additive — both layered). The CLI handler wires
    --include / --exclude flags into these.
    """
    cfg = _load_rust_docs_config(project_root)
    includes = list(cfg["include"]) + list(cli_includes or [])
    excludes = list(cfg["exclude"]) + list(cli_excludes or [])

    members = _enumerate_workspace_members(project_root)
    project_name = project_root.name
    result = AssessmentResult(project_name=project_name)

    for crate_dir in members:
        if not _crate_matches_filters(crate_dir, project_root, includes, excludes):
            continue
        report = CrateReport(name=crate_dir.name)
        for rust_file in _walk_rust_files(crate_dir):
            doc, tot = _count_documentation(rust_file, strict=strict)
            report.documented += doc
            report.total += tot
            report.files_walked += 1
        result.crates.append(report)

    return result


def _format_human(result: AssessmentResult) -> str:
    """Render an AssessmentResult for terminal display."""
    lines = [
        "============================================================",
        "📚 RUST-DOCS-ASSESS",
        "============================================================",
        "",
        f"🌑 Overall Coverage: {result.coverage}%",
        f"   Features: {result.documented}/{result.total} documented",
        "",
        "📋 Per-Crate Coverage:",
        "--------------------------------------------------",
    ]
    for crate in result.crates:
        if crate.total == 0:
            lines.append(f"   ◦ {crate.name}: (no public surface)")
            continue
        pct = round(100 * crate.documented / crate.total, 1)
        lines.append(f"   • {crate.name}: {pct}% ({crate.documented}/{crate.total})")
    lines.append("")
    lines.append(
        f"Discovered: {len(result.crates)} crates, {result.files_walked} .rs files"
    )
    lines.append("")
    lines.append("============================================================")
    return "\n".join(lines)


def _format_json(result: AssessmentResult) -> str:
    """Render an AssessmentResult in docpistemic-compatible JSON."""
    payload = {
        "tool": "rust-docs-assess",
        "project": result.project_name,
        "epistemic": {
            "overall_coverage": result.coverage,
            "documented_features": result.documented,
            "total_features": result.total,
        },
        "categories": [
            {
                "name": crate.name,
                "documented": crate.documented,
                "total": crate.total,
            }
            for crate in result.crates
            if crate.total > 0
        ],
        "discovery": {
            "crates_walked": len(result.crates),
            "files_walked": result.files_walked,
        },
    }
    return json.dumps(payload, indent=2)


def handle_rust_docs_assess(args) -> int:
    """CLI entry point — invoked from cli_core dispatch table."""
    project_root = Path(getattr(args, "project_root", None) or ".").resolve()
    strict = bool(getattr(args, "strict", False))
    includes = list(getattr(args, "include", []) or [])
    excludes = list(getattr(args, "exclude", []) or [])
    output_format = getattr(args, "output", "human")

    result = assess_project(
        project_root,
        strict=strict,
        cli_includes=includes,
        cli_excludes=excludes,
    )

    if output_format == "json":
        sys.stdout.write(_format_json(result) + "\n")
    else:
        sys.stdout.write(_format_human(result) + "\n")
    return 0
