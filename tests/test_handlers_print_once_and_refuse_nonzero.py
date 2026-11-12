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

import pytest

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
    """print(...) of a dict literal carrying "ok": False, or of a message that starts with ❌. Read from the AST, not the
    source text: a help line that merely names the flag (`print("'ok': False marks a refusal")`) is not a refusal."""
    if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print" and node.args):
        return False
    first = node.args[0]
    for sub in ast.walk(first):
        if isinstance(sub, ast.Dict) and any(
            isinstance(k, ast.Constant) and k.value == "ok" and isinstance(v, ast.Constant) and v.value is False
            for k, v in zip(sub.keys, sub.values)
        ):
            return True
    if isinstance(first, ast.JoinedStr) and first.values:
        first = first.values[0]
    return isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith("❌")


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


# ── the wider shapes (pre-publish sweep for 1.14.22) ───────────────────────────────────────────────────────────────────
#
# The walk above matches a refusal print immediately followed by a bare return, and a printed name returned. The sweep found
# the same defects one step removed: a refusal printed in an if/else and a shared bare return after it, a statement between
# the print and the return, a dict literal returned after printing. KNOWN_OLDER lists the handlers that had that shape before
# 1.14.22 (by file and function: line numbers move); a new one fails here, and the list only shrinks.

KNOWN_OLDER: set[tuple[str, str]] = set()  # emptied by goal 494dcfee; a new instance fails the guard


def _prints_json(node) -> bool:
    return _json_print_arg(node) is not None


def _bare_return(node) -> bool:
    return isinstance(node, ast.Return) and (
        node.value is None or (isinstance(node.value, ast.Constant) and node.value.value is None)
    )


def _has_wide_shape(fn) -> bool:
    for node in ast.walk(fn):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list):
                continue
            for i, stmt in enumerate(stmts):
                before = stmts[max(0, i - 3) : i]
                refused = any(_is_refusal_print(n) for s in before for n in ast.walk(s))
                printed = any(_prints_json(n) for s in before for n in ast.walk(s))
                if (_bare_return(stmt) and refused) or (
                    isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict) and printed
                ):
                    return True
    return False


def _wide_hits() -> set[tuple[str, str]]:
    return {(name, fn.name) for name, fn in _handlers() if _has_wide_shape(fn)}


def test_no_new_handler_has_the_wider_shapes():
    assert _wide_hits() - KNOWN_OLDER == set()


def test_the_known_list_only_names_handlers_that_still_have_the_shape():
    """A fixed handler leaves the list, so the list stays the backlog it claims to be."""
    assert KNOWN_OLDER - _wide_hits() == set()


def test_resolve_of_an_unknown_id_prints_one_document_and_exits_1(tmp_path):
    proc = _cli(tmp_path, "resolve", "00000000-dead", "--output", "json")

    assert proc.returncode == 1
    assert len(_documents(proc.stdout)) == 1


def test_project_switch_to_an_unknown_project_exits_1(tmp_path):
    proc = _cli(tmp_path, "project-switch", "no-such-project-xyz", "--output", "json")

    assert proc.returncode == 1


# ── the scans' own negative controls (goal 546e7c33) ──────────────────────────────────────────────────────────────────────
#
# The positive control above shows each scan fires on a handler written to have the defect. These show it stays quiet on
# handlers that only look like one: a printed result with an exit code returned, a success printed, a help line that names
# the "ok" flag. A scan that fires on those is fixed in the scan, not by adding the handler to KNOWN_OLDER.


def _fn(src: str):
    return next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef))


def _strict_hits(fn) -> list[str]:
    printed = {x.id for node in ast.walk(fn) if isinstance(x := _json_print_arg(node), ast.Name)}
    returned = [
        "returns-printed"
        for node in ast.walk(fn)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name) and node.value.id in printed
    ]
    refusals = [
        "refusal-then-none"
        for node in ast.walk(fn)
        for field in ("body", "orelse", "finalbody")
        if isinstance(getattr(node, field, None), list)
        for a, b in pairwise(getattr(node, field))
        if isinstance(a, ast.Expr) and _is_refusal_print(a.value) and _bare_return(b)
    ]
    return returned + refusals


