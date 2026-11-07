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


# ── the wider shapes (pre-publish sweep for 1.14.22) ───────────────────────────────────────────────────────────────────
#
# The walk above matches a refusal print immediately followed by a bare return, and a printed name returned. The sweep found
# the same defects one step removed: a refusal printed in an if/else and a shared bare return after it, a statement between
# the print and the return, a dict literal returned after printing. KNOWN_OLDER lists the handlers that had that shape before
# 1.14.22 (by file and function: line numbers move); a new one fails here, and the list only shrinks.

KNOWN_OLDER = {
    ("_workflow_preflight.py", "handle_preflight_submit_command"),
    ("config_commands.py", "handle_config_show_command"),
    ("domain_commands.py", "handle_domain_validate_command"),
    ("edit_verification_command.py", "handle_edit_with_confidence_command"),
    (
        "goal_commands.py",
        "handle_goals_complete_task_command",
    ),  # exits 1 via sys.exit before the return: a false positive
    ("goal_commands.py", "handle_goals_progress_command"),
    ("goal_commands.py", "handle_goals_get_tasks_command"),
    ("identity_commands.py", "handle_identity_create_command"),
    ("identity_commands.py", "handle_identity_export_command"),
    ("memory_commands.py", "handle_session_rollup_command"),
    ("monitor_commands.py", "handle_mco_load_command"),
    ("monitor_commands.py", "handle_system_status_command"),
    ("projects_commands.py", "handle_projects_unregister_command"),
    ("session_commands.py", "handle_sessions_show_command"),
    ("setup_claude_code.py", "handle_setup_claude_code_command"),
    ("utility_commands.py", "handle_qdrant_cleanup_command"),
    ("utility_commands.py", "handle_qdrant_status_command"),
    ("workspace_commands.py", "handle_workspace_backfill_entities_command"),
}


def _prints_json(node) -> bool:
    return _json_print_arg(node) is not None


def _bare_return(node) -> bool:
    return isinstance(node, ast.Return) and (
        node.value is None or (isinstance(node.value, ast.Constant) and node.value.value is None)
    )


def _wide_hits() -> set[tuple[str, str]]:
    found = set()
    for name, fn in _handlers():
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
                        found.add((name, fn.name))
    return found


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
