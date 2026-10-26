"""A test that measures the box instead of the code is a test whose green means nothing (goal c8e5c548, the review of the tests themselves).

A whole-suite run in a clean environment (`env -i HOME=<empty> PATH=/usr/bin:/bin`) failed 259 of 11,756 tests, in nine files. Every one spawned a
bare `empirica`, `python` or `ruff` from PATH: they passed on a developer box and in CI only because those binaries happened to be installed there,
they tested whatever was installed rather than this source tree, and four of them passed only because the repo's own `.empirica` was in the working
directory. This file turns that finding into a floor. It scans every test file's AST for the three classes that are objectively wrong and found at zero:

* **bare tool**: `subprocess.run(["empirica" | "python" | "ruff" | ..., ...])`. Use `[sys.executable, "-m", "empirica.cli", ...]` (or `-m ruff`);
* **short hang guard**: a CLI launch with `timeout=` under 30 s. A cold CLI start takes about a second and four times that under load, so a tight
  timeout is a load-flaky assertion about the machine, not a guard against a hang (the old CLI file failed 3 of 3 under full-suite load);
* **multi-valued return code**: `assert r.returncode in [0, 1]` passes on a crash that exits 1 and says nothing about what the command did.

Every detector is shown a snippet that violates it before it is trusted on the real tree: a scan that cannot flag a violation reports clean forever.
`git`, `sh`, `tmux` and the like are system binaries, not this project's tools, and are not flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SPAWNERS = {"run", "check_output", "check_call", "call", "Popen"}
#: Tools this project installs. Spawned from PATH they test the box, not the tree. (System binaries such as git are deliberately absent.)
OWN_TOOLS = {"empirica", "empirica-mcp", "python", "python3", "ruff", "pyright", "pytest", "radon", "vulture"}
MIN_HANG_GUARD_SECONDS = 30


def _spawn_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SPAWNERS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.args
        ):
            yield node


def bare_tool_calls(source: str) -> list[tuple[int, str]]:
    """(line, tool) for each subprocess call whose command list starts with one of this project's tools spelled as a bare string."""
    found = []
    for call in _spawn_calls(ast.parse(source)):
        command = call.args[0]
        if (
            isinstance(command, (ast.List, ast.Tuple))
            and command.elts
            and isinstance(command.elts[0], ast.Constant)
            and command.elts[0].value in OWN_TOOLS
        ):
            found.append((call.lineno, command.elts[0].value))
    return found


def _launches_cli(command: ast.expr) -> bool:
    text = ast.unparse(command)
    return "empirica.cli" in text or (
        isinstance(command, (ast.List, ast.Tuple))
        and bool(command.elts)
        and isinstance(command.elts[0], ast.Constant)
        and command.elts[0].value == "empirica"
    )


def short_hang_guards(source: str) -> list[tuple[int, float]]:
    """(line, seconds) for each CLI launch with a `timeout=` under MIN_HANG_GUARD_SECONDS."""
    found = []
    for call in _spawn_calls(ast.parse(source)):
        if not _launches_cli(call.args[0]):
            continue
        for kw in call.keywords:
            if (
                kw.arg == "timeout"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, (int, float))
                and kw.value.value < MIN_HANG_GUARD_SECONDS
            ):
                found.append((call.lineno, kw.value.value))
    return found


