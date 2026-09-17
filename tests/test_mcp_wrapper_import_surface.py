"""Core must not silently remove what `empirica-mcp` imports from it.

`empirica-mcp` is a thin wrapper (9 files) that reaches core two ways:

  * **subprocess** for execution — stable, nothing to break
  * **import** for schema generation — it introspects core's argparse parser to
    build MCP tool schemas

That second path is the coupling, and it is exactly two symbols:
`empirica.cli.cli_core.create_argument_parser` and
`empirica.utils.session_resolver.get_active_project_path`. Neither is marked
private, and nothing in core's suite recorded that anything outside core depends
on them.

**Why the test lives in CORE and not in the wrapper.** A test in `empirica-mcp`
fails when the wrapper is built — after core has already shipped the removal. A
test here fails in the commit that removes the symbol, which is the only moment
someone can cheaply choose a different refactor. The dependency direction is
wrapper→core, so the guard has to run against the side that moves.

This is deliberately NOT a defence of the `empirica==<exact>` pin in
`empirica-mcp/pyproject.toml`. That pin is under review: an exact equality drags
core BACK DOWN under a shared interpreter whenever the pair is split
(`pip install -U empirica` on a box carrying empirica-mcp self-reverts), and it is
protecting a two-symbol surface that this test can protect directly. Measured on a
client box at core 1.13.33 → 1.13.47.
"""

from __future__ import annotations

import inspect

import pytest

# (module path, symbol) — the surface empirica-mcp imports. Grepped from
# empirica-mcp/, not assumed.
_WRAPPER_IMPORTS = [
    ("empirica.cli.cli_core", "create_argument_parser"),
    ("empirica.utils.session_resolver", "get_active_project_path"),
]


@pytest.mark.parametrize(("module_path", "symbol"), _WRAPPER_IMPORTS)
def test_the_symbol_empirica_mcp_imports_still_exists(module_path: str, symbol: str):
    import importlib

    mod = importlib.import_module(module_path)

    assert hasattr(mod, symbol), (
        f"empirica-mcp imports {module_path}.{symbol} and it is gone. The wrapper "
        "builds its MCP tool schemas by introspecting this, so removing it breaks "
        "every MCP install — and it breaks them at THEIR build, not in this commit. "
        "Either keep the symbol, or update empirica-mcp and this list together."
    )


def test_create_argument_parser_is_callable_with_no_required_args():
    """The wrapper calls it bare — a new required parameter is a breaking change.

    "The symbol exists" and "the wrapper can still call it" are different claims,
    and only the second is what the wrapper needs.
    """
    from empirica.cli.cli_core import create_argument_parser

    sig = inspect.signature(create_argument_parser)
    required = [
        p
        for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]

    assert not required, (
        f"create_argument_parser grew required parameters {[p.name for p in required]}; "
        "empirica-mcp calls it with none, so this is a silent break in the wrapper."
    )


def test_the_parser_it_returns_is_introspectable():
    """The wrapper walks subparsers to generate schemas.

    A parser that returns fine but exposes no actions would produce an empty tool
    list — an MCP server that starts, reports healthy, and offers nothing. Same
    shape as the rest of this family: a working-looking failure.
    """
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()

    assert hasattr(parser, "_actions"), "the wrapper needs an argparse-shaped object"
    assert parser._actions, "a parser with no actions yields an MCP server with no tools"


def test_get_active_project_path_accepts_the_optional_session_id():
    """The wrapper passes a session id positionally-or-not; both must keep working."""
    from empirica.utils.session_resolver import get_active_project_path

    sig = inspect.signature(get_active_project_path)
    params = list(sig.parameters.values())

    assert params, "expected at least the session-id parameter"
    assert params[0].default is not inspect.Parameter.empty, (
        "the first parameter lost its default; the wrapper calls this with no arguments"
    )


def test_the_import_list_matches_what_the_wrapper_actually_imports():
    """Guards this file's own premise.

    The list above was grepped from empirica-mcp once. If the wrapper starts
    importing a third symbol and nobody updates this, the guard silently covers
    less than it claims — a partial check reading as a complete one, which is the
    defect this whole area keeps producing.
    """
    from pathlib import Path

    wrapper = Path(__file__).resolve().parent.parent / "empirica-mcp"
    if not wrapper.is_dir():
        pytest.skip("empirica-mcp not present in this checkout")

    found: set[tuple[str, str]] = set()
    for py in wrapper.rglob("*.py"):
        for line in py.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("from empirica.") and " import " in line:
                mod, _, names = line[len("from ") :].partition(" import ")
                for name in names.split("#")[0].split(","):
                    name = name.strip().split(" as ")[0].strip("() ")
                    if name:
                        found.add((mod.strip(), name))

    declared = set(_WRAPPER_IMPORTS)
    missing = found - declared
    assert not missing, (
        f"empirica-mcp imports {sorted(missing)} from core, and this guard does not "
        "cover them. Add them to _WRAPPER_IMPORTS so a core refactor that removes "
        "one fails here rather than in the wrapper's build."
    )
