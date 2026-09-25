#!/usr/bin/env python3
"""Census of except handlers that return a value without saying anything loud.

A handler counts when it catches, returns, and neither raises nor logs at
WARNING or above, nor prints, nor writes to stderr. Those are the sites where a
failure can come back as a confident answer: a predicate saying "no", a count
saying zero, a lookup saying "absent". The census does not judge them; it lists
and ranks them for a person to triage, and by-design verdicts go into
.broccoli-accept so the next pass does not re-litigate them.

Rank favours what the value FEEDS (goal 61da889f): predicate-named functions,
boolean and zero returns, and modules on the hook, POSTFLIGHT, calibration or
doctor paths.

    python3 scripts/silent_handler_census.py              # ranked TSV, all sites
    python3 scripts/silent_handler_census.py --top 40     # the triage slice
    python3 scripts/silent_handler_census.py --count      # just the total

Measured 2026-09-25 with the seven files the first pass triaged excluded: 883
sites, then 861 once the 22 git-notes store checks were made loud (bf2da231d).
A regex census over the same tree says 1275, because it cannot see a returned
error payload or a log more than a couple of lines away; use this one. The
count is a trend to watch, not a target: most sites are by design.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

LOUD = {"warning", "error", "exception", "critical"}
TRIAGED_FIRST_PASS = {
    "sentinel-gate.py",
    "session_resolver.py",
    "session-init.py",
    "bayesian_beliefs.py",
    "artifact_log_commands.py",
    "mailbox_commands.py",
    "identity_migration.py",
}
_PREDICATE = re.compile(
    r"^_?(is|has|can|should)_|check|verify|valid|gate|allow|deny|exists|verdict|passed|match|owned|alive|running|live"
)
_HOT_PATH = re.compile(r"hooks|post_test|_workflow|sentinel|calibration|grounded|compliance|doctor")


def _signals(body: list[ast.stmt]) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            found.add("raise")
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in LOUD | {"debug", "info"}:
                found.add(f.attr)
            elif isinstance(f, ast.Name) and f.id == "print":
                found.add("print")
            elif isinstance(f, ast.Attribute) and f.attr == "write" and "stderr" in ast.unparse(f.value):
                found.add("stderr")
    return found


def census(root: Path, exclude: set[str]):
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root.parent))
        if "/tests/" in rel or path.name in exclude:
            continue
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for node in ast.walk(fn):
                if not isinstance(node, ast.Try):
                    continue
                for handler in node.handlers:
                    returns = [
                        r for r in ast.walk(ast.Module(body=handler.body, type_ignores=[])) if isinstance(r, ast.Return)
                    ]
                    if not returns:
                        continue
                    signals = _signals(handler.body)
                    if signals & (LOUD | {"raise", "print", "stderr"}):
                        continue
                    value = ast.unparse(returns[0].value) if returns[0].value is not None else "None"
                    caught = ast.unparse(handler.type) if handler.type is not None else "BARE"
                    yield rel, handler.lineno, fn.name, caught, value[:80], ",".join(sorted(signals)) or "none"


def score(row) -> int:
    rel, _line, fn, _caught, value, logs = row
    s = 3 if _PREDICATE.search(fn.lower()) else 0
    s += 2 if value in ("False", "True", "0", "0.0") else 0
    s += 1 if value in ("[]", "{}") else 0
    s += 1 if _HOT_PATH.search(rel) else 0
    s += 1 if logs == "none" else 0
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="empirica", help="package root to scan (default: empirica)")
    ap.add_argument("--top", type=int, default=0, help="print only the N highest-ranked sites")
    ap.add_argument("--count", action="store_true", help="print only the number of sites")
    ap.add_argument("--include-first-pass", action="store_true", help="also scan the seven files already triaged")
    args = ap.parse_args()

    exclude = set() if args.include_first_pass else TRIAGED_FIRST_PASS
    rows = sorted(census(Path(args.root), exclude), key=lambda r: (-score(r), r[0], r[1]))
    if args.count:
        print(len(rows))
        return 0
    for row in rows[: args.top] if args.top else rows:
        print("\t".join([str(score(row)), *map(str, row)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
