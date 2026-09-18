"""Census: except handlers that return a value with no logging, warning or raise.

Prints JSON, one row per site: file, line, enclosing function, exception type,
the returned value, whether the function is check-shaped (is_/has_/count/...),
and whether the value is a confident one (True/False/0/empty). Rank by
`verdict_shaped and confident_value` first: those are where a failure reads
as a confident answer. Used for goal 61da889f; the seven files in TRIAGED
were judged per site before this script existed.

    python3 scripts/silent_handler_census.py | jq '[.[] | select(.verdict_shaped and .confident_value)]'
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "empirica"
TRIAGED = {
    "sentinel-gate.py",
    "session_resolver.py",
    "session-init.py",
    "bayesian_beliefs.py",
    "artifact_log_commands.py",
    "mailbox_commands.py",
    "identity_migration.py",
}
LOUD = {"warning", "error", "exception", "critical", "warn", "print", "warn_unless_missing_table", "handle_cli_error"}
VERDICTY = re.compile(
    r"^(is_|has_|can_|should_|check|verify|validate|count|_count|exists|_exists|_is_|_has_|detect|find_)|(_ok|_exists|_count|_valid|_fresh|_stale|_alive|_running|_healthy)$"
)


def is_loud(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Raise):
            return True
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in LOUD:
                return True
    return False


def ret_value(h):
    for stmt in h.body:
        if isinstance(stmt, ast.Return):
            return "None" if stmt.value is None else ast.unparse(stmt.value)[:60]
    return None


out, seen = [], set()
for path in sorted(ROOT.rglob("*.py")):
    if path.name in TRIAGED or "/tests/" in str(path):
        continue
    try:
        tree = ast.parse(path.read_text(), str(path))
    except SyntaxError:
        continue
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.ExceptHandler) or (str(path), node.lineno) in seen:
                continue
            val = ret_value(node)
            if val is None or is_loud(node):
                continue
            seen.add((str(path), node.lineno))
            confident = val in ("True", "False", "0", "[]", "{}", "''", '""', "0.0", "()")
            out.append(
                {
                    "file": str(path.relative_to(ROOT.parent)),
                    "line": node.lineno,
                    "func": fn.name,
                    "exc": ast.unparse(node.type) if node.type else "bare",
                    "returns": val,
                    "verdict_shaped": bool(VERDICTY.search(fn.name)),
                    "confident_value": confident,
                }
            )
json.dump(out, sys.stdout, indent=0)