def multi_valued_return_codes(source: str) -> list[tuple[int, str]]:
    """(line, assertion) for `assert <x>.returncode in [a, b, ...]`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
            test = node.test
            if (
                "returncode" in ast.unparse(test.left)
                and isinstance(test.ops[0], ast.In)
                and isinstance(test.comparators[0], (ast.List, ast.Tuple, ast.Set))
                and len(test.comparators[0].elts) > 1
            ):
                found.append((node.lineno, ast.unparse(test)))
    return found


# ── positive controls: each detector must flag the violation it exists for, and leave the sound form alone ──


@pytest.mark.parametrize("tool", sorted(OWN_TOOLS))
def test_the_bare_tool_detector_flags_each_project_tool(tool):
    assert bare_tool_calls(f'import subprocess\nsubprocess.run(["{tool}", "--help"])\n') == [(2, tool)]


def test_the_bare_tool_detector_flags_a_multi_line_command_list_and_check_output():
    source = (
        'import subprocess\nsubprocess.check_output(\n    [\n        "empirica",\n        "goals-list",\n    ]\n)\n'
    )
    assert bare_tool_calls(source) == [(2, "empirica")]


def test_the_bare_tool_detector_leaves_the_sound_forms_alone():
    sound = (
        "import subprocess, sys\n"
        'subprocess.run([sys.executable, "-m", "empirica.cli", "check", "--help"])\n'
        'subprocess.run(["git", "init"])\n'
        'subprocess.run(["tmux", "list-sessions"])\n'
        'subprocess.run(["sh", "-c", "true"])\n'
    )
    assert bare_tool_calls(sound) == []


def test_the_short_hang_guard_detector_flags_a_tight_cli_timeout():
    assert short_hang_guards('subprocess.run(["empirica", "x"], timeout=5)\n') == [(1, 5)]
    assert short_hang_guards('subprocess.run([sys.executable, "-m", "empirica.cli", "x"], timeout=10)\n') == [(1, 10)]


def test_the_short_hang_guard_detector_leaves_other_processes_and_generous_guards_alone():
    assert short_hang_guards('subprocess.run([sys.executable, "-m", "empirica.cli", "x"], timeout=60)\n') == []
    assert short_hang_guards('subprocess.run(["git", "status"], timeout=5)\n') == [], (
        "a short timeout on git is not a CLI hang guard"
    )
    assert short_hang_guards('subprocess.run([sys.executable, "-m", "empirica.cli", "x"])\n') == []


def test_the_return_code_detector_flags_a_multi_valued_assertion_only():
    assert multi_valued_return_codes("assert result.returncode in [0, 1]\n") == [(1, "result.returncode in [0, 1]")]
    assert multi_valued_return_codes("assert result.returncode in (0, 2)\n")
    assert multi_valued_return_codes("assert result.returncode == 0\n") == []
    assert multi_valued_return_codes("assert result.returncode in [0]\n") == []
    assert multi_valued_return_codes("assert value in [0, 1]\n") == [], "only return codes are in scope"


# ── enforcement over the real tree ──


def _scan(detector) -> list[str]:
    hits = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path == Path(__file__).resolve():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            found = detector(source)
        except SyntaxError:
            continue
        hits.extend(f"{path.relative_to(TESTS_DIR.parent)}:{line}: {detail}" for line, detail in found)
    return hits


def test_the_scan_walks_the_tree_it_guards():
    """Control: a scan over an empty file list also reports clean."""
    assert sum(1 for _ in TESTS_DIR.rglob("test_*.py")) > 500


def test_no_test_spawns_a_bare_project_tool_from_path():
    hits = _scan(bare_tool_calls)
    assert not hits, (
        'A test spawns a bare tool from PATH and tests the box, not this tree. Use [sys.executable, "-m", "empirica.cli", ...] '
        "(or -m ruff):\n  " + "\n  ".join(hits)
    )


def test_no_cli_launch_has_a_hang_guard_under_thirty_seconds():
    hits = _scan(short_hang_guards)
    assert not hits, (
        f"A CLI launch has a timeout under {MIN_HANG_GUARD_SECONDS} s; a cold start is ~1 s and 4x that under load, so this fails under "
        "load without a hang. Use 60 s or more as a hang guard:\n  " + "\n  ".join(hits)
    )


def test_no_test_accepts_several_return_codes_as_passing():
    hits = _scan(multi_valued_return_codes)
    assert not hits, (
        "assert returncode in [...] passes on a crash that exits non-zero; assert the code the command is documented to give:\n  "
        + "\n  ".join(hits)
    )
