"""Every substring or prefix matcher over a module-level keyword list is registered with a near-miss test (goal 3fcde812).

A hand probe of 12 such matchers found 8 firing on near-miss text: a word containing the keyword, a flag in another spelling,
the keyword quoted in source. Every one had positive tests only (broccoli §C, "Near-miss firing").

The scan finds ``any(k in text for k in CONST)`` and ``text.startswith(CONST)`` / ``endswith(CONST)`` where CONST is a
module-level tuple, list or set of at least two string literals. A matcher found here must be either registered in
NEAR_MISS_TESTS with the test that holds its near miss (that test must exist), or listed in KNOWN_UNTESTED, which only
shrinks. A new matcher with neither fails the suite. Inline literal tuples and regex tables are not scanned yet.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "empirica"

#: "<path relative to the repo>:<CONST>" -> "<test file>::<test name>" holding the text the matcher must NOT fire on.
NEAR_MISS_TESTS: dict[str, str] = {
    "empirica/plugins/claude-code-integration/hooks/tool-router.py:_NOTIFICATION_STARTS": (
        "tests/test_tool_router_skips_notifications.py::test_a_prompt_that_quotes_a_notification_is_still_a_prompt"
    ),
}

#: Matchers that existed when the inventory began (2026-11-13), without a near-miss test. Add one and move the entry to
#: NEAR_MISS_TESTS; never add to this set. The Sentinel's lists are discipline, not security (David 2026-11-11): a near miss
#: there over-gates a read, which is still worth a test, never a reason to grow the list.
KNOWN_UNTESTED: set[str] = {
    "empirica/cli/command_handlers/goal_commands.py:_RULING_PREFIXES",
    "empirica/cli/command_handlers/provision_practice_commands.py:_GITIGNORE_RULES",
    "empirica/cli/command_handlers/scan_commands.py:_INTERPRETERS",
    "empirica/core/cockpit/instance_actions.py:_PANE_BUSY_HINTS",
    "empirica/core/docs/semantic_scan.py:SKIP_PATTERNS",
    "empirica/core/scanner/env_names.py:_INTERESTING_FRAGMENTS",
    "empirica/core/sentinel_discipline.py:_PATCH_REMOVES",
    "empirica/local_server/vectors.py:_NOT_FOUND",
    "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py:EMPIRICA_READ_SUFFIXES",
    "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py:TRANSITION_COMMANDS",
    "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py:_ALLOW_REASONS_FOR_THE_MODEL",
    "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py:_EXEC_ENV_PREFIXES",
    "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py:_GIT_INERT_GLOBAL_VALUED",
    "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py:_JOURNALCTL_MUTATING",
    "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py:_NVIDIA_SMI_READ_PREFIXES",
    "empirica/plugins/claude-code-integration/hooks/tool-failure.py:IGNORE_PATTERNS",
}


def _string_constants(tree: ast.Module) -> set[str]:
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if (
                len(targets) == 1
                and isinstance(targets[0], ast.Name)
                and isinstance(value, (ast.Tuple, ast.List, ast.Set))
                and len(value.elts) >= 2
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts)
            ):
                out.add(targets[0].id)
    return out


def scan_matchers(text: str) -> set[str]:
    """The module constants ``text`` (a module's source) uses as substring or prefix matchers."""
    tree = ast.parse(text)
    consts = _string_constants(tree)
    found = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.GeneratorExp)
            and node.generators
            and isinstance(node.generators[0].iter, ast.Name)
            and node.generators[0].iter.id in consts
            and isinstance(node.elt, ast.Compare)
            and any(isinstance(op, ast.In) for op in node.elt.ops)
        ):
            found.add(node.generators[0].iter.id)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("startswith", "endswith")
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in consts
        ):
            found.add(node.args[0].id)
    return found


@functools.lru_cache(maxsize=1)
def _inventory() -> frozenset[str]:
    """Walked once per process: parsing the tree is the whole cost (goal: under a second added to the suite)."""
    found = set()
    for path in SRC.rglob("*.py"):
        try:
            text = path.read_text()
            if "any(" not in text and "startswith(" not in text and "endswith(" not in text:
                continue  # no shape the scan looks for: skip the parse
            names = scan_matchers(text)
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        found |= {f"{rel}:{name}" for name in names}
    return frozenset(found)


def test_every_matcher_is_registered_with_a_near_miss_test():
    unregistered = _inventory() - set(NEAR_MISS_TESTS) - KNOWN_UNTESTED
    assert not unregistered, (
        "matcher(s) over a keyword list with no near-miss test: add a test holding the nearest text each must NOT fire on "
        f"(a word containing the keyword, another spelling, the keyword quoted), then register it here: {sorted(unregistered)}"
    )


def test_the_known_untested_list_only_names_matchers_that_still_exist():
    """A matcher that was removed or renamed leaves the list, so the list stays the backlog it claims to be."""
    assert KNOWN_UNTESTED - _inventory() == set()
    assert not KNOWN_UNTESTED & set(NEAR_MISS_TESTS), "tested matchers leave KNOWN_UNTESTED"


def test_each_registered_test_exists():
    for matcher, ref in NEAR_MISS_TESTS.items():
        file, _, name = ref.partition("::")
        assert f"def {name}(" in (ROOT / file).read_text(), f"{matcher}: {ref} not found"


# ── the scan's own controls ────────────────────────────────────────────────────────────────────────────────────────────


def test_the_scan_finds_each_shape_it_claims_to():
    src = """
KEYWORDS = ("alpha", "beta")
PREFIXES = ["git ", "gh "]
def a(text):
    return any(k in text for k in KEYWORDS)
def b(cmd):
    return cmd.startswith(PREFIXES)
"""
    assert scan_matchers(src) == {"KEYWORDS", "PREFIXES"}


def test_the_scan_leaves_exact_membership_and_non_string_lists_alone():
    src = """
STATUSES = ("open", "closed")
NUMBERS = (1, 2, 3)
def a(status):
    return status in STATUSES          # exact membership: no near-miss class
def b(n, text):
    return any(str(x) in text for x in NUMBERS)
"""
    assert scan_matchers(src) == set()


def test_the_inventory_walked_real_files():
    assert len(_inventory()) >= len(KNOWN_UNTESTED)  # the scan is not vacuously green
