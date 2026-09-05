"""
The `<field>` / `<field>_full` pair must carry the overflow, not discard it.

Ten payload builders had the condition inverted — `_full` was populated only
when the text already fitted in the preview, so it was None in exactly the case
it exists for. Readers everywhere are written as `payload.get("x_full") or
payload.get("x")`, so they silently received a 500-char fragment and nothing in
the response said it was one.

Measured before the fix: 483/991 points in `global_learnings` (2026-08-21, fixed
there only) and 2,507/5,833 in one practice's `memory` collection (2026-09-05).

These tests assert the CONTRACT PROPERTY — a reader can reconstruct the original
and can tell whether it had to — rather than the surface shape of any one
payload, so they survive a field being renamed or a cap being retuned.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from empirica.core.qdrant.text_preview import DEFAULT_PREVIEW, preview_fields

SRC = Path(__file__).resolve().parents[1] / "empirica"


def _read(payload: dict, name: str) -> str | None:
    """Read a preview field the way every consumer in the codebase does."""
    return payload.get(f"{name}_full") or payload.get(name)


@pytest.mark.parametrize("length", [0, 1, 499, 500, 501, 5000])
def test_reader_always_reconstructs_the_original(length):
    text = "x" * length
    got = _read(preview_fields("body", text), "body")
    assert (got or "") == text, f"reader lost {length - len(got or '')} chars at length {length}"


def test_full_is_present_exactly_when_the_preview_cannot_hold_it():
    """The inversion this file exists to prevent: `_full` set on the wrong branch."""
    short = preview_fields("body", "x" * (DEFAULT_PREVIEW - 1))
    assert short["body_full"] is None, "short text does not need a full copy — preview holds it"

    long = preview_fields("body", "x" * (DEFAULT_PREVIEW + 1))
    assert long["body_full"] is not None, (
        "THE INVERSION: overflow discarded. `_full` must be populated when the "
        "preview truncates, which is the only case it exists for."
    )


def test_truncation_is_declared_not_inferred():
    """Completeness must be checkable from the response, not re-derived by length."""
    assert preview_fields("body", "x" * DEFAULT_PREVIEW)["body_truncated"] is False
    assert preview_fields("body", "x" * (DEFAULT_PREVIEW + 1))["body_truncated"] is True
    assert preview_fields("body", None)["body_truncated"] is False


def test_custom_limit_is_honoured():
    p = preview_fields("narrative", "x" * 1500, limit=1000)
    assert len(p["narrative"]) == 1000
    assert p["narrative_truncated"] is True
    assert _read(p, "narrative") == "x" * 1500


def test_no_payload_builder_still_hand_rolls_the_inverted_condition():
    """
    Walk the AST for `<x>_full: <v> if len(<v>) <= N else None`.

    Grepping cannot distinguish the defect from a docstring quoting it — this
    file's own prose describes the inverted form, and a text search would match
    itself. So this reads the syntax, and only in real source.
    """
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:  # pragma: no cover - not our concern here
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    continue
                if not key.value.endswith("_full"):
                    continue
                # The defect shape: a conditional whose test is a <= / < length bound.
                if not isinstance(value, ast.IfExp):
                    continue
                for cmp_node in ast.walk(value.test):
                    if isinstance(cmp_node, ast.Compare) and any(
                        isinstance(op, (ast.LtE, ast.Lt)) for op in cmp_node.ops
                    ):
                        if isinstance(cmp_node.left, ast.Call) and getattr(cmp_node.left.func, "id", "") == "len":
                            offenders.append(f"{path.relative_to(SRC.parent)}:{key.lineno} {key.value}")
    assert not offenders, (
        "payload builders populating `_full` on the FITS branch (the inversion):\n  "
        + "\n  ".join(offenders)
        + "\nUse empirica.core.qdrant.text_preview.preview_fields instead."
    )


def test_the_helper_is_actually_what_the_embedders_use():
    """
    Positive control for the test above.

    An absence check proves nothing through a dead instrument: if no payload
    builder imported the helper, the AST sweep would pass on an empty search
    just as happily as on a fixed one. Assert the fix is PRESENT before
    trusting that the defect is absent.
    """
    users = [p for p in SRC.rglob("*.py") if "preview_fields(" in p.read_text() and p.name != "text_preview.py"]
    assert len(users) >= 6, f"expected the helper to be in use across the embedders, found {len(users)}: {users}"
