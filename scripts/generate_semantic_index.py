#!/usr/bin/env python3
"""Regenerate docs/SEMANTIC_INDEX.yaml on demand.

The loader at empirica/config/semantic_index_loader.py auto-scans when
the cache is stale, so this script is no longer needed for correctness —
it stays as an explicit way to:
  1. Refresh the committed YAML inventory (for human inspection)
  2. Dry-run the scan to compare cached vs current

Scan logic lives in empirica.core.docs.semantic_scan; this script is a
thin wrapper that writes the result to disk.

Usage:
    python3 scripts/generate_semantic_index.py             # Write to docs/
    python3 scripts/generate_semantic_index.py --dry-run   # Preview
    python3 scripts/generate_semantic_index.py --output .empirica
    python3 scripts/generate_semantic_index.py --no-embed   # defer the Qdrant re-index

Writing the index and embedding it are ONE operation: the file on disk being
ahead of Qdrant is invisible at the point of use (docs-explain just quietly
answers from keyword search), so the embed runs by default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the empirica package importable when running from a checkout
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _summarize(entries: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries.values():
        t = entry.get("doc_type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def _health(project_root: Path, entries: dict) -> dict[str, int] | None:
    """Both sides of the ledger, not just the flattering one.

    `total_docs_indexed` counts what the rules MATCHED, not what exists. It
    only goes up, and it looks best right after you narrow the rules — a
    coverage figure wearing a health figure's clothes. UNCLASSIFIED is the
    number that can fall, so it is the one that makes silence-by-oversight
    visible.

    Returns None outside a git checkout, where "tracked" has no meaning.
    """
    import subprocess

    from empirica.core.docs.semantic_scan import _is_marked_internal

    try:
        listed = subprocess.run(
            ["git", "ls-files", "--", "*.md"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if listed.returncode != 0:
            return None
    except Exception:
        return None

    # Filter in Python, not in the pathspec. git's default pathspec globbing
    # lets `*` cross `/`, so `*.md` matched every markdown file in the tree —
    # empirica/, tests/, .github/ — and the ledger reported 192 tracked docs
    # against 136 real ones. A denominator that counts the wrong population
    # makes UNCLASSIFIED unfalsifiable, which is the exact failure this ledger
    # exists to expose. Caught by cross-checking the number rather than reading it.
    tracked = [
        p.strip()
        for p in listed.stdout.splitlines()
        if p.strip() and (p.startswith("docs/") or "/" not in p.strip())
    ]
    if not tracked:
        return None

    internal = sum(1 for p in tracked if _is_marked_internal(project_root / p, project_root))
    indexed = sum(1 for p in tracked if p in entries)
    return {
        "tracked_docs": len(tracked),
        "indexed": indexed,
        "deliberately_internal": internal,
        "unclassified": len(tracked) - indexed - internal,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SEMANTIC_INDEX.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--output", default="docs", help="Output directory (default: docs/)")
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Write the index but skip the Qdrant re-index (leaves Qdrant behind the file on disk)",
    )
    args = parser.parse_args()

    from empirica.core.docs.semantic_scan import scan_project

    project_root = Path(args.root).resolve()
    entries = scan_project(project_root)

    print(f"Scanned {project_root}")
    print(f"Total entries: {len(entries)}")
    for t, count in sorted(_summarize(entries).items(), key=lambda x: -x[1]):
        print(f"  {t}: {count}")

    if args.dry_run:
        print("\n--- Preview (first 20 entries) ---")
        for path, meta in list(entries.items())[:20]:
            desc = (meta.get("description") or "")[:60]
            print(f"  {path}: {desc}")
        print("\n(dry-run, not written)")
        return 0

    import yaml
    output_dir = project_root / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "SEMANTIC_INDEX.yaml"
    index = {
        "version": "1.0",
        "generated_by": "scripts/generate_semantic_index.py",
        "total_docs_indexed": len(entries),
        "index": entries,
    }
    output_path.write_text(
        yaml.dump(index, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"\nWritten to: {output_path}")

    health = _health(project_root, entries)
    if health:
        print(
            f"\nCoverage ledger: {health['indexed']} indexed"
            f" · {health['deliberately_internal']} deliberately internal"
            f" · {health['unclassified']} UNCLASSIFIED"
            f"  (of {health['tracked_docs']} tracked docs)"
        )
        if health["unclassified"]:
            print("  UNCLASSIFIED is the number that can fall — a doc no rule matched and no")
            print("  marker excluded is silent by oversight, not by decision.")

    if args.no_embed:
        print("\nSkipped embedding (--no-embed). The index on disk is ahead of Qdrant.")
        return 0

    # The generator used to PRINT this step and leave it to operator memory.
    # A printed instruction is not a mechanism: a peer practice restructured 24
    # files, regenerated the index, reported success, and never ran it — index
    # current, embedding empty, and docs-explain silently on the keyword path.
    # If the operation requires a second step, the second step is part of it.
    print("\nEmbedding into Qdrant...")
    import subprocess

    embed = subprocess.run(["empirica", "project-embed"], cwd=project_root, timeout=600)
    if embed.returncode != 0:
        # Non-zero: the index was written but the operation did NOT complete.
        # Returning 0 here would be partial-success-as-success — precisely the
        # silence this change exists to remove.
        print("\nEMBED FAILED — the index on disk is ahead of Qdrant, so docs-explain")
        print("will fall back to keyword search. Re-run `empirica project-embed`, or")
        print("pass --no-embed if you meant to defer it.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