@pytest.mark.parametrize(
    "src",
    [
        "def handle_a(args):\n    result = {'ok': True}\n    print(json.dumps(result))\n    return 0",
        "def handle_a(args):\n    result = {'ok': True}\n    print(json.dumps(result))\n    return other",
        "def handle_a(args):\n    print(json.dumps({'ok': False, 'error': 'x'}))\n    return 1",
        "def handle_a(args):\n    print(json.dumps({'ok': True}))\n    return",
        "def handle_a(args):\n    print('\u2705 done')\n    return",
        "def handle_a(args):\n    print(\"use --output json; 'ok': False marks a refusal\")\n    return",
        "def handle_a(args):\n    print('see \"ok\": False in the docs')\n    return None",
    ],
)
def test_the_scans_stay_quiet_on_handlers_that_only_look_like_the_defect(src):
    fn = _fn(src)
    assert _strict_hits(fn) == []
    assert _has_wide_shape(fn) is False


@pytest.mark.parametrize(
    "src",
    [
        "def handle_a(args):\n    print(json.dumps({'ok': False, 'error': 'x'}))\n    return",
        'def handle_a(args):\n    print(json.dumps({"ok": False}, indent=2))\n    return None',
        "def handle_a(args):\n    print(f'\u274c {err}')\n    return",
        "def handle_a(args):\n    print('\u274c failed')\n    return",
    ],
)
def test_the_scans_still_fire_on_a_refusal_that_returns_none(src):
    assert _strict_hits(_fn(src)) == ["refusal-then-none"]


# ── the handlers KNOWN_OLDER listed, driven from the CLI (goal 494dcfee) ───────────────────────────────────────────────────
#
# Each printed its refusal and returned None (exit 0), or printed its result and returned the dict, which the dispatcher
# printed again. One JSON document, and exit 1 on a refusal.


@pytest.mark.parametrize(
    "argv",
    [
        ("config", "--section", "no-such-section", "--output", "json"),
        ("edit-with-confidence", "--file-path", "missing.py", "--old-str", "a", "--new-str", "b", "--output", "json"),
        ("goals-progress", "--goal-id", "00000000-dead", "--output", "json"),
        ("goals-get-tasks", "--goal-id", "00000000-dead", "--output", "json"),
        ("goals-complete-task", "--task-id", "00000000-dead", "--output", "json"),
        ("identity-export", "--ai-id", "nobody-here", "--output", "json"),
        ("sessions-show", "00000000-dead", "--output", "json"),
        ("system-status", "--output", "json"),
    ],
    ids=lambda a: a[0],
)
def test_an_older_handlers_refusal_exits_1(tmp_path, argv):
    proc = _cli(tmp_path, *argv)

    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    docs = _documents(proc.stdout)
    assert len(docs) <= 1 and all(d.get("ok") is False for d in docs)


def test_identity_create_over_an_existing_identity_exits_1(tmp_path):
    assert _cli(tmp_path, "identity-create", "--ai-id", "twice", "--output", "json").returncode == 0
    proc = _cli(tmp_path, "identity-create", "--ai-id", "twice", "--output", "json")

    assert proc.returncode == 1
    docs = _documents(proc.stdout)
    assert len(docs) == 1 and docs[0]["ok"] is False


def test_workspace_backfill_prints_one_document(tmp_path):
    proc = _cli(tmp_path, "workspace-backfill-entities", "--dry-run", "--output", "json")

    assert proc.returncode == 0, proc.stderr
    assert len(_documents(proc.stdout)) == 1


def test_config_output_json_prints_one_document(tmp_path):
    # The handler read args.format, which the parser never sets, so --output json printed banners and YAML.
    proc = _cli(tmp_path, "config", "--section", "personas", "--output", "json")

    assert proc.returncode == 0, proc.stderr
    assert len(_documents(proc.stdout)) == 1
