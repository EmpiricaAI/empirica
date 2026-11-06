"""A command handler prints its answer once, and a refusal exits non-zero.

The dispatcher (cli_core._handle_command_result) prints any dict a handler returns, and exits 0 on None. So a handler that
prints its own JSON and then returns the dict prints it twice (`goals-add-task --output json` on an unknown goal gave two JSON
documents), and one that prints a refusal and returns None exits 0 (`empirica note` with no text did). An AST walk over the
handlers found 14 of the first shape and 5 of the second on 2026-11-07; this keeps the count at zero.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HANDLERS = REPO / "empirica" / "cli" / "command_handlers"


def _json_print_arg(node):
    """print(json.dumps(X, ...)) -> X."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print" and node.args:
        first = node.args[0]
        if (
            isinstance(first, ast.Call)
            and isinstance(first.func, ast.Attribute)
            and first.func.attr == "dumps"
            and first.args
        ):
            return first.args[0]
    return None


def _is_refusal_print(node) -> bool:
    if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print" and node.args):
        return False
    text = ast.unparse(node.args[0])
    return '"ok": False' in text or "'ok': False" in text or text.lstrip("f").lstrip("'\"").startswith("❌")


def _handlers():
    for path in sorted(HANDLERS.rglob("*.py")):
        for fn in ast.walk(ast.parse(path.read_text())):
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("handle_"):
                yield path.name, fn


def test_no_handler_returns_a_dict_it_already_printed():
    found = []
    for name, fn in _handlers():
        printed = {x.id for node in ast.walk(fn) if isinstance(x := _json_print_arg(node), ast.Name)}
        found += [
            f"{name}:{node.lineno} {fn.name}"
            for node in ast.walk(fn)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Name) and node.value.id in printed
        ]
    assert found == []


def test_no_handler_prints_a_refusal_and_returns_none():
    found = []
    for name, fn in _handlers():
        for node in ast.walk(fn):
            for field in ("body", "orelse", "finalbody"):
                stmts = getattr(node, field, None)
                if not isinstance(stmts, list):
                    continue
                for a, b in pairwise(stmts):
                    if (
                        isinstance(a, ast.Expr)
                        and _is_refusal_print(a.value)
                        and isinstance(b, ast.Return)
                        and (b.value is None or (isinstance(b.value, ast.Constant) and b.value.value is None))
                    ):
                        found.append(f"{name}:{b.lineno} {fn.name}")
    assert found == []


def test_the_scan_sees_both_shapes(tmp_path):
    """Positive control: the walk above finds each shape in a handler written to have it."""
    src = """
import json
def handle_a(args):
    result = {"ok": True}
    print(json.dumps(result))
    return result
def handle_b(args):
    print(json.dumps({"ok": False, "error": "x"}))
    return
"""
    tree = ast.parse(src)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert any(isinstance(_json_print_arg(n), ast.Name) for n in ast.walk(fns[0]))
    first = fns[1].body[0]
    assert isinstance(first, ast.Expr) and _is_refusal_print(first.value)


def _cli(tmp_path, *argv):
    home = tmp_path / "home"
    (home / ".empirica").mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(REPO)}
    return subprocess.run(
        [sys.executable, "-m", "empirica.cli", *argv],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=120,
    )


def _documents(text: str) -> list:
    decoder, docs, i = json.JSONDecoder(), [], 0
    text = text.strip()
    while i < len(text):
        doc, end = decoder.raw_decode(text, i)
        docs.append(doc)
        i = end
        while i < len(text) and text[i].isspace():
            i += 1
    return docs


def test_goals_add_task_on_an_unknown_goal_prints_one_document_and_exits_1(tmp_path):
    proc = _cli(tmp_path, "goals-add-task", "--goal-id", "00000000-dead", "--description", "x", "--output", "json")

    docs = _documents(proc.stdout)
    assert proc.returncode == 1
    assert len(docs) == 1 and docs[0]["ok"] is False


def test_note_without_text_exits_1(tmp_path):
    proc = _cli(tmp_path, "note", "--output", "json")

    assert proc.returncode == 1
    assert json.loads(proc.stdout.strip().splitlines()[-1])["ok"] is False
