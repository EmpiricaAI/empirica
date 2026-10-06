#!/usr/bin/env python3
"""Verbatim-quote check for a tagger's artifacts: the one check on a tagger that does not depend on any model's judgement.

    python3 check_tag_quotes.py TAG.json ROOT

TAG.json is one tagger output ({artifacts: [{type, text, file, line, quote, severity}], ...}) or a list of them. An artifact passes
when its quote, whitespace-normalised, appears in the lines within 3 of its stated line in ROOT/file. A missing file, a line beyond
the file or an empty quote fails. Exit 1 if any artifact fails. A passing quote shows the tagger was looking at that code; it does
not show the claim is true: that is the skeptic's and the tests' job.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

WINDOW = 3


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_quote(art: dict, root: Path, window: int = WINDOW) -> bool:
    quote = _norm(art.get("quote") or "")
    path = Path(root) / (art.get("file") or "")
    if not quote or not path.is_file():
        return False
    lines = path.read_text(errors="ignore").splitlines()
    line = art.get("line")
    if not isinstance(line, int) or not 1 <= line <= len(lines):
        return False
    i = line - 1
    return quote in _norm(" ".join(lines[max(0, i - window) : i + window + 1]))


def check(tag: dict, root: Path) -> dict:
    by_type: dict[str, list[int]] = {}
    invalid = []
    for k, art in enumerate(tag["artifacts"]):
        ok = check_quote(art, root)
        cell = by_type.setdefault(art["type"], [0, 0])
        cell[1] += 1
        cell[0] += ok
        if not ok:
            invalid.append({"index": k, "type": art["type"], "file": art.get("file"), "line": art.get("line")})
    return {"artifacts": len(tag["artifacts"]), "valid": len(tag["artifacts"]) - len(invalid), "by_type": by_type, "invalid": invalid}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    payload = json.loads(Path(args[0]).read_text())
    tags = payload if isinstance(payload, list) else [payload]
    bad = 0
    for tag in tags:
        rep = check(tag, Path(args[1]))
        bad += len(rep["invalid"])
        print(f"{tag.get('unit', '?')}: {rep['valid']}/{rep['artifacts']} quotes verbatim; by type {rep['by_type']}")
        for inv in rep["invalid"]:
            print(f"  FAIL #{inv['index']} {inv['type']} {inv['file']}:{inv['line']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
