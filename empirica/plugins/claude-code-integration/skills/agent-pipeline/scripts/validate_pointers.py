#!/usr/bin/env python3
"""Check a mapper's pointers against the files themselves, with no model involved.

    python3 validate_pointers.py POINTERS.json --root REPO --files a.py b.py [--max-span 150]

POINTERS.json is {"pointers": [...]} or a bare list; each pointer is {file, start, end, reason, note}. A pointer is dropped, with its
reason, when its file is not in the unit (unknown_file), its range is inverted or starts before line 1 (bad_range), it runs past the
end of the file (beyond_file) or spans more than --max-span lines (too_long). Prints {valid, dropped, covered_lines, total_lines};
covered_lines counts each line once, so overlapping pointers do not inflate coverage. A cheap mapper's line numbers are the first
thing worth distrusting: this is the gate between the map and the tagger.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def validate(pointers: list[dict], lines: dict[str, int], max_span: int = 150) -> dict:
    valid, dropped = [], []
    covered: set[tuple[str, int]] = set()
    for p in pointers:
        n = lines.get(p.get("file"))
        start, end = p.get("start"), p.get("end")
        if n is None:
            reason = "unknown_file"
        elif not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            reason = "bad_range"
        elif end > n:
            reason = "beyond_file"
        elif end - start + 1 > max_span:
            reason = "too_long"
        else:
            valid.append(p)
            covered.update((p["file"], line) for line in range(start, end + 1))
            continue
        dropped.append({"pointer": p, "reason": reason})
    return {"valid": valid, "dropped": dropped, "covered_lines": len(covered), "total_lines": sum(lines.values())}


def line_counts(root: Path, files: list[str]) -> dict[str, int]:
    return {f: sum(1 for _ in (root / f).open(errors="ignore")) for f in files}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pointers")
    ap.add_argument("--root", required=True)
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--max-span", type=int, default=150)
    a = ap.parse_args(argv)
    payload = json.loads(Path(a.pointers).read_text())
    pointers = payload["pointers"] if isinstance(payload, dict) else payload
    out = validate(pointers, line_counts(Path(a.root), a.files), a.max_span)
    print(json.dumps(out, indent=1))
    print(f"{len(out['valid'])} valid, {len(out['dropped'])} dropped, {out['covered_lines']} of {out['total_lines']} lines pointed at", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
