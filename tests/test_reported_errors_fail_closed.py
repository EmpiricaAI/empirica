"""A command that PRINTS an error must not EXIT successfully.

``handle_cli_error`` returns None, and ``_handle_command_result`` mapped a None
return to exit 0. Measured by AST walk 2026-09-03: **107 of 152** call sites
report an error and then fall off the end of their except block. So the command
prints ``❌ ... error: ...`` and exits 0, and a scripted caller checking the exit
code records a goal or artifact that does not exist.

Observed twice in one session on ``goals-create``: once rejecting an invalid
criterion, once on a UNIQUE-constraint save failure that had already built the
correct ``ok: False`` result and then returned None over the top of it.

Broccoli: *decision-downgraded-across-a-boundary*. The handler decided DENY and
the dispatcher's vocabulary (None) could not express it, so it degraded to ALLOW.
The fix belongs at the boundary — patching 107 handlers would fix those 107 and
leave the 108th to be written wrong.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from empirica.cli.cli_core import _handle_command_result
from empirica.cli.cli_utils import errors_reported, handle_cli_error, reset_reported_errors


class _Args:
    output = "json"


@pytest.fixture(autouse=True)
def _clean():
    reset_reported_errors()
    yield
    reset_reported_errors()


def test_a_reported_error_makes_a_none_return_exit_nonzero():
    """THE regression."""
    handle_cli_error(ValueError("boom"), "Create goal")

    assert _handle_command_result(None, _Args()) == 1


def test_a_clean_none_return_still_exits_zero():
    """NEGATIVE CONTROL, and the failure mode worse than the bug: most handlers
    legitimately return None on success."""
    assert _handle_command_result(None, _Args()) == 0


def test_an_explicit_nonzero_return_still_wins():
    handle_cli_error(ValueError("boom"), "x")
    assert _handle_command_result(3, _Args()) == 3


def test_an_ok_false_dict_is_unaffected():
    assert _handle_command_result({"ok": False, "error": "e"}, _Args()) == 1


def test_an_ok_true_dict_is_unaffected_even_after_a_reported_error():
    """A handler that reported a recoverable error and then genuinely succeeded
    must still be allowed to say so — the dict is an explicit verdict and the
    fall-through guard must not override one."""
    handle_cli_error(ValueError("recovered"), "x")
    assert _handle_command_result({"ok": True}, _Args()) == 0


def test_broken_pipe_is_not_recorded_as_an_error():
    """Piping to `head` is normal. It was already exempt from printing; it must
    stay exempt from the exit code, or every `| head` becomes a failure."""
    handle_cli_error(BrokenPipeError(), "x")

    assert errors_reported() == []
    assert _handle_command_result(None, _Args()) == 0


def test_the_reported_error_is_retrievable_not_just_a_flag():
    """A bare bool would give an exit code nobody can explain."""
    handle_cli_error(ValueError("boom"), "Create goal")

    assert any("Create goal" in e and "boom" in e for e in errors_reported())


# ── the class, measured ──────────────────────────────────────────────────────


def _fallthrough_sites() -> list[str]:
    root = Path(__file__).parent.parent / "empirica"
    out = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "handle_cli_error"
                for n in ast.walk(node)
            ):
                continue
            last = node.body[-1] if node.body else None
            terminating = isinstance(last, ast.Raise) or (
                isinstance(last, ast.Return)
                and last.value is not None
                and not (isinstance(last.value, ast.Constant) and last.value.value is None)
            )
            if not terminating:
                out.append(f"{path.relative_to(root)}:{node.lineno}")
    return out


def test_the_scan_is_live():
    """POSITIVE CONTROL. This measurement is the justification for fixing at the
    boundary rather than per-handler, so an instrument that silently matched
    nothing would make the whole argument unfalsifiable."""
    root = Path(__file__).parent.parent / "empirica"
    calls = sum(src.count("handle_cli_error(") for src in (p.read_text() for p in root.rglob("*.py")))

    assert calls > 100, f"expected the known ~152 call sites, found {calls}"


def test_the_fallthrough_shape_is_still_widespread_and_that_is_now_SAFE():
    """Not a cleanup target. These handlers stay as they are — the point of the
    boundary fix is that this shape is no longer a silent success. If someone
    later 'fixes' them one by one and this drops to zero, the boundary guard
    would still be needed for the next one written, so this documents the
    intent rather than demanding a number."""
    sites = _fallthrough_sites()

    assert len(sites) > 50, (
        f"only {len(sites)} fall-through sites found — if these were individually "
        "patched, confirm the boundary guard in _handle_command_result survived"
    )
