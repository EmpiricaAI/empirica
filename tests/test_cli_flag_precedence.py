"""A truthy default before an `or` fallback silently discards the CLI flag.

The instance: `decision-log --reversibility committal` stored `exploratory`, with no
error and no warning, for every pure-flag invocation. The line was

    reversibility = cfg.get("reversibility", "exploratory") or getattr(args, "reversibility", ...)

`cfg` is `config_data or {}` — empty on a flag-only call — so `.get()` returned the
literal default `"exploratory"`, which is truthy, so `or` short-circuited and `args`
was never read. The flag parsed fine. It was simply never consulted.

Reported by empirica-outreach 2026-08-18 with the blast radius measured on their own
graph: 335 of 343 decisions stored `exploratory`, the 8 `committal` rows having come
through the stdin-JSON path where `cfg` carries the key explicitly.

The instance is one line. The CLASS is what this file guards: any
`cfg.get(k, <truthy>) or getattr(args, k, ...)` in a command handler is the same bug
wearing a different field name, and it fails the way that is hardest to notice —
success, with the wrong value. The correct shape puts the default LAST, after every
source has had a chance to speak:

    x = cfg.get(k) or getattr(args, k, None) or <default>

Falsy defaults (`""`, `0`, `None`) are fine and common; they do not short-circuit.
This walks the AST rather than grepping so it sees the shape, not the spelling.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from empirica.data.artifact_fields import updatable_fields

HANDLERS_DIR = Path(__file__).resolve().parent.parent / "empirica" / "cli" / "command_handlers"


def _is_truthy_constant(node: ast.expr) -> bool:
    """True for a literal that would short-circuit an `or` (e.g. "x", 1, True)."""
    return isinstance(node, ast.Constant) and bool(node.value)


def _short_circuiting_defaults(tree: ast.AST) -> list[tuple[int, str]]:
    """Find `<mapping>.get(key, <truthy literal>) or getattr(...)` expressions."""
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        first = node.values[0]
        if not (
            isinstance(first, ast.Call)
            and isinstance(first.func, ast.Attribute)
            and first.func.attr == "get"
            and len(first.args) == 2
            and _is_truthy_constant(first.args[1])
        ):
            continue
        # Only a problem when a LATER operand is a real fallback source that the
        # short circuit prevents from ever being evaluated.
        falls_back_to_args = any(
            isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "getattr" for v in node.values[1:]
        )
        if falls_back_to_args:
            key = first.args[0].value if isinstance(first.args[0], ast.Constant) else "<expr>"
            offenders.append((node.lineno, str(key)))
    return offenders


@pytest.mark.parametrize("path", sorted(HANDLERS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_no_truthy_default_short_circuits_a_flag_fallback(path: Path):
    offenders = _short_circuiting_defaults(ast.parse(path.read_text(), filename=str(path)))
    assert not offenders, (
        f"{path.name}: a truthy default before `or getattr(args, ...)` means the CLI flag is "
        f"parsed and never read — the value is silently wrong, not missing. "
        f"Put the default LAST: cfg.get(k) or getattr(args, k, None) or <default>. "
        f"Offending (line, key): {offenders}"
    )


def test_the_guard_actually_fires_on_the_original_shape():
    """Negative control — an absence proved by an instrument never shown to be live is not evidence."""
    original = 'reversibility = cfg.get("reversibility", "exploratory") or getattr(args, "reversibility", "x")'
    assert _short_circuiting_defaults(ast.parse(original)) == [(1, "reversibility")]

    fixed = 'reversibility = cfg.get("reversibility") or getattr(args, "reversibility", None) or "exploratory"'
    assert _short_circuiting_defaults(ast.parse(fixed)) == []

    falsy_default_is_fine = 'rationale = cfg.get("rationale", "") or getattr(args, "rationale", "")'
    assert _short_circuiting_defaults(ast.parse(falsy_default_is_fine)) == []


def test_reversibility_is_correctable_after_the_fact():
    """The repair path for rows already miscoded by the bug.

    Without this, a decision logged wrong had no supported correction at all:
    resolution closes a row, deletion destroys it, and neither is what a
    mis-tagged-but-true decision needs.
    """
    assert "reversibility" in updatable_fields("decision")


def test_the_claim_text_of_a_decision_stays_immutable():
    """Adding a metadata field must not open the claim itself."""
    assert "choice" not in updatable_fields("decision")
    assert "rationale" not in updatable_fields("decision")
