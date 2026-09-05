"""Two grounding defects, one class in opposite directions.

`fef998c1` invents an ANSWER on read-failure: PREFLIGHT returned `patterns: null`
whether retrieval broke, was never attempted, or the graph was genuinely empty —
so a practice could run with **no retrieval at all and never learn it**.

`c360aa8b` invents a DESTINATION on write-failure: an unresolvable project got
`md5(f"session-{id}")` as its project_id, minting a Qdrant collection no reader
can query. Measured 6 such collections holding 45 points of real findings and
dead-ends, up from 3 the week before.

The read-side one is the more dangerous despite being smaller: the write-side
leaves residue you can enumerate afterwards, and the read-side leaves nothing.
"""

from __future__ import annotations

from empirica.cli.command_handlers._workflow_preflight import _patterns_block
from empirica.cli.command_handlers.artifact_log_commands import UNRESOLVED_PROJECT_ID

# ── read side: patterns is never null ────────────────────────────────────────


def test_a_broken_backend_is_UNAVAILABLE_not_empty():
    """THE regression. An unreachable embedding backend used to render exactly
    like a graph with nothing to say."""
    block = _patterns_block({"__retrieval_error__": "ConnectionError: [Errno 111] refused"})

    assert block["state"] == "unavailable"
    assert "refused" in block["reason"]
    assert "ungrounded" in block["impact"]


def test_a_genuinely_empty_graph_says_EMPTY():
    """NEGATIVE CONTROL, and the state that must stay distinguishable — a new
    practice with nothing logged yet is healthy, not degraded."""
    block = _patterns_block({"lessons": [], "dead_ends": [], "retrieved_from": {"project_id": "x"}})

    assert block["state"] == "empty"


def test_retrieval_never_attempted_is_its_OWN_state():
    """Three states, not two: no project_id or no search context means retrieval
    never ran, and the remedy (supply context) differs from both the others."""
    block = _patterns_block(None)

    assert block["state"] == "not_attempted"
    assert "project_id" in block["reason"]


def test_a_populated_result_says_OK_and_keeps_its_content():
    block = _patterns_block({"lessons": [{"name": "L"}], "dead_ends": []})

    assert block["state"] == "ok"
    assert block["lessons"] == [{"name": "L"}]


def test_metadata_alone_does_not_count_as_content():
    """`retrieved_from` and `_context_budget` are always present, so counting
    them as substance would make every empty retrieval report `ok` — the
    inverse defect, and the one that would hide the fix."""
    block = _patterns_block(
        {"retrieved_from": {"project_id": "x"}, "_context_budget": {"injected_total": 0}, "lessons": []}
    )

    assert block["state"] == "empty"


def test_the_block_is_never_None():
    """The whole point. A key that can be absent cannot be distinguished from a
    key that is present and says nothing happened."""
    for candidate in (None, {}, {"lessons": []}, {"__retrieval_error__": "x"}):
        assert isinstance(_patterns_block(candidate), dict)


# ── write side: one sentinel, not N invented ids ─────────────────────────────


def test_the_fallback_id_is_a_fixed_sentinel():
    """THE regression. `md5(session-<id>)` gave every unresolved session its own
    id, so N sessions minted N collections nobody could find. One well-known
    bucket is auditable and reattachable; the safety property (do not lose the
    artifact) is unchanged."""
    assert UNRESOLVED_PROJECT_ID == "unresolved-project"


def test_the_sentinel_is_not_derived_from_anything():
    """A sentinel computed from session or time would reintroduce uniqueness
    under a different name — which is exactly how the defect looked before
    anyone enumerated the collections."""
    import ast
    import inspect

    import empirica.cli.command_handlers.artifact_log_commands as mod

    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "UNRESOLVED_PROJECT_ID":
                    assert isinstance(node.value, ast.Constant), (
                        "the unresolved-project sentinel must be a literal constant — a computed "
                        "value reintroduces the per-session uniqueness that made 6 collections invisible"
                    )
                    return
    raise AssertionError("UNRESOLVED_PROJECT_ID assignment not found")


def test_no_md5_project_id_minting_remains():
    """Structural, over the AST rather than the text.

    The first version grepped the source for `md5(...session...)` and failed on
    the COMMENT that explains what was removed — a self-referential grep, the
    fourth time that pattern has bitten in one session. Prose discussing a call
    is not the call, so only executable nodes are inspected.
    """
    import ast
    import inspect

    import empirica.cli.command_handlers.artifact_log_commands as mod

    tree = ast.parse(inspect.getsource(mod))
    minting = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in ("md5", "sha1", "sha256"):
            continue
        # A hash of session-derived text used as an identifier is the defect;
        # hashing for any other purpose is not.
        src = ast.dump(node)
        if "session" in src.lower():
            minting.append(f"line {node.lineno}: {name}(...session...)")

    assert not minting, f"a session-derived hash is being used as an id again: {minting}"
