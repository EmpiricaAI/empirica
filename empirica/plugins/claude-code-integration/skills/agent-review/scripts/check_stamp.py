#!/usr/bin/env python3
"""Check a reviewer's stamp against the turns it was made from. Mechanical, no model.

    python3 check_stamp.py STAMP.json TURNS.json

An artifact is anchor-valid when its anchor names a turn that exists AND its quote is a verbatim
substring of that turn (whitespace collapsed, HTML entities decoded). That proves the evidence exists
where the reviewer says it does. It does NOT prove the artifact is typed correctly, or that the
agent's own claim in that turn is true: see "self_report_share" in the report.

Exit code 1 if any artifact fails, so a pipeline can refuse an unchecked stamp.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def check(stamp: dict, turns: list[dict]) -> dict:
    by_n = {t["n"]: t for t in turns}
    last = max(by_n) if by_n else None
    artifacts = stamp.get("artifacts", [])
    problems: list[dict] = []
    valid = final_report = hindsight = 0
    for i, art in enumerate(artifacts):
        turn = by_n.get(art.get("anchor"))
        quote = _norm(art.get("quote"))
        if turn is None:
            problems.append({"artifact": i, "reason": f"anchor {art.get('anchor')!r} is not a turn in this thread"})
            continue
        if not quote:
            problems.append({"artifact": i, "reason": "empty quote"})
            continue
        if quote not in _norm(turn["text"]):
            problems.append({"artifact": i, "reason": f"quote is not a verbatim substring of T{turn['n']}"})
            continue
        valid += 1
        final_report += art.get("anchor") == last
        hindsight += bool(art.get("needs_hindsight"))
    edges = stamp.get("edges", [])
    bad_edges = [
        i for i, e in enumerate(edges) if not (0 <= e.get("from", -1) < len(artifacts) and 0 <= e.get("to", -1) < len(artifacts))
    ]
    n = len(artifacts)
    return {
        "artifacts": n,
        "anchors_valid": valid,
        "problems": problems,
        "edges": len(edges),
        "edges_with_bad_index": bad_edges,
        "hindsight_share": round(hindsight / valid, 3) if valid else None,
        "self_report_share": round(final_report / valid, 3) if valid else None,
        "ok": n > 0 and not problems and not bad_edges,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stamp")
    ap.add_argument("turns")
    args = ap.parse_args(argv)
    report = check(json.loads(Path(args.stamp).read_text()), json.loads(Path(args.turns).read_text()))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
