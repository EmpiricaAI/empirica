"""The default project-search must read the collection artifacts are written to.

`project-search --type` defaults to `focused`, and `focused` resolved to
[docs, eidetic, episodic] — omitting `memory`, which is exactly where
`finding-log` / `decision-log` / `mistake-log` / `deadend-log` write. A practice
could not retrieve its own artifacts by searching its own project.

Nothing failed loudly. The `memory` key was absent from the result dict entirely
rather than present-and-empty, so the renderer's memory band never fired and the
absence looked like "no matches" instead of "never asked". Cortex lost three weeks
on a client pipeline to it, re-deriving from code a finding they had logged
themselves.

These pin the WRITE-PATH/READ-PATH CONTRACT, not just the current constant: any
collection an artifact verb writes to must be reachable from the CLI default.
"""

from __future__ import annotations

import pytest

from empirica.core.qdrant import memory as qmem

# Collections that artifact-logging verbs write into. If a verb starts writing
# somewhere new, add it here — that is the point of the test.
ARTIFACT_COLLECTIONS = ["memory"]

CLI_DEFAULT_KIND = "focused"


def _kinds_for(kind: str) -> list[str]:
    """Resolve a `kind` to its collection list without touching Qdrant.

    `search()` resolves kinds and then returns `empty_result` (a dict keyed by
    exactly those collections) when Qdrant is unavailable — so the keys of that
    dict ARE the resolution, and we can assert on it with no live service.
    """
    return sorted(qmem.search("00000000-0000-0000-0000-000000000000", "q", kind=kind).keys())


@pytest.mark.parametrize("collection", ARTIFACT_COLLECTIONS)
def test_cli_default_searches_every_artifact_collection(collection):
    """THE regression. If this fails, artifacts are write-only under the default."""
    kinds = _kinds_for(CLI_DEFAULT_KIND)
    assert collection in kinds, (
        f"'{collection}' is written by the artifact-logging verbs but the CLI default "
        f"(--type {CLI_DEFAULT_KIND}) resolves to {kinds}. Artifacts would be write-only."
    )


def test_default_still_includes_the_context_collections():
    """Adding memory must not have displaced what focused already covered."""
    kinds = _kinds_for(CLI_DEFAULT_KIND)
    for expected in ("docs", "eidetic", "episodic"):
        assert expected in kinds


def test_all_remains_an_alias_of_focused():
    """`all` is documented as backward compat. Now that focused carries memory the
    two sets are identical — pinned so a future edit to one does not silently make
    `all` broader than the default again, which is how this bug looked originally."""
    assert _kinds_for("focused") == _kinds_for("all")


def test_intelligence_still_skips_docs():
    """Cortex's cross-project lane is deliberately doc-free — unchanged by this fix."""
    kinds = _kinds_for("intelligence")
    assert "docs" not in kinds
    assert "memory" in kinds


def test_parser_default_is_the_kind_this_module_pins():
    """Guards the other half: these tests assert about `focused`, so they are only
    meaningful while `focused` is what the CLI actually defaults to."""
    import argparse

    from empirica.cli.parsers.checkpoint_parsers import add_checkpoint_parsers

    parser = argparse.ArgumentParser()
    add_checkpoint_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["project-search", "--project-id", "p", "--task", "t"])
    assert args.type == CLI_DEFAULT_KIND


def test_boosted_collections_are_reachable_from_the_default():
    """`_COLLECTION_BOOST` weighted `memory` at 1.2 while the default never queried
    it — tuning relevance for a collection you do not search. That mismatch was the
    tell, so pin the general invariant: anything boosted above the docs baseline
    should be reachable from the default or from `intelligence`."""
    reachable = set(_kinds_for(CLI_DEFAULT_KIND)) | set(_kinds_for("intelligence"))
    boost = qmem.search.__globals__.get("_COLLECTION_BOOST")
    if boost is None:  # defined inside search(); skip rather than assert a false pass
        pytest.skip("_COLLECTION_BOOST is function-local; covered by the explicit tests above")
    for name, weight in boost.items():
        if weight >= 1.0:
            assert name in reachable, f"'{name}' is boosted to {weight} but unreachable"
